"""Run ATS detection across companies in the tracker.

Usage:
  python scripts/run_ats_detection.py --profile default
  python scripts/run_ats_detection.py --profile default --limit 20
  python scripts/run_ats_detection.py --profile default --company "Wealthsimple"

The detector runs a 3-signal cascade per company:
  1. Manual override   (config/profiles/{profile}.yaml ats_overrides)
  2. Careers-page scan (https://{domain}/careers and variants)
  3. Slug probe        (probe each ATS endpoint with case variants)

Successful detections persist to companies.ats_platform / ats_slug;
failures persist ats_detected_at + ats_detection_method='not_detected'
so subsequent runs skip them. Use --limit to run a sub-batch first.

After running, run scripts/populate_ats_slugs.py to copy detected
slugs back into the YAML config.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from engine.discovery.ats_detector import ATSDetector
from engine.persistence.tracker import Tracker

logger = logging.getLogger(__name__)


def _load_overrides(profile_id: str) -> dict:
    path = PROJECT_ROOT / "config" / "profiles" / f"{profile_id}.yaml"
    if not path.exists():
        return {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return cfg.get("ats_overrides") or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_one(detector, tracker, name, overrides) -> int:
    """Detect a single company by name. Returns 0 on success, 1 if
    the company is not in the tracker."""
    row = tracker._query_one(
        "SELECT id, canonical_domain FROM companies "
        "WHERE name_normalized = ?",
        (name.strip().lower(),),
    )
    if row is None:
        print(f"company '{name}' not found in tracker")
        return 1
    cid = row["id"]
    domain = row["canonical_domain"]
    print(f"detecting {name} (id={cid}, domain={domain or '-'})...")
    result = detector.detect(name, domain, overrides=overrides)
    if result is None:
        print("  no ATS detected")
        tracker.update_company_ats(
            cid,
            ats_detected_at=_now(),
            ats_detection_method="not_detected",
            ats_detection_confidence=0.0,
        )
        return 0
    print(
        f"  detected: platform={result.platform} "
        f"slug={result.slug!r} method={result.method} "
        f"conf={result.confidence}"
    )
    tracker.update_company_ats(
        cid,
        ats_platform=result.platform,
        ats_slug=result.slug,
        ats_detected_at=_now(),
        ats_detection_method=result.method,
        ats_detection_confidence=result.confidence,
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="cap the batch (testing / partial runs)",
    )
    parser.add_argument(
        "--company", default=None,
        help="run detection only for the company whose name "
             "matches this string (case-insensitive)",
    )
    parser.add_argument(
        "--rate-limit", type=float, default=1.0,
        help="seconds between HTTP requests (default 1.0)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    tracker = Tracker(args.profile)
    overrides = _load_overrides(args.profile)
    detector = ATSDetector(rate_limit=args.rate_limit)

    try:
        if args.company:
            return _detect_one(detector, tracker, args.company, overrides)

        t0 = time.monotonic()
        result = detector.detect_batch(
            tracker, overrides=overrides, limit=args.limit,
        )
        elapsed = time.monotonic() - t0
        print()
        print("=" * 70)
        print("ATS DETECTION COMPLETE")
        print("=" * 70)
        print(f"  wall:         {elapsed/60:.1f} min ({elapsed:.0f}s)")
        print(f"  detected:     {result['detected']}")
        print(f"  not_detected: {result['not_detected']}")
        print(f"  errors:       {result['errors']}")
        if result["by_platform"]:
            print("  by platform:")
            for platform, n in sorted(result["by_platform"].items()):
                print(f"    {platform:<12} {n}")
        return 0
    finally:
        tracker.close()


if __name__ == "__main__":
    sys.exit(main())
