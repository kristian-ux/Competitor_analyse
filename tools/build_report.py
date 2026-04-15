"""
build_report.py
---------------
Phase 4: Generate an HTML competitive analysis report.

Calls GPT-4o once to produce narrative analysis sections (overview, strengths/gaps,
head-to-head, key takeaways), then renders a self-contained HTML file with
embedded CSS. No external dependencies required to open the report.

Usage (standalone):
    python -m tools.build_report \\
        --target .tmp/notion_profile.json \\
        --competitors .tmp/competitors.json \\
        --profiles .tmp/evernote_profile.json .tmp/obsidian_profile.json

Programmatic:
    from tools.build_report import build_report
    from tools import BUDGET
    path = build_report(target_profile, competitor_profiles, BUDGET)

Output: reports/{slug}_competitive_analysis_{YYYY-MM-DD}.html
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
REPORTS_DIR = ROOT_DIR / "reports"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ── helpers ────────────────────────────────────────────────────────────────


def format_followers(n: int | None) -> str:
    if n is None:
        return "—"
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


def _esc(s: str | None) -> str:
    """HTML-escape a string."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _val(profile: dict, *keys: str, default: str = "—") -> str:
    for k in keys:
        v = profile.get(k)
        if v is not None and v != "":
            return _esc(str(v))
    return default


# ── LLM narrative analysis ─────────────────────────────────────────────────


_ANALYSIS_SCHEMA = {
    "overview_paragraph": "string (3-4 sentences: target market, competitive dynamic, what stands out)",
    "target_analysis": {
        "strengths": ["string", "string", "string"],
        "gaps": ["string", "string"],
        "positioning": "string (2 sentences on how target positions vs competitors)",
    },
    "competitor_analyses": [
        {
            "name": "string",
            "strengths": ["string", "string"],
            "gaps": ["string", "string"],
            "positioning": "string",
        }
    ],
    "head_to_head": "string (3-4 sentences comparing pricing, features, and audience)",
    "key_takeaways": ["string (actionable recommendation)", "string", "string"],
}


def _llm_analyze(
    target: dict,
    competitors: list[dict],
    openai_client: OpenAI,
) -> dict:
    """Call GPT-4o to produce the narrative analysis sections."""

    def profile_summary(p: dict) -> str:
        """Trim a profile to ~2000 chars for the LLM context."""
        trimmed = {
            k: p.get(k)
            for k in [
                "name", "url", "tagline", "description", "category",
                "founding_year", "headcount_range", "funding_stage",
                "pricing_model", "pricing_tiers", "key_features",
                "target_audience", "website_tone", "website_impression",
                "social",
            ]
        }
        return json.dumps(trimmed, ensure_ascii=False)[:2000]

    companies_block = f"### TARGET: {target.get('name', '')}\n{profile_summary(target)}\n"
    for i, c in enumerate(competitors, 1):
        companies_block += f"\n### COMPETITOR {i}: {c.get('name', '')}\n{profile_summary(c)}\n"

    system_prompt = (
        "Je bent een senior strategie-consultant die een competitive intelligence briefing schrijft. "
        "Schrijf in helder, direct Nederlands voor een zakelijk publiek. Wees specifiek — verwijs naar "
        "concrete functies, prijzen, volgersaantallen en oprichtingsjaren als die beschikbaar zijn. "
        "Vermijd vage adjectieven zoals 'robuust' of 'krachtig'. "
        "Retourneer ALLEEN geldige JSON die overeenkomt met het schema."
    )

    user_prompt = f"""## Bedrijven onder analyse
{companies_block}

## Vereist JSON-uitvoerschema:
{json.dumps(_ANALYSIS_SCHEMA, indent=2)}

## Schrijfinstructies (schrijf alles in het Nederlands):
- overview_paragraph: 3-4 zinnen die de markt, de spelers en de belangrijkste concurrentiedynamiek samenvatten
- strengths: 2-3 concrete, specifieke sterktes per bedrijf (citeer functies of datapunten)
- gaps: 1-2 zichtbare zwakheden of blinde vlekken per bedrijf
- positioning: hoe elk bedrijf zijn niche afbakent ten opzichte van de anderen
- head_to_head: 3-4 zinnen die de prijsaanpak, functiediepte en doelgroepfocus direct vergelijken
- key_takeaways: 3-5 concrete aanbevelingen voor het DOELBEDRIJF (niet generiek)
- Wees specifiek. "Geen transparante prijspagina" is beter dan "Prijsstelling kan duidelijker".

Retourneer alleen het JSON-object. Geen markdown-hekken."""

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1800,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    raw = response.choices[0].message.content.strip()

    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    return json.loads(raw)


# ── HTML rendering ─────────────────────────────────────────────────────────


_CSS = """
:root {
  --primary: #1a1a2e;
  --accent: #4f46e5;
  --bg: #f8fafc;
  --card: #ffffff;
  --border: #e2e8f0;
  --text: #1e293b;
  --muted: #64748b;
  --green: #16a34a;
  --green-bg: #f0fdf4;
  --amber: #ca8a04;
  --amber-bg: #fffbeb;
  --red: #dc2626;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg); color: var(--text);
  line-height: 1.6; padding: 24px;
}
.container { max-width: 1100px; margin: 0 auto; }
.header {
  background: var(--primary); color: white;
  padding: 32px 36px; border-radius: 12px; margin-bottom: 24px;
}
.header h1 { font-size: 26px; font-weight: 700; margin-bottom: 6px; }
.header .meta { color: #94a3b8; font-size: 14px; }
.section {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 28px; margin-bottom: 20px;
}
.section h2 {
  font-size: 18px; font-weight: 700; color: var(--primary);
  border-bottom: 2px solid var(--accent);
  padding-bottom: 10px; margin-bottom: 20px;
}
.company-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}
.company-card {
  background: var(--bg); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px;
}
.company-card.target { border-color: var(--accent); border-width: 2px; }
.company-card h3 { font-size: 15px; font-weight: 700; margin-bottom: 6px; }
.badge {
  display: inline-block; font-size: 11px; font-weight: 600;
  padding: 2px 8px; border-radius: 4px; margin-bottom: 10px; text-transform: uppercase;
}
.badge-target { background: var(--accent); color: white; }
.badge-competitor { background: #e2e8f0; color: var(--muted); }
.stat-row {
  display: flex; justify-content: space-between; align-items: flex-start;
  font-size: 13px; padding: 5px 0;
  border-bottom: 1px solid var(--border);
  gap: 8px;
}
.stat-row:last-child { border-bottom: none; }
.stat-label { color: var(--muted); white-space: nowrap; }
.stat-value { font-weight: 600; text-align: right; }
.tagline { font-size: 13px; color: var(--muted); font-style: italic; margin-bottom: 12px; line-height: 1.4; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th {
  background: var(--primary); color: white;
  padding: 10px 14px; text-align: left; font-size: 13px;
}
tbody td { padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
tbody tr:nth-child(even) td { background: #f8fafc; }
tbody tr:last-child td { border-bottom: none; }
.conf-high { color: var(--green); }
.conf-medium { color: var(--amber); }
.conf-low { color: var(--red); }
.conf-not_found { color: #cbd5e1; }
.sg-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; }
.sg-strengths { background: var(--green-bg); border-radius: 8px; padding: 14px; }
.sg-gaps { background: var(--amber-bg); border-radius: 8px; padding: 14px; }
.sg-strengths h4 { color: var(--green); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
.sg-gaps h4 { color: var(--amber); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }
.sg-strengths ul, .sg-gaps ul { padding-left: 16px; }
.sg-strengths li, .sg-gaps li { font-size: 13px; margin-bottom: 4px; line-height: 1.5; }
.company-sg { margin-bottom: 24px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }
.company-sg:last-child { margin-bottom: 0; padding-bottom: 0; border-bottom: none; }
.company-sg h3 { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
.company-sg .positioning { font-size: 13px; color: var(--muted); margin: 8px 0 12px; line-height: 1.5; }
.takeaways ol { padding-left: 20px; }
.takeaways li { margin-bottom: 10px; font-size: 14px; line-height: 1.6; }
.skipped-note {
  background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 8px; padding: 14px 18px; margin-top: 16px;
  font-size: 13px; color: #92400e;
}
.skipped-note ul { padding-left: 16px; margin-top: 6px; }
.skipped-note li { margin-bottom: 3px; }
.budget-footer {
  text-align: center; font-size: 12px; color: var(--muted);
  padding: 20px; margin-top: 8px;
}
.conf-legend { font-size: 12px; color: var(--muted); margin-top: 10px; }
p { font-size: 14px; line-height: 1.7; }
@media (max-width: 640px) {
  .company-grid { grid-template-columns: 1fr; }
  .sg-grid { grid-template-columns: 1fr; }
  .header { padding: 20px; }
  .section { padding: 18px; }
}
"""


def _social_cell(platform: str, social: dict, key: str = "followers") -> str:
    data = social.get(platform, {})
    count = data.get(key) or data.get("followers")
    conf = data.get("confidence", "not_found")
    formatted = format_followers(count)
    conf_class = f"conf-{conf}" if conf in ("high", "medium", "low", "not_found") else "conf-not_found"
    return f'<td><span class="{conf_class}">{_esc(formatted)}</span></td>'


def _render_html(
    target: dict,
    competitors: list[dict],
    analysis: dict,
    budget_summary: dict,
    report_date: str,
) -> str:
    all_companies = [target] + competitors
    target_name = _esc(target.get("name", "Company"))
    competitor_names = ", ".join(_esc(c.get("name", "")) for c in competitors)

    # ── Header ────────────────────────────────────────────────
    calls_used = budget_summary.get("global_used", "?")
    calls_max = budget_summary.get("global_max", 40)
    header = f"""
    <div class="header">
      <h1>{target_name} — Concurrentieanalyse</h1>
      <div class="meta">
        Gegenereerd op {_esc(report_date)} &nbsp;·&nbsp;
        {calls_used}/{calls_max} API-aanroepen gebruikt &nbsp;·&nbsp;
        Concurrenten: {competitor_names or "geen gevonden"}
      </div>
    </div>"""

    # ── Overview ──────────────────────────────────────────────
    overview_text = analysis.get("overview_paragraph", "")
    overview = f"""
    <div class="section">
      <h2>Marktoverzicht</h2>
      <p>{_esc(overview_text)}</p>
    </div>"""

    # ── Company Profiles ──────────────────────────────────────
    cards = ""
    for i, company in enumerate(all_companies):
        is_target = i == 0
        card_class = "company-card target" if is_target else "company-card"
        badge_class = "badge badge-target" if is_target else "badge badge-competitor"
        badge_label = "Doelwit" if is_target else "Concurrent"

        tagline_html = ""
        if company.get("tagline"):
            tagline_html = f'<div class="tagline">{_esc(company["tagline"])}</div>'

        rows = [
            ("Categorie", _val(company, "category")),
            ("Opgericht", _val(company, "founding_year")),
            ("Omvang", _val(company, "headcount_range")),
            ("Financiering", _val(company, "funding_stage")),
            ("Hoofdkantoor", _val(company, "headquarters")),
            ("Prijsmodel", _val(company, "pricing_model")),
            ("Tone of voice", _val(company, "website_tone")),
        ]
        rows_html = "".join(
            f'<div class="stat-row"><span class="stat-label">{_esc(lbl)}</span>'
            f'<span class="stat-value">{val}</span></div>'
            for lbl, val in rows
            if val != "—"
        )

        cards += f"""
        <div class="{card_class}">
          <div class="{badge_class}">{badge_label}</div>
          <h3>{_esc(company.get("name", ""))}</h3>
          {tagline_html}
          {rows_html}
        </div>"""

    profiles_section = f"""
    <div class="section">
      <h2>Bedrijfsprofielen</h2>
      <div class="company-grid">
        {cards}
      </div>
    </div>"""

    # ── Social Media Table ────────────────────────────────────
    platforms = [
        ("LinkedIn", "linkedin", "followers"),
        ("Instagram", "instagram", "followers"),
        ("TikTok", "tiktok", "followers"),
        ("YouTube", "youtube", "subscribers"),
        ("Facebook", "facebook", "followers"),
    ]
    th_companies = "".join(f"<th>{_esc(c.get('name',''))}</th>" for c in all_companies)
    social_rows = ""
    for plat_label, plat_key, count_key in platforms:
        row_cells = "".join(
            _social_cell(plat_key, c.get("social", {}), count_key)
            for c in all_companies
        )
        social_rows += f"<tr><td><strong>{plat_label}</strong></td>{row_cells}</tr>"

    social_section = f"""
    <div class="section">
      <h2>Social Media Aanwezigheid</h2>
      <table>
        <thead>
          <tr><th>Platform</th>{th_companies}</tr>
        </thead>
        <tbody>
          {social_rows}
        </tbody>
      </table>
      <div class="conf-legend">
        Betrouwbaarheid:
        <span class="conf-high">&#9632;</span> Hoog (officiële pagina)
        <span class="conf-medium">&#9632;</span> Gemiddeld (directory-snippet)
        <span class="conf-low">&#9632;</span> Laag (afgeleid)
        <span class="conf-not_found">&#9632;</span> Niet gevonden
        &nbsp;·&nbsp; Aantallen afkomstig uit zoeksnippets; kunnen 30–90 dagen achterlopen.
      </div>
    </div>"""

    # ── Strengths & Gaps ──────────────────────────────────────
    def _sg_block(company: dict, analysis_block: dict, is_target: bool) -> str:
        badge_class = "badge badge-target" if is_target else "badge badge-competitor"
        badge_label = "Doelwit" if is_target else "Concurrent"
        strengths = analysis_block.get("strengths", [])
        gaps = analysis_block.get("gaps", [])
        positioning = analysis_block.get("positioning", "")
        s_items = "".join(f"<li>{_esc(s)}</li>" for s in strengths)
        g_items = "".join(f"<li>{_esc(g)}</li>" for g in gaps)
        return f"""
        <div class="company-sg">
          <span class="{badge_class}">{badge_label}</span>
          <h3>{_esc(company.get('name', ''))}</h3>
          {f'<p class="positioning">{_esc(positioning)}</p>' if positioning else ''}
          <div class="sg-grid">
            <div class="sg-strengths">
              <h4>Sterktes</h4>
              <ul>{s_items or '<li>—</li>'}</ul>
            </div>
            <div class="sg-gaps">
              <h4>Zwaktes / Hiaten</h4>
              <ul>{g_items or '<li>—</li>'}</ul>
            </div>
          </div>
        </div>"""

    sg_blocks = _sg_block(target, analysis.get("target_analysis", {}), True)
    for i, comp in enumerate(competitors):
        comp_analyses = analysis.get("competitor_analyses", [])
        comp_analysis = comp_analyses[i] if i < len(comp_analyses) else {}
        sg_blocks += _sg_block(comp, comp_analysis, False)

    sg_section = f"""
    <div class="section">
      <h2>Sterktes &amp; Zwaktes</h2>
      {sg_blocks}
    </div>"""

    # ── Head-to-Head ──────────────────────────────────────────
    h2h_text = analysis.get("head_to_head", "")
    h2h_section = f"""
    <div class="section">
      <h2>Directe Vergelijking</h2>
      <p>{_esc(h2h_text)}</p>
    </div>"""

    # ── Key Takeaways ─────────────────────────────────────────
    takeaways = analysis.get("key_takeaways", [])
    ta_items = "".join(f"<li>{_esc(t)}</li>" for t in takeaways)
    ta_section = f"""
    <div class="section takeaways">
      <h2>Conclusies &amp; Aanbevelingen</h2>
      <ol>{ta_items or '<li>Geen aanbevelingen gegenereerd.</li>'}</ol>
    </div>"""

    # ── Skipped items + footer ────────────────────────────────
    skipped = budget_summary.get("skipped", [])
    per_company = budget_summary.get("per_company", {})
    per_co_str = " · ".join(f"{k}: {v}/10" for k, v in per_company.items())

    skipped_html = ""
    if skipped:
        items_html = "".join(f"<li>{_esc(s)}</li>" for s in skipped)
        skipped_html = f"""
        <div class="skipped-note">
          <strong>Opmerking:</strong> De volgende gegevens waren niet beschikbaar of zijn overgeslagen
          vanwege budget- of toegangsbeperkingen:
          <ul>{items_html}</ul>
        </div>"""

    footer = f"""
    {skipped_html}
    <div class="budget-footer">
      API-budget: {calls_used}/{calls_max} aanroepen gebruikt
      {f'&nbsp;·&nbsp; Per bedrijf: {_esc(per_co_str)}' if per_co_str else ''}
      &nbsp;·&nbsp; Gegenereerd door Competitive Intelligence Agent
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{target_name} Competitive Analysis — {_esc(report_date)}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="container">
  {header}
  {overview}
  {profiles_section}
  {social_section}
  {sg_section}
  {h2h_section}
  {ta_section}
  {footer}
</div>
</body>
</html>"""


# ── main function ──────────────────────────────────────────────────────────


def build_report(
    target_profile: dict,
    competitor_profiles: list[dict],
    budget,  # CallBudget instance
    output_dir: str | Path = REPORTS_DIR,
    skip_llm: bool = False,
) -> str:
    """
    Generate and save an HTML competitive analysis report.

    Returns the path to the saved HTML file.
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    target_name = target_profile.get("name", "company")
    slug = target_profile.get("slug", target_name.lower().replace(" ", "-"))
    report_date = date.today().isoformat()
    filename = f"{slug}_competitive_analysis_{report_date}.html"
    output_path = output_dir / filename

    log(f"Building report: {filename}")

    # ── LLM analysis ──────────────────────────────────────────
    if skip_llm or not OPENAI_API_KEY:
        if not OPENAI_API_KEY:
            log("  WARN: OPENAI_API_KEY not set — generating data-only report")
        analysis: dict = {
            "overview_paragraph": (
                f"Competitive analysis for {target_name}. "
                "LLM narrative analysis was skipped due to budget or configuration."
            ),
            "target_analysis": {"strengths": [], "gaps": [], "positioning": ""},
            "competitor_analyses": [],
            "head_to_head": "",
            "key_takeaways": [],
        }
    elif not budget.can_afford(1):
        log("  WARN: Budget exhausted — generating data-only report (no LLM analysis)")
        budget.skipped.append("build_report: skipped llm:narrative_analysis — budget")
        analysis = {
            "overview_paragraph": (
                f"Competitive analysis for {target_name}. "
                "LLM narrative analysis was skipped — budget exhausted."
            ),
            "target_analysis": {"strengths": [], "gaps": [], "positioning": ""},
            "competitor_analyses": [],
            "head_to_head": "",
            "key_takeaways": [],
        }
    else:
        budget.charge("llm:narrative_analysis")
        client = OpenAI(api_key=OPENAI_API_KEY)
        log("  Running LLM narrative analysis...")
        try:
            analysis = _llm_analyze(target_profile, competitor_profiles, client)
        except (json.JSONDecodeError, Exception) as e:
            log(f"  LLM analysis failed: {e}. Generating data-only report.")
            budget.skipped.append(f"build_report: llm:narrative_analysis failed — {e}")
            analysis = {
                "overview_paragraph": f"Competitive analysis for {target_name}.",
                "target_analysis": {"strengths": [], "gaps": [], "positioning": ""},
                "competitor_analyses": [],
                "head_to_head": "",
                "key_takeaways": [],
            }

    # ── Render HTML ───────────────────────────────────────────
    html = _render_html(
        target=target_profile,
        competitors=competitor_profiles,
        analysis=analysis,
        budget_summary=budget.summary(),
        report_date=report_date,
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"  Report saved: {output_path}")
    return str(output_path)


# ── CLI entry point ────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate competitive analysis HTML report")
    parser.add_argument("--target", required=True, help="Path to target company profile JSON")
    parser.add_argument(
        "--profiles", nargs="*", default=[], help="Paths to competitor profile JSONs"
    )
    parser.add_argument(
        "--competitors",
        help="Path to competitors.json (used to resolve competitor slugs if --profiles not given)",
    )
    parser.add_argument("--skip-llm", action="store_true", help="Skip LLM analysis call")
    args = parser.parse_args()

    with open(args.target, encoding="utf-8") as f:
        target = json.load(f)

    comp_profiles = []
    for p in args.profiles:
        with open(p, encoding="utf-8") as f:
            comp_profiles.append(json.load(f))

    from tools import BUDGET

    path = build_report(target, comp_profiles, BUDGET, skip_llm=args.skip_llm)
    print(f"Report: {path}")
    BUDGET.print_summary()


if __name__ == "__main__":
    main()
