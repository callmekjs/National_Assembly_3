from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "analyze_retrieval_trace.py"
SPEC = importlib.util.spec_from_file_location("analyze_retrieval_trace_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def record(row_id: str, chunk_id: str, category: str = "fact") -> dict:
    return {
        "id": row_id, "answerable": True, "category": category,
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {"evidence": [{"chunk_id": chunk_id}]},
    }


def unanswerable_record(row_id: str) -> dict:
    return {
        "id": row_id, "answerable": False, "category": "unanswerable",
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {"evidence": []},
    }


def result(row_id: str, *, keyword, vector, rrf, final, committee="정무위") -> dict:
    def rows(ids):
        return [{"chunk_id": value} for value in ids]

    return {"id": row_id, "response": {"retrieval_trace": {
        "filters": {"committees": [committee], "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "keyword_candidates": rows(keyword),
        "vector_candidates": rows(vector),
        "rrf_candidates": rows(rrf),
        "final_results": rows(final),
    }}}


def test_stage_recall_distinguishes_rrf_hit_from_final_context_drop():
    report = MODULE.analyze(
        [record("blind-001", "gold-1")],
        [result(
            "blind-001", keyword=["gold-1"], vector=[],
            rrf=["gold-1"], final=["other-1"],
        )],
    )
    assert report["recall"]["rrf_at_10"] == 1.0
    assert report["recall"]["final_context_at_5"] == 0.0
    assert report["miss_ids"]["final_context_at_5"] == ["blind-001"]
    assert report["status"] == "FAIL"


def test_final_context_counts_gold_chunk_restored_from_same_turn():
    candidate = result(
        "blind-001", keyword=["hit-1"], vector=["hit-1"], rrf=["hit-1"], final=["hit-1"],
    )
    candidate["response"]["retrieval_trace"]["final_results"][0]["support_chunk_ids"] = [
        "hit-1", "gold-1"
    ]
    report = MODULE.analyze([record("blind-001", "gold-1")], [candidate])
    assert report["recall"]["rrf_at_10"] == 0.0
    assert report["recall"]["final_context_at_5"] == 1.0


def test_complete_matching_trace_passes():
    report = MODULE.analyze(
        [record("blind-001", "gold-1")],
        [result("blind-001", keyword=["gold-1"], vector=["gold-1"], rrf=["gold-1"], final=["gold-1"])],
    )
    assert report["status"] == "PASS"


def test_portfolio_retrieval_threshold_is_85_percent():
    dataset = [record(f"blind-{i:03d}", f"gold-{i}") for i in range(1, 21)]
    results = []
    for i in range(1, 21):
        gold = f"gold-{i}"
        hit = i <= 17
        results.append(result(
            f"blind-{i:03d}", keyword=[gold], vector=[gold] if hit else [],
            rrf=[gold] if hit else ["other"], final=[gold] if hit else ["other"],
        ))
    report = MODULE.analyze(dataset, results)
    assert report["recall"]["rrf_at_10"] == 0.85
    assert report["recall"]["final_context_at_5"] == 0.85
    assert report["thresholds"] == {
        "rrf_at_10": 0.85,
        "final_context_at_5": 0.85,
        "category_rrf_at_10": 0.75,
        "filter_errors": 0,
    }
    assert report["status"] == "PASS"


def test_filter_mismatch_is_hard_failure():
    report = MODULE.analyze(
        [record("blind-001", "gold-1")],
        [result("blind-001", keyword=["gold-1"], vector=["gold-1"], rrf=["gold-1"], final=["gold-1"], committee="국방위")],
    )
    assert report["filter_mismatch_ids"] == ["blind-001"]
    assert report["status"] == "FAIL"


def test_unanswerable_filter_mismatch_cannot_create_false_valid_refusal():
    report = MODULE.analyze(
        [record("blind-001", "gold-1"), unanswerable_record("blind-091")],
        [
            result("blind-001", keyword=["gold-1"], vector=["gold-1"], rrf=["gold-1"], final=["gold-1"]),
            result("blind-091", keyword=[], vector=[], rrf=[], final=[], committee="국방위"),
        ],
    )
    assert report["answerable_records"] == 1
    assert report["filter_mismatch_ids"] == ["blind-091"]
    assert report["status"] == "FAIL"


def test_unanswerable_missing_trace_is_hard_failure():
    report = MODULE.analyze(
        [record("blind-001", "gold-1"), unanswerable_record("blind-091")],
        [
            result("blind-001", keyword=["gold-1"], vector=["gold-1"], rrf=["gold-1"], final=["gold-1"]),
            {"id": "blind-091", "response": {}},
        ],
    )
    assert report["missing_trace_ids"] == ["blind-091"]
    assert report["status"] == "FAIL"
