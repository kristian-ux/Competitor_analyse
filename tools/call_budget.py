"""
call_budget.py
--------------
Shared call budget counter for the competitive intelligence pipeline.

Every Firecrawl scrape, Firecrawl search, and OpenAI LLM call must be
charged through this module. The singleton instance is created in
tools/__init__.py and imported by all other tools.

Limits:
  GLOBAL_MAX        = 40  (total API calls for the entire run)
  PER_COMPANY_MAX   = 10  (per target company or competitor)
"""

from __future__ import annotations

import threading
from datetime import datetime


class CallBudget:
    GLOBAL_MAX: int = 40
    PER_COMPANY_MAX: int = 10

    def __init__(self) -> None:
        self.global_used: int = 0
        self.per_company: dict[str, int] = {}
        self.skipped: list[str] = []
        self.active_company: str | None = None
        self._log: list[str] = []
        self._lock = threading.Lock()

    # ── active company ─────────────────────────────────────────────────────

    def set_active(self, company_slug: str) -> None:
        """Set the active company so callers don't have to pass it every time."""
        self.active_company = company_slug
        if company_slug not in self.per_company:
            self.per_company[company_slug] = 0

    # ── core charge / check ────────────────────────────────────────────────

    def charge(self, label: str, company: str | None = None, cost: int = 1) -> bool:
        """
        Attempt to charge `cost` calls against both the global and per-company
        budgets.

        Returns True  → charge accepted; caller may proceed.
        Returns False → limit would be exceeded; caller must skip.

        On False the reason is appended to self.skipped.
        Thread-safe: uses an internal lock so concurrent calls don't race.
        """
        with self._lock:
            return self._charge_locked(label, company, cost)

    def _charge_locked(self, label: str, company: str | None, cost: int) -> bool:
        """Inner charge logic — must be called with self._lock held."""
        slug = company or self.active_company

        # Check global limit
        if self.global_used + cost > self.GLOBAL_MAX:
            msg = (
                f"[{_ts()}] SKIP {label!r}"
                + (f" ({slug})" if slug else "")
                + f" — global limit {self.GLOBAL_MAX} reached "
                + f"({self.global_used} used)"
            )
            self.skipped.append(msg)
            self._log.append(msg)
            return False

        # Check per-company limit (only when a company is active)
        if slug:
            company_used = self.per_company.get(slug, 0)
            if company_used + cost > self.PER_COMPANY_MAX:
                msg = (
                    f"[{_ts()}] SKIP {label!r} ({slug})"
                    f" — per-company limit {self.PER_COMPANY_MAX} reached "
                    f"({company_used} used for this company)"
                )
                self.skipped.append(msg)
                self._log.append(msg)
                return False

        # Commit the charge
        self.global_used += cost
        if slug:
            self.per_company[slug] = self.per_company.get(slug, 0) + cost

        log_msg = (
            f"[{_ts()}] CHARGE {label!r}"
            + (f" ({slug})" if slug else "")
            + f" — global {self.global_used}/{self.GLOBAL_MAX}"
            + (
                f", company {self.per_company[slug]}/{self.PER_COMPANY_MAX}"
                if slug
                else ""
            )
        )
        self._log.append(log_msg)
        return True

    def can_afford(self, cost: int = 1, company: str | None = None) -> bool:
        """
        Pure check — no side effects, no logging.
        Returns True if `cost` calls can be charged without hitting any limit.
        """
        slug = company or self.active_company
        if self.global_used + cost > self.GLOBAL_MAX:
            return False
        if slug:
            company_used = self.per_company.get(slug, 0)
            if company_used + cost > self.PER_COMPANY_MAX:
                return False
        return True

    # ── helpers ────────────────────────────────────────────────────────────

    def remaining_for(self, company: str | None = None) -> int:
        """Calls remaining before hitting either the global or per-company cap."""
        slug = company or self.active_company
        global_rem = self.GLOBAL_MAX - self.global_used
        if slug:
            company_used = self.per_company.get(slug, 0)
            per_company_rem = self.PER_COMPANY_MAX - company_used
            return min(global_rem, per_company_rem)
        return global_rem

    @property
    def global_remaining(self) -> int:
        return self.GLOBAL_MAX - self.global_used

    def summary(self) -> dict:
        return {
            "global_used": self.global_used,
            "global_remaining": self.global_remaining,
            "global_max": self.GLOBAL_MAX,
            "per_company": dict(self.per_company),
            "skipped_count": len(self.skipped),
            "skipped": self.skipped,
        }

    def print_summary(self) -> None:
        s = self.summary()
        print(f"\n{'─'*50}")
        print(f"  Budget summary: {s['global_used']}/{s['global_max']} calls used")
        for slug, used in s["per_company"].items():
            print(f"    {slug}: {used}/{self.PER_COMPANY_MAX}")
        if s["skipped"]:
            print(f"  Skipped items ({s['skipped_count']}):")
            for item in s["skipped"]:
                print(f"    · {item}")
        print(f"{'─'*50}\n")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")
