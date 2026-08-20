from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "summarize_stability.py"
SPEC = importlib.util.spec_from_file_location("summarize_stability_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Scorer:
    @staticmethod
    def score(record, result):
        failed = result.get("failed", False)
        return {
            "stage1_verdict": "AUTO_FAIL" if failed else "SEMANTIC_REVIEW_REQUIRED",
            "auto_fail_reasons": ["failure"] if failed else [],
        }


def dataset():
    return [{"id": f"reserve-{index:03d}"} for index in range(1, 31)]


def run(failed_id=None):
    return [{
        "id": f"reserve-{index:03d}",
        "failed": f"reserve-{index:03d}" == failed_id,
        "response": {
            "grounding": "FULL", "verification": {"flags": []},
            "citations": [{"chunk_id": f"chunk-{index}"}],
        },
    } for index in range(1, 31)]


def test_three_clean_runs_pass_stage1_gate():
    summary, rows = MODULE.summarize(dataset(), [run(), run(), run()], Scorer)
    assert summary["stage1_gate"] == "PASS"
    assert len(rows) == 30


def test_one_failure_exposure_fails_gate():
    summary, _ = MODULE.summarize(dataset(), [run(), run("reserve-005"), run()], Scorer)
    assert summary["stage1_gate"] == "FAIL"
    assert summary["stage1_failure_exposures"] == 1


def test_wrong_run_count_rejected():
    try:
        MODULE.summarize(dataset(), [run(), run()], Scorer)
    except ValueError as exc:
        assert "exactly 3" in str(exc)
    else:
        raise AssertionError("two runs must fail")


def config(runtime_hash="same"):
    return {
        "git_head": "head",
        "dataset_sha256": "dataset",
        "base_url": "http://127.0.0.1:8000",
        "requested_records": 30,
        "safe_settings": {"ANSWER_MODEL": "gpt"},
        "runtime_sha256": {"backend/answer.py": runtime_hash},
        "max_additional_cost": 0.7,
    }


def test_three_runs_require_identical_runtime_configs():
    digest = MODULE.validate_run_configs([config(), config(), config()])
    assert len(digest) == 64
    try:
        MODULE.validate_run_configs([config(), config("changed"), config()])
    except ValueError as exc:
        assert "configs differ" in str(exc)
    else:
        raise AssertionError("mixed runtime stability runs must fail")
