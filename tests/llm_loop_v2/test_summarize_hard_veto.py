from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "summarize_hard_veto.py"
SPEC = importlib.util.spec_from_file_location("hard_veto_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_unanswerable_citation_forces_early_fail():
    dataset = []
    scores = []
    for index in range(1, 101):
        row_id = f"blind-{index:03d}"
        answerable = index <= 90
        dataset.append({"id": row_id, "answerable": answerable})
        reasons = ["unanswerable_has_citations"] if index == 91 else []
        scores.append({
            "id": row_id,
            "stage1_verdict": "AUTO_FAIL" if reasons else "SEMANTIC_REVIEW_REQUIRED",
            "auto_fail_reasons": reasons,
            "gold_source_cited": answerable,
            "gold_evidence_chunk_cited": answerable,
        })
    report = MODULE.summarize(dataset, scores)
    assert report["gate"] == "FAIL_EARLY"
    assert report["refusal_accuracy"] == 0.9
    assert report["semantic_review"] == "SKIPPED_AFTER_HARD_VETO"


def test_portfolio_profile_allows_semantic_review_with_bounded_stage1_failures():
    dataset = []
    scores = []
    for index in range(1, 101):
        row_id = f"blind-{index:03d}"
        answerable = index <= 90
        dataset.append({"id": row_id, "answerable": answerable})
        failed = index <= 9
        scores.append({
            "id": row_id,
            "stage1_verdict": "AUTO_FAIL" if failed else "SEMANTIC_REVIEW_REQUIRED",
            "auto_fail_reasons": ["missing_exact_number"] if failed else [],
            # 이 테스트는 숫자 누락 하드 실패 수만 검증한다. 인용 정확도 축은 정상으로 고정한다.
            "gold_source_cited": answerable,
            "gold_evidence_chunk_cited": answerable,
        })
    report = MODULE.summarize(dataset, scores, profile="portfolio")
    assert report["gate"] == "SEMANTIC_REVIEW_REQUIRED"
    assert report["profile"] == "portfolio"
    assert report["semantic_review"] == "REQUIRED"


def test_exact_evidence_chunk_overrides_same_source_match():
    dataset = []
    scores = []
    for index in range(1, 101):
        row_id = f"blind-{index:03d}"
        answerable = index <= 90
        dataset.append({"id": row_id, "answerable": answerable})
        scores.append({
            "id": row_id,
            "stage1_verdict": "SEMANTIC_REVIEW_REQUIRED",
            "auto_fail_reasons": [],
            "gold_source_cited": answerable,
            "gold_evidence_chunk_cited": False if index == 1 else answerable,
        })
    report = MODULE.summarize(dataset, scores)
    assert report["citation_passed"] == 89


def test_duplicate_deterministic_id_is_rejected():
    dataset = [{"id": "blind-001", "answerable": True}, {"id": "blind-002", "answerable": False}]
    score = {
        "id": "blind-001", "stage1_verdict": "SEMANTIC_REVIEW_REQUIRED",
        "auto_fail_reasons": [], "gold_evidence_chunk_cited": True,
    }
    try:
        MODULE.summarize(dataset, [score, dict(score)], expected=2)
    except ValueError as exc:
        assert "duplicate id" in str(exc)
    else:
        raise AssertionError("duplicate deterministic id must fail")


def test_hard_veto_expected_unanswerable_composition_is_enforced():
    dataset = [{"id": "blind-001", "answerable": True}, {"id": "blind-002", "answerable": False}]
    scores = [
        {"id": "blind-001", "stage1_verdict": "SEMANTIC_REVIEW_REQUIRED",
         "auto_fail_reasons": [], "gold_evidence_chunk_cited": True},
        {"id": "blind-002", "stage1_verdict": "SEMANTIC_REVIEW_REQUIRED",
         "auto_fail_reasons": [], "gold_evidence_chunk_cited": False},
    ]
    try:
        MODULE.summarize(dataset, scores, expected=2, expected_unanswerable=0)
    except ValueError as exc:
        assert "expected 0 unanswerable" in str(exc)
    else:
        raise AssertionError("wrong answerable/unanswerable composition must fail")
