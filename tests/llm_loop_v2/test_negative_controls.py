from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "build_unanswerable_controls.py"
SPEC = importlib.util.spec_from_file_location("build_negative_controls_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_excluded_sources_support_queue_and_gold(tmp_path):
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({"source_id": "queue-source"}) + "\n", encoding="utf-8")
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({
        "gold": {"evidence": [{"source_id": "gold-source"}]},
    }) + "\n", encoding="utf-8")
    assert MODULE.excluded_source_ids([queue, gold]) == {"queue-source", "gold-source"}


def test_excluded_sources_support_prior_negative_proofs(tmp_path):
    proof = tmp_path / "proof.jsonl"
    proof.write_text(
        json.dumps({"target_source_ids": ["negative-a", "negative-b"]}) + "\n",
        encoding="utf-8",
    )
    assert MODULE.excluded_source_ids([proof]) == {"negative-a", "negative-b"}


def test_item_id_supports_separate_dev_and_blind_splits():
    assert MODULE.item_id("dev", 19) == "dev-019"
    assert MODULE.item_id("blind", 91) == "blind-091"
    with pytest.raises(ValueError):
        MODULE.item_id("reserve", 1)
