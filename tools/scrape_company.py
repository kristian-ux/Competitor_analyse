"""
scrape_company.py
-----------------
Phase 1: Research one company.

Scrapes the company website (homepage + up to 3 secondary pages), searches
for social media presence, searches for company background, then uses GPT-4o
to extract a structured profile.

Usage (standalone):
    python -m tools.scrape_company --name "Notion" --url "https://notion.so"

Programmatic:
    from tools.scrape_company import research_company
    from tools import BUDGET
    profile = research_company("Notion", "https://notion.so", BUDGET)

Budget: max 10 calls per company (configurable via CallBudget).
Results are cached in .tmp/{slug}_profile.json for 24 hours.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import concurrent.futures

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
TMP_DIR = ROOT_DIR / ".tmp"

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CACHE_TTL_HOURS = 24


# ── helpers ────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def derive_url(name: str) -> str:
    """Best-effort URL from a company name when none is provided."""
    slug = re.sub(r"[^\w]", "", name.lower())
    return f"https://www.{slug}.com"


def format_followers(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


# ── Firecrawl ──────────────────────────────────────────────────────────────


def _firecrawl_scrape(url: str) -> str:
    """Scrape a URL via Firecrawl and return markdown. Returns '' on error."""
    endpoint = "https://api.firecrawl.dev/v1/scrape"
    headers = {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"url": url, "formats": ["markdown"]}
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return data.get("markdown", "") or ""
    except Exception as e:
        log(f"  Firecrawl scrape error ({url[:60]}): {e}")
        return ""


def _firecrawl_search(query: str, limit: int = 8) -> list[dict]:
    """Search the web via Firecrawl. Returns list of {url, title, description}."""
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


# ── URL discovery ──────────────────────────────────────────────────────────

# Priority-ordered patterns: pricing > features/product > about
_SECONDARY_PAGE_PATTERNS = [
    ("pricing", re.compile(r"/pricing|/plans|/tarifs|/prijzen|/tarieven|/pakketten", re.I)),
    ("product", re.compile(r"/features|/product(?!s-)|/solutions|/platform|/oplossingen|/werkwijze", re.I)),
    ("services", re.compile(r"/services|/products(?!\.)|/diensten|/aanbod", re.I)),
    ("about", re.compile(r"/about(?:-us)?|/team|/company|/over(?:-ons)?|/wie-zijn-we", re.I)),
]


def _discover_secondary_urls(base_url: str, markdown: str) -> list[tuple[str, str]]:
    """
    Parse markdown for internal links matching known page patterns.
    Returns list of (label, absolute_url) sorted by priority.
    """
    base = urlparse(base_url)
    found: list[tuple[str, str]] = []
    seen_labels: set[str] = set()

    # Extract all markdown links: [text](url)
    links = re.findall(r"\[.*?\]\(([^)]+)\)", markdown)
    # Also pick up bare href-style paths that Firecrawl sometimes includes
    links += re.findall(r'href=["\']([^"\']+)["\']', markdown)

    for href in links:
        href = href.strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        # Resolve to absolute
        if href.startswith("http"):
            abs_url = href
        else:
            abs_url = urljoin(base_url, href)
        # Must be same domain
        parsed = urlparse(abs_url)
        if parsed.netloc and parsed.netloc != base.netloc:
            continue
        path = parsed.path
        for label, pattern in _SECONDARY_PAGE_PATTERNS:
            if label in seen_labels:
                continue
            if pattern.search(path):
                seen_labels.add(label)
                found.append((label, abs_url.split("?")[0].split("#")[0]))
                break

    return found


# ── social data extraction ──────────────────────────────────────────────────


def _parse_follower_count(text: str) -> int | None:
    """
    Extract a follower/subscriber count from raw text snippets.
    Handles: 1.2K, 4.5M, 1,234, 1.234 (European), plain integers.
    """
    # Patterns like "12.3K", "1.2M", "456K"
    m = re.search(r"([\d,.]+)\s*([KkMmBb])\s*(?:followers?|subscribers?|abonnés|Follower)", text)
    if m:
        num_str = m.group(1).replace(",", ".")
        try:
            num = float(num_str)
        except ValueError:
            return None
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
        return int(num * mult.get(m.group(2).lower(), 1))

    # Plain integers near follow-related keywords
    m = re.search(
        r"([\d][,.\d]*[\d])\s*(?:followers?|subscribers?|abonnés|Follower|likes?)",
        text,
        re.I,
    )
    if m:
        raw = m.group(1).replace(" ", "")
        # European decimal: 1.234 → 1234, or US thousand sep: 1,234 → 1234
        if re.match(r"^\d{1,3}[.,]\d{3}$", raw):
            return int(raw.replace(",", "").replace(".", ""))
        try:
            return int(raw.replace(",", "").replace(".", ""))
        except ValueError:
            return None
    return None


def _extract_social_from_results(
    results: list[dict], platform: str
) -> tuple[str | None, int | None, str]:
    """
    Given search results, extract (url, follower_count, confidence) for a platform.
    Confidence: 'high' = official platform URL, 'medium' = directory, 'low' = inferred.
    """
    platform_domains = {
        "linkedin": "linkedin.com/company",
        "instagram": "instagram.com",
        "tiktok": "tiktok.com",
        "youtube": "youtube.com",
        "facebook": "facebook.com",
        "twitter": "twitter.com",
        "x": "x.com",
    }
    target_domain = platform_domains.get(platform.lower(), platform.lower())

    for r in results:
        url = r.get("url", "")
        snippet = r.get("title", "") + " " + r.get("description", "")
        count = _parse_follower_count(snippet)

        if target_domain in url.lower():
            confidence = "high" if count is not None else "medium"
            return url, count, confidence

    # Fallback: any result mentioning the platform in snippet
    for r in results:
        snippet = r.get("title", "") + " " + r.get("description", "")
        if platform.lower() in snippet.lower():
            count = _parse_follower_count(snippet)
            if count:
                return None, count, "low"

    return None, None, "not_found"


# ── LLM extraction ─────────────────────────────────────────────────────────

_OUTPUT_SCHEMA = {
    "slug": "string",
    "name": "string",
    "url": "string",
    "tagline": "string or null",
    "description": "string (2-3 sentences)",
    "category": "string (e.g. B2B SaaS, E-commerce, FinTech, Marketing Agency)",
    "founding_year": "integer or null",
    "headcount_range": "string or null (e.g. '51-200', '1-10', '1000+')",
    "funding_stage": "string or null (e.g. Bootstrapped, Seed, Series A, IPO)",
    "total_funding_usd": "integer or null",
    "headquarters": "string or null (city, country)",
    "pricing_model": "one of: subscription, freemium, one-time, enterprise, agency, unknown",
    "pricing_tiers": "list of strings (tier names + prices if visible) or []",
    "key_features": "list of 3-7 specific product or service features",
    "target_audience": "string (1 sentence describing who the product is for)",
    "website_tone": "string (e.g. professional, casual, technical, human, corporate)",
    "website_impression": "string (e.g. minimal/clean, busy/rich, corporate, playful)",
}


def _llm_extract_profile(
    name: str,
    url: str,
    scraped_pages: dict[str, str],
    social_results: dict[str, list[dict]],
    background_results: list[dict],
    openai_client: OpenAI,
) -> dict:
    """Call GPT-4o to extract a structured company profile from gathered content."""

    # Build content block (truncate each page to 3000 chars)
    content_parts = []
    for label, md in scraped_pages.items():
        if md:
            content_parts.append(f"### {label.title()} Page\n{md[:3000]}")

    # Combine social + background search snippets
    all_snippets = []
    for platform, results in social_results.items():
        for r in results[:3]:
            all_snippets.append(
                f"[{platform}] {r.get('title','')} — {r.get('description','')}"
            )
    for r in background_results[:5]:
        all_snippets.append(
            f"[background] {r.get('title','')} — {r.get('description','')}"
        )

    system_prompt = (
        "You are a business intelligence analyst. Extract structured data about a company "
        "from the provided web content. Return ONLY valid JSON matching the schema. "
        "Use null for fields that cannot be determined with reasonable confidence. "
        "CRITICAL: The scraped page content (### Homepage Page, etc.) is the authoritative source. "
        "Search snippets are secondary and may contain data about a different company with the same name — "
        "ignore any snippet that contradicts the scraped pages. Do not hallucinate data."
    )

    user_prompt = f"""## Company: {name}
## Official URL: {url}

## Collected Web Content:
{chr(10).join(content_parts) if content_parts else "[No pages scraped]"}

## Search Snippets (social media + company background):
{chr(10).join(all_snippets[:30]) if all_snippets else "[No search results]"}

## Required JSON Schema:
{json.dumps(_OUTPUT_SCHEMA, indent=2)}

## Instructions:
- tagline: main value proposition or slogan (1 sentence max)
- description: 2-3 sentence summary of what they do and who they serve
- category: the market vertical (e.g. "B2B SaaS", "E-commerce", "Marketing Agency")
- pricing_model: subscription / freemium / one-time / enterprise / agency / unknown
- pricing_tiers: list actual tier names and prices if visible, otherwise []
- key_features: 3-7 specific features or services mentioned on the site
- website_tone: e.g. professional, casual, friendly, technical, corporate, human
- website_impression: e.g. minimal/clean, feature-rich, busy, corporate, playful
- Use null for any field you cannot determine from the provided text

Return only the JSON object. No markdown fences, no explanation."""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1200,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    raw = response.choices[0].message.content.strip()

    # Strip code fences if present
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    return json.loads(raw)


# ── main research function ─────────────────────────────────────────────────


def _domain_from_url(url: str | None) -> str | None:
    """Extract bare domain (e.g. 'digitalgrowthagency.nl') from a URL."""
    if not url:
        return None
    parsed = urlparse(url)
    netloc = parsed.netloc or parsed.path
    return netloc.replace("www.", "").strip("/") or None


def research_company(
    name: str,
    url: str | None,
    budget,  # CallBudget instance
    cache_dir: str | Path = TMP_DIR,
) -> dict | None:
    """
    Research a company and return a structured profile dict.

    Writes result to {cache_dir}/{slug}_profile.json.
    Returns cached version (< 24h old) without any API calls.
    Returns None if the homepage could not be scraped.
    """
    TMP_DIR.mkdir(exist_ok=True)
    slug = slugify(name)
    cache_path = Path(cache_dir) / f"{slug}_profile.json"

    # ── Check cache ──────────────────────────────────────────
    if cache_path.exists():
        age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
        if age < timedelta(hours=CACHE_TTL_HOURS):
            log(f"  [{slug}] Using cached profile ({age.seconds // 3600}h old)")
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

    # ── Set active company ────────────────────────────────────
    budget.set_active(slug)
    resolved_url = url or derive_url(name)
    domain = _domain_from_url(resolved_url)
    # Use domain as a search qualifier to avoid confusing same-name companies
    # in different countries (e.g. "Digital Growth Agency" exists in AU and NL).
    search_qualifier = f'"{name}" {domain}' if domain else f'"{name}"'
    log(f"  [{slug}] Researching: {resolved_url}")

    scraped_pages: dict[str, str] = {}
    social_results: dict[str, list[dict]] = {}
    background_results: list[dict] = []
    calls_used_start = budget.per_company.get(slug, 0)

    # ── Step B: Homepage (required) ───────────────────────────
    if not budget.charge("scrape:homepage", slug):
        log(f"  [{slug}] Cannot scrape homepage — budget exhausted")
        return None

    md_home = _firecrawl_scrape(resolved_url)
    if not md_home:
        log(f"  [{slug}] Homepage returned empty — trying bare domain")
        # Try without www
        parsed = urlparse(resolved_url)
        alt = f"{parsed.scheme}://{parsed.netloc.replace('www.', '')}{parsed.path}"
        if alt != resolved_url:
            md_home = _firecrawl_scrape(alt)

    scraped_pages["homepage"] = md_home

    # ── Step C: Discover secondary URLs ──────────────────────
    secondary_candidates = _discover_secondary_urls(resolved_url, md_home)
    log(f"  [{slug}] Discovered {len(secondary_candidates)} secondary pages")

    # ── Step D: Scrape secondary pages (up to 3 more calls) ──
    scraped_labels = {"homepage"}
    skipped_pages: list[str] = []

    for label, page_url in secondary_candidates[:4]:
        if label in scraped_labels:
            continue
        if not budget.can_afford(1, slug):
            skipped_pages.append(label)
            budget.skipped.append(f"{slug}: skipped scrape:{label} — budget")
            continue
        budget.charge(f"scrape:{label}", slug)
        md = _firecrawl_scrape(page_url)
        scraped_pages[label] = md
        scraped_labels.add(label)
        log(f"  [{slug}] Scraped {label} ({len(md)} chars)")

    # ── Steps E–H: Social + background searches (parallel) ───────────────────
    # Budget decisions are made sequentially so limits are respected, then all
    # approved HTTP calls are executed concurrently.
    _SOCIAL_SEARCHES = [
        ("linkedin",         lambda q: f'site:linkedin.com/company {q}',               5),
        ("instagram_tiktok", lambda q: f'{q} instagram followers OR tiktok followers',  6),
        ("youtube",          lambda q: f'{q} youtube channel subscribers',              5),
        ("background",       lambda q: f'{q} founded year employees headcount funding', 8),
    ]

    searches_approved: list[tuple[str, str, int]] = []
    for key, query_fn, limit in _SOCIAL_SEARCHES:
        if budget.can_afford(1, slug):
            budget.charge(f"search:{key}", slug)
            searches_approved.append((key, query_fn(search_qualifier), limit))
        else:
            budget.skipped.append(f"{slug}: skipped search:{key} — budget")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_firecrawl_search, query, limit): key
                for key, query, limit in searches_approved}
        for fut in concurrent.futures.as_completed(futs):
            key = futs[fut]
            results = fut.result()
            if key == "background":
                background_results = results
            else:
                social_results[key] = results
            log(f"  [{slug}] search:{key} done ({len(results)} results)")

    # ── Step I: LLM extraction ────────────────────────────────
    if not OPENAI_API_KEY:
        log("  ERROR: OPENAI_API_KEY not set. Skipping LLM extraction.")
        profile = {"slug": slug, "name": name, "url": resolved_url}
    elif not budget.can_afford(1, slug):
        log(f"  [{slug}] Cannot run LLM extraction — budget exhausted")
        budget.skipped.append(f"{slug}: skipped llm:profile_extraction — budget")
        profile = {"slug": slug, "name": name, "url": resolved_url}
    else:
        budget.charge("llm:profile_extraction", slug)
        log(f"  [{slug}] Running LLM profile extraction...")
        client = OpenAI(api_key=OPENAI_API_KEY)
        try:
            profile = _llm_extract_profile(
                name,
                resolved_url,
                scraped_pages,
                social_results,
                background_results,
                client,
            )
        except (json.JSONDecodeError, Exception) as e:
            log(f"  [{slug}] LLM extraction failed: {e}. Using partial profile.")
            profile = {"slug": slug, "name": name, "url": resolved_url}

    # ── Merge social data extracted from snippets ─────────────
    social_profile: dict = {}

    # LinkedIn
    li_url, li_count, li_conf = _extract_social_from_results(
        social_results.get("linkedin", []), "linkedin"
    )
    social_profile["linkedin"] = {"url": li_url, "followers": li_count, "confidence": li_conf}

    # Instagram
    ig_url, ig_count, ig_conf = _extract_social_from_results(
        social_results.get("instagram_tiktok", []), "instagram"
    )
    social_profile["instagram"] = {"url": ig_url, "followers": ig_count, "confidence": ig_conf}

    # TikTok
    tt_url, tt_count, tt_conf = _extract_social_from_results(
        social_results.get("instagram_tiktok", []), "tiktok"
    )
    social_profile["tiktok"] = {"url": tt_url, "followers": tt_count, "confidence": tt_conf}

    # YouTube
    yt_url, yt_count, yt_conf = _extract_social_from_results(
        social_results.get("youtube", []), "youtube"
    )
    social_profile["youtube"] = {"url": yt_url, "subscribers": yt_count, "confidence": yt_conf}

    # Facebook (no dedicated search, extracted from instagram_tiktok results if lucky)
    fb_url, fb_count, fb_conf = _extract_social_from_results(
        social_results.get("instagram_tiktok", []), "facebook"
    )
    social_profile["facebook"] = {"url": fb_url, "followers": fb_count, "confidence": fb_conf}

    # ── Assemble final profile ────────────────────────────────
    calls_used = budget.per_company.get(slug, 0) - calls_used_start
    profile.update(
        {
            "slug": slug,
            "name": profile.get("name", name),
            "url": profile.get("url", resolved_url),
            "social": social_profile,
            "data_sources": list(scraped_labels),
            "calls_used": calls_used,
            "skipped_pages": skipped_pages,
            "scraped_at": datetime.now().isoformat(),
        }
    )

    # ── Cache to disk ─────────────────────────────────────────
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    log(f"  [{slug}] Profile saved ({calls_used} calls used)")

    return profile


# ── CLI entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Research a single company")
    parser.add_argument("--name", required=True, help="Company name")
    parser.add_argument("--url", help="Company website URL")
    args = parser.parse_args()

    if not FIRECRAWL_API_KEY:
        print("ERROR: FIRECRAWL_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    from tools import BUDGET

    profile = research_company(args.name, args.url, BUDGET)
    if profile:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        BUDGET.print_summary()
    else:
        print("ERROR: Research failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
