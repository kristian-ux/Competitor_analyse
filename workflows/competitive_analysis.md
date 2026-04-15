# Competitive Analysis Workflow

## Purpose

Given a company name and optional URL, research that company and its top 2–3 direct competitors, then produce a structured HTML comparison report covering website positioning, social media presence, pricing, and competitive strengths/gaps.

## Trigger

```bash
# Full run (recommended)
python -m tools.run_analysis --company "Company Name" --url "https://company.com"

# URL auto-derived from company name
python -m tools.run_analysis --company "Company Name"

# Limit competitors
python -m tools.run_analysis --company "Company Name" --max-competitors 2
```

## Required Setup

Before running, populate `.env`:
```
FIRECRAWL_API_KEY=...    # web scraping and search
OPENAI_API_KEY=...       # LLM profile extraction and report analysis
```

Both keys are available in the sibling Keyword_research project.

## Budget Envelope

| Limit | Value |
|-------|-------|
| Global maximum (entire run) | 40 calls |
| Per company (target + each competitor) | 10 calls |
| Maximum competitors researched | 3 |

A "call" = 1 Firecrawl scrape OR 1 Firecrawl search OR 1 OpenAI LLM call.

## Phase Breakdown

### Phase 1 — Target Company Research (≤10 calls)

**Tool:** `tools/scrape_company.py`

| Step | Type | Calls | Priority |
|------|------|-------|----------|
| Scrape homepage | Firecrawl scrape | 1 | Required |
| Scrape pricing page | Firecrawl scrape | 1 | High |
| Scrape product/features page | Firecrawl scrape | 1 | High |
| Scrape about page | Firecrawl scrape | 1 | Medium |
| Search LinkedIn (`site:linkedin.com/company "Name"`) | Firecrawl search | 1 | High |
| Search Instagram + TikTok followers | Firecrawl search | 1 | Medium |
| Search YouTube subscribers | Firecrawl search | 1 | Low |
| Search background (founded, employees, funding) | Firecrawl search | 1 | Medium |
| LLM profile extraction (GPT-4o) | OpenAI | 1 | Required |

Steps are skipped gracefully if per-company budget runs out.

**Output:** `.tmp/{slug}_profile.json`

### Phase 2 — Competitor Discovery (~4 calls)

**Tool:** `tools/find_competitors.py`

| Step | Type | Calls |
|------|------|-------|
| Search: `"{name}" competitors alternatives` | Firecrawl search | 1 |
| Search: `best {category} companies 2026` | Firecrawl search | 1 |
| Search: `{category} tools alternatives {audience}` | Firecrawl search | 1 |
| LLM selection of top 3 (GPT-4o) | OpenAI | 1 |

Results are deduplicated by domain. Review aggregators (G2, Capterra, Reddit) are filtered out before the LLM sees them.

**Output:** `.tmp/competitors.json`

### Phase 3 — Competitor Research (≤10 calls × up to 3 competitors)

**Tool:** `tools/scrape_company.py` (same as Phase 1, once per competitor)

Each competitor gets the same research treatment as the target. The per-company budget cap of 10 applies independently to each.

If budget is running low:
- Competitors are skipped entirely when < 2 global calls remain
- Skipped competitors appear as stubs in the report with a note

**Output:** `.tmp/{slug}_profile.json` for each competitor

### Phase 4 — Report Generation (1 call)

**Tool:** `tools/build_report.py`

| Step | Type | Calls |
|------|------|-------|
| LLM narrative analysis (GPT-4o) | OpenAI | 1 |
| HTML rendering | Python (no API) | 0 |

If budget is exhausted, the report renders with raw profile data only (no narrative analysis). All data sections are still present.

**Output:** `reports/{slug}_competitive_analysis_{YYYY-MM-DD}.html`

## Typical Budget Usage (3 competitors)

| Phase | Typical calls | Notes |
|-------|--------------|-------|
| Phase 1 (target) | 7–9 | Some pages not found → fewer scrapes |
| Phase 2 (find competitors) | 3–4 | |
| Phase 3 (3 × competitor) | 18–24 | ~6–8 per competitor |
| Phase 4 (report) | 1 | |
| **Total** | **29–38** | Well under the 40-call limit |

## Graceful Degradation Rules

| Global calls remaining | Behaviour |
|-----------------------|-----------|
| ≥ 8 per company | Full research (all pages + all social searches) |
| 5–7 per company | Skip about page; keep pricing + social |
| 3–4 per company | Homepage + 1 social search + LLM only |
| 2 per company | Homepage + LLM only |
| < 2 global | Skip competitor entirely; add stub |
| < 1 global for report | Render data-only report (no LLM narratives) |

All skipped items are logged and shown in the report footer.

## Caching

- Each company profile is cached in `.tmp/{slug}_profile.json` for 24 hours
- Rerunning the orchestrator within 24 hours reuses cached profiles (0 API calls)
- To force a fresh run: delete `.tmp/{slug}_profile.json` before running

## Data Quality Notes

**Social follower counts**
- Extracted from search engine snippets, NOT from direct platform scrapes
- LinkedIn, Instagram, TikTok, Facebook, and YouTube all block direct scraping
- Counts may lag 30–90 days (search engine cache)
- Confidence levels: `high` = official platform URL in snippet, `medium` = directory, `low` = inferred

**Company background**
- Founded, headcount, and funding data depends on indexed press coverage
- Accuracy is high for companies with recent press; low for bootstrapped/quiet companies

**Pricing**
- Extracted from the company's own pricing/plans page
- Accuracy is high when a pricing page is found; shown as `unknown` otherwise

## Output File

```
reports/{company_slug}_competitive_analysis_{YYYY-MM-DD}.html
```

A self-contained HTML file with embedded CSS — no server needed. Open directly in any browser.

## Running Individual Tools

Each tool can be run standalone for testing:

```bash
# Research a single company
python -m tools.scrape_company --name "Notion" --url "https://notion.so"

# Find competitors given an existing profile
python -m tools.find_competitors --profile .tmp/notion_profile.json

# Build a report from existing profiles
python -m tools.build_report \
  --target .tmp/notion_profile.json \
  --profiles .tmp/evernote_profile.json .tmp/obsidian_profile.json
```

## Troubleshooting

| Problem | Solution |
|---------|---------|
| `FIRECRAWL_API_KEY not set` | Add key to `.env` |
| `OPENAI_API_KEY not set` | Add key to `.env`; report will still generate with data-only mode |
| Homepage scrape returns empty | Try providing the full URL with `--url`; some sites block scrapers |
| No competitors found | Try running `find_competitors` standalone with a more specific category in the profile |
| Report shows all `—` for social | Social data relies on public search snippets; small companies may not appear |
| Budget exhausted mid-run | Increase `GLOBAL_MAX` in `call_budget.py` (or accept partial results) |

## Self-Improvement Log

_Update this section when you discover new constraints, better query patterns, or recurring issues._

- 2026-04-15: Initial workflow created.
