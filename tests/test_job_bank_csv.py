"""Unit tests for engine.discovery.job_bank_csv."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.discovery.job_bank_csv import (  # noqa: E402
    JobBankCSVClient,
    _parse_date,
    _parse_salary,
)


# --- helpers --------------------------------------------------------

def _write_csv(path: Path, rows: list[dict], header: list[str]) -> None:
    """Write a CSV with proper quoting (handles commas inside cells)."""
    import csv
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in header})


# --- _parse_salary helper ------------------------------------------

def test_parse_salary_handles_dollar_and_comma():
    assert _parse_salary("$80,000") == 80000.0
    assert _parse_salary("80000") == 80000.0
    assert _parse_salary("80000.5") == 80000.5
    assert _parse_salary("") is None
    assert _parse_salary(None) is None
    assert _parse_salary("not a number") is None


# --- _parse_date helper --------------------------------------------

def test_parse_date_handles_iso_and_slash_formats():
    assert _parse_date("2025-06-01") == datetime(
        2025, 6, 1, tzinfo=timezone.utc,
    )
    assert _parse_date("2025/06/01") == datetime(
        2025, 6, 1, tzinfo=timezone.utc,
    )
    assert _parse_date("06/01/2025") == datetime(
        2025, 6, 1, tzinfo=timezone.utc,
    )
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("garbage") is None


# --- Client behavior -----------------------------------------------

def test_parse_csv_returns_records(tmp_path):
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [
            {"title": "Project Manager", "employer": "Acme",
             "location": "Toronto, ON",
             "url": "https://jb/1", "date_posted": "2025-06-01",
             "salary_min": "80000", "salary_max": "100000"},
            {"title": "Scrum Master", "employer": "Beta",
             "location": "Toronto, ON",
             "url": "https://jb/2", "date_posted": "2025-06-02",
             "salary_min": "75000", "salary_max": "95000"},
        ],
        header=["title", "employer", "location", "url",
                "date_posted", "salary_min", "salary_max"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert len(records) == 2
    assert records[0].title == "Project Manager"
    assert records[1].title == "Scrum Master"


def test_missing_csv_returns_empty(tmp_path):
    nope = tmp_path / "does_not_exist.csv"
    client = JobBankCSVClient(csv_path=nope)
    assert list(client.fetch()) == []


def test_location_filter_applied(tmp_path):
    """Rows whose location does not contain any target city are
    skipped."""
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [
            {"title": "Tor PM", "employer": "Acme",
             "location": "Toronto, ON", "url": "https://jb/1"},
            {"title": "Tulsa PM", "employer": "Beta",
             "location": "Tulsa, OK", "url": "https://jb/2"},
            {"title": "Cal PM", "employer": "Gamma",
             "location": "Calgary, AB", "url": "https://jb/3"},
        ],
        header=["title", "employer", "location", "url"],
    )
    client = JobBankCSVClient(
        csv_path=p, target_cities=["toronto", "calgary"],
    )
    records = list(client.fetch())
    titles = [r.title for r in records]
    assert titles == ["Tor PM", "Cal PM"]


def test_salary_parsed(tmp_path):
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [{"title": "PM", "employer": "Acme", "location": "Toronto",
          "url": "https://jb/1", "salary_min": "$80,000",
          "salary_max": "$120,000"}],
        header=["title", "employer", "location", "url",
                "salary_min", "salary_max"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert records[0].salary_min == 80000.0
    assert records[0].salary_max == 120000.0


def test_source_is_job_bank_csv(tmp_path):
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [{"title": "PM", "employer": "Acme",
          "location": "Toronto", "url": "https://jb/1"}],
        header=["title", "employer", "location", "url"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert records[0].source == "job_bank_csv"


def test_currency_defaults_to_cad(tmp_path):
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [{"title": "PM", "employer": "Acme",
          "location": "Toronto", "url": "https://jb/1",
          "salary_min": "80000"}],
        header=["title", "employer", "location", "url", "salary_min"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert records[0].salary_currency == "CAD"


def test_empty_csv_returns_nothing(tmp_path):
    p = tmp_path / "jb.csv"
    p.write_text(
        "title,employer,location,url\n",
        encoding="utf-8",
    )
    client = JobBankCSVClient(csv_path=p)
    assert list(client.fetch()) == []


def test_malformed_rows_skipped(tmp_path):
    """Rows with no title and no employer are skipped (would fail
    persist anyway)."""
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [
            {"title": "", "employer": "",
             "location": "Toronto", "url": ""},
            {"title": "PM", "employer": "Acme",
             "location": "Toronto", "url": "https://jb/2"},
        ],
        header=["title", "employer", "location", "url"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert len(records) == 1
    assert records[0].title == "PM"


def test_alternate_column_names(tmp_path):
    """Title falls back to job_title; employer to company; etc."""
    p = tmp_path / "jb.csv"
    _write_csv(
        p,
        [{"job_title": "Project Manager", "company": "Acme",
          "city": "Toronto", "job_url": "https://jb/1"}],
        header=["job_title", "company", "city", "job_url"],
    )
    client = JobBankCSVClient(csv_path=p)
    records = list(client.fetch())
    assert records[0].title == "Project Manager"
    assert records[0].employer == "Acme"
    assert records[0].location == "Toronto"
    assert records[0].source_url == "https://jb/1"
