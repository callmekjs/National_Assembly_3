from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "audit_run.py"
SPEC = importlib.util.spec_from_file_location("audit_run_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def record(row_id="blind-001"):
    return {
        "id": row_id,
        "question": "질문입니다?",
        "mode": "qa",
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
    }


def result(row_id="blind-001"):
    return {
        "id": row_id,
        "question": "질문입니다?",
        "request": {"mode": "qa", "include_trace": True},
        "http_status": 200,
        "response": {
            "answer": "답변 [1]",
            "citations": [{"chunk_id": "chunk-1"}],
            "retrieval_trace": {
                "keyword_candidates": [], "vector_candidates": [],
                "rrf_candidates": [], "final_results": [],
            },
        },
        "error": None,
    }


def test_clean_run_passes():
    report = MODULE.audit([record()], [result()], expected_records=1)
    assert report["status"] == "PASS"


def test_duplicate_result_is_failure():
    report = MODULE.audit([record()], [result(), result()], expected_records=1)
    assert report["status"] == "FAIL"
    assert "duplicate_result_id" in report["errors"]


def test_wrong_filter_is_failure():
    candidate = result()
    candidate["request"]["committee"] = "국방위"
    report = MODULE.audit([record()], [candidate], expected_records=1)
    assert report["status"] == "FAIL"
    assert report["request_mismatch_ids"] == ["blind-001"]


def test_http_error_is_failure():
    candidate = result()
    candidate["http_status"] = 429
    report = MODULE.audit([record()], [candidate], expected_records=1)
    assert report["status"] == "FAIL"
    assert report["http_failure_ids"] == ["blind-001"]


def test_missing_retrieval_trace_is_failure():
    candidate = result()
    del candidate["response"]["retrieval_trace"]
    report = MODULE.audit([record()], [candidate], expected_records=1)
    assert report["status"] == "FAIL"
    assert report["missing_trace_ids"] == ["blind-001"]
