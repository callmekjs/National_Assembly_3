from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "summarize_api_spend.py"
SPEC = importlib.util.spec_from_file_location("summarize_api_spend_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_authoring_usage_is_included(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    write_jsonl(tmp_path / "authoring_results.jsonl", [{
        "model": "gpt-5.6-sol",
        "usage": {"input_tokens": 1000, "output_tokens": 100},
    }])
    summary = MODULE.summarize(runs)
    component = summary["components"]["gold_author_sol"]
    assert component["calls"] == 1
    assert component["estimated_cost_usd"] == 0.008


def test_official_price_calculation():
    assert MODULE.cost("gpt-5.6-terra", 1_000_000, 1_000_000) == 17.5


def test_additional_approved_cap_is_applied(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    summary = MODULE.summarize(runs, approved_cap_usd=16.0)
    assert summary["approved_cap_usd"] == 16.0
    assert summary["remaining_to_cap_usd"] == 16.0
    assert summary["within_cap"] is True


def test_incremental_cap_does_not_reuse_old_unused_budget(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    # 총액 gate와 별개로 승인 시점 이후 정확히 $6만 허용한다.
    summary = MODULE.summarize(
        runs, approved_cap_usd=16.0, baseline_spend_usd=-6.01, additional_cap_usd=6.0
    )
    assert summary["additional_spent_usd"] == 6.01
    assert summary["within_additional_cap"] is False
    assert summary["within_cap"] is False


def test_call_ledger_takes_precedence_over_success_rows(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    result = tmp_path / "authoring_results.jsonl"
    write_jsonl(result, [{"usage": {"input_tokens": 100, "output_tokens": 10}}])
    write_jsonl(result.with_suffix(".jsonl.calls.jsonl"), [
        {"status": "INVALID_JSON", "usage": {"input_tokens": 200, "output_tokens": 20}},
        {"status": "SUCCESS", "usage": {"input_tokens": 100, "output_tokens": 10}},
    ])
    summary = MODULE.summarize(runs)
    component = summary["components"]["gold_author_sol"]
    assert component["calls"] == 2
    assert component["input_tokens"] == 300


def test_named_semantic_judgment_ledger_is_included(tmp_path):
    runs = tmp_path / "runs"
    output = runs / "blind" / "semantic_judgments_portfolio.jsonl"
    write_jsonl(output, [{"usage": {"input_tokens": 100, "output_tokens": 10}}])
    write_jsonl(output.with_suffix(".jsonl.calls.jsonl"), [
        {"status": "SUCCESS", "usage": {"input_tokens": 300, "output_tokens": 20}},
        {"status": "INVALID_JSON", "usage": {"input_tokens": 200, "output_tokens": 10}},
    ])
    summary = MODULE.summarize(runs)
    component = summary["components"]["semantic_judge_sol"]
    assert component["calls"] == 2
    assert component["input_tokens"] == 500
