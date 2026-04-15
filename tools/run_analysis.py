"""
run_analysis.py
---------------
Main orchestrator for the competitive intelligence pipeline.

Phases:
  1. Research the target company      (≤10 calls)
  2. Find top competitors             (~4 calls)
  3. Research each competitor         (≤10 calls × up to 3)
  4. Generate HTML report             (1 call)

Hard limits (enforced by CallBudget):
  Global: 40 calls total
  Per company: 10 calls

Usage:
  python -m tools.run_analysis --company "Notion" --url "https://notion.so"
  python -m tools.run_analysis --company "Notion"          # URL auto-derived
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).parent.parent
TMP_DIR = ROOT_DIR / ".tmp"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


# ── graceful degradation check ─────────────────────────────────────────────


def _should_skip_competitor(budget, competitor_name: str) -> tuple[bool, str]:
    """
    Decide whether to skip researching a competitor based on remaining budget.
    Returns (skip: bool, reason: str).
    """
    remaining = budget.global_remaining
    if remaining < 2:
        return True, f"only {remaining} global calls left"
    if remaining < 4 and len(budget.per_company) >= 2:
        return True, f"only {remaining} calls left — conserving for report"
    return False, ""


# ── main orchestration ─────────────────────────────────────────────────────


def run(company_name: str, url: str | None, max_competitors: int = 3) -> str:
    """
    Run the full competitive intelligence pipeline.
    Returns the path to the generated HTML report.
    """
    # Import here so the singleton is created once per process
    from tools import BUDGET
    from tools.scrape_company import research_company
    from tools.find_competitors import find_competitors
    from tools.build_report import build_report

    TMP_DIR.mkdir(exist_ok=True)

    log("=" * 60)
    log(f"  Competitive Intelligence: {company_name}")
    log(f"  Budget: {BUDGET.GLOBAL_MAX} global / {BUDGET.PER_COMPANY_MAX} per company")
    log("=" * 60)

    # ── Phase 1: Target company ───────────────────────────────
    log(f"\n[Phase 1] Researching target: {company_name}")
    target_profile = research_company(company_name, url, BUDGET)

    if target_profile is None:
        log("ERROR: Could not scrape target company homepage.")
        log("  Check the URL and your FIRECRAWL_API_KEY.")
        sys.exit(1)

    log(f"  Target: {target_profile.get('name')} | {target_profile.get('category', 'unknown category')}")
    log(f"  Budget used so far: {BUDGET.global_used}/{BUDGET.GLOBAL_MAX}")

    # ── Phase 2: Find competitors ─────────────────────────────
    # Clear active company so Phase 2 calls are counted globally only,
    # not against the target company's per-company cap.
    BUDGET.active_company = None

    log(f"\n[Phase 2] Finding competitors for: {company_name}")

    if not BUDGET.can_afford(2):  # need at least 1 search + 1 LLM
        log("  WARN: Budget too tight to run competitor search. Skipping.")
        BUDGET.skipped.append("Phase 2 skipped — insufficient budget")
        competitor_list = []
    else:
        competitor_list = find_competitors(
            target_profile, BUDGET, max_competitors=max_competitors
        )

    if competitor_list:
        for c in competitor_list:
            log(f"  Found: {c['name']} — {c.get('reason', '')}")
    else:
        log("  No competitors found. Report will cover target only.")

    log(f"  Budget used so far: {BUDGET.global_used}/{BUDGET.GLOBAL_MAX}")

    # ── Phase 3: Research each competitor ────────────────────
    competitor_profiles: list[dict] = []

    for i, comp in enumerate(competitor_list, 1):
        comp_name = comp.get("name", f"Competitor {i}")
        comp_url = comp.get("url")

        log(f"\n[Phase 3.{i}] Researching competitor: {comp_name}")

        # Check if we have enough budget for meaningful research
        skip, reason = _should_skip_competitor(BUDGET, comp_name)
        if skip:
            log(f"  SKIP: {comp_name} — {reason}")
            BUDGET.skipped.append(
                f"Competitor '{comp_name}' skipped — {reason}"
            )
            # Add a stub so the report can still mention this competitor
            competitor_profiles.append(
                {
                    "slug": slugify(comp_name),
                    "name": comp_name,
                    "url": comp_url or "",
                    "tagline": None,
                    "description": comp.get("reason", ""),
                    "category": target_profile.get("category"),
                    "founding_year": None,
                    "headcount_range": None,
                    "funding_stage": None,
                    "headquarters": None,
                    "pricing_model": "unknown",
                    "pricing_tiers": [],
                    "key_features": [],
                    "target_audience": None,
                    "website_tone": None,
                    "website_impression": None,
                    "social": {},
                    "data_sources": [],
                    "calls_used": 0,
                    "skipped_pages": [],
                    "_stub": True,
                }
            )
            continue

        profile = research_company(comp_name, comp_url, BUDGET)

        if profile:
            competitor_profiles.append(profile)
            log(f"  Done: {profile.get('name')} | {profile.get('category', '?')}")
        else:
            log(f"  WARN: Could not research {comp_name}. Adding stub.")
            BUDGET.skipped.append(f"Research failed for '{comp_name}' — homepage not accessible")

        log(f"  Budget used so far: {BUDGET.global_used}/{BUDGET.GLOBAL_MAX}")

    # ── Phase 4: Build report ─────────────────────────────────
    log(f"\n[Phase 4] Generating report...")

    skip_llm = not BUDGET.can_afford(1)
    if skip_llm:
        log("  WARN: No budget for LLM analysis — generating data-only report")

    report_path = build_report(
        target_profile=target_profile,
        competitor_profiles=competitor_profiles,
        budget=BUDGET,
        skip_llm=skip_llm,
    )

    # ── Final summary ─────────────────────────────────────────
    log("\n" + "=" * 60)
    log(f"  Report saved: {report_path}")
    BUDGET.print_summary()

    return report_path


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run competitive intelligence analysis on a company",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m tools.run_analysis --company "Notion" --url "https://notion.so"
  python -m tools.run_analysis --company "Linear"
  python -m tools.run_analysis --company "Figma" --max-competitors 2
        """,
    )
    parser.add_argument("--company", required=True, help="Target company name")
    parser.add_argument("--url", help="Company website URL (auto-derived if omitted)")
    parser.add_argument(
        "--max-competitors",
        type=int,
        default=3,
        help="Max competitors to research (default: 3, max: 3)",
    )
    args = parser.parse_args()

    # Validate environment
    missing = []
    if not os.getenv("FIRECRAWL_API_KEY"):
        missing.append("FIRECRAWL_API_KEY")
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")

    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        print("  Add them to your .env file. See .env.example for reference.", file=sys.stderr)
        sys.exit(1)

    max_comp = min(max(1, args.max_competitors), 3)
    if max_comp != args.max_competitors:
        print(f"Note: --max-competitors clamped to {max_comp}")

    report_path = run(
        company_name=args.company,
        url=args.url,
        max_competitors=max_comp,
    )

    print(f"\nDone. Open the report:")
    print(f"  open {report_path}")


if __name__ == "__main__":
    main()
