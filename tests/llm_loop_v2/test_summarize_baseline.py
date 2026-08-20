from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "summarize_baseline.py"
SPEC = importlib.util.spec_from_file_location("summarize_baseline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_pass_requires_perfect_scores_and_empty_error_lists():
    judgment = {
        "verdict": "PASS",
        "correctness": 2,
        "completeness": 2,
        "citation_support": 2,
        "missing_required_claims": [],
        "unsupported_claims": [],
        "critical_errors": [],
    }
    assert MODULE.semantic_consistent(judgment)
    judgment["unsupported_claims"] = ["근거 없음"]
    assert not MODULE.semantic_consistent(judgment)


def test_rubric_invalid_is_excluded_but_system_fail_remains_failure():
    ids = ["a", "b"]
    dataset = [{"id": item_id} for item_id in ids]
    results = [{"id": item_id, "response": {"grounding": "FULL"}} for item_id in ids]
    deterministic = [
        {"id": item_id, "stage1_verdict": "SEMANTIC_REVIEW_REQUIRED", "auto_fail_reasons": []}
        for item_id in ids
    ]
    perfect = {
        "verdict": "PASS",
        "correctness": 2,
        "completeness": 2,
        "citation_support": 2,
        "missing_required_claims": [],
        "unsupported_claims": [],
        "critical_errors": [],
    }
    semantic = [
        {"id": "a", "judgment": perfect, "usage": {}},
        {"id": "b", "judgment": perfect, "usage": {}},
    ]
    adjudications = {
        "items": [
            {"id": "a", "classification": "RUBRIC_INVALID", "reason": "bad rubric"},
            {"id": "b", "classification": "SYSTEM_FAIL", "reason": "unsupported"},
        ]
    }
    rows, summary = MODULE.combine(dataset, results, deterministic, semantic, adjudications)
    assert rows[0]["final_verdict"] == "EXCLUDED_RUBRIC_INVALID"
    assert rows[1]["final_verdict"] == "FAIL"
    assert summary["included_records"] == 1
    assert not summary["gate"]["passed"]
