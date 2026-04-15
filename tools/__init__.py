"""
tools/__init__.py
-----------------
Exports the shared BUDGET singleton used by all tools in this package.
Import pattern:

    from tools import BUDGET

Because all tool modules run in the same process (imported by run_analysis.py),
this singleton is shared across the entire pipeline run.
"""

from tools.call_budget import CallBudget

BUDGET = CallBudget()
