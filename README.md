# Competitor Analyse

An AI-powered competitive analysis tool that researches a target company and its top competitors, then generates a structured HTML report covering positioning, social media, pricing, and competitive gaps.

Built on the **WAT framework** (Workflows, Agents, Tools) — deterministic Python scripts handle data collection while an LLM handles extraction and narrative analysis.

## What It Does

1. **Scrapes** the target company's homepage, pricing page, features page, and about page
2. **Searches** for social media presence (LinkedIn, Instagram, TikTok, YouTube)
3. **Discovers** the top 2–3 direct competitors automatically
4. **Repeats** the same research for each competitor
5. **Generates** a self-contained HTML report with side-by-side comparisons and AI-written narrative analysis

## Example Output

```
reports/digital-growth-agency_competitive_analysis_2026-04-15.html
```

A single HTML file with embedded CSS — open directly in any browser, no server needed.

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/kristian-ux/Competitor_analyse.git
cd Competitor_analyse
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API keys

Create a `.env` file in the project root:

```env
FIRECRAWL_API_KEY=your_firecrawl_key
OPENAI_API_KEY=your_openai_key
```

- **Firecrawl** — web scraping and search ([firecrawl.dev](https://firecrawl.dev))
- **OpenAI** — profile extraction and narrative analysis (GPT-4o)

## Usage

```bash
# Full analysis (recommended)
python -m tools.run_analysis --company "Company Name" --url "https://company.com"

# URL auto-derived from company name
python -m tools.run_analysis --company "Company Name"

# Limit number of competitors
python -m tools.run_analysis --company "Company Name" --max-competitors 2
```

## Budget

Each run uses a maximum of **40 API calls** (Firecrawl scrapes + searches + OpenAI calls). A typical run with 3 competitors uses 29–38 calls.

| Phase | Calls |
|-------|-------|
| Target company research | 7–9 |
| Competitor discovery | 3–4 |
| 3 × competitor research | 18–24 |
| Report generation | 1 |
| **Total** | **29–38** |

## Project Structure

```
tools/
  run_analysis.py      # Main orchestrator — run this
  scrape_company.py    # Scrapes + profiles a single company
  find_competitors.py  # Discovers top competitors via search
  build_report.py      # Generates the HTML report
  call_budget.py       # Global API call budget tracker
workflows/
  competitive_analysis.md   # Full SOP: inputs, phases, edge cases
requirements.txt
```

## Running Individual Tools

```bash
# Research a single company
python -m tools.scrape_company --name "Notion" --url "https://notion.so"

# Find competitors from an existing profile
python -m tools.find_competitors --profile .tmp/notion_profile.json

# Build report from existing profiles
python -m tools.build_report \
  --target .tmp/notion_profile.json \
  --profiles .tmp/evernote_profile.json .tmp/obsidian_profile.json
```

## Caching

Company profiles are cached in `.tmp/{slug}_profile.json` for 24 hours. Re-running within that window reuses cached data (0 API calls). Delete the cache file to force a fresh run.

## Troubleshooting

| Problem | Solution |
|---------|---------|
| `FIRECRAWL_API_KEY not set` | Add key to `.env` |
| `OPENAI_API_KEY not set` | Add key to `.env` |
| Homepage scrape returns empty | Provide the full URL with `--url` |
| No competitors found | Run `find_competitors` standalone with a more specific company category |
| Budget exhausted mid-run | Increase `GLOBAL_MAX` in `tools/call_budget.py` |
