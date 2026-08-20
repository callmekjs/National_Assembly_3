from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "preflight_round3_sources.py"
SPEC = importlib.util.spec_from_file_location("preflight_round3_sources_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_source_sets_keep_dev_blind_reserve_disjoint():
    sets = MODULE.source_sets(
        [{"source_id": "dev-a"}],
        [
            {"split": "blind", "source_id": "blind-a"},
            {"split": "reserve", "source_id": "reserve-a"},
        ],
        [{"target_source_ids": ["dev-negative"]}],
        [{"target_source_ids": ["blind-negative"]}],
    )
    MODULE.assert_disjoint(sets)
    assert sets["dev"] == {"dev-a", "dev-negative"}


def test_source_overlap_is_hard_failure():
    with pytest.raises(ValueError, match="source leakage"):
        MODULE.assert_disjoint({"dev": {"same"}, "blind": {"same"}, "reserve": set()})
