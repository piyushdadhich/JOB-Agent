"""Classification helpers for opportunity rows.

Pure functions (no LLM, no I/O). Used at insert time by
persist_record to populate the v2.11 classification columns
(city, ai_subtype, eval_priority), and by the v2.11 backfill to
populate them on existing rows.
"""
from __future__ import annotations

import re
from typing import Optional

# City patterns mirror dashboard/backend/routes/shortlist.py:CITY_FILTERS
# (lowercased; the LIKE %x% pattern collapses to a substring check).
# Rows persisted with classify_city land in the same buckets the UI
# filters on today.
_CITY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Toronto", (
        "toronto", "gta", "greater toronto", "mississauga",
        "brampton", "markham", "scarborough", "vaughan",
        "richmond hill", "oakville", "burlington", "hamilton",
        "oshawa", "pickering", "ajax", "whitby", "ontario",
    )),
    ("Calgary", ("calgary",)),
    ("Edmonton", ("edmonton",)),
    ("Remote-Canada", ("remote", "anywhere")),
)


def classify_city(location: Optional[str]) -> Optional[str]:
    """Map a location string to a canonical city or None.

    Returns one of "Toronto", "Calgary", "Edmonton", "Remote-Canada"
    on substring match (case-insensitive); else None. Toronto is
    checked first, matching the existing GTA filter semantics in
    shortlist.py.
    """
    if not location:
        return None
    text = location.lower()
    for canonical, patterns in _CITY_PATTERNS:
        for p in patterns:
            if p in text:
                return canonical
    return None


# Order matters: more specific patterns first. Word boundaries
# stop "AI" matching inside "main", "maintained", etc.
_AI_SUBTYPE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("ai_governance", re.compile(
        r"\bAI\s+(?:Governance|Risk)\b", re.IGNORECASE,
    )),
    ("ai_pm", re.compile(
        r"\b(?:AI\s+Product\s+Manager|AI\s+PM)\b", re.IGNORECASE,
    )),
    ("ai_engineer", re.compile(
        r"\b(?:AI\s+Engineer"
        r"|ML\s+Engineer"
        r"|Machine\s+Learning\s+Engineer)\b",
        re.IGNORECASE,
    )),
    ("ml_general", re.compile(
        r"\bMachine\s+Learning\b", re.IGNORECASE,
    )),
    ("ai_general", re.compile(
        r"\bAI\b", re.IGNORECASE,
    )),
)


def classify_ai_subtype(
    title: Optional[str],
    search_context: Optional[dict] = None,  # noqa: ARG001
) -> Optional[str]:
    """Return the AI subtype if the title matches an AI/ML pattern.

    search_context is accepted for symmetry with future signal-fusion
    logic (e.g. boost when discovered via an AI keyword search) but
    is currently unused -- title pattern is the dominant signal
    because ATS sources carry no per-keyword search_context.
    """
    if not title:
        return None
    for subtype, pattern in _AI_SUBTYPE_PATTERNS:
        if pattern.search(title):
            return subtype
    return None


def derive_eval_priority(ai_subtype: Optional[str]) -> int:
    """1 = AI/high-priority cloud direct; 2 = standard pre-filter."""
    return 1 if ai_subtype else 2


# Function (job family) classifier. First match wins; patterns are
# ordered from most specific (compound titles, multi-word phrases)
# to most generic. Each tuple is (canonical_label, compiled_regex).
# Word boundaries prevent "BA " matching inside "BAseline", etc.
_FUNCTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("Project Management & Delivery", re.compile(
        r"\b(?:project\s+manage(?:r|ment)|programme?\s+manage(?:r|ment)"
        r"|delivery\s+(?:manage(?:r|ment)|lead|owner)|pmo"
        r"|scrum\s+master|agile\s+(?:coach|lead)"
        r"|portfolio\s+manage(?:r|ment)|tech\s+delivery)\b",
        re.IGNORECASE,
    )),
    ("Business Analysis", re.compile(
        r"\b(?:business\s+analyst|business\s+systems?\s+analyst"
        r"|requirements?\s+analyst|BA\s|\(BA\)"
        r"|business\s+advisory)\b",
        re.IGNORECASE,
    )),
    ("Product Management", re.compile(
        r"\b(?:product\s+(?:owner|manage(?:r|ment))|associate\s+product"
        r"|product\s+lead)\b",
        re.IGNORECASE,
    )),
    ("Data & Analytics", re.compile(
        r"\b(?:data\s+(?:analyst|scien(?:ce|tist)|engineer)"
        r"|analytics\s+(?:lead|manage(?:r|ment)|engineer)"
        r"|business\s+intelligence|bi\s+develop\w*|bi\s+analyst"
        r"|machine\s+learning\s+(?:analyst|engineer)"
        r"|quantitative\s+analyst)\b",
        re.IGNORECASE,
    )),
    ("Software Engineering", re.compile(
        r"\b(?:software\s+(?:engineer|develop|architect)\w*"
        r"|senior\s+develop\w*|full[\s-]?stack|back[\s-]?end\s+\w+"
        r"|front[\s-]?end\s+(?:engineer|develop)\w*"
        r"|sre|site\s+reliability|devops|platform\s+engineer\w*"
        r"|cloud\s+engineer\w*|mobile\s+(?:engineer|develop)\w*"
        r"|member\s+of\s+technical\s+staff|principal\s+engineer\w*"
        r"|staff\s+engineer\w*|qa\s+engineer\w*|test\s+engineer\w*)\b",
        re.IGNORECASE,
    )),
    ("Finance & Accounting", re.compile(
        r"\b(?:finance\s+(?:manage(?:r|ment)|analyst|lead)"
        r"|accountant|accounting\s+(?:manage(?:r|ment)|lead)"
        r"|controller|fp&a|financial\s+analyst|treasury"
        r"|audit(?:or|ing)|bookkeep(?:er|ing)|tax\s+(?:analyst|manage(?:r|ment))"
        r"|record\s+to\s+report|procure\s+to\s+pay|order\s+to\s+cash"
        r"|accounts?\s+(?:payable|receivable)|valuation\s+(?:services|analyst|director)"
        r"|underwrit(?:er|ing))\b",
        re.IGNORECASE,
    )),
    ("Human Resources", re.compile(
        r"\b(?:human\s+resources?|hr\s+(?:manage(?:r|ment)|business)"
        r"|talent\s+(?:acquisition|partner|manage(?:r|ment))"
        r"|recruit(?:er|ment|ing)|people\s+(?:partner|operations))\b",
        re.IGNORECASE,
    )),
    ("Marketing", re.compile(
        r"\b(?:marketing\s+(?:manage(?:r|ment)|lead|coordinat|specialist)"
        r"|brand\s+(?:manage(?:r|ment)|lead)|content\s+(?:marketing|strateg)"
        r"|seo\s+(?:specialist|manage)|growth\s+marketing"
        r"|social\s+media\s+(?:manage(?:r|ment)|lead)"
        r"|communications\s+manage(?:r|ment))\b",
        re.IGNORECASE,
    )),
    ("Sales", re.compile(
        r"\b(?:sales\s+(?:manage(?:r|ment)|lead|representative|director|associate)"
        r"|account\s+(?:exec(?:utive)?|manage(?:r|ment))\w*"
        r"|business\s+development\s+(?:rep|manage|associate)\w*"
        r"|bdr|sdr|territory\s+manage(?:r|ment)"
        r"|banker|bank\s+manage(?:r|ment)|branch\s+manage(?:r|ment)"
        r"|relationship\s+manage(?:r|ment)|banking\s+(?:associate|advisor)"
        r"|financ(?:e|ial)\s+(?:advisor|planner|sales)\w*"
        r"|mortgage\s+(?:specialist|advisor|broker)\w*"
        r"|financial\s+services\s+representative"
        r"|wealth\s+(?:management\s+)?advisor\w*|investment\s+advisor\w*"
        r"|insurance\s+(?:advisor|broker|agent)\w*"
        r"|advisor\s+trainee|advisory\s+agent"
        r"|teller\w*|premier\s+(?:client|relationship)\w*)\b",
        re.IGNORECASE,
    )),
    ("Operations", re.compile(
        r"(?:\boperations?\s+(?:manage(?:r|ment)|lead|analyst|coordinator|associate)\w*"
        r"|\blogistics\b|\bsupply\s+chain\b|\bwarehouse\s+manage(?:r|ment)\w*"
        r"|\bprocurement\b|\bfulfillment\b|\bstore\s+manage(?:r|ment)\w*"
        r"|\bassistant\s+store\s+manage(?:r|ment)\w*"
        r"|\bstore\s+assistant\b|\bstore\s+supervisor\b"
        r"|\bteam\s+member\b|\bteam\s+lead\b|\bteam\s+leader\b"
        r"|\bassistant\s+manage(?:r|ment)\b|\bsupervisor\b"
        r"|\bexecutive\s+assistant\b|\badministrative\s+assistant\b"
        r"|\boffice\s+(?:manage(?:r|ment)|coordinator)\w*"
        r"|\bfacilities\s+manage(?:r|ment)\w*"
        r"|\bproperty\s+manage(?:r|ment)\w*"
        r"|\bfood\s+captain\b|\bg[eé]rant(?:e|s)?\b)",
        re.IGNORECASE,
    )),
    ("Legal & Compliance", re.compile(
        r"\b(?:legal\s+(?:counsel|advisor|manage(?:r|ment))"
        r"|compliance\s+(?:manage(?:r|ment)|officer|analyst)"
        r"|regulatory\s+(?:affairs|manage(?:r|ment))"
        r"|paralegal|attorney|lawyer|risk\s+(?:manage(?:r|ment)|officer))\b",
        re.IGNORECASE,
    )),
    ("IT & Infrastructure", re.compile(
        r"\b(?:it\s+(?:manage(?:r|ment)|support|administrator)"
        r"|infrastructure\s+(?:engineer|manage(?:r|ment))"
        r"|systems?\s+administrator|network\s+(?:engineer|administrator)"
        r"|sys\s?admin|helpdesk|service\s+desk"
        r"|application\s+support|technical\s+support\s+engineer"
        r"|maintenance\s+technician)\b",
        re.IGNORECASE,
    )),
    ("Design", re.compile(
        r"\b(?:ux\s+(?:designer|researcher|lead)"
        r"|ui\s+(?:designer|develop)|product\s+designer"
        r"|graphic\s+designer|visual\s+designer"
        r"|creative\s+(?:director|lead))\b",
        re.IGNORECASE,
    )),
    ("Customer Success", re.compile(
        r"(?:\bcustomer\s+(?:success|support|experience|service)\w*"
        r"|\bclient\s+(?:success|services?|service\s+representative"
        r"|service\s+associate)\w*"
        r"|\bcx\s+(?:manage(?:r|ment)|lead)\w*"
        r"|\btechnical\s+support\b|\bcall\s+cent(?:er|re)\s+agent\b"
        r"|\bcontact\s+cent(?:er|re)\s+(?:agent|representative)\w*"
        r"|\bservice\s+representative\b"
        r"|pr[eé]pos[eé])",
        re.IGNORECASE,
    )),
)


def classify_function(
    title: Optional[str],
    posting_text: Optional[str] = None,
) -> Optional[str]:
    """Return a canonical job-family label or None.

    First match wins. Title is scanned first because it's the strongest
    signal; posting_text is a fallback for cases where the title is
    a generic placeholder (e.g. "Senior Position").
    """
    haystack_parts: list[str] = []
    if title:
        haystack_parts.append(title)
    if posting_text:
        haystack_parts.append(posting_text[:2000])
    if not haystack_parts:
        return None
    haystack = "\n".join(haystack_parts)
    for label, pattern in _FUNCTION_PATTERNS:
        if pattern.search(haystack):
            return label
    return None


# Industry classifier. Patterns are ordered specific -> generic; the
# first match wins. Most patterns don't anchor a trailing word
# boundary so they match common compound names like "Scotiabank",
# "Pharmaceuticals", "Hospitality".
_INDUSTRY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("Financial Services", re.compile(
        r"(?:bank(?:ing|er|s)?\b|\bcredit\s+union\b|\binsur(?:ance|er)\w*"
        r"|\bwealth\s+manage\w*|\basset\s+manage\w*|\bpension\w*"
        r"|\bsecurities\b|\binvestment\s+(?:manage|firm|bank)\w*"
        r"|\bfintech\w*|\bpayments?\b|\bbrokerage\w*|\bcapital\s+market\w*)",
        re.IGNORECASE,
    )),
    ("Energy & Utilities", re.compile(
        r"(?:\benergy\b|\boil\s+(?:and|&)\s+gas\b|\bpetroleum\b"
        r"|\butilit(?:y|ies)\b|\belectric\s+power\b|\bhydro\b|\bgas\s+co\w*"
        r"|\bpipeline\w*|\brenewables?\b|\bsolar\b|\bwind\s+(?:farm|power)\b)",
        re.IGNORECASE,
    )),
    ("Public Sector", re.compile(
        r"(?:\bgovernment\b|\bpublic\s+sector\b|\bcrown\s+corp\w*|\bministry\b"
        r"|\bmunicipal\w*|\bfederal\s+(?:agency|department)\b"
        r"|\bprovincial\b|\bcity\s+of\s+|\bregion\s+of\s+|\bcounty\s+of\b"
        r"|\bpublic\s+service\w*)",
        re.IGNORECASE,
    )),
    ("Real Estate & Construction", re.compile(
        r"(?:\breal\s+estate\b|\bproperty\s+(?:manage|develop)\w*|\breit\b"
        r"|\bconstruction\b|\bhome[\s-]?build\w*"
        r"|\bdevelop(?:er|ment)\s+(?:firm|corp)\w*"
        r"|\barchitect(?:ure|ural)\s+firm\w*|\bengineering\s+consult\w*"
        r"|\binfrastructure\s+(?:develop|build)\w*)",
        re.IGNORECASE,
    )),
    ("Healthcare", re.compile(
        r"(?:\bhealth\s?care\w*|\bhospital\w*|\bclinic\w*|\bpharma\w*"
        r"|\bbiotech\w*|\bmedical\s+(?:device|center|practice)\w*"
        r"|\blife\s+sciences\b|\blong[\s-]?term\s+care\b|\bnursing\b"
        r"|\bdental\b)",
        re.IGNORECASE,
    )),
    ("Education", re.compile(
        r"(?:\beducation\b|\buniversit(?:y|ies)\b|\bcollege\b"
        r"|\bschool\s+board\w*|\bacademic\b|\bk[-\s]12\b|\bedtech\b"
        r"|\bpolytechnic\b|\binstitute\s+of\b)",
        re.IGNORECASE,
    )),
    ("Telecommunications", re.compile(
        r"(?:\btelecom\w*|\bwireless\b|\bmobile\s+carrier\b"
        r"|\bisp\b|\bbroadband\b|\bcable\s+co\w*|\btelco\b)",
        re.IGNORECASE,
    )),
    ("Media & Entertainment", re.compile(
        r"(?:\bmedia\b|\bbroadcast\w*|\bpublish\w*|\bnews\s+"
        r"|\bentertainment\b|\bfilm\b|\btelevision\b|\bstreaming\b"
        r"|\bgaming\s+studio\b|\badvertising\s+agenc\w*)",
        re.IGNORECASE,
    )),
    ("Transportation & Logistics", re.compile(
        r"(?:\btransport\w*|\blogistics\b|\bshipping\b|\bfreight\b"
        r"|\brailway\b|\bairline\w*|\baviation\b|\bcourier\b"
        r"|\btrucking\b|\bfleet\b)",
        re.IGNORECASE,
    )),
    ("Mining & Resources", re.compile(
        r"(?:\bmining\b|\bmetals\b|\bminerals\b|\bforestr(?:y|ies)\b"
        r"|\bnatural\s+resources\b|\bexploration\s+(?:co|firm)\w*)",
        re.IGNORECASE,
    )),
    ("Retail & Consumer", re.compile(
        r"(?:\bretail\w*|\be[-\s]?commerce\b|\bconsumer\s+(?:goods|products)\b"
        r"|\bgrocer(?:y|ies)\b|\bfashion\b|\bapparel\b|\bcpg\b"
        r"|\bfood\s+service\w*)",
        re.IGNORECASE,
    )),
    ("Manufacturing", re.compile(
        r"(?:\bmanufactur\w*|\bindustrial\b|\bfactory\b|\bplant\s+operations\b"
        r"|\bproduction\s+facility\b|\bautomotive\b|\baerospace\b)",
        re.IGNORECASE,
    )),
    ("Professional Services", re.compile(
        r"(?:\bconsult(?:ing|ant|ancy|ants)\b|\badvisory\s+(?:firm|service)\w*"
        r"|\bprofessional\s+services\b|\blaw\s+firm\b|\baccounting\s+firm\b)",
        re.IGNORECASE,
    )),
    ("Technology", re.compile(
        r"(?:\bsoftware\b|\bsaas\b|\btechnology\s+(?:firm|co|company)\b"
        r"|\btech\s+(?:firm|startup)\b|\bit\s+services\b"
        r"|\bdata\s+(?:platform|company)\b|\bcloud\s+(?:platform|services)\b"
        r"|\bai\s+(?:startup|company)\b)",
        re.IGNORECASE,
    )),
)


# Known-brand fallback for employers whose names lack industry keywords.
# Keyed by lowercase substring; first hit wins. Order matters only for
# rare collisions ("intact financial" before "intact" is unnecessary
# because the matcher uses `in` checks, not regex).
_BRAND_INDUSTRY: tuple[tuple[str, str], ...] = (
    ("bmo", "Financial Services"),
    ("scotiabank", "Financial Services"),
    ("scotia bank", "Financial Services"),
    ("td bank", "Financial Services"),
    ("cibc", "Financial Services"),
    ("sun life", "Financial Services"),
    ("manulife", "Financial Services"),
    ("desjardins", "Financial Services"),
    ("intact", "Financial Services"),
    ("aviva", "Financial Services"),
    ("ia financial", "Financial Services"),
    ("ia group", "Financial Services"),
    ("wealthsimple", "Financial Services"),
    ("questrade", "Financial Services"),
    ("neo financial", "Financial Services"),
    ("eqbank", "Financial Services"),
    ("eq bank", "Financial Services"),
    ("td trust", "Financial Services"),
    ("citi ", "Financial Services"),
    ("citigroup", "Financial Services"),
    ("capital one", "Financial Services"),
    ("omers", "Financial Services"),
    ("psp investments", "Financial Services"),
    ("cpp investments", "Financial Services"),
    ("ontario teachers", "Financial Services"),
    ("tmx", "Financial Services"),
    ("bdc", "Financial Services"),
    ("meridian credit", "Financial Services"),
    ("nicola wealth", "Financial Services"),
    ("pwc", "Professional Services"),
    ("kpmg", "Professional Services"),
    ("deloitte", "Professional Services"),
    ("ernst & young", "Professional Services"),
    ("ernst and young", "Professional Services"),
    ("bdo canada", "Professional Services"),
    ("mnp ", "Professional Services"),
    ("accenture", "Professional Services"),
    ("mckinsey", "Professional Services"),
    ("bcg", "Professional Services"),
    ("bain & company", "Professional Services"),
    ("robertson & company", "Professional Services"),
    ("walmart", "Retail & Consumer"),
    ("tjx canada", "Retail & Consumer"),
    ("loblaws", "Retail & Consumer"),
    ("loblaw companies", "Retail & Consumer"),
    ("metro inc", "Retail & Consumer"),
    ("sobeys", "Retail & Consumer"),
    ("hellofresh", "Retail & Consumer"),
    ("ebay", "Retail & Consumer"),
    ("amazon ", "Retail & Consumer"),
    ("compass group", "Retail & Consumer"),
    ("telus", "Telecommunications"),
    ("rogers ", "Telecommunications"),
    ("bell canada", "Telecommunications"),
    ("bell mobility", "Telecommunications"),
    ("freedom mobile", "Telecommunications"),
    ("amazon web services", "Technology"),
    ("google", "Technology"),
    ("microsoft", "Technology"),
    ("shopify", "Technology"),
    ("salesforce", "Technology"),
    ("ibm ", "Technology"),
    ("oracle", "Technology"),
    ("ashby", "Technology"),
    ("buspl", "Technology"),
    ("alignerr", "Technology"),
    ("pelmorex", "Media & Entertainment"),
    ("triparc", "Media & Entertainment"),
    ("cbc", "Media & Entertainment"),
    ("ctv", "Media & Entertainment"),
    ("lcbo", "Public Sector"),
    ("opg", "Public Sector"),
    ("olg", "Public Sector"),
    ("testco", "Public Sector"),
    ("canada post", "Public Sector"),
    ("hydro one", "Energy & Utilities"),
    ("enbridge", "Energy & Utilities"),
    ("suncor", "Energy & Utilities"),
    ("tc energy", "Energy & Utilities"),
    ("capital power", "Energy & Utilities"),
    ("altalink", "Energy & Utilities"),
    ("epcor", "Energy & Utilities"),
    ("atco", "Energy & Utilities"),
    ("atkinsr", "Real Estate & Construction"),
    ("quadreal", "Real Estate & Construction"),
    ("bgis", "Real Estate & Construction"),
    ("brookfield", "Real Estate & Construction"),
    ("pcl construction", "Real Estate & Construction"),
)


def classify_industry(
    employer_name: Optional[str],
    employer_industry: Optional[str] = None,
) -> Optional[str]:
    """Return a canonical industry label or None.

    Checks employer_industry (if set) first because it's typically the
    source's own classification; then a brand-name shortlist for big
    employers whose names lack industry keywords (e.g. "BMO", "PwC");
    then keyword regex fallback on the combined haystack.
    """
    haystack_parts: list[str] = []
    if employer_industry:
        haystack_parts.append(employer_industry)
    if employer_name:
        haystack_parts.append(employer_name)
    if not haystack_parts:
        return None
    haystack = " ".join(haystack_parts)
    if employer_name:
        name_lower = employer_name.lower()
        for needle, label in _BRAND_INDUSTRY:
            if needle in name_lower:
                return label
    for label, pattern in _INDUSTRY_PATTERNS:
        if pattern.search(haystack):
            return label
    return None
