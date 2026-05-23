"""Tests for the daily shortlist generator."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.persistence.tracker import Tracker
from scripts.generate_shortlist import generate_shortlist


TEST_DB = PROJECT_ROOT / "data" / "test_shortlist.db"


def _init_schema(db_path: Path) -> None:
    return None


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        if TEST_DB.exists():
            TEST_DB.unlink()
        _init_schema(TEST_DB)
        self.tracker = Tracker(profile_id="test_shortlist",
                               db_path=str(TEST_DB))

    def tearDown(self):
        try:
            self.tracker.close()
        except Exception:
            pass
        if TEST_DB.exists():
            TEST_DB.unlink()

    def _add(self, title: str, employer: str, source: str = "test",
             sector: str = None,
             fit_score: int = None, **kw) -> int:
        url = kw.pop("url", f"https://example.com/{id(title)}/{title[:10]}")
        company_id = self.tracker.upsert_company(name=employer)
        opp_id, _ = self.tracker.insert_opportunity(
            company_id=company_id,
            source=source, source_url=url, title=title,
            location="Toronto, ON",
            **kw,
        )
        if sector is not None or fit_score is not None:
            self.tracker.record_evaluation(
                opportunity_id=opp_id,
                evaluator_version="legacy",
                tier="EXPLORATORY",
                fit_score=fit_score,
                sector=sector,
                role_type=None,
                stage_trace={"source": "test_shortlist"},
                reasoning=None,
            )
        return opp_id

    def test_empty_tracker(self):
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("# Daily Shortlist", md)
        self.assertIn("New opportunities this week: 0", md)

    def test_single_opportunity(self):
        self._add("Senior PM", "Acme",
                   fit_score=8, url="https://example.com/single1")
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("Senior PM", md)
        self.assertIn("Acme", md)
        self.assertIn("8/10", md)
        self.assertIn("New opportunities this week: 1", md)

    def test_grouped_by_sector(self):
        self._add("Ops Officer", "CRA", sector="public_sector",
                   fit_score=7,
                   url="https://example.com/public1")
        self._add("Fintech PM", "WS", sector="financial_services",
                   fit_score=8,
                   url="https://example.com/fin1")
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("Public Sector", md)
        self.assertIn("Financial Services", md)

    def test_all_opportunities_section(self):
        self._add("High Score", "A", fit_score=9,
                   url="https://example.com/all1")
        self._add("Low Score", "B", fit_score=3,
                   url="https://example.com/all2")
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("## All New Opportunities", md)
        all_section = md.split("## All New Opportunities")[1]
        high_pos = all_section.find("High Score")
        low_pos = all_section.find("Low Score")
        self.assertLess(high_pos, low_pos)

    def test_unscored_opportunities_shown(self):
        self._add("Unscored Role", "Corp",
                   url="https://example.com/unscored1")
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("Unscored Role", md)
        self.assertIn("n/a", md)

    def test_url_included(self):
        self._add("URL Test", "Corp",
                   url="https://boards.greenhouse.io/test/jobs/123")
        md = generate_shortlist(self.tracker, days=7)
        self.assertIn("https://boards.greenhouse.io/test/jobs/123", md)


if __name__ == "__main__":
    unittest.main()
