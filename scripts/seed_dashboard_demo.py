"""Seed a self-contained demo profile for a manual dashboard smoke test.

Drops a placeholder applicant profile YAML in config/profiles/, wipes
+ recreates data/demo/tracker.db with three TOP_TIER / STRONG fake
postings, and prints a tab-by-tab walkthrough you can follow in the
browser.

Re-running is safe: each invocation wipes the demo tracker DB and the
demo applicant YAML before reseeding. The "default" profile (your real
data) is never touched.

Usage:
  python scripts/seed_dashboard_demo.py
  python scripts/dashboard.py --profile demo
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker

PROFILE_ID = "demo"
DEMO_DB = PROJECT_ROOT / "data" / PROFILE_ID / "tracker.db"
DEMO_PROFILE_YAML = (
    PROJECT_ROOT / "config" / "profiles" / f"{PROFILE_ID}_applicant.yaml"
)


PLACEHOLDER_PROFILE = """\
# Demo applicant profile -- placeholder PII so the dashboard can boot.
# This file is gitignored; replace with real values if you want to
# drive a real apply session.
first_name: "Demo"
last_name: "Applicant"
email: "demo@example.invalid"
phone: "555-0100"
city: "Toronto"
province: "Ontario"
country: "Canada"
postal_code: "M5V 0A1"
linkedin_url: "https://linkedin.com/in/demo"
work_authorization: "Permanent Resident"
requires_sponsorship: false
willing_to_relocate: true
relocation_cities: ["Toronto", "Calgary"]
salary_expectation: "Competitive / Open to discussion"
start_date: "Immediately"
education:
  - degree: "MBA"
    school: "Demo University"
    graduation_year: "2018"
certifications:
  - name: "PMP"
    issuer: "PMI"
    active: true
"""


SAMPLE_POSTINGS = [
    {
        "employer": "Demo Bank",
        "title": "Senior Project Manager",
        "location": "Toronto, ON",
        "url": "https://boards.greenhouse.io/demobank/jobs/1001",
        "tier": "TOP_TIER",
        "fit": 9,
        "reasoning": (
            "10+ years delivery in financial services; "
            "PMP + Agile certifications align with the JD."
        ),
        "posting_text": (
            "Lead a team of 8 across delivery and ops. "
            "PMP required. Agile / Scrum experience essential."
        ),
    },
    {
        "employer": "Acme Energy",
        "title": "Delivery Lead, Pipeline Programs",
        "location": "Calgary, AB",
        "url": "https://jobs.lever.co/acme-energy/abc-123",
        "tier": "TOP_TIER",
        "fit": 8,
        "reasoning": "Cross-functional leadership + stakeholder mgmt.",
        "posting_text": (
            "Own delivery of multi-year programs. "
            "Stakeholder management at executive levels."
        ),
    },
    {
        "employer": "Northstar Software",
        "title": "Operations Manager",
        "location": "Remote, Canada",
        "url": "https://jobs.ashbyhq.com/northstar/xyz-789",
        "tier": "STRONG",
        "fit": 7,
        "reasoning": "Operations + process improvement match.",
        "posting_text": (
            "Optimize operations across product + support. "
            "Reporting + process documentation required."
        ),
    },
]


def _wipe(path: Path) -> None:
    if path.exists():
        path.unlink()


def _seed_profile_yaml() -> None:
    DEMO_PROFILE_YAML.parent.mkdir(parents=True, exist_ok=True)
    DEMO_PROFILE_YAML.write_text(PLACEHOLDER_PROFILE, encoding="utf-8")


def _seed_tracker() -> list[int]:
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    _wipe(DEMO_DB)
    t = Tracker(profile_id=PROFILE_ID, db_path=DEMO_DB)
    now = datetime.now(timezone.utc).isoformat()
    posting_ids: list[int] = []
    try:
        for p in SAMPLE_POSTINGS:
            cid = t.upsert_company(p["employer"])
            opp_id, _ = t.insert_opportunity(
                company_id=cid, source="demo_seed",
                source_url=p["url"], title=p["title"],
                location=p["location"], posting_text=p["posting_text"],
            )
            t._execute(
                "INSERT INTO eval_decisions "
                "(opportunity_id, evaluator_version, tier, fit_score, "
                " stage_trace, reasoning, evaluated_at) "
                "VALUES (?, 'demo-v1', ?, ?, '[]', ?, ?)",
                (opp_id, p["tier"], p["fit"], p["reasoning"], now),
            )
            posting_ids.append(opp_id)
    finally:
        t.close()
    return posting_ids


def _print_walkthrough(posting_ids: list[int]) -> None:
    print()
    print("=" * 70)
    print("DEMO SEEDED")
    print("=" * 70)
    print(f"  profile id:       {PROFILE_ID}")
    print(f"  tracker db:       {DEMO_DB}")
    print(f"  applicant yaml:   {DEMO_PROFILE_YAML}")
    print(f"  seeded postings:  {posting_ids}")
    print()
    print("=" * 70)
    print("MANUAL SMOKE TEST -- step through each tab in the browser")
    print("=" * 70)
    print()
    print("  1. Boot the dashboard (in another terminal):")
    print(f"       python scripts/dashboard.py --profile {PROFILE_ID}")
    print()
    print("  2. DASHBOARD page (default landing)")
    print("     - Pipeline funnel shows 3 Discovered, 3 Evaluated,")
    print("       2 Shortlisted (TOP_TIER + STRONG seeded; one EXPLORATORY).")
    print("     - Click 'Today / Week / Month' toggle to verify the")
    print("       range query rebinds without reload.")
    print("     - Trend chart (Recharts) draws four lines (discovered,")
    print("       evaluated, shortlisted, applied). Hover for tooltip.")
    print("     - Source health card lists workday/greenhouse/lever.")
    print("     - Cloud evaluator card shows tier breakdown.")
    print("     - Toggle Light/Dark from the sidebar — every card and")
    print("       the chart should reflow colors instantly.")
    print()
    print("  3. SHORTLIST page")
    print("     - Verify 3 cards appear (TOP_TIER first, STRONG last).")
    print("     - Click Apply on 2 of them; click Skip on the third.")
    print("     - Counters at the top should read '2 selected'.")
    print()
    print("  4. PROMPTS page")
    print("     - Queue shows 2 of 2.")
    print("     - For each posting:")
    print("         a. Click 'Copy to clipboard' under Resume.")
    print("         b. Paste THIS into the resume textarea (any markdown")
    print("            following the spec template will do):")
    print()
    print("         # FIRST LAST")
    print("         Senior PM | Toronto, ON")
    print("         demo@example.invalid")
    print()
    print("         ## PROFESSIONAL SUMMARY")
    print("         Senior delivery leader.")
    print()
    print("         ## SKILLS")
    print("         **Delivery:** Agile, Scrum")
    print()
    print("         ## WORK EXPERIENCE")
    print("         ### PM | Acme | Toronto | Jan 2020 - Present")
    print("         - Led 5 cross-functional teams")
    print()
    print("         ## EDUCATION & CERTIFICATIONS")
    print("         - MBA, Demo U (2018)")
    print()
    print("         c. Click 'Save Resume'. Status pill flips to 'Saved'.")
    print("         d. Click 'Preview .docx' -- a Word doc should open")
    print("            inline in a new tab (zip-magic bytes test).")
    print("         e. Repeat for Cover Letter (any 3-4 paragraph text")
    print("            starting with '# Cover Letter -- ...').")
    print("         f. Footer pill flips to '-> Ready to Apply'.")
    print("         g. Click Next; do the same for the second posting.")
    print()
    print("  5. APPLY page")
    print("     - Both postings appear under 'Ready to apply'.")
    print("     - The seeded posting URLs are real ATS shapes")
    print("       (greenhouse / lever / ashby) so 'Apply Now' would")
    print("       actually launch a Chromium against them. For the")
    print("       smoke test, click 'Mark Applied' on each instead --")
    print("       no Playwright, no real submission. Both rows should")
    print("       leave the queue.")
    print()
    print("  6. HISTORY page")
    print("     - Both applications appear with status='submitted'.")
    print("     - Click 'View Resume' on one; modal renders the saved")
    print("       markdown text round-tripped from SQLite.")
    print("     - Use the status dropdown to flip one to 'interviewing'.")
    print()
    print("  7. Back to DASHBOARD")
    print("     - After applying, the Applied column should advance.")
    print("     - With one row flipped to 'interviewing', the Interview")
    print("       funnel cell shows 1 with a +1 delta.")
    print()
    print("  CLEANUP:")
    print(f"     rm -rf {DEMO_DB.parent}")
    print(f"     rm    {DEMO_PROFILE_YAML}")
    print()


def main() -> int:
    _seed_profile_yaml()
    posting_ids = _seed_tracker()
    _print_walkthrough(posting_ids)
    return 0


if __name__ == "__main__":
    sys.exit(main())
