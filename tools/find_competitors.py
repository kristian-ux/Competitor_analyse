"""
find_competitors.py
-------------------
Phase 2: Discover top 2-3 direct competitors for the target company.

Runs up to 3 web searches, filters/deduplicates results, then uses GPT-4o
to select the most relevant competitors.

Usage (standalone):
    python -m tools.find_competitors --profile .tmp/notion_profile.json

Programmatic:
    from tools.find_competitors import find_competitors
    from tools import BUDGET
    competitors = find_competitors(target_profile, BUDGET)

Budget: ~4 calls (3 searches + 1 LLM). Results cached in .tmp/competitors.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import concurrent.futures

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
TMP_DIR = ROOT_DIR / ".tmp"

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
COMPETITORS_CACHE_TTL_HOURS = 24

# Domains to strip from competitor results (review aggregators, news, social)
_NOISE_DOMAINS = {
    "reddit.com", "quora.com", "trustpilot.com", "g2.com", "capterra.com",
    "getapp.com", "softwareadvice.com", "producthunt.com", "slashdot.org",
    "alternativeto.net", "techradar.com", "pcmag.com", "forbes.com",
    "techcrunch.com", "venturebeat.com", "medium.com", "wikipedia.org",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "glassdoor.com", "indeed.com",
    # Dutch noise domains
    "frankwatching.com", "marketingfacts.nl", "emerce.nl", "twinkle.nl",
    "bureauregister.nl", "clutch.co", "sortlist.nl", "sortlist.com",
    "agentschapnl.nl", "digitalmarketing.nl", "adformatie.nl",
    "7be.io", "cbinsights.com", "bouncewatch.com", "similarweb.com",
    "semrush.com", "ahrefs.com", "moz.com",
}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Firecrawl search ───────────────────────────────────────────────────────


def _firecrawl_search(query: str, limit: int = 10) -> list[dict]:
    endpoint = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "limit": limit}
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("data", [])
        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "description": r.get("description", ""),
            }
            for r in results
        ]
    except Exception as e:
        log(f"  Firecrawl search error ({query[:50]}): {e}")
        return []


# ── deduplication / filtering ─────────────────────────────────────────────


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def _clean_results(
    results: list[dict], target_url: str
) -> list[dict]:
    """Remove noise domains, target domain, and deduplicate by domain."""
    target_domain = _domain_of(target_url)
    seen_domains: set[str] = set()
    cleaned: list[dict] = []

    for r in results:
        domain = _domain_of(r.get("url", ""))
        if not domain:
            continue
        if domain == target_domain:
            continue
        if any(noise in domain for noise in _NOISE_DOMAINS):
            continue
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        cleaned.append(r)

    return cleaned


# ── LLM competitor selection ───────────────────────────────────────────────


def _llm_select_competitors(
    target_profile: dict,
    search_results: list[dict],
    openai_client: OpenAI,
    max_competitors: int = 3,
) -> list[dict]:
    """Use GPT-4o to select the most relevant direct competitors."""
    name = target_profile.get("name", "")
    category = target_profile.get("category", "")
    description = target_profile.get("description", "")
    target_audience = target_profile.get("target_audience", "")
    key_features = target_profile.get("key_features", [])
    headcount = target_profile.get("headcount_range", "")

    system_prompt = (
        "Je bent een competitive intelligence analist. Selecteer de meest directe concurrenten "
        "van het doelbedrijf uit de opgegeven zoekresultaten. "
        "Een directe concurrent: bedient een vergelijkbaar klantprofiel met een vergelijkbare oplossing "
        "en is qua omvang en markt vergelijkbaar. "
        "Geef de voorkeur aan Nederlandse bedrijven (domeinen eindigend op .nl) en bedrijven die "
        "actief zijn op de Nederlandse markt. "
        "Retourneer ALLEEN geldige JSON — een JSON-array, niets anders."
    )

    user_prompt = f"""## Doelbedrijf
Naam: {name}
Categorie: {category}
Beschrijving: {description}
Doelgroep: {target_audience}
Kernfuncties: {json.dumps(key_features)}
Omvang: {headcount or "onbekend"}

## Zoekresultaten (potentiële concurrenten)
{json.dumps(search_results[:30], indent=2)[:4000]}

## Vereiste uitvoer
Retourneer een JSON-array van maximaal {max_competitors} objecten:
[
  {{
    "name": "Naam Concurrent",
    "url": "https://concurrent.nl",
    "reason": "Één zin die uitlegt waarom dit een directe concurrent is"
  }}
]

Regels:
- Sluit het doelbedrijf zelf NIET in
- Sluit reviewsites, nieuwsartikelen, lijstpagina's of sociale profielen NIET in
- Geef de voorkeur aan Nederlandse bedrijven (.nl domein) of bedrijven gericht op de Nederlandse markt
- Geef de voorkeur aan bedrijven met een eigen product/dienst website
- Pas op grootte: als het doel klein is, vermeld dan geen grote multinationals
- Als er minder dan {max_competitors} sterke kandidaten zijn, retourneer dan minder
- Retourneer alleen de array — geen markdown, geen extra tekst"""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=600,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    competitors = json.loads(raw)
    # Validate structure
    valid = []
    for c in competitors:
        if isinstance(c, dict) and c.get("name") and c.get("url"):
            valid.append(c)
    return valid[:max_competitors]


# ── main function ──────────────────────────────────────────────────────────


def find_competitors(
    target_profile: dict,
    budget,  # CallBudget instance
    max_competitors: int = 3,
) -> list[dict]:
    """
    Find top 2-3 direct competitors for the target company.

    Returns list of {name, url, reason}.
    Writes result to .tmp/competitors.json.
    """
    TMP_DIR.mkdir(exist_ok=True)
    name = target_profile.get("name", "")
    category = target_profile.get("category", "") or ""
    target_audience = target_profile.get("target_audience", "") or ""
    target_url = target_profile.get("url", "")

    # ── Check competitors cache ───────────────────────────────
    cache_path = TMP_DIR / "competitors.json"
    if cache_path.exists():
        from datetime import timedelta
        age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age < timedelta(hours=COMPETITORS_CACHE_TTL_HOURS):
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("target") == name:
                log(f"  Using cached competitors ({age.seconds // 3600}h old)")
                return cached.get("competitors", [])

    log(f"  Finding competitors for: {name}")

    all_results: list[dict] = []

    # ── Pre-charge approved searches (sequential budget decisions) ────────────
    search_queue: list[tuple[str, str]] = []

    if budget.can_afford(1):
        budget.charge("search:competitors_q1")
        search_queue.append(("q1", f'"{name}" concurrenten alternatieven Nederland'))
    else:
        budget.skipped.append("find_competitors: skipped search 1 — budget")

    if budget.can_afford(1) and category:
        budget.charge("search:competitors_q2")
        search_queue.append(("q2", f"beste {category} bureau Nederland 2026"))
    elif not category:
        log("  Skipping search 2 — no category available")
    else:
        budget.skipped.append("find_competitors: skipped search 2 — budget")

    if budget.can_afford(1) and category:
        budget.charge("search:competitors_q3")
        audience_snippet = target_audience[:60] if target_audience else ""
        q3 = (
            f"{category} bureau Nederland {audience_snippet}".strip()
            if audience_snippet
            else f"top {category} bureau Nederland"
        )
        search_queue.append(("q3", q3))
    else:
        if category:
            budget.skipped.append("find_competitors: skipped search 3 — budget")

    # ── Execute approved searches in parallel ─────────────────────────────────
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_firecrawl_search, query, 10): label
                for label, query in search_queue}
        for fut in concurrent.futures.as_completed(futs):
            label = futs[fut]
            results = fut.result()
            all_results.extend(results)
            log(f"  Competitor search {label}: {len(results)} results")

    # ── Clean and deduplicate ─────────────────────────────────
    cleaned = _clean_results(all_results, target_url)
    log(f"  Cleaned results: {len(cleaned)} unique candidate domains")

    if not cleaned:
        log("  No competitor candidates found — returning empty list")
        return []

    # ── LLM selection ─────────────────────────────────────────
    if not OPENAI_API_KEY:
        log("  ERROR: OPENAI_API_KEY not set. Returning raw top results.")
        competitors = [
            {"name": _domain_of(r["url"]), "url": r["url"], "reason": r.get("title", "")}
            for r in cleaned[:max_competitors]
        ]
    elif not budget.can_afford(1):
        log("  Cannot run LLM selection — budget exhausted. Using top raw results.")
        budget.skipped.append("find_competitors: skipped llm:competitor_selection — budget")
        competitors = [
            {"name": r.get("title", _domain_of(r["url"])), "url": r["url"], "reason": "Selected from search results"}
            for r in cleaned[:max_competitors]
        ]
    else:
        budget.charge("llm:competitor_selection")
        client = OpenAI(api_key=OPENAI_API_KEY)
        log("  Running LLM competitor selection...")
        try:
            competitors = _llm_select_competitors(
                target_profile, cleaned, client, max_competitors
            )
        except (json.JSONDecodeError, Exception) as e:
            log(f"  LLM selection failed: {e}. Using top raw results.")
            competitors = [
                {"name": r.get("title", _domain_of(r["url"])), "url": r["url"], "reason": "Selected from search results"}
                for r in cleaned[:max_competitors]
            ]

    log(f"  Selected {len(competitors)} competitors: {[c['name'] for c in competitors]}")

    # ── Cache to disk ─────────────────────────────────────────
    output = {
        "target": name,
        "competitors": competitors,
        "generated_at": datetime.now().isoformat(),
    }
    cache_path = TMP_DIR / "competitors.json"
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return competitors


# ── CLI entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Find competitors for a company")
    parser.add_argument(
        "--profile", required=True, help="Path to target company profile JSON"
    )
    parser.add_argument("--max", type=int, default=3, help="Max competitors to find")
    args = parser.parse_args()

    if not FIRECRAWL_API_KEY:
        print("ERROR: FIRECRAWL_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    with open(args.profile, encoding="utf-8") as f:
        profile = json.load(f)

    from tools import BUDGET

    competitors = find_competitors(profile, BUDGET, max_competitors=args.max)
    print(json.dumps(competitors, ensure_ascii=False, indent=2))
    BUDGET.print_summary()


if __name__ == "__main__":
    main()
