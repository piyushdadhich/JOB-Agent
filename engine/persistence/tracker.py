"""Per-profile SQLite persistence layer (schema v2).

Each profile gets its own tracker.db at data/{profile_id}/tracker.db.
The schema (data/schemas/v2/schema.sql) is auto-applied on first
construction so callers don't have to think about it.

Schema v2 splits employer state out of opportunities into a companies
table, and moves fit_score / sector / fit_reasoning into eval_decisions.
The single-table read API is preserved through join-based reads on
get_opportunity_by_*. Writes use upsert_company + insert_opportunity
directly.

All timestamps written are UTC ISO 8601 strings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


# --- Exceptions -------------------------------------------------------------

class TrackerError(Exception):
    """Base class for tracker-specific errors."""


class InvalidStatusError(TrackerError):
    """Raised when an invalid status value is supplied."""


# --- Allowed enum values (mirror schema CHECK constraints) ------------------

OPPORTUNITY_STATUSES = {"new", "shortlisted", "dismissed", "pursued"}
APPLICATION_STATUSES = {
    "drafted", "ready_to_submit", "submitted", "confirmed_received",
    "responded", "interviewing", "offered", "rejected", "ghosted",
    "silent_rejected", "withdrawn",
}
RESUME_VARIANTS = {
    "public_sector", "financial_services", "healthcare_education", "real_estate",
}
SECTORS = {
    "public_sector", "financial_services", "healthcare_education",
    "real_estate", "exploration", "unclassified",
}
COMMUNICATION_CHANNELS = {
    "email", "phone", "linkedin", "in_person", "video_call", "ats_automated",
}
COMMUNICATION_DIRECTIONS = {"inbound", "outbound"}
EVENT_TYPES = {
    "opportunity_discovered", "opportunity_seen_again",
    "opportunity_classified", "opportunity_scored",
    "opportunity_dismissed", "company_upserted",
    "application_drafted", "application_submitted",
    "status_changed", "communication_logged", "email_received", "error",
}
EVAL_TIERS = {"TOP_TIER", "STRONG", "EXPLORATORY", "SKIP", "EXCLUDED"}


# --- Project paths ----------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_PATH = _PROJECT_ROOT / "logs" / "tracker.log"
_SCHEMA_PATH = _PROJECT_ROOT / "data" / "schemas" / "v2_21" / "schema.sql"
_MIGRATION_V2_TO_V22_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_2" / "migration.sql"
)
_MIGRATION_V22_TO_V23_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_3" / "migration.sql"
)
_MIGRATION_V23_TO_V24_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_4" / "migration.sql"
)
_MIGRATION_V24_TO_V25_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_5" / "migration.sql"
)
_MIGRATION_V25_TO_V26_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_6" / "migration.sql"
)
_MIGRATION_V26_TO_V27_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_7" / "migration.sql"
)
_MIGRATION_V27_TO_V28_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_8" / "migration.sql"
)
_MIGRATION_V28_TO_V29_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_9" / "migration.sql"
)
_MIGRATION_V29_TO_V210_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_10" / "migration.sql"
)
_MIGRATION_V210_TO_V211_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_11" / "migration.sql"
)
_MIGRATION_V211_TO_V212_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_12" / "migration.sql"
)
_MIGRATION_V212_TO_V213_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_13" / "migration.sql"
)
_MIGRATION_V213_TO_V214_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_14" / "migration.sql"
)
_MIGRATION_V214_TO_V215_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_15" / "migration.sql"
)
_MIGRATION_V215_TO_V216_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_16" / "migration.sql"
)
_MIGRATION_V216_TO_V217_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_17" / "migration.sql"
)
_MIGRATION_V217_TO_V218_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_18" / "migration.sql"
)
_MIGRATION_V218_TO_V219_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_19" / "migration.sql"
)
_MIGRATION_V219_TO_V220_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_20" / "migration.sql"
)
_MIGRATION_V220_TO_V221_PATH = (
    _PROJECT_ROOT / "data" / "schemas" / "v2_21" / "migration.sql"
)


# --- Logging setup ----------------------------------------------------------

logger = logging.getLogger("tracker")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        # Tests in tmp dirs may not have logs/ — stream-only is fine.
        pass
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# --- URL normalization ------------------------------------------------------

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref"}


def _is_tracking_param(key: str) -> bool:
    k = key.lower()
    if k in _TRACKING_PARAMS:
        return True
    return any(k.startswith(p) for p in _TRACKING_PARAM_PREFIXES)


def _normalize_url(url: str) -> str:
    """Canonicalize a URL for dedup hashing.

    Strip whitespace, lowercase scheme and host, drop trailing slash,
    remove tracking query params (utm_*, fbclid, gclid, mc_*, ref),
    drop fragment. Preserves all other query params and their order.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]

    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if not _is_tracking_param(k)]
        query = urlencode(kept, doseq=True)
    else:
        query = ""

    return urlunsplit((scheme, netloc, path, query, ""))


def _hash_url(url: str) -> str:
    return hashlib.sha256(_normalize_url(url).encode("utf-8")).hexdigest()


# --- Small helpers ----------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def normalize_employer_name(name: str) -> str:
    """Lowercase, strip, collapse internal whitespace. Used for company dedup."""
    if name is None:
        return ""
    return _WS_RE.sub(" ", name.strip()).lower()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> Optional[dict]:
    return dict(row) if row is not None else None


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _to_json_or_none(value: Optional[dict]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _bool_to_int_or_none(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0


# --- Main Tracker class -----------------------------------------------------

class Tracker:
    """SQLite-backed persistence per profile."""

    def __init__(
        self,
        profile_id: str,
        db_path: Optional[Union[str, Path]] = None,
    ):
        """Open (and if needed, initialize) the tracker for a profile.

        Args:
            profile_id: Identifier matching config/profiles/{profile_id}.yaml.
                Required.
            db_path: Optional override; defaults to
                {project_root}/data/{profile_id}/tracker.db.

        The data/{profile_id}/ directory is auto-created. If the database
        is brand new (or has no schema_version table) the v2 schema is
        applied automatically.
        """
        if not profile_id:
            raise TrackerError("Tracker requires a profile_id")
        self.profile_id = profile_id

        if db_path is None:
            db_path = _PROJECT_ROOT / "data" / profile_id / "tracker.db"
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)

        try:
            # check_same_thread=False so the FastAPI dashboard can
            # share one Tracker across the request worker threads
            # used by Starlette's TestClient and uvicorn. sqlite3's
            # default threading mode is SERIALIZED, so the underlying
            # connection is itself thread-safe.
            self._conn = sqlite3.connect(
                self.db_path, check_same_thread=False,
            )
        except sqlite3.Error as e:
            logger.error("Failed to open database %s: %s", self.db_path, e)
            raise TrackerError(
                f"Cannot open database {self.db_path}: {e}"
            ) from e
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

        self._ensure_schema()
        logger.info("Tracker opened profile=%s db=%s",
                    self.profile_id, self.db_path)

    # -- Schema bootstrap --------------------------------------------------

    def _ensure_schema(self) -> None:
        """Initialize a new DB or migrate an older one to the latest schema."""
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'schema_version'"
        )
        if cur.fetchone() is None:
            # Fresh DB — apply latest schema directly.
            if not _SCHEMA_PATH.exists():
                raise TrackerError(f"Schema not found at {_SCHEMA_PATH}")
            sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info("Applied schema v2.21 to %s", self.db_path)
            return

        # Existing DB — apply pending migrations based on current version.
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        """Bring an existing DB up to the latest schema version."""
        current = self.schema_version()
        if current == 2:
            if not _MIGRATION_V2_TO_V22_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V2_TO_V22_PATH}"
                )
            sql = _MIGRATION_V2_TO_V22_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2 to v2.2", self.db_path
            )
            current = self.schema_version()

        if current == 22:
            if not _MIGRATION_V22_TO_V23_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V22_TO_V23_PATH}"
                )
            sql = _MIGRATION_V22_TO_V23_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.2 to v2.3", self.db_path
            )
            current = self.schema_version()

        if current == 23:
            if not _MIGRATION_V23_TO_V24_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V23_TO_V24_PATH}"
                )
            sql = _MIGRATION_V23_TO_V24_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.3 to v2.4", self.db_path
            )
            current = self.schema_version()

        if current == 24:
            if not _MIGRATION_V24_TO_V25_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V24_TO_V25_PATH}"
                )
            sql = _MIGRATION_V24_TO_V25_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.4 to v2.5", self.db_path
            )
            current = self.schema_version()

        if current == 25:
            if not _MIGRATION_V25_TO_V26_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V25_TO_V26_PATH}"
                )
            sql = _MIGRATION_V25_TO_V26_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.5 to v2.6", self.db_path
            )
            current = self.schema_version()

        if current == 26:
            if not _MIGRATION_V26_TO_V27_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V26_TO_V27_PATH}"
                )
            sql = _MIGRATION_V26_TO_V27_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.6 to v2.7", self.db_path
            )
            current = self.schema_version()

        if current == 27:
            if not _MIGRATION_V27_TO_V28_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V27_TO_V28_PATH}"
                )
            sql = _MIGRATION_V27_TO_V28_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.7 to v2.8", self.db_path
            )
            current = self.schema_version()

        if current == 28:
            if not _MIGRATION_V28_TO_V29_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V28_TO_V29_PATH}"
                )
            sql = _MIGRATION_V28_TO_V29_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.8 to v2.9", self.db_path
            )
            current = self.schema_version()

        if current == 29:
            if not _MIGRATION_V29_TO_V210_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V29_TO_V210_PATH}"
                )
            sql = _MIGRATION_V29_TO_V210_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.9 to v2.10", self.db_path
            )
            current = self.schema_version()

        if current == 210:
            if not _MIGRATION_V210_TO_V211_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V210_TO_V211_PATH}"
                )
            sql = _MIGRATION_V210_TO_V211_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            # Run the Python backfill now that the new columns exist.
            # data/schemas/ is not a Python package, so load by path.
            import importlib.util
            backfill_path = (
                _PROJECT_ROOT / "data" / "schemas" / "v2_11"
                / "backfill.py"
            )
            spec = importlib.util.spec_from_file_location(
                "_v2_11_backfill", backfill_path,
            )
            if spec is None or spec.loader is None:
                raise TrackerError(
                    f"Cannot load backfill module from {backfill_path}"
                )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run(self._conn)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.10 to v2.11", self.db_path
            )
            current = self.schema_version()

        if current == 211:
            if not _MIGRATION_V211_TO_V212_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V211_TO_V212_PATH}"
                )
            sql = _MIGRATION_V211_TO_V212_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.11 to v2.12", self.db_path
            )
            current = self.schema_version()

        if current == 212:
            if not _MIGRATION_V212_TO_V213_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V212_TO_V213_PATH}"
                )
            sql = _MIGRATION_V212_TO_V213_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.12 to v2.13", self.db_path
            )
            current = self.schema_version()

        if current == 213:
            if not _MIGRATION_V213_TO_V214_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V213_TO_V214_PATH}"
                )
            sql = _MIGRATION_V213_TO_V214_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.13 to v2.14", self.db_path
            )
            current = self.schema_version()

        if current == 214:
            if not _MIGRATION_V214_TO_V215_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V214_TO_V215_PATH}"
                )
            sql = _MIGRATION_V214_TO_V215_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.14 to v2.15", self.db_path
            )
            current = self.schema_version()

        if current == 215:
            if not _MIGRATION_V215_TO_V216_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V215_TO_V216_PATH}"
                )
            sql = _MIGRATION_V215_TO_V216_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.15 to v2.16", self.db_path
            )
            current = self.schema_version()

        if current == 216:
            if not _MIGRATION_V216_TO_V217_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V216_TO_V217_PATH}"
                )
            sql = _MIGRATION_V216_TO_V217_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.16 to v2.17", self.db_path
            )
            current = self.schema_version()

        if current == 217:
            if not _MIGRATION_V217_TO_V218_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V217_TO_V218_PATH}"
                )
            sql = _MIGRATION_V217_TO_V218_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.17 to v2.18", self.db_path
            )
            current = self.schema_version()

        if current == 218:
            if not _MIGRATION_V218_TO_V219_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V218_TO_V219_PATH}"
                )
            sql = _MIGRATION_V218_TO_V219_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.18 to v2.19", self.db_path
            )
            current = self.schema_version()

        if current == 219:
            if not _MIGRATION_V219_TO_V220_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V219_TO_V220_PATH}"
                )
            sql = _MIGRATION_V219_TO_V220_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.19 to v2.20", self.db_path
            )
            current = self.schema_version()

        if current == 220:
            if not _MIGRATION_V220_TO_V221_PATH.exists():
                raise TrackerError(
                    f"Migration not found at {_MIGRATION_V220_TO_V221_PATH}"
                )
            sql = _MIGRATION_V220_TO_V221_PATH.read_text(encoding="utf-8")
            self._conn.executescript(sql)
            self._conn.commit()
            logger.info(
                "Migrated %s from schema v2.20 to v2.21", self.db_path
            )
            current = self.schema_version()
        # Future migrations chain here as additional `if current == N:` blocks.

    def schema_version(self) -> int:
        row = self._query_one("SELECT version FROM schema_version")
        return int(row["version"]) if row else 0

    # -- Internal helpers --------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur
        except sqlite3.Error as e:
            logger.error("SQL failed: %s | params=%s | err=%s", sql, params, e)
            raise TrackerError(f"Database operation failed: {e}") from e

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        try:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()
        except sqlite3.Error as e:
            logger.error("Query failed: %s | params=%s | err=%s", sql, params, e)
            raise TrackerError(f"Database query failed: {e}") from e

    def _query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        try:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()
        except sqlite3.Error as e:
            logger.error("Query failed: %s | params=%s | err=%s", sql, params, e)
            raise TrackerError(f"Database query failed: {e}") from e

    # -- Company methods ---------------------------------------------------

    def upsert_company(
        self,
        name: str,
        industry: Optional[str] = None,
    ) -> int:
        """Insert or update a company. Idempotent on name_normalized.

        On insert: stores name, normalized name, optional industry, and
        timestamps. On match: updates last_seen_at, and only fills in
        industry if currently NULL (don't clobber).

        Returns the company id.
        """
        if not name or not name.strip():
            raise TrackerError("upsert_company requires a non-empty name")
        norm = normalize_employer_name(name)
        now = _utc_now_iso()

        existing = self._query_one(
            "SELECT id, industry FROM companies "
            "WHERE name_normalized = ?",
            (norm,),
        )
        if existing is None:
            cur = self._execute(
                "INSERT INTO companies "
                "(name, name_normalized, industry, "
                " first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, norm, industry, now, now),
            )
            company_id = cur.lastrowid
            self.log_event(
                event_type="company_upserted",
                summary=f"company created: {name}",
                entity_type="company",
                entity_id=company_id,
                details={"name": name, "industry": industry},
            )
            return company_id

        company_id = existing["id"]
        new_industry = existing["industry"] or industry
        self._execute(
            "UPDATE companies "
            "SET industry = ?, last_seen_at = ? "
            "WHERE id = ?",
            (new_industry, now, company_id),
        )
        return company_id

    def get_company_by_id(self, company_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ))

    def get_company_by_name(self, name: str) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM companies WHERE name_normalized = ?",
            (normalize_employer_name(name),),
        ))

    def count_companies(self) -> int:
        return int(self._query_one(
            "SELECT COUNT(*) AS n FROM companies"
        )["n"])

    def set_company_deep_target(
        self, company_id: int, is_deep_target: bool,
    ) -> None:
        """Set the is_deep_target flag on a company (v2.16). Used
        by the Spec D1 expansion agent after user confirms the
        weekly report's deep-target suggestions.
        """
        self._execute(
            "UPDATE companies SET is_deep_target = ? WHERE id = ?",
            (1 if is_deep_target else 0, company_id),
        )

    def update_company_ats(
        self,
        company_id: int,
        *,
        canonical_domain: Optional[str] = None,
        ats_platform: Optional[str] = None,
        ats_slug: Optional[str] = None,
        ats_detected_at: Optional[str] = None,
        ats_detection_method: Optional[str] = None,
        ats_detection_confidence: Optional[float] = None,
    ) -> None:
        """Update ATS-detection-related fields on a company row.

        Only non-None values are written; passing None for a field
        leaves it unchanged. ats_slug is stored verbatim — never
        lowercased (decision #29).
        """
        fields = {
            "canonical_domain": canonical_domain,
            "ats_platform": ats_platform,
            "ats_slug": ats_slug,
            "ats_detected_at": ats_detected_at,
            "ats_detection_method": ats_detection_method,
            "ats_detection_confidence": ats_detection_confidence,
        }
        sets: list[str] = []
        params: list[Any] = []
        for col, value in fields.items():
            if value is not None:
                sets.append(f"{col} = ?")
                params.append(value)
        if not sets:
            return
        params.append(company_id)
        self._execute(
            f"UPDATE companies SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )

    # -- Opportunity methods ----------------------------------------------

    def insert_opportunity(
        self,
        company_id: int,
        source: str,
        source_url: str,
        title: str,
        *,
        source_id: Optional[str] = None,
        location: Optional[str] = None,
        posting_text: Optional[str] = None,
        is_remote: Optional[bool] = None,
        posted_at: Optional[str] = None,
        salary_min: Optional[float] = None,
        salary_max: Optional[float] = None,
        salary_currency: Optional[str] = None,
        salary_interval: Optional[str] = None,
        raw_payload: Optional[dict] = None,
        search_context: Optional[dict] = None,
    ) -> tuple[int, bool]:
        """Insert an opportunity. Idempotent on url_hash.

        Returns (opportunity_id, was_new). On duplicate: updates last_seen_at,
        raw_payload, and search_context only; date_discovered, status, and
        company_id are preserved.
        """
        if not source or not source_url or not title:
            raise TrackerError(
                "insert_opportunity requires source, source_url, title"
            )
        url_hash = _hash_url(source_url)
        now = _utc_now_iso()
        raw_json = _to_json_or_none(raw_payload)
        ctx_json = _to_json_or_none(search_context)
        is_remote_int = _bool_to_int_or_none(is_remote)

        existing = self._query_one(
            "SELECT id FROM opportunities WHERE url_hash = ?", (url_hash,)
        )
        if existing is not None:
            opp_id = existing["id"]
            self._execute(
                "UPDATE opportunities "
                "SET last_seen_at = ?, raw_payload = ?, search_context = ? "
                "WHERE id = ?",
                (now, raw_json, ctx_json, opp_id),
            )
            self.log_event(
                event_type="opportunity_seen_again",
                summary=f"opportunity {opp_id} re-seen",
                entity_type="opportunity",
                entity_id=opp_id,
                details={"source": source, "source_url": source_url},
            )
            return opp_id, False

        cur = self._execute(
            "INSERT INTO opportunities ("
            "  company_id, source, source_id, source_url, url_hash,"
            "  title, location, posting_text, is_remote, posted_at,"
            "  salary_min, salary_max, salary_currency, salary_interval,"
            "  raw_payload, search_context,"
            "  date_discovered, last_seen_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?)",
            (company_id, source, source_id, source_url, url_hash,
             title, location, posting_text, is_remote_int, posted_at,
             salary_min, salary_max, salary_currency, salary_interval,
             raw_json, ctx_json, now, now),
        )
        opp_id = cur.lastrowid
        self.log_event(
            event_type="opportunity_discovered",
            summary=f"{title} (company_id={company_id})",
            entity_type="opportunity",
            entity_id=opp_id,
            details={"source": source, "source_url": source_url},
        )
        return opp_id, True

    # JOIN helper that exposes legacy column aliases for code that
    # hasn't migrated. Spec 4 TASK 4 partial cleanup (2026-05-12):
    #
    #   DROPPED — no consumers anywhere in the tree:
    #     * o.posted_at AS posted_date  (0 callers grepped)
    #   DROPPED — alias was cosmetic (column name matches alias):
    #     * latest_eval.fit_score AS fit_score
    #     * latest_eval.sector AS sector
    #
    #   KEPT — real consumers still exist; migration deferred to a
    #     future dedicated spec (estimated ~20 caller sites):
    #     * c.name AS employer       (~14 callers)
    #     * o.source_url AS url      (~5 callers)
    #     * latest_eval.reasoning AS fit_reasoning  (~2 callers)
    #
    # New consumers should call get_company_by_id and
    # get_latest_evaluation directly, or use the canonical column
    # names (name, source_url, reasoning) — they are exposed
    # naturally via `o.*` and the JOINs.
    _OPPORTUNITY_SELECT = (
        "SELECT o.*, "
        "       c.name AS employer, "
        "       o.source_url AS url, "
        "       latest_eval.fit_score, "
        "       latest_eval.reasoning AS fit_reasoning, "
        "       latest_eval.sector "
        "FROM opportunities o "
        "JOIN companies c ON c.id = o.company_id "
        "LEFT JOIN ("
        "    SELECT e1.* FROM eval_decisions e1 "
        "    JOIN ("
        "        SELECT opportunity_id, MAX(evaluated_at) AS max_at "
        "        FROM eval_decisions GROUP BY opportunity_id"
        "    ) e2 ON e1.opportunity_id = e2.opportunity_id "
        "       AND e1.evaluated_at = e2.max_at"
        ") latest_eval ON latest_eval.opportunity_id = o.id"
    )

    def get_opportunity_by_id(self, opportunity_id: int) -> Optional[dict]:
        row = self._query_one(
            f"{self._OPPORTUNITY_SELECT} WHERE o.id = ?",
            (opportunity_id,),
        )
        return _row_to_dict(row)

    def get_opportunity_by_hash(self, url_hash: str) -> Optional[dict]:
        row = self._query_one(
            f"{self._OPPORTUNITY_SELECT} WHERE o.url_hash = ?",
            (url_hash,),
        )
        return _row_to_dict(row)

    def list_opportunities(
        self,
        sector: Optional[str] = None,
        status: Optional[str] = None,
        min_fit_score: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if status is not None:
            clauses.append("o.status = ?")
            params.append(status)
        if sector is not None:
            clauses.append("latest_eval.sector = ?")
            params.append(sector)
        if min_fit_score is not None:
            clauses.append("latest_eval.fit_score >= ?")
            params.append(min_fit_score)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (f"{self._OPPORTUNITY_SELECT} {where} "
               f"ORDER BY o.date_discovered DESC LIMIT ?")
        params.append(limit)
        rows = self._query_all(sql, tuple(params))
        return _rows_to_dicts(rows)

    def list_unscored_opportunity_ids(self, limit: int = 100) -> list[int]:
        rows = self._query_all(
            "SELECT o.id FROM opportunities o "
            "WHERE NOT EXISTS ("
            "    SELECT 1 FROM eval_decisions ed "
            "    WHERE ed.opportunity_id = o.id "
            "    AND ed.fit_score IS NOT NULL"
            ") "
            "ORDER BY o.date_discovered DESC LIMIT ?",
            (limit,),
        )
        return [r["id"] for r in rows]

    def update_opportunity_status(
        self, opportunity_id: int, status: str,
    ) -> None:
        if status not in OPPORTUNITY_STATUSES:
            raise InvalidStatusError(
                f"Invalid opportunity status {status!r}; "
                f"allowed: {sorted(OPPORTUNITY_STATUSES)}"
            )
        self._execute(
            "UPDATE opportunities SET status = ? WHERE id = ?",
            (status, opportunity_id),
        )
        evt_type = ("opportunity_classified" if status == "shortlisted"
                    else "opportunity_dismissed" if status == "dismissed"
                    else "status_changed")
        self.log_event(
            event_type=evt_type,
            summary=f"opportunity {opportunity_id} -> {status}",
            entity_type="opportunity",
            entity_id=opportunity_id,
            details={"new_status": status},
        )

    def update_opportunity_fit(
        self, opportunity_id: int, fit_score: int,
        fit_reasoning: Optional[str] = None,
        sector: Optional[str] = None,
    ) -> int:
        """Record a fit score for an opportunity by inserting an
        eval_decisions row (evaluator_version='legacy').
        Returns the eval_decisions id.
        """
        return self.record_evaluation(
            opportunity_id=opportunity_id,
            evaluator_version="legacy",
            tier="EXPLORATORY",
            fit_score=fit_score,
            sector=sector,
            role_type=None,
            stage_trace={"source": "update_opportunity_fit"},
            reasoning=fit_reasoning,
        )

    def mark_opportunity_pursued(self, opportunity_id: int) -> int:
        self.update_opportunity_status(opportunity_id, "pursued")
        return opportunity_id

    def update_opportunity_classification(
        self,
        opportunity_id: int,
        *,
        city: Optional[str],
        ai_subtype: Optional[str],
        eval_priority: int,
    ) -> None:
        """Set v2.11 classification columns on an opportunity row.

        Called by persist_record after upsert+insert so newly-discovered
        rows get city/ai_subtype/eval_priority stamped in the same
        write path. function and industry_normalized are populated only
        by the v2.11 backfill (not enough signal at insert time).
        """
        self._execute(
            "UPDATE opportunities "
            "SET city = ?, ai_subtype = ?, eval_priority = ? "
            "WHERE id = ?",
            (city, ai_subtype, eval_priority, opportunity_id),
        )

    # -- Flag rules (v2.12) --------------------------------------------

    def list_flag_rules(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM flag_rules"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY created_at DESC"
        return _rows_to_dicts(self._query_all(sql))

    def insert_flag_rule(
        self, *,
        source_opportunity_id: Optional[int] = None,
        employer_pattern: Optional[str] = None,
        title_pattern: Optional[str] = None,
        industry_pattern: Optional[str] = None,
        function_pattern: Optional[str] = None,
        ai_subtype_pattern: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO flag_rules ("
            "  employer_pattern, title_pattern, industry_pattern, "
            "  function_pattern, ai_subtype_pattern, "
            "  source_opportunity_id, created_at, active"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                employer_pattern, title_pattern, industry_pattern,
                function_pattern, ai_subtype_pattern,
                source_opportunity_id, created_at or _utc_now_iso(),
            ),
        )
        return cur.lastrowid

    def set_flag_rule_active(self, rule_id: int, active: bool) -> bool:
        """Returns True if a row was updated; False if not found."""
        cur = self._execute(
            "UPDATE flag_rules SET active = ? WHERE id = ?",
            (1 if active else 0, rule_id),
        )
        return cur.rowcount > 0

    def flag_opportunity_atomic(
        self, *,
        opportunity_id: int,
        flagged_at: str,
        rule: Optional[dict] = None,
    ) -> dict:
        """Atomic: insert eval_label, mark latest decision SKIP+
        flagged_at, optionally insert flag_rule. Rolls back on any
        failure. Returns {'opportunity_id', 'rule_id'}.

        Reason='user_flagged' on the eval_label so the few-shot
        selector can prioritize it as a strong negative signal.
        """
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO eval_labels "
                "(opportunity_id, verdict, reason, labeled_at, labeled_by) "
                "VALUES (?, 'skip', 'user_flagged', ?, ?)",
                (opportunity_id, flagged_at, self.profile_id),
            )
            self._conn.execute(
                "UPDATE eval_decisions "
                "SET tier='SKIP', flagged_at=? "
                "WHERE opportunity_id = ? "
                "  AND id = (SELECT MAX(id) FROM eval_decisions "
                "             WHERE opportunity_id = ?)",
                (flagged_at, opportunity_id, opportunity_id),
            )
            rule_id: Optional[int] = None
            if rule is not None:
                cur = self._conn.execute(
                    "INSERT INTO flag_rules ("
                    "  employer_pattern, title_pattern, industry_pattern, "
                    "  function_pattern, ai_subtype_pattern, "
                    "  source_opportunity_id, created_at, active"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        rule.get("employer_pattern"),
                        rule.get("title_pattern"),
                        rule.get("industry_pattern"),
                        rule.get("function_pattern"),
                        rule.get("ai_subtype_pattern"),
                        opportunity_id, flagged_at,
                    ),
                )
                rule_id = cur.lastrowid
            self._conn.commit()
            return {"opportunity_id": opportunity_id, "rule_id": rule_id}
        except sqlite3.Error as e:
            self._conn.rollback()
            raise TrackerError(
                f"flag_opportunity_atomic failed: {e}",
            ) from e

    def count_opportunities(self) -> int:
        return int(self._query_one(
            "SELECT COUNT(*) AS n FROM opportunities"
        )["n"])

    # -- Job posters / hiring team (v2.14) --------------------------------

    def find_job_poster_by_url(
        self, profile_url: str,
    ) -> Optional[dict]:
        if not profile_url:
            return None
        return _row_to_dict(self._query_one(
            "SELECT * FROM job_posters WHERE linkedin_profile_url = ?",
            (profile_url,),
        ))

    def find_job_poster_by_name_employer(
        self, name: str, employer: str,
    ) -> Optional[dict]:
        if not name or not employer:
            return None
        return _row_to_dict(self._query_one(
            "SELECT * FROM job_posters "
            "WHERE full_name = ? AND employer_at_posting = ? "
            "LIMIT 1",
            (name, employer),
        ))

    def upsert_job_poster(
        self,
        name: str,
        title: Optional[str],
        profile_url: Optional[str],
        employer: Optional[str],
        observed_at: str,
    ) -> int:
        """Insert a job_posters row or update an existing match.

        Idempotent on linkedin_profile_url when present, otherwise on
        (full_name, employer_at_posting). On match, last_seen_at is
        bumped and total_postings_observed is incremented.
        Returns the job_poster id.
        """
        if not name or not name.strip():
            raise TrackerError(
                "upsert_job_poster requires a non-empty name"
            )

        existing = None
        if profile_url:
            existing = self.find_job_poster_by_url(profile_url)
        if existing is None and employer:
            existing = self.find_job_poster_by_name_employer(name, employer)

        if existing is not None:
            new_count = int(existing.get("total_postings_observed") or 0) + 1
            self._execute(
                "UPDATE job_posters "
                "SET last_seen_at = ?, total_postings_observed = ? "
                "WHERE id = ?",
                (observed_at, new_count, existing["id"]),
            )
            return int(existing["id"])

        now = _utc_now_iso()
        cur = self._execute(
            "INSERT INTO job_posters "
            "(linkedin_profile_url, full_name, title_at_posting, "
            " employer_at_posting, first_seen_at, last_seen_at, "
            " total_postings_observed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                profile_url, name, title, employer,
                now, observed_at, 1,
            ),
        )
        return int(cur.lastrowid)

    def link_opportunity_to_poster(
        self,
        opportunity_id: int,
        job_poster_id: int,
        role_on_posting: str,
        observed_at: str,
    ) -> None:
        """Insert an opportunity_posters row. Idempotent on the composite
        PK (opportunity_id, job_poster_id, role_on_posting) — duplicate
        re-observations are a silent no-op."""
        try:
            self._execute(
                "INSERT INTO opportunity_posters "
                "(opportunity_id, job_poster_id, role_on_posting, observed_at) "
                "VALUES (?, ?, ?, ?)",
                (opportunity_id, job_poster_id, role_on_posting, observed_at),
            )
        except TrackerError as e:
            # _execute wraps sqlite3.IntegrityError in TrackerError; the
            # PK violation we tolerate is the only case where the inner
            # message contains "UNIQUE constraint failed". Anything else
            # is a real error.
            if "UNIQUE constraint failed" not in str(e):
                raise

    # -- Persons (v2.18, Spec H1) -----------------------------------------

    def find_person_by_linkedin_url(
        self, linkedin_url: str,
    ) -> Optional[dict]:
        if not linkedin_url:
            return None
        return _row_to_dict(self._query_one(
            "SELECT * FROM persons WHERE linkedin_url = ?",
            (linkedin_url,),
        ))

    def find_person_by_github_username(
        self, github_username: str,
    ) -> Optional[dict]:
        if not github_username:
            return None
        return _row_to_dict(self._query_one(
            "SELECT * FROM persons WHERE github_username = ?",
            (github_username,),
        ))

    def upsert_person(
        self,
        *,
        full_name: str,
        source: str,
        linkedin_url: Optional[str] = None,
        github_username: Optional[str] = None,
        source_url: Optional[str] = None,
        headline: Optional[str] = None,
        current_employer: Optional[str] = None,
        current_title: Optional[str] = None,
        personal_site_url: Optional[str] = None,
        email_primary: Optional[str] = None,
        email_source: Optional[str] = None,
        email_confidence: Optional[str] = None,
        location: Optional[str] = None,
        personalization_hooks: Optional[list] = None,
        raw_payload: Optional[dict] = None,
        company_id: Optional[int] = None,
        job_poster_id: Optional[int] = None,
    ) -> int:
        """Insert or update a persons row. Idempotent on linkedin_url
        (preferred) or github_username. Returns the person id.

        On update: last_seen_at bumped, scalar columns refreshed only
        when the new value is non-None.
        """
        if not full_name or not full_name.strip():
            raise TrackerError(
                "upsert_person requires a non-empty full_name"
            )
        existing = None
        if linkedin_url:
            existing = self.find_person_by_linkedin_url(linkedin_url)
        if existing is None and github_username:
            existing = self.find_person_by_github_username(github_username)
        now = _utc_now_iso()
        hooks_json = _to_json_or_none(personalization_hooks)
        raw_json = _to_json_or_none(raw_payload)
        if existing is not None:
            pid = int(existing["id"])
            updates: dict[str, Any] = {"last_seen_at": now}
            for col, value in [
                ("headline", headline),
                ("current_employer", current_employer),
                ("current_title", current_title),
                ("personal_site_url", personal_site_url),
                ("email_primary", email_primary),
                ("email_source", email_source),
                ("email_confidence", email_confidence),
                ("location", location),
                ("personalization_hooks", hooks_json),
                ("raw_payload", raw_json),
                ("company_id", company_id),
                ("job_poster_id", job_poster_id),
                ("github_username", github_username),
                ("linkedin_url", linkedin_url),
                ("source_url", source_url),
            ]:
                if value is not None:
                    updates[col] = value
            sets = ", ".join(f"{c} = ?" for c in updates)
            params = list(updates.values()) + [pid]
            self._execute(
                f"UPDATE persons SET {sets} WHERE id = ?", tuple(params),
            )
            return pid
        cur = self._execute(
            "INSERT INTO persons ("
            "  source, source_url, full_name, headline, current_employer, "
            "  current_title, linkedin_url, github_username, "
            "  personal_site_url, email_primary, "
            "  email_source, email_confidence, location, "
            "  personalization_hooks, raw_payload, date_discovered, "
            "  last_seen_at, company_id, job_poster_id"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source, source_url, full_name, headline, current_employer,
                current_title, linkedin_url, github_username,
                personal_site_url, email_primary,
                email_source, email_confidence, location, hooks_json,
                raw_json, now, now, company_id, job_poster_id,
            ),
        )
        return int(cur.lastrowid)

    def list_persons(
        self,
        *,
        company_id: Optional[int] = None,
        source: Optional[str] = None,
        tier: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """List persons with optional filters. tier filters by the
        latest person_scores row per person (None means any).
        """
        sql = (
            "SELECT p.*, ps.tier, ps.score_overall "
            "FROM persons p "
            "LEFT JOIN ("
            "  SELECT person_id, tier, score_overall, "
            "         scored_at, "
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY person_id ORDER BY scored_at DESC"
            "         ) AS rn "
            "  FROM person_scores"
            ") ps ON ps.person_id = p.id AND ps.rn = 1 "
            "WHERE 1=1 "
        )
        params: list[Any] = []
        if company_id is not None:
            sql += "AND p.company_id = ? "
            params.append(company_id)
        if source is not None:
            sql += "AND p.source = ? "
            params.append(source)
        if tier is not None:
            sql += "AND ps.tier = ? "
            params.append(tier)
        sql += "ORDER BY ps.score_overall DESC NULLS LAST, p.id DESC "
        if limit is not None:
            sql += "LIMIT ? "
            params.append(int(limit))
        rows = self._query_all(sql, tuple(params))
        return [dict(r) for r in rows]

    def get_person_by_id(self, person_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM persons WHERE id = ?", (person_id,),
        ))

    def insert_person_email(
        self,
        *,
        person_id: int,
        email: str,
        source: str,
        confidence: str,
        source_url: Optional[str] = None,
    ) -> int:
        """Insert a person_emails row. Returns the row id; on
        duplicate (person_id, email) returns the existing id and the
        new attempt is a silent no-op."""
        if not email:
            raise TrackerError("insert_person_email requires non-empty email")
        existing = self._query_one(
            "SELECT id FROM person_emails "
            "WHERE person_id = ? AND email = ?",
            (person_id, email),
        )
        if existing is not None:
            return int(existing["id"])
        cur = self._execute(
            "INSERT INTO person_emails "
            "(person_id, email, source, source_url, confidence, "
            " discovered_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (person_id, email, source, source_url, confidence,
             _utc_now_iso()),
        )
        return int(cur.lastrowid)

    def list_person_emails(self, person_id: int) -> list[dict]:
        return [
            dict(r) for r in self._query_all(
                "SELECT * FROM person_emails "
                "WHERE person_id = ? "
                "ORDER BY discovered_at DESC",
                (person_id,),
            )
        ]

    def deactivate_person_email(
        self, person_id: int, email: str, reason: str = "",
    ) -> None:
        self._execute(
            "UPDATE person_emails SET is_active = 0 "
            "WHERE person_id = ? AND email = ?",
            (person_id, email),
        )

    def insert_person_score(
        self,
        *,
        person_id: int,
        score_overall: float,
        score_read_rate: float,
        score_reachability: float,
        score_fit: float,
        tier: str,
        scorer_version: str,
        reasoning: Optional[dict] = None,
    ) -> int:
        cur = self._execute(
            "INSERT INTO person_scores "
            "(person_id, score_overall, score_read_rate, "
            " score_reachability, score_fit, tier, reasoning, "
            " scorer_version, scored_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                person_id, score_overall, score_read_rate,
                score_reachability, score_fit, tier,
                _to_json_or_none(reasoning), scorer_version,
                _utc_now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def latest_person_score(self, person_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM person_scores "
            "WHERE person_id = ? "
            "ORDER BY scored_at DESC LIMIT 1",
            (person_id,),
        ))

    # outreach_targets + outreach_drafts tables are schema artifacts
    # from v2.18 / v2.19; the v1 release dropped the in-app outreach
    # subsystem (Spec 15 TASK 5). The tables remain in the schema
    # because removing them would need a new migration — but no
    # Python code reads or writes them.

    def upsert_company_email_format(
        self,
        *,
        company_id: int,
        format_pattern: str,
        confidence: float,
        sample_count: int,
    ) -> None:
        existing = self._query_one(
            "SELECT id FROM company_email_formats WHERE company_id = ?",
            (company_id,),
        )
        now = _utc_now_iso()
        if existing is not None:
            self._execute(
                "UPDATE company_email_formats "
                "SET format_pattern = ?, confidence = ?, "
                "    sample_count = ?, inferred_at = ? "
                "WHERE company_id = ?",
                (format_pattern, confidence, sample_count, now,
                 company_id),
            )
            return
        self._execute(
            "INSERT INTO company_email_formats "
            "(company_id, format_pattern, confidence, "
            " sample_count, inferred_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, format_pattern, confidence, sample_count, now),
        )

    def get_company_email_format(
        self, company_id: int,
    ) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM company_email_formats WHERE company_id = ?",
            (company_id,),
        ))

    def get_company_domain(self, company_id: int) -> Optional[str]:
        """Return companies.canonical_domain (non-empty) or None.

        Used by EmailFinder's harvester source to resolve a company
        domain from a person.company_id."""
        row = self._query_one(
            "SELECT canonical_domain FROM companies WHERE id = ?",
            (int(company_id),),
        )
        if row is None:
            return None
        d = (row["canonical_domain"] or "").strip()
        return d or None

    def add_do_not_contact(
        self,
        *,
        identifier_type: str,
        identifier_value: str,
        reason: str,
    ) -> None:
        """Insert a do_not_contact row. Idempotent on
        (identifier_type, identifier_value)."""
        try:
            self._execute(
                "INSERT INTO do_not_contact "
                "(identifier_type, identifier_value, reason, added_at) "
                "VALUES (?, ?, ?, ?)",
                (identifier_type, identifier_value, reason,
                 _utc_now_iso()),
            )
        except TrackerError as e:
            if "UNIQUE constraint failed" not in str(e):
                raise

    def get_persons_without_email(
        self, *, limit: int = 50,
    ) -> list[dict]:
        """Return persons rows that lack an email_primary AND have
        no entry in person_emails. Sorted by most-recent-discovered."""
        rows = self._query_all(
            "SELECT p.* FROM persons p "
            "LEFT JOIN person_emails pe ON pe.person_id = p.id "
            "WHERE p.email_primary IS NULL OR p.email_primary = '' "
            "GROUP BY p.id "
            "HAVING COUNT(pe.id) = 0 "
            "ORDER BY p.date_discovered DESC "
            "LIMIT ?",
            (int(limit),),
        )
        return [dict(r) for r in rows]

    def get_confirmed_emails_at_company(
        self, company_id: int,
    ) -> list[tuple[str, str]]:
        """Return [(full_name, email)] for high-confidence emails at the
        given company. Pulls from person_emails rows whose confidence is
        'high' joined with the owning person."""
        rows = self._query_all(
            "SELECT p.full_name, pe.email "
            "FROM person_emails pe "
            "JOIN persons p ON p.id = pe.person_id "
            "WHERE p.company_id = ? "
            "  AND pe.is_active = 1 "
            "  AND pe.confidence = 'high'",
            (company_id,),
        )
        return [(r["full_name"], r["email"]) for r in rows]

    def set_person_primary_email(
        self,
        *,
        person_id: int,
        email: str,
        source: str,
        confidence: str,
    ) -> None:
        """Set email_primary / email_source / email_confidence on a
        person row. Mirrors the same fields on the row that would
        result from re-upserting the person."""
        self._execute(
            "UPDATE persons "
            "SET email_primary = ?, email_source = ?, "
            "    email_confidence = ?, last_seen_at = ? "
            "WHERE id = ?",
            (email, source, confidence, _utc_now_iso(), person_id),
        )

    def hours_since_last_unsubscribe(self) -> Optional[float]:
        row = self._query_one(
            "SELECT MAX(added_at) AS a FROM do_not_contact "
            "WHERE reason LIKE 'unsubscribe%'",
        )
        if row is None or not row["a"]:
            return None
        from datetime import datetime as _dt
        try:
            then = _dt.fromisoformat(str(row["a"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        now = _dt.now(timezone.utc)
        delta = (now - then).total_seconds() / 3600.0
        return max(0.0, delta)

    def is_do_not_contact(
        self, identifier_type: str, identifier_value: str,
    ) -> bool:
        row = self._query_one(
            "SELECT 1 FROM do_not_contact "
            "WHERE identifier_type = ? AND identifier_value = ?",
            (identifier_type, identifier_value),
        )
        return row is not None

    # -- Profile skills / extracted skill IDs (v2.2) ----------------------

    def upsert_profile_skills(
        self,
        profile_id: str,
        taxonomy: str,
        skill_ids: list[str],
        source_doc: str,
        raw_extraction: Optional[str] = None,
    ) -> None:
        """Insert or replace a profile_skills row keyed by
        (profile_id, taxonomy). skill_ids is serialized to JSON."""
        self._execute(
            "INSERT OR REPLACE INTO profile_skills "
            "(profile_id, taxonomy, skill_ids, extracted_at, "
            " source_doc, raw_extraction) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (profile_id, taxonomy, json.dumps(skill_ids),
             _utc_now_iso(), source_doc, raw_extraction),
        )

    def get_profile_skills(
        self, profile_id: str, taxonomy: str,
    ) -> Optional[dict]:
        """Return the profile_skills row for (profile_id, taxonomy) as a
        dict with skill_ids deserialized. None if missing."""
        row = self._query_one(
            "SELECT * FROM profile_skills "
            "WHERE profile_id = ? AND taxonomy = ?",
            (profile_id, taxonomy),
        )
        if row is None:
            return None
        d = dict(row)
        d["skill_ids"] = json.loads(d["skill_ids"])
        return d

    def update_opportunity_skills(
        self, opportunity_id: int, skill_ids: list[str],
    ) -> None:
        """Set extracted_skill_ids (JSON) on an opportunity row.

        Backward-compat single-taxonomy method. New code should use
        update_opportunity_skills_dual instead.
        """
        self._execute(
            "UPDATE opportunities SET extracted_skill_ids = ? WHERE id = ?",
            (json.dumps(skill_ids), opportunity_id),
        )

    def update_opportunity_skills_dual(
        self,
        opportunity_id: int,
        primary_skill_ids: list[str],
        secondary_skill_ids: list[str],
        secondary_taxonomy: str,
    ) -> None:
        """Set primary + secondary extracted skill IDs and the
        secondary taxonomy name on an opportunity row.

        Primary lands in extracted_skill_ids (consumed by the
        three-signal scorer). Secondary lands in
        extracted_skill_ids_secondary (annotation only, never read
        by the scorer). secondary_taxonomy is a freeform string
        ('esco' currently); stored per-row so re-runs with different
        taxonomy choices remain self-describing.
        """
        self._execute(
            "UPDATE opportunities "
            "SET extracted_skill_ids = ?, "
            "    extracted_skill_ids_secondary = ?, "
            "    secondary_taxonomy = ? "
            "WHERE id = ?",
            (
                json.dumps(primary_skill_ids),
                json.dumps(secondary_skill_ids),
                secondary_taxonomy,
                opportunity_id,
            ),
        )

    # -- Match scores / skill labels (v2.4) -------------------------------

    def insert_match_score(
        self,
        opportunity_id: int,
        scorer_version: str,
        overlap_count: int,
        posting_skill_count: int,
        inventory_skill_count: int,
        coverage_raw: float,
        coverage_idf: float,
        overlap_skill_ids: list[str],
        missed_skill_ids: list[str],
        bucket: str,
    ) -> None:
        """Insert or replace deterministic match-score fields.

        Use update_match_score_gemma to set Gemma fields on an
        existing row. INSERT OR REPLACE so re-running the
        deterministic scorer is idempotent and overwrites prior
        Gemma fields with NULL -- intentional, since a changed
        deterministic input means any prior Gemma run was based
        on stale overlap and should be redone.
        """
        self._execute(
            "INSERT OR REPLACE INTO match_scores ("
            "  opportunity_id, scorer_version,"
            "  overlap_count, posting_skill_count,"
            "  inventory_skill_count,"
            "  coverage_raw, coverage_idf,"
            "  overlap_skill_ids, missed_skill_ids, bucket,"
            "  scored_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                opportunity_id, scorer_version,
                overlap_count, posting_skill_count,
                inventory_skill_count,
                coverage_raw, coverage_idf,
                json.dumps(overlap_skill_ids),
                json.dumps(missed_skill_ids),
                bucket,
                _utc_now_iso(),
            ),
        )

    def update_match_score_gemma(
        self,
        opportunity_id: int,
        scorer_version: str,
        gemma_score: int,
        gemma_top_matches: list,
        gemma_transferable: list,
        gemma_critical_gaps: list,
        gemma_summary: str,
        gemma_hallucination_flags: int,
        gemma_raw_response: str,
    ) -> None:
        """Update Gemma fields on an existing match_scores row."""
        self._execute(
            "UPDATE match_scores SET "
            "  gemma_score = ?,"
            "  gemma_top_matches = ?,"
            "  gemma_transferable = ?,"
            "  gemma_critical_gaps = ?,"
            "  gemma_summary = ?,"
            "  gemma_hallucination_flags = ?,"
            "  gemma_raw_response = ? "
            "WHERE opportunity_id = ? AND scorer_version = ?",
            (
                gemma_score,
                json.dumps(gemma_top_matches),
                json.dumps(gemma_transferable),
                json.dumps(gemma_critical_gaps),
                gemma_summary,
                gemma_hallucination_flags,
                gemma_raw_response,
                opportunity_id, scorer_version,
            ),
        )

    def get_match_score(
        self, opportunity_id: int, scorer_version: str,
    ) -> Optional[dict]:
        """Return the match_scores row for (opp_id, scorer_version)
        as a dict with JSON columns deserialized. None if missing."""
        row = self._query_one(
            "SELECT * FROM match_scores "
            "WHERE opportunity_id = ? AND scorer_version = ?",
            (opportunity_id, scorer_version),
        )
        if row is None:
            return None
        d = dict(row)
        d["overlap_skill_ids"] = json.loads(d["overlap_skill_ids"])
        d["missed_skill_ids"] = json.loads(d["missed_skill_ids"])
        for k in ("gemma_top_matches", "gemma_transferable",
                  "gemma_critical_gaps"):
            if d.get(k):
                d[k] = json.loads(d[k])
        return d

    def list_match_scores(
        self,
        scorer_version: str,
        bucket: Optional[str] = None,
        order_by: str = "coverage_idf DESC",
        limit: Optional[int] = None,
    ) -> list[dict]:
        """List match_scores rows for a scorer_version, optionally
        filtered by bucket. order_by is interpolated raw -- callers
        must use a trusted constant.
        """
        clauses = ["scorer_version = ?"]
        params: list[Any] = [scorer_version]
        if bucket is not None:
            clauses.append("bucket = ?")
            params.append(bucket)
        sql = (
            "SELECT * FROM match_scores "
            f"WHERE {' AND '.join(clauses)} "
            f"ORDER BY {order_by}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._query_all(sql, tuple(params))
        out = []
        for r in rows:
            d = dict(r)
            d["overlap_skill_ids"] = json.loads(d["overlap_skill_ids"])
            d["missed_skill_ids"] = json.loads(d["missed_skill_ids"])
            for k in ("gemma_top_matches", "gemma_transferable",
                      "gemma_critical_gaps"):
                if d.get(k):
                    d[k] = json.loads(d[k])
            out.append(d)
        return out

    def upsert_skill_label(
        self,
        skill_id: str,
        label: str,
        taxonomy: str,
        match_type: Optional[str] = None,
        source: str = "backfill",
    ) -> None:
        """Insert or update a skill label lookup entry."""
        self._execute(
            "INSERT OR REPLACE INTO skill_labels "
            "(skill_id, label, taxonomy, match_type, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (skill_id, label, taxonomy, match_type, source),
        )

    def get_skill_labels(
        self, skill_ids: list[str],
    ) -> dict[str, str]:
        """Return {skill_id: label} for the given IDs. Missing IDs
        map to '<unknown>'. Single bulk query."""
        if not skill_ids:
            return {}
        placeholders = ",".join("?" * len(skill_ids))
        rows = self._query_all(
            f"SELECT skill_id, label FROM skill_labels "
            f"WHERE skill_id IN ({placeholders})",
            tuple(skill_ids),
        )
        found = {r["skill_id"]: r["label"] for r in rows}
        return {sid: found.get(sid, "<unknown>") for sid in skill_ids}

    # -- Eval labels (v2.5) ----------------------------------------------

    def insert_eval_label(
        self,
        opportunity_id: int,
        verdict: str,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
        labeled_by: str = "default",
    ) -> None:
        """Insert or replace an eval_labels row for an opportunity."""
        if verdict not in ("shortlist", "skip", "unsure"):
            raise TrackerError(
                f"Invalid verdict {verdict!r}; "
                f"allowed: shortlist, skip, unsure"
            )
        self._execute(
            "INSERT OR REPLACE INTO eval_labels "
            "(opportunity_id, verdict, reason, notes, "
            " labeled_at, labeled_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (opportunity_id, verdict, reason, notes,
             _utc_now_iso(), labeled_by),
        )

    def get_eval_label(self, opportunity_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM eval_labels WHERE opportunity_id = ?",
            (opportunity_id,),
        ))

    def list_eval_labels(
        self, verdict: Optional[str] = None,
    ) -> list[dict]:
        if verdict is not None:
            rows = self._query_all(
                "SELECT * FROM eval_labels WHERE verdict = ? "
                "ORDER BY opportunity_id ASC",
                (verdict,),
            )
        else:
            rows = self._query_all(
                "SELECT * FROM eval_labels "
                "ORDER BY opportunity_id ASC"
            )
        return _rows_to_dicts(rows)

    # -- Stage decisions (v2.6) ------------------------------------------

    def insert_stage_decision(
        self,
        opportunity_id: int,
        stage_name: str,
        stage_version: str,
        decision: str,
        reason: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Insert or replace a stage_decisions row.

        metadata is JSON-serialized; pass a dict for stage-specific
        payload (e.g. Stage 2's confidence, role_type, raw response).
        """
        meta_json = (
            json.dumps(metadata, default=str)
            if metadata is not None else None
        )
        self._execute(
            "INSERT OR REPLACE INTO stage_decisions "
            "(opportunity_id, stage_name, stage_version, "
            " decision, reason, metadata, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (opportunity_id, stage_name, stage_version,
             decision, reason, meta_json, _utc_now_iso()),
        )

    def get_stage_decision(
        self,
        opportunity_id: int,
        stage_name: str,
        stage_version: str,
    ) -> Optional[dict]:
        """Return the stage_decisions row as a dict with metadata
        deserialized. None if missing."""
        row = self._query_one(
            "SELECT * FROM stage_decisions "
            "WHERE opportunity_id = ? AND stage_name = ? "
            "AND stage_version = ?",
            (opportunity_id, stage_name, stage_version),
        )
        if row is None:
            return None
        d = dict(row)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (TypeError, ValueError):
                pass
        return d

    def list_stage_decisions(
        self,
        stage_name: Optional[str] = None,
        stage_version: Optional[str] = None,
        decision: Optional[str] = None,
    ) -> list[dict]:
        """List stage_decisions, optionally filtered."""
        clauses = []
        params: list[Any] = []
        if stage_name is not None:
            clauses.append("stage_name = ?")
            params.append(stage_name)
        if stage_version is not None:
            clauses.append("stage_version = ?")
            params.append(stage_version)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._query_all(
            f"SELECT * FROM stage_decisions {where} "
            f"ORDER BY decided_at DESC",
            tuple(params),
        )
        out = []
        for r in rows:
            d = dict(r)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return out

    # -- Evaluation -------------------------------------------------------

    def record_evaluation(
        self, opportunity_id: int, evaluator_version: str,
        tier: str, fit_score: Optional[int],
        sector: Optional[str], role_type: Optional[str],
        stage_trace: dict, reasoning: Optional[str],
        *,
        letter_grade: Optional[str] = None,
        interview_plan: Optional[list] = None,
        red_flags: Optional[list] = None,
        culture_signals: Optional[list] = None,
    ) -> int:
        if tier not in EVAL_TIERS:
            raise InvalidStatusError(
                f"Invalid tier {tier!r}; allowed: {sorted(EVAL_TIERS)}"
            )
        cur = self._execute(
            "INSERT INTO eval_decisions "
            "(opportunity_id, evaluator_version, tier, fit_score, sector,"
            " role_type, stage_trace, reasoning, evaluated_at,"
            " letter_grade, interview_plan, red_flags, culture_signals) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (opportunity_id, evaluator_version, tier, fit_score, sector,
             role_type, json.dumps(stage_trace, default=str),
             reasoning, _utc_now_iso(),
             letter_grade,
             json.dumps(interview_plan, default=str)
                if interview_plan is not None else None,
             json.dumps(red_flags, default=str)
                if red_flags is not None else None,
             json.dumps(culture_signals, default=str)
                if culture_signals is not None else None,
             ),
        )
        eval_id = cur.lastrowid
        self.log_event(
            event_type="opportunity_scored",
            summary=f"opportunity {opportunity_id} scored "
                    f"{fit_score if fit_score is not None else 'n/a'}/10",
            entity_type="opportunity",
            entity_id=opportunity_id,
            details={"tier": tier, "fit_score": fit_score,
                     "evaluator_version": evaluator_version,
                     "letter_grade": letter_grade},
        )
        return eval_id

    def get_latest_evaluation(self, opportunity_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM eval_decisions WHERE opportunity_id = ? "
            "ORDER BY evaluated_at DESC LIMIT 1",
            (opportunity_id,),
        ))

    # -- Application methods ----------------------------------------------

    def create_application(self, opportunity_id: int, resume_variant: str,
                           **fields) -> int:
        if self.get_opportunity_by_id(opportunity_id) is None:
            raise TrackerError(
                f"opportunity_id {opportunity_id} does not exist"
            )

        now = _utc_now_iso()
        row = {
            "opportunity_id":         opportunity_id,
            "resume_variant":         resume_variant,
            "cover_letter_path":      fields.get("cover_letter_path"),
            "federal_responses_path": fields.get("federal_responses_path"),
            "submitted_date":         fields.get("submitted_date"),
            "submitted_via":          fields.get("submitted_via"),
            "recruiter_id":           fields.get("recruiter_id"),
            "status":                 "drafted",
            "status_updated_at":      now,
            "tags":                   fields.get("tags"),
            "notes":                  fields.get("notes"),
        }
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        cur = self._execute(
            f"INSERT INTO applications ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        app_id = cur.lastrowid
        self._execute(
            "INSERT INTO status_history "
            "(application_id, from_status, to_status, changed_at, reason) "
            "VALUES (?, NULL, ?, ?, ?)",
            (app_id, "drafted", now, "application created"),
        )
        self.log_event(
            event_type="application_drafted",
            summary=f"application {app_id} drafted for opportunity "
                    f"{opportunity_id}",
            entity_type="application",
            entity_id=app_id,
            details={"opportunity_id": opportunity_id,
                     "resume_variant": resume_variant},
        )
        return app_id

    def get_application_by_id(self, application_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ))

    def list_applications(self, status: Optional[str] = None,
                          resume_variant: Optional[str] = None,
                          limit: int = 100) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if resume_variant is not None:
            clauses.append("resume_variant = ?")
            params.append(resume_variant)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (f"SELECT * FROM applications {where} "
               f"ORDER BY status_updated_at DESC LIMIT ?")
        params.append(limit)
        rows = self._query_all(sql, tuple(params))
        return _rows_to_dicts(rows)

    def update_application_status(self, application_id: int, new_status: str,
                                  reason: Optional[str] = None) -> None:
        if new_status not in APPLICATION_STATUSES:
            raise InvalidStatusError(
                f"Invalid application status {new_status!r}; "
                f"allowed: {sorted(APPLICATION_STATUSES)}"
            )
        current = self.get_application_by_id(application_id)
        if current is None:
            raise TrackerError(
                f"application_id {application_id} does not exist"
            )
        from_status = current["status"]
        now = _utc_now_iso()
        self._execute(
            "UPDATE applications SET status = ?, status_updated_at = ? "
            "WHERE id = ?",
            (new_status, now, application_id),
        )
        self._execute(
            "INSERT INTO status_history "
            "(application_id, from_status, to_status, changed_at, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (application_id, from_status, new_status, now, reason),
        )
        evt_type = ("application_submitted"
                    if new_status == "submitted" else "status_changed")
        self.log_event(
            event_type=evt_type,
            summary=f"application {application_id}: "
                    f"{from_status} -> {new_status}",
            entity_type="application",
            entity_id=application_id,
            details={"from": from_status, "to": new_status, "reason": reason},
        )

    def update_application_fields(self, application_id: int,
                                  **fields) -> None:
        allowed = {"cover_letter_path", "federal_responses_path",
                   "submitted_date", "submitted_via", "recruiter_id",
                   "tags", "notes",
                   # v2.9 (Phase 11 Playwright Application Agent):
                   "resume_text", "cover_letter_text", "ats_platform",
                   "screenshot_path", "submitted_url",
                   "screening_answers",
                   # v2.9 dashboard lifecycle timestamps:
                   "selected_at", "prompt_generated_at",
                   "docs_ready_at",
                   # v2.10 auto-prompt generation (Spec 3):
                   "resume_prompt", "cover_letter_prompt",
                   # v2.17 follow-up email log (JSON list):
                   "follow_ups"}
        bad = set(fields) - allowed
        if bad:
            raise TrackerError(
                f"update_application_fields: disallowed fields "
                f"{sorted(bad)}; allowed={sorted(allowed)}"
            )
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = tuple(fields.values()) + (application_id,)
        self._execute(
            f"UPDATE applications SET {set_clause} WHERE id = ?", params
        )

    def auto_transition_ghosted(self, days: int = 60) -> int:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=days)).isoformat()
        candidates = self._query_all(
            "SELECT a.id FROM applications a "
            "WHERE a.status IN ('submitted', 'confirmed_received') "
            "AND a.status_updated_at <= ? "
            "AND NOT EXISTS (SELECT 1 FROM communications c "
            "                WHERE c.application_id = a.id "
            "                AND c.occurred_at > ?)",
            (cutoff, cutoff),
        )
        transitioned = 0
        for r in candidates:
            self.update_application_status(
                r["id"], "ghosted",
                reason=f"auto-ghosted after {days} days of no communication",
            )
            transitioned += 1
        return transitioned

    # -- Recruiter methods ------------------------------------------------

    def add_recruiter(self, agency_name: str, **fields) -> int:
        if not agency_name:
            raise TrackerError("agency_name is required")
        row = {
            "agency_name":           agency_name,
            "contact_name":          fields.get("contact_name"),
            "contact_email":         fields.get("contact_email"),
            "contact_phone":         fields.get("contact_phone"),
            "linkedin_url":          fields.get("linkedin_url"),
            "first_contact_date":    fields.get("first_contact_date",
                                                _utc_now_iso()),
            "resume_variant_shared": fields.get("resume_variant_shared"),
            "notes":                 fields.get("notes"),
        }
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        cur = self._execute(
            f"INSERT INTO recruiters ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        return cur.lastrowid

    def get_recruiter_by_id(self, recruiter_id: int) -> Optional[dict]:
        return _row_to_dict(self._query_one(
            "SELECT * FROM recruiters WHERE id = ?", (recruiter_id,)
        ))

    def list_recruiters(self) -> list[dict]:
        return _rows_to_dicts(self._query_all(
            "SELECT * FROM recruiters ORDER BY agency_name ASC"
        ))

    def check_duplicate_submission(self, employer: str, title: str) -> bool:
        """True iff a recruiter has already submitted for this employer/title.

        Joins applications -> opportunities -> companies; matches on the
        normalized company name so casing/whitespace doesn't matter.
        """
        blocking = ("submitted", "confirmed_received", "responded",
                    "interviewing", "offered", "rejected",
                    "silent_rejected", "ghosted")
        placeholders = ", ".join("?" * len(blocking))
        norm = normalize_employer_name(employer)
        row = self._query_one(
            f"SELECT 1 FROM applications a "
            f"JOIN opportunities o ON o.id = a.opportunity_id "
            f"JOIN companies c ON c.id = o.company_id "
            f"WHERE a.submitted_via = 'recruiter' "
            f"AND a.status IN ({placeholders}) "
            f"AND c.name_normalized = ? AND o.title = ? LIMIT 1",
            blocking + (norm, title),
        )
        return row is not None

    # -- Communication methods --------------------------------------------

    def log_communication(self, application_id: int, channel: str,
                          direction: str, summary: str, **fields) -> int:
        if channel not in COMMUNICATION_CHANNELS:
            raise TrackerError(
                f"Invalid channel {channel!r}; "
                f"allowed: {sorted(COMMUNICATION_CHANNELS)}"
            )
        if direction not in COMMUNICATION_DIRECTIONS:
            raise TrackerError(
                f"Invalid direction {direction!r}; "
                f"allowed: {sorted(COMMUNICATION_DIRECTIONS)}"
            )
        if not summary:
            raise TrackerError("summary is required")

        row = {
            "application_id":   application_id,
            "channel":          channel,
            "direction":        direction,
            "occurred_at":      fields.get("occurred_at", _utc_now_iso()),
            "summary":          summary,
            "followup_needed":  1 if fields.get("followup_needed") else 0,
            "followup_by":      fields.get("followup_by"),
            "notes":            fields.get("notes"),
        }
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        cur = self._execute(
            f"INSERT INTO communications ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        cid = cur.lastrowid
        self.log_event(
            event_type="communication_logged",
            summary=f"{direction} {channel} on application {application_id}",
            entity_type="communication",
            entity_id=cid,
            details={"application_id": application_id,
                     "channel": channel, "direction": direction},
        )
        return cid

    def list_communications(self, application_id: Optional[int] = None,
                            followup_needed: Optional[bool] = None,
                            limit: int = 100) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if application_id is not None:
            clauses.append("application_id = ?")
            params.append(application_id)
        if followup_needed is not None:
            clauses.append("followup_needed = ?")
            params.append(1 if followup_needed else 0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (f"SELECT * FROM communications {where} "
               f"ORDER BY occurred_at DESC LIMIT ?")
        params.append(limit)
        return _rows_to_dicts(self._query_all(sql, tuple(params)))

    def get_pending_followups(self) -> list[dict]:
        today = datetime.now(timezone.utc).date().isoformat()
        return _rows_to_dicts(self._query_all(
            "SELECT * FROM communications "
            "WHERE followup_needed = 1 "
            "AND (followup_by IS NULL OR followup_by <= ?) "
            "ORDER BY followup_by ASC",
            (today,),
        ))

    # -- Event methods ----------------------------------------------------

    def log_event(self, event_type: str, summary: str,
                  entity_type: Optional[str] = None,
                  entity_id: Optional[int] = None,
                  details: Optional[dict] = None) -> None:
        if event_type not in EVENT_TYPES:
            raise TrackerError(
                f"Invalid event_type {event_type!r}; "
                f"allowed: {sorted(EVENT_TYPES)}"
            )
        details_json = (json.dumps(details, default=str)
                        if details else None)
        self._execute(
            "INSERT INTO events (occurred_at, event_type, entity_type, "
            "entity_id, summary, details) VALUES (?, ?, ?, ?, ?, ?)",
            (_utc_now_iso(), event_type, entity_type, entity_id,
             summary, details_json),
        )

    def list_recent_events(self, limit: int = 50) -> list[dict]:
        return _rows_to_dicts(self._query_all(
            "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?",
            (limit,),
        ))

    def list_events_by_type(self, event_type: str,
                            limit: int = 100) -> list[dict]:
        return _rows_to_dicts(self._query_all(
            "SELECT * FROM events WHERE event_type = ? "
            "ORDER BY occurred_at DESC LIMIT ?",
            (event_type, limit),
        ))

    def list_events_by_entity(self, entity_type: str,
                              entity_id: int) -> list[dict]:
        return _rows_to_dicts(self._query_all(
            "SELECT * FROM events WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY occurred_at DESC",
            (entity_type, entity_id),
        ))

    # -- Dashboard summary ------------------------------------------------

    def get_dashboard_summary(self) -> dict:
        week_ago = (datetime.now(timezone.utc)
                    - timedelta(days=7)).isoformat()

        total_opps = self._query_one(
            "SELECT COUNT(*) AS n FROM opportunities"
        )["n"]
        opps_week = self._query_one(
            "SELECT COUNT(*) AS n FROM opportunities "
            "WHERE date_discovered >= ?", (week_ago,),
        )["n"]
        opps_by_sector_rows = self._query_all(
            "SELECT COALESCE(latest_eval.sector, 'unclassified') AS sector, "
            "       COUNT(*) AS n "
            "FROM opportunities o "
            "LEFT JOIN ("
            "    SELECT e1.* FROM eval_decisions e1 "
            "    JOIN ("
            "        SELECT opportunity_id, MAX(evaluated_at) AS max_at "
            "        FROM eval_decisions GROUP BY opportunity_id"
            "    ) e2 ON e1.opportunity_id = e2.opportunity_id "
            "       AND e1.evaluated_at = e2.max_at"
            ") latest_eval ON latest_eval.opportunity_id = o.id "
            "GROUP BY sector"
        )
        opps_by_sector = {r["sector"]: r["n"] for r in opps_by_sector_rows}

        total_apps = self._query_one(
            "SELECT COUNT(*) AS n FROM applications"
        )["n"]
        apps_by_status_rows = self._query_all(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        )
        apps_by_status = {r["status"]: r["n"] for r in apps_by_status_rows}
        apps_week = self._query_one(
            "SELECT COUNT(*) AS n FROM applications "
            "WHERE id IN (SELECT application_id FROM status_history "
            "             WHERE from_status IS NULL AND changed_at >= ?)",
            (week_ago,),
        )["n"]

        pending_followups = self._query_one(
            "SELECT COUNT(*) AS n FROM communications "
            "WHERE followup_needed = 1"
        )["n"]
        active_interviews = apps_by_status.get("interviewing", 0)

        return {
            "total_opportunities":      total_opps,
            "opportunities_this_week":  opps_week,
            "opportunities_by_sector":  opps_by_sector,
            "total_applications":       total_apps,
            "applications_by_status":   apps_by_status,
            "applications_this_week":   apps_week,
            "pending_followups":        pending_followups,
            "active_interviews":        active_interviews,
        }

    # -- Lifecycle --------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
            logger.info("Tracker closed db=%s", self.db_path)
        except sqlite3.Error as e:
            raise TrackerError(f"Error closing database: {e}") from e
