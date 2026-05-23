"""Spec JA-2 — populate companies.canonical_domain.

Pass 1: MANUAL_MAP for the top ~100 employers by posting count.
Pass 2: ATS slug -> {slug}.com/.ca/.io with DNS MX validation.
Pass 3: Name-derived candidates probed against DNS MX (rate-limited).

Idempotent: skips companies that already have canonical_domain set.
Run: .\\venv\\Scripts\\python.exe scripts/backfill_domains.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import dns.resolver

from engine.persistence.tracker import Tracker


# Highest-confidence manual map. Built from top-100 employers by
# posting count in default tracker.db. Keys are matched
# case-insensitively against companies.name and companies.name_normalized.
MANUAL_MAP: dict[str, str] = {
    # Banks + financial (the user's primary target sector)
    "TD Bank": "td.com",
    "BMO": "bmo.com",
    "BMO Financial Group": "bmo.com",
    "Desjardins": "desjardins.com",
    "Intact Financial": "intact.ca",
    "Intact": "intact.ca",
    "iA Financial": "ia.ca",
    "BDC": "bdc.ca",
    "CPP Investments": "cppinvestments.com",
    "CPP Investments | Investissements RPC": "cppinvestments.com",
    "Scotiabank": "scotiabank.com",
    "CIBC": "cibc.com",
    "Manulife": "manulife.com",
    "Sun Life": "sunlife.com",
    "EQ Bank": "eqbank.ca",
    "eqbank": "eqbank.ca",
    "BDO Canada": "bdo.ca",
    "Wealthsimple": "wealthsimple.com",
    "wealthsimple": "wealthsimple.com",
    "Citi": "citigroup.com",
    "Capital One": "capitalone.com",
    "Aviva Canada": "aviva.ca",
    "Neo Financial": "neofinancial.com",
    "Questrade Financial Group": "questrade.com",
    "Meridian Credit Union": "meridiancu.ca",
    "OMERS": "omers.com",
    "Ontario Teachers' Pension Plan": "otpp.com",
    "PSP Investments": "investpsp.com",
    "TMX Group": "tmx.com",
    "Nicola Wealth": "nicolawealth.com",
    "nicolawealth": "nicolawealth.com",

    # Energy + utilities (target sector)
    "Enbridge": "enbridge.com",
    "TC Energy": "tcenergy.com",
    "Suncor Energy": "suncor.com",
    "Capital Power": "capitalpower.com",
    "AltaLink": "altalink.ca",
    "ATCO": "atco.com",
    "EPCOR": "epcor.com",

    # Crown corps + public sector (target sector)
    "LCBO": "lcbo.com",
    "TestCo": "testco.com",
    "Canada Post": "canadapost.ca",
    "OPG": "opg.com",
    "Government Of Alberta": "alberta.ca",
    "Alberta Health Services": "albertahealthservices.ca",

    # Real estate + infrastructure (target sector)
    "Brookfield Properties": "brookfieldproperties.com",
    "Brookfield Asset Mgmt": "brookfield.com",
    "EllisDon": "ellisdon.com",
    "Colliers": "colliers.com",
    "AtkinsRéalis": "atkinsrealis.com",
    "Aecon Group Inc.": "aecon.com",
    "Bird Construction": "bird.ca",
    "Chandos Construction": "chandos.com",
    "WSP in Canada": "wsp.com",
    "BGIS": "bgis.com",
    "GHD": "ghd.com",
    "AECOM": "aecom.com",

    # Large tech / consulting
    "Accenture": "accenture.com",
    "Amazon Web Services": "amazon.com",
    "Amazon.com": "amazon.com",
    "Amazon": "amazon.com",
    "Autodesk": "autodesk.com",
    "Shoppers Drug Mart": "shoppersdrugmart.ca",
    "Loblaw Companies Limited": "loblaw.ca",
    "Loblaw": "loblaw.ca",
    "Loblaw Digital": "loblaw.ca",
    "Canadian Tire": "canadiantire.ca",
    "Canadian Tire Corporation, Ltd.": "canadiantire.ca",
    "Couche-Tard": "couche-tard.com",
    "Walmart": "walmart.ca",
    "TELUS Health": "telushealth.com",
    "TELUS Digital": "telusdigital.com",
    "telus-digital": "telusdigital.com",
    "Telus": "telus.com",
    "Capgemini": "capgemini.com",
    "PwC Canada": "pwc.com",
    "EY": "ey.com",
    "KPMG": "kpmg.ca",
    "MNP": "mnp.ca",
    "Thomson Reuters": "thomsonreuters.com",
    "Insight Global": "insightglobal.com",
    "eBay": "ebay.com",
    "Sanofi": "sanofi.com",
    "Okta": "okta.com",
    "Real Canadian Superstore": "realcanadiansuperstore.ca",
    "Sobeys": "sobeys.com",
    "Staples Canada": "staples.ca",
    "Kinaxis": "kinaxis.com",
    "D2L": "d2l.com",
    "StackAdapt": "stackadapt.com",
    "Geotab": "geotab.com",
    "Crossover": "crossover.com",

    # Mid-market (the user's sweet spot)
    "Clio": "clio.com",
    "HelloFresh": "hellofresh.com",
    "MaintainX": "getmaintainx.com",
    "Hatch": "hatchglobal.com",
    "Cohere": "cohere.com",
    "Affirm": "affirm.com",
    "Guidepoint": "guidepoint.com",
    "Clutch": "clutch.ca",
    "Avetta": "avetta.com",
    "Fullscript": "fullscript.com",
    "Pelmorex": "pelmorex.com",
    "Agoda": "agoda.com",
    "Yotpo": "yotpo.com",
    "missionlane": "missionlane.com",
    "Pantheon": "pantheon.io",
    "Launch Potato": "launchpotato.com",
    "launchpotato": "launchpotato.com",
    "ICON plc": "iconplc.com",
    "Banyan Software": "banyansoftware.com",
    "banyansoftware": "banyansoftware.com",
    "Synechron": "synechron.com",
    "7shifts": "7shifts.com",
    "Alignerr": "alignerr.com",
    "BusPlanner": "busplanner.com",
    "Jerry": "getjerry.com",
    "CoolIT Systems": "coolitsystems.com",
    "Priceline": "priceline.com",

    # Alberta + other targets
    "AMA - Alberta Motor Association": "ama.ab.ca",
    "407 ETR": "407etr.com",
    "AMD": "amd.com",
    "AIG": "aig.com",
}


# --- Pass 1: manual map -----------------------------------------------------


def apply_manual_map(tracker: Tracker) -> dict[str, int]:
    """Apply MANUAL_MAP entries to companies that don't yet have a domain.

    Matching is case-insensitive: matches LOWER(name) or name_normalized
    against the lowercased key.
    """
    applied = 0
    skipped = 0
    not_found = 0
    for company_name, domain in MANUAL_MAP.items():
        norm = company_name.lower().strip()
        rows = tracker._query_all(
            "SELECT id, name, canonical_domain FROM companies "
            "WHERE LOWER(name) = ? OR name_normalized = ?",
            (norm, norm),
        )
        if not rows:
            not_found += 1
            continue
        for row in rows:
            if row["canonical_domain"]:
                skipped += 1
                continue
            tracker._execute(
                "UPDATE companies SET canonical_domain = ? WHERE id = ?",
                (domain, row["id"]),
            )
            applied += 1
            print(f"  Manual: {row['name']} -> {domain}")
    tracker._conn.commit()
    return {"applied": applied, "skipped": skipped, "not_found": not_found}


# --- DNS MX helper ----------------------------------------------------------


def _has_mx(domain: str) -> bool:
    """Return True if the domain has at least one MX record."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return len(answers) > 0
    except Exception:
        return False


# --- Pass 2: ATS slug inference --------------------------------------------


def infer_from_ats_slug(tracker: Tracker) -> dict[str, int]:
    """For companies with ats_slug but no domain, try {slug}.com/.ca/.io
    and apply the first one with an MX record.
    """
    rows = tracker._query_all(
        "SELECT id, name, ats_platform, ats_slug "
        "FROM companies "
        "WHERE ats_slug IS NOT NULL AND ats_slug != '' "
        "AND (canonical_domain IS NULL OR canonical_domain = '')",
        (),
    )
    applied = 0
    skipped = 0
    for row in rows:
        slug = row["ats_slug"].strip()
        if not slug or "/" in slug or " " in slug:
            skipped += 1
            continue
        found = False
        for suffix in (".com", ".ca", ".io"):
            candidate = slug + suffix
            if _has_mx(candidate):
                tracker._execute(
                    "UPDATE companies SET canonical_domain = ? "
                    "WHERE id = ?",
                    (candidate, row["id"]),
                )
                applied += 1
                print(f"  ATS slug: {row['name']} -> {candidate}")
                found = True
                break
            time.sleep(0.05)
        if not found:
            skipped += 1
    tracker._conn.commit()
    return {"applied": applied, "skipped": skipped}


# --- Pass 3: name-derived probe --------------------------------------------


_SUFFIX_RE = re.compile(
    r"\b(Inc\.?|Ltd\.?|Corp\.?|Corporation|Limited|"
    r"Company|Co\.?|Group|Pvt\.?|LLC|LLP|PLC|plc|"
    r"International|Global|Canada|Canadian|"
    r"Technologies|Technology|Solutions|Services|"
    r"Consulting|Partners)\b",
    re.I,
)


def _name_to_candidates(name: str) -> list[str]:
    """Generate plausible domain candidates from a company name."""
    stripped = _SUFFIX_RE.sub("", name)
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", "", stripped).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return []

    words = cleaned.lower().split()
    candidates: list[str] = []

    joined = "".join(words)
    if len(joined) >= 2:
        candidates.append(f"{joined}.com")
        candidates.append(f"{joined}.ca")
        candidates.append(f"{joined}.io")

    if len(words) > 1:
        hyphenated = "-".join(words)
        candidates.append(f"{hyphenated}.com")
        candidates.append(f"{hyphenated}.ca")

    if len(words) > 1 and len(words[0]) >= 2:
        candidates.append(f"{words[0]}.com")
        candidates.append(f"{words[0]}.ca")

    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def probe_remaining(
    tracker: Tracker,
    batch_size: int = 500,
) -> dict[str, int]:
    """For each company without a domain, probe candidate domains via DNS."""
    rows = tracker._query_all(
        "SELECT id, name FROM companies "
        "WHERE (canonical_domain IS NULL OR canonical_domain = '') "
        "ORDER BY id LIMIT ?",
        (batch_size,),
    )
    applied = 0
    skipped = 0
    for row in rows:
        candidates = _name_to_candidates(row["name"])
        found = False
        for candidate in candidates:
            if _has_mx(candidate):
                tracker._execute(
                    "UPDATE companies SET canonical_domain = ? "
                    "WHERE id = ?",
                    (candidate, row["id"]),
                )
                applied += 1
                print(f"  Probe: {row['name']} -> {candidate}")
                found = True
                break
        if not found:
            skipped += 1
        time.sleep(0.1)
    tracker._conn.commit()
    return {"applied": applied, "skipped": skipped}


def main() -> None:
    tracker = Tracker("default")
    try:
        total = tracker._query_all(
            "SELECT COUNT(*) AS c FROM companies", ()
        )[0]["c"]
        before = tracker._query_all(
            "SELECT COUNT(*) AS c FROM companies "
            "WHERE canonical_domain IS NOT NULL "
            "AND canonical_domain != ''",
            (),
        )[0]["c"]
        print(f"Companies: {total}, with domain: {before}")

        print("\n=== Pass 1: Manual map ===")
        r1 = apply_manual_map(tracker)
        print(
            f"  Applied: {r1['applied']}, Skipped: {r1['skipped']}, "
            f"Not found: {r1['not_found']}"
        )

        print("\n=== Pass 2: ATS slug inference ===")
        r2 = infer_from_ats_slug(tracker)
        print(f"  Applied: {r2['applied']}, Skipped: {r2['skipped']}")

        print("\n=== Pass 3: Automated probe ===")
        r3 = probe_remaining(tracker, batch_size=2500)
        print(f"  Applied: {r3['applied']}, Skipped: {r3['skipped']}")

        after = tracker._query_all(
            "SELECT COUNT(*) AS c FROM companies "
            "WHERE canonical_domain IS NOT NULL "
            "AND canonical_domain != ''",
            (),
        )[0]["c"]
        print(f"\n  Before: {before}/{total}")
        print(f"  After:  {after}/{total}")
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
