from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "author_gold.py"
SPEC = importlib.util.spec_from_file_location("author_gold_budget_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_current_and_prior_costs_can_be_accounted_separately():
    current = [{"estimated_cost_usd": 0.25}, {"estimated_cost_usd": 0.5}]
    prior = [{"estimated_cost_usd": 4.0}]
    current_spent = MODULE.cost_total(current)
    audited_total = current_spent + MODULE.cost_total(prior)
    assert current_spent == 0.75
    assert audited_total == 4.75
    assert current_spent < 2.5 < audited_total
