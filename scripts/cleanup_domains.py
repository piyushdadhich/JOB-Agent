"""Spec JA-3 TASK 2 -- clear bad Pass-3 probe-derived domains.

Pass 3 of backfill_domains.py probes name-derived candidates against
DNS MX records. Single-word company names match common dictionary-word
parked domains. This script audits every domain whose root (before
.com/.ca/.io) is a single dictionary word and NULLs the value when the
domain root doesn't closely match the company's name.

Idempotent. Logs cleared rows. Commit-only fix; running it again after
a future Pass-3 run is the intended use.

Run: .\\venv\\Scripts\\python.exe scripts/cleanup_domains.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from engine.persistence.tracker import Tracker


# Dictionary words that *frequently* show up as the root of company
# names AND as registered parked domains. Single-word names are the
# Pass 3 failure mode -- they probe a candidate like dream.com that
# has MX records (because the squatter set them up) but isn't actually
# the company's site.
DICTIONARY_WORDS: frozenset[str] = frozenset({
    "business", "dream", "summit", "gateway", "digital",
    "software", "creative", "modern", "global", "premier",
    "central", "national", "pacific", "western", "northern",
    "southern", "eastern", "united", "universal", "general",
    "advanced", "strategic", "smart", "direct", "express",
    "classic", "dynamic", "standard", "signature", "select",
    "compass", "horizon", "pioneer", "sterling", "prime",
    "eagle", "falcon", "sirius", "atlas", "matrix",
    "genesis", "zenith", "apex", "nexus", "nova",
    "quest", "titan", "vanguard", "vertex", "alpha",
    "phoenix", "fusion", "catalyst", "prism", "synergy",
})

_DOMAIN_RE = re.compile(r"^([a-z0-9-]+)\.(com|ca|io|net|org)$", re.IGNORECASE)
_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _name_tokens(name: str) -> set[str]:
    return {tok.lower() for tok in _NAME_TOKEN_RE.findall(name)}


_STOPWORD_TOKENS: frozenset[str] = frozenset({
    "inc", "ltd", "corp", "corporation", "limited", "company", "co",
    "group", "pvt", "llc", "llp", "plc", "international", "global",
    "canada", "canadian", "technologies", "technology", "solutions",
    "services", "consulting", "partners", "the", "of", "and", "&",
})


def _meaningful_tokens(name: str) -> list[str]:
    return [
        tok.lower()
        for tok in _NAME_TOKEN_RE.findall(name)
        if tok.lower() not in _STOPWORD_TOKENS
    ]


def cleanup(profile: str = "default", dry_run: bool = False) -> dict:
    """Clear Pass-3 probe domains whose root is a dictionary word AND
    whose company name has more than just that word.

    A single-token company name (e.g. "Apex") with a matching
    dictionary-word domain (apex.com) is plausible -- keep it. A
    multi-token name (e.g. "Apex Group Ltd") whose root happens to be
    a dictionary word is the Pass-3 failure mode -- clear it.
    """
    tracker = Tracker(profile)

    rows = tracker._query_all(
        "SELECT id, name, canonical_domain FROM companies "
        "WHERE canonical_domain IS NOT NULL AND canonical_domain != ''",
        (),
    )

    cleared: list[tuple[str, str]] = []
    scanned = 0
    for row in rows:
        scanned += 1
        domain = (row["canonical_domain"] or "").strip().lower()
        name = (row["name"] or "").strip()
        m = _DOMAIN_RE.match(domain)
        if not m:
            continue
        root = m.group(1).lower()
        if root not in DICTIONARY_WORDS:
            continue
        tokens = _meaningful_tokens(name)
        # If the name reduces to just the dictionary word (e.g. "Apex",
        # "Acuity Inc"), keep -- that's the company's actual brand. If
        # there are *additional* meaningful tokens, the probe latched
        # onto a parked domain.
        if len(tokens) <= 1:
            continue
        cleared.append((name, domain))
        if not dry_run:
            tracker._conn.execute(
                "UPDATE companies SET canonical_domain = NULL WHERE id = ?",
                (row["id"],),
            )

    if not dry_run:
        tracker._conn.commit()
    tracker.close()

    return {
        "scanned": scanned,
        "cleared": cleared,
    }


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    result = cleanup(profile="default", dry_run=dry)
    print(f"Scanned {result['scanned']} companies with canonical_domain set.")
    print(f"{'Would clear' if dry else 'Cleared'}: {len(result['cleared'])}")
    for name, domain in result["cleared"]:
        print(f"  {name!r}  <-  {domain}")
