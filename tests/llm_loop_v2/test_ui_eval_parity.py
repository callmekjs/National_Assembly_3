from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "llm_loop_v2" / "run_baseline.py"
SPEC = importlib.util.spec_from_file_location("run_baseline_ui_parity_test", SCRIPT)
RUNNER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(RUNNER)


def test_frontend_and_evaluation_use_same_query_route_and_semantic_fields():
    api = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
    start = api.index("export function postQuery")
    end = api.index("export function getCitation", start)
    post_query = api[start:end]
    assert "request('/query'" in post_query
    assert "JSON.stringify({ question, mode })" in post_query
    assert all(field not in post_query for field in ("committee", "date_from", "date_to"))

    record = {
        "question": "질문입니다",
        "mode": "qa",
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
    }
    evaluation = RUNNER.query_body(record)
    assert {key: evaluation[key] for key in ("question", "mode")} == {
        "question": "질문입니다", "mode": "qa",
    }
    assert not {"committee", "date_from", "date_to"} & evaluation.keys()
