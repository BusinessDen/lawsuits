#!/usr/bin/env python3
"""
Colorado Business Lawsuit Tracker - Scraper
Pulls new federal court filings from CourtListener API,
filters for business-relevant cases, and generates AI summaries.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import urllib.request
import urllib.parse
import urllib.error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COURTLISTENER_TOKEN = os.environ.get("COURTLISTENER_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Colorado federal courts
COURTS = {
    "cod": "U.S. District Court for Colorado",
    "cob": "U.S. Bankruptcy Court for Colorado",
}

DATA_FILE = Path("lawsuit-data.json")
LOOKBACK_DAYS = 2  # How far back to search for new filings
MAX_CASES_PER_RUN = 50  # Cap on new cases to process per run
MAX_AI_SUMMARIES_PER_RUN = 20  # Cap on Claude API calls per run

# Nature-of-suit codes that indicate business disputes
# See: https://www.uscourts.gov/sites/default/files/js_044_style.pdf
BUSINESS_NOS_CODES = {
    # Contract
    "110": "Insurance",
    "120": "Marine",
    "130": "Miller Act",
    "140": "Negotiable Instrument",
    "150": "Recovery of Overpayment",
    "151": "Medicare Act",
    "152": "Recovery of Student Loans",
    "153": "Recovery of Veteran Benefits",
    "160": "Stockholder Suits",
    "190": "Other Contract",
    "195": "Contract Product Liability",
    "196": "Franchise",
    # Real Property
    "210": "Land Condemnation",
    "220": "Foreclosure",
    "230": "Rent/Lease/Ejectment",
    "240": "Torts to Land",
    "245": "Tort Product Liability",
    "290": "All Other Real Property",
    # Bankruptcy
    "422": "Appeal 28 USC 158",
    "423": "Withdrawal 28 USC 157",
    # Property Rights / IP
    "820": "Copyrights",
    "830": "Patent",
    "835": "Patent - Abbreviated New Drug",
    "840": "Trademark",
    # Antitrust
    "410": "Antitrust",
    # Banks and Banking
    "430": "Banks and Banking",
    # Commerce
    "450": "Commerce",
    "460": "Deportation",
    # Securities
    "850": "Securities/Commodities/Exchange",
    # Tax
    "870": "IRS Third Party 26 USC 7609",
    "871": "IRS Third Party 26 USC 7609",
    # Labor (business-relevant subset)
    "710": "Fair Labor Standards Act",
    "720": "Labor/Management Relations",
    "740": "Railway Labor Act",
    "751": "Family and Medical Leave Act",
    "790": "Other Labor Litigation",
    "791": "Employee Retirement Income Security Act",
    # Fraud
    "370": "Other Fraud",
    "371": "Truth in Lending",
    # Environmental
    "893": "Environmental Matters",
    # Other business-relevant
    "375": "False Claims Act",
    "376": "Qui Tam (31 USC 3729(a))",
    "470": "Racketeer Influenced",
    "480": "Consumer Credit",
    "490": "Cable/Satellite TV",
    "890": "Other Statutory Actions",
    "891": "Agricultural Acts",
    "895": "Freedom of Information Act",
    "899": "Administrative Procedure Act",
    "950": "Constitutionality of State Statutes",
}

# Keywords in party names that suggest business entities
BUSINESS_ENTITY_PATTERNS = [
    r"\bLLC\b", r"\bINC\b", r"\bCORP\b", r"\bLTD\b", r"\bLP\b", r"\bLLP\b",
    r"\bCO\.\b", r"\bCOMPANY\b", r"\bGROUP\b", r"\bHOLDINGS\b",
    r"\bPARTNERS\b", r"\bFUND\b", r"\bTRUST\b", r"\bBANK\b", r"\bN\.A\.\b",
    r"\bASSOCIATION\b", r"\bFOUNDATION\b", r"\bENTERPRISES?\b",
    r"\bVENTURES?\b", r"\bCAPITAL\b", r"\bINVESTMENT[S]?\b",
    r"\bINSURANCE\b", r"\bINDUSTRIES\b", r"\bSERVICES?\b",
    r"\bTECHNOLOG(Y|IES)\b", r"\bSOLUTIONS?\b", r"\bSYSTEMS?\b",
    r"\bNETWORK[S]?\b", r"\bMEDIA\b", r"\bDEVELOPMENT\b",
    r"\bPROPERT(Y|IES)\b", r"\bREALTY\b", r"\bMORTGAGE\b",
    r"\bFINANCIAL\b", r"\bCONSTRUCTION\b", r"\bBREWING\b",
    r"\bRESTAURANT[S]?\b", r"\bHOTEL[S]?\b", r"\bRESORTS?\b",
    r"\bAIRLINES?\b", r"\bMOTORS?\b", r"\bAUTO\b",
    r"\bP\.?C\.\b", r"\bP\.?A\.\b",  # professional corps
    r"\bD/?B/?A\b",  # doing business as
]

BUSINESS_RE = re.compile("|".join(BUSINESS_ENTITY_PATTERNS), re.IGNORECASE)

# Entities to always flag (add Denver/Colorado companies you cover)
WATCHED_ENTITIES_FILE = Path("watched-entities.json")

# ---------------------------------------------------------------------------
# Topic / Theme detection
# Each theme has keywords that trigger if found in the case name,
# party names, cause, nature of suit, or docket entry descriptions.
# Cases matching a theme get tagged even if they don't pass the
# standard business-entity filter.
# ---------------------------------------------------------------------------

THEMES = {
    "homelessness": {
        "label": "Homelessness",
        "keywords": [
            r"\bhomeless\b", r"\bunhoused\b", r"\bunsheltered\b",
            r"\bencampment[s]?\b", r"\bcamping ban\b", r"\burban camping\b",
            r"\bright to rest\b", r"\bvagranc(y|ies)\b",
            r"\bshelter[s]?\b(?!.*\b(tax|animal|bomb)\b)",  # shelter but not tax shelter etc
            r"\btransient[s]?\b", r"\bpanhandl(e|ing|er)\b",
            r"\bloiter(ing)?\b", r"\bsidewalk (obstruct|camp|sleep)",
            r"\bsweep[s]?\b.*\b(camp|encampment|tent|homeless)",
            r"\btent cit(y|ies)\b", r"\bsafe outdoor space",
            r"\bhousing first\b", r"\bcontinuum of care\b",
            r"\bpoint[ -]in[ -]time\b", r"\bPIT count\b",
            r"\bnavigation (center|campus)\b",
            r"\bDHOL\b", r"\bDenver Homeless Out Loud\b",
            r"\bMDHI\b", r"\bMetro Denver Homeless Initiative\b",
            r"\bCoalition for the Homeless\b",
            r"\bDenver Rescue Mission\b",
            r"\bUrban Peak\b",
            r"\bSamaritan House\b",
            r"\bGathering Place\b",
            r"\bDelores Project\b",
            r"\bColorado Village Collaborative\b",
            r"\bSprings Rescue Mission\b",
            r"\bHousing Stability\b", r"\bHOST\b",
        ],
        # NOS codes relevant to homelessness litigation
        "nos_codes": [
            "440",  # Other Civil Rights
            "441",  # Voting
            "442",  # Employment Civil Rights
            "443",  # Housing/Accommodations
            "444",  # Welfare
            "445",  # American with Disabilities - Employment
            "446",  # American with Disabilities - Other
        ],
    },
}

# Pre-compile theme patterns
for theme_id, theme in THEMES.items():
    theme["_compiled"] = [re.compile(kw, re.IGNORECASE) for kw in theme["keywords"]]


def detect_themes(case):
    """Detect which themes a case matches. Returns list of theme IDs."""
    matched = []
    # Build search corpus from all available text
    case_name = case.get("caseName", "") or case.get("case_name", "") or ""
    nos = case.get("suitNature", "") or case.get("nature_of_suit", "") or ""
    cause = case.get("cause", "") or ""
    parties = " ".join(p.get("name", "") for p in case.get("parties", []))
    entries = " ".join(e.get("description", "") for e in case.get("docket_entries", []))
    corpus = f"{case_name} {nos} {cause} {parties} {entries}"

    for theme_id, theme in THEMES.items():
        # Check keywords
        for pattern in theme["_compiled"]:
            if pattern.search(corpus):
                matched.append(theme_id)
                break
        else:
            # Check NOS codes
            for code in theme.get("nos_codes", []):
                if code in nos:
                    matched.append(theme_id)
                    break
    return matched

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def api_get(url, token=None, params=None):
    """Make a GET request and return parsed JSON."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Token {token}")
    req.add_header("User-Agent", "BusinessDen-LawsuitTracker/1.0")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry = int(e.headers.get("Retry-After", 60))
            print(f"  Rate limited. Waiting {retry}s...")
            time.sleep(min(retry, 120))
            return api_get(url.split("?")[0], token, params)
        print(f"  HTTP {e.code}: {e.reason} for {url}")
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def anthropic_summarize(case_info):
    """Send case metadata to Claude for summary and newsworthiness score."""
    if not ANTHROPIC_API_KEY:
        return None, None

    prompt = f"""You are a Denver business journalist reviewing new federal court filings.
Analyze this case and provide:
1. A 2-3 sentence plain-English summary suitable for a business news audience. Focus on WHO is suing whom, WHAT the dispute is about, and WHY it matters.
2. A newsworthiness score from 1-5:
   - 1: Routine/boilerplate (debt collection, standard contract dispute)
   - 2: Minor business dispute, limited public interest
   - 3: Notable dispute involving recognizable companies or significant amounts
   - 4: Major business dispute, public interest angle, large dollar amounts
   - 5: Blockbuster case - major company, fraud, class action, huge stakes

Case details:
- Case name: {case_info.get('case_name', 'Unknown')}
- Court: {case_info.get('court_name', 'Unknown')}
- Date filed: {case_info.get('date_filed', 'Unknown')}
- Nature of suit: {case_info.get('nature_of_suit', 'Unknown')}
- Cause: {case_info.get('cause', 'Unknown')}
- Jurisdiction: {case_info.get('jurisdiction_type', 'Unknown')}
- Jury demand: {case_info.get('jury_demand', 'Unknown')}
- Parties: {json.dumps(case_info.get('parties', []), indent=2)}
- Recent docket entries: {json.dumps(case_info.get('docket_entries', []), indent=2)}

Respond in this exact JSON format and nothing else:
{{"summary": "Your 2-3 sentence summary here", "score": 3, "tags": ["contract", "fraud", etc]}}
"""

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        text = data["content"][0]["text"].strip()
        # Strip markdown fences if present
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        return parsed.get("summary", ""), parsed.get("score", 1), parsed.get("tags", [])
    except Exception as e:
        print(f"  AI summary error: {e}")
        return None, None, None


# ---------------------------------------------------------------------------
# CourtListener data fetching
# ---------------------------------------------------------------------------

def search_new_filings(court_id, filed_after):
    """Search for new filings in a court since a given date."""
    results = []
    params = {
        "type": "d",
        "court": court_id,
        "filed_after": filed_after,
        "order_by": "dateFiled desc",
    }
    url = "https://www.courtlistener.com/api/rest/v4/search/"

    print(f"  Searching {court_id} for filings after {filed_after}...")
    data = api_get(url, COURTLISTENER_TOKEN, params)
    if not data:
        return results

    count = data.get("count", 0)
    print(f"  Found {count} total results")

    for item in data.get("results", []):
        results.append(item)

    # Paginate if needed, up to our cap
    next_url = data.get("next")
    pages = 1
    while next_url and len(results) < MAX_CASES_PER_RUN:
        pages += 1
        time.sleep(0.5)  # Be polite
        data = api_get(next_url, COURTLISTENER_TOKEN)
        if not data:
            break
        for item in data.get("results", []):
            results.append(item)
        next_url = data.get("next")

    print(f"  Retrieved {len(results)} cases across {pages} page(s)")
    return results[:MAX_CASES_PER_RUN]


def fetch_docket_detail(docket_id):
    """Fetch full docket metadata."""
    url = f"https://www.courtlistener.com/api/rest/v4/dockets/{docket_id}/"
    return api_get(url, COURTLISTENER_TOKEN)


def fetch_parties(docket_id):
    """Fetch parties for a docket."""
    url = "https://www.courtlistener.com/api/rest/v4/parties/"
    params = {"docket": docket_id}
    data = api_get(url, COURTLISTENER_TOKEN, params)
    if not data:
        return []

    parties = []
    for p in data.get("results", []):
        party = {
            "name": p.get("name", ""),
            "type": "",
        }
        # Get party type from the role in this specific docket
        for role in p.get("roles", []):
            if role.get("docket") and str(role["docket"]).endswith(f"/{docket_id}/"):
                party["type"] = role.get("role_str", "")
                break
        # Fallback: check party_types
        if not party["type"]:
            for pt in p.get("party_types", []):
                party["type"] = pt.get("name", "")
                break
        parties.append(party)
    return parties


def fetch_docket_entries(docket_id, limit=5):
    """Fetch recent docket entries for context."""
    url = "https://www.courtlistener.com/api/rest/v4/docket-entries/"
    params = {
        "docket": docket_id,
        "order_by": "-date_filed",
        "fields": "id,entry_number,date_filed,description",
        "omit": "recap_documents__plain_text",
    }
    data = api_get(url, COURTLISTENER_TOKEN, params)
    if not data:
        return []

    entries = []
    for e in data.get("results", [])[:limit]:
        entries.append({
            "entry_number": e.get("entry_number"),
            "date_filed": e.get("date_filed"),
            "description": e.get("description", ""),
        })
    return entries


# ---------------------------------------------------------------------------
# Filtering logic
# ---------------------------------------------------------------------------

def load_watched_entities():
    """Load list of entities to always flag.
    
    Supports two formats:
    - Plain string: "Chipotle"
    - Tagged dict: {"name": "AEG Presents", "associated_with": "Philip Anschutz"}
    
    Returns list of (name, associated_with_or_None) tuples.
    """
    if WATCHED_ENTITIES_FILE.exists():
        with open(WATCHED_ENTITIES_FILE) as f:
            raw = json.load(f)
        entities = []
        for entry in raw:
            if isinstance(entry, str):
                entities.append((entry, None))
            elif isinstance(entry, dict):
                entities.append((entry.get("name", ""), entry.get("associated_with")))
        return entities
    return []


def is_business_case(case):
    """
    Determine if a case is business-related.
    Returns (is_business: bool, reasons: list[str])
    """
    reasons = []

    # Check nature of suit
    nos = case.get("suitNature", "") or case.get("nature_of_suit", "") or ""
    nos_clean = nos.strip()
    # Try to match against known codes or descriptions
    for code, desc in BUSINESS_NOS_CODES.items():
        if code in nos_clean or desc.lower() in nos_clean.lower():
            reasons.append(f"Nature of suit: {desc}")
            break

    # Check case name for business entity patterns
    case_name = case.get("caseName", "") or case.get("case_name", "") or ""
    if BUSINESS_RE.search(case_name):
        reasons.append("Business entity in case name")

    # Check parties if available
    parties = case.get("parties", [])
    for p in parties:
        name = p.get("name", "")
        if BUSINESS_RE.search(name):
            reasons.append(f"Business entity party: {name[:60]}")
            break

    # Check against watched entities (supports tagged format)
    watched = load_watched_entities()
    case_text = case_name.upper()
    # Also check party names
    party_text = " ".join(p.get("name", "") for p in parties).upper()
    search_text = case_text + " " + party_text
    for entity_name, associated_with in watched:
        if entity_name.upper() in search_text:
            if associated_with:
                reasons.append(f"Watched entity: {entity_name} ({associated_with})")
            else:
                reasons.append(f"Watched entity: {entity_name}")
            break

    # Bankruptcy court cases are inherently business-relevant
    court_id = case.get("court_id", "") or case.get("court", "")
    if "cob" in str(court_id).lower():
        reasons.append("Bankruptcy court filing")

    # Theme matches also pass the filter
    themes = detect_themes(case)
    for t in themes:
        label = THEMES[t]["label"]
        reasons.append(f"Theme: {label}")

    return len(reasons) > 0, reasons


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def load_existing_data():
    """Load existing case data."""
    if DATA_FILE.exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"cases": [], "last_updated": None, "metadata": {}}


def save_data(data):
    """Save case data to JSON."""
    data["last_updated"] = datetime.now(timezone.utc).isoformat() + "Z"
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(data['cases'])} total cases to {DATA_FILE}")


def run():
    print("=" * 60)
    print("Colorado Business Lawsuit Tracker")
    print(f"Run time: {datetime.now(timezone.utc).isoformat()}Z")
    print("=" * 60)

    if not COURTLISTENER_TOKEN:
        print("WARNING: No COURTLISTENER_TOKEN set. Using unauthenticated API (100 req/day limit).")

    # Load existing data
    data = load_existing_data()
    existing_ids = {c["docket_id"] for c in data["cases"]}
    print(f"Existing cases: {len(existing_ids)}")

    # Calculate lookback date
    filed_after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # Search all Colorado federal courts
    all_results = []
    for court_id, court_name in COURTS.items():
        print(f"\n--- {court_name} ---")
        results = search_new_filings(court_id, filed_after)
        for r in results:
            r["court_name"] = court_name
            r["court_id_resolved"] = court_id
        all_results.extend(results)
        time.sleep(1)  # Be polite between courts

    print(f"\nTotal raw results: {len(all_results)}")

    # Deduplicate against existing data
    new_results = []
    for r in all_results:
        docket_id = r.get("docket_id") or r.get("id")
        if docket_id and docket_id not in existing_ids:
            r["docket_id"] = docket_id
            new_results.append(r)
    print(f"New (not previously seen): {len(new_results)}")

    # Filter for business cases
    business_cases = []
    for r in new_results:
        is_biz, reasons = is_business_case(r)
        if is_biz:
            r["filter_reasons"] = reasons
            business_cases.append(r)
    print(f"Business-relevant: {len(business_cases)}")

    if not business_cases:
        print("No new business cases found. Done.")
        # Still update the timestamp
        save_data(data)
        return

    # Fetch details for business cases
    print(f"\nFetching details for {len(business_cases)} cases...")
    detailed_cases = []
    for i, case in enumerate(business_cases):
        docket_id = case["docket_id"]
        print(f"  [{i+1}/{len(business_cases)}] {case.get('caseName', 'Unknown')[:60]}...")

        # Get docket detail (may fail without auth)
        detail = fetch_docket_detail(docket_id) or {}
        time.sleep(0.3)

        # Get parties (may fail without auth)
        parties = fetch_parties(docket_id)
        time.sleep(0.3)

        # Get recent docket entries (may fail without auth)
        entries = fetch_docket_entries(docket_id)
        time.sleep(0.3)

        # Re-check business relevance with party info
        case["parties"] = parties
        case["docket_entries"] = entries
        is_biz, reasons = is_business_case(case)

        # Detect themes with full data
        themes = detect_themes(case)

        case_record = {
            "docket_id": docket_id,
            "case_name": detail.get("case_name", "") or case.get("caseName", ""),
            "case_name_short": detail.get("case_name_short", ""),
            "docket_number": detail.get("docket_number", "") or case.get("docketNumber", ""),
            "court_id": case.get("court_id_resolved", ""),
            "court_name": case.get("court_name", ""),
            "date_filed": detail.get("date_filed", "") or case.get("dateFiled", ""),
            "date_terminated": detail.get("date_terminated"),
            "date_last_filing": detail.get("date_last_filing"),
            "nature_of_suit": detail.get("nature_of_suit", "") or case.get("suitNature", ""),
            "cause": detail.get("cause", "") or case.get("cause", ""),
            "jurisdiction_type": detail.get("jurisdiction_type", ""),
            "jury_demand": detail.get("jury_demand", ""),
            "assigned_to_str": detail.get("assigned_to_str", ""),
            "referred_to_str": detail.get("referred_to_str", ""),
            "parties": parties,
            "docket_entries": entries,
            "absolute_url": detail.get("absolute_url", "") or case.get("absolute_url", ""),
            "filter_reasons": reasons,
            "themes": themes,
            "summary": None,
            "ai_score": None,
            "ai_tags": [],
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        detailed_cases.append(case_record)

    # AI summarization
    if ANTHROPIC_API_KEY:
        print(f"\nGenerating AI summaries (up to {MAX_AI_SUMMARIES_PER_RUN})...")
        summarized = 0
        for case in detailed_cases:
            if summarized >= MAX_AI_SUMMARIES_PER_RUN:
                break
            print(f"  Summarizing: {case['case_name'][:60]}...")
            summary, score, tags = anthropic_summarize(case)
            if summary:
                case["summary"] = summary
                case["ai_score"] = score
                case["ai_tags"] = tags or []
                summarized += 1
            time.sleep(1)  # Rate limit courtesy
        print(f"  Summarized {summarized} cases")
    else:
        print("\nNo ANTHROPIC_API_KEY set. Skipping AI summaries.")

    # Merge new cases into existing data
    data["cases"].extend(detailed_cases)

    # Sort by date filed descending
    data["cases"].sort(key=lambda c: c.get("date_filed", ""), reverse=True)

    # Update metadata
    data["metadata"] = {
        "courts": list(COURTS.values()),
        "total_cases": len(data["cases"]),
        "last_run": datetime.now(timezone.utc).isoformat() + "Z",
        "new_cases_this_run": len(detailed_cases),
    }

    save_data(data)
    print(f"\nDone! Added {len(detailed_cases)} new cases.")


if __name__ == "__main__":
    run()
