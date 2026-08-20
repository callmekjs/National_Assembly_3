from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "summarize_gate.py"
SPEC = importlib.util.spec_from_file_location("summarize_gate_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def record(row_id: str, answerable: bool = True):
    return {"id": row_id, "answerable": answerable}


def rule(row_id: str, reasons=None):
    return {
        "id": row_id,
        "stage1_verdict": "AUTO_FAIL" if reasons else "SEMANTIC_REVIEW_REQUIRED",
        "auto_fail_reasons": reasons or [],
        "gold_source_cited": True,
        "gold_evidence_chunk_cited": True,
    }


def judge(row_id: str, *, verdict="PASS", unsupported=None, critical=None, citation=2):
    return {"id": row_id, "judgment": {
        "verdict": verdict,
        "unsupported_claims": unsupported or [],
        "critical_errors": critical or [],
        "citation_support": citation,
    }}


def test_perfect_gate_passes():
    dataset = [record("blind-001"), record("blind-002", False)]
    summary, rows = MODULE.summarize(
        dataset,
        [rule("blind-001"), rule("blind-002")],
        [judge("blind-001"), judge("blind-002")],
        expected_records=2,
    )
    assert summary["gate"] == "PASS"
    assert all(row["passed"] for row in rows)


def test_unsupported_claim_forces_gate_failure_even_with_pass_verdict():
    summary, rows = MODULE.summarize(
        [record("blind-001"), record("blind-002", False)],
        [rule("blind-001"), rule("blind-002")],
        [judge("blind-001", unsupported=["근거 없는 숫자"]), judge("blind-002")],
        expected_records=2,
    )
    assert summary["gate"] == "FAIL"
    assert rows[0]["critical_hallucination"] is True


def test_same_document_wrong_chunk_fails_citation_gate():
    wrong_chunk = rule("blind-001")
    wrong_chunk["gold_evidence_chunk_cited"] = False
    summary, rows = MODULE.summarize(
        [record("blind-001"), record("blind-002", False)],
        [wrong_chunk, rule("blind-002")],
        [judge("blind-001"), judge("blind-002")],
        expected_records=2,
    )
    assert rows[0]["citation_ok"] is False
    assert summary["gate"] == "FAIL"


def test_missing_exact_chunk_field_cannot_fall_back_to_source_match():
    source_only = rule("blind-001")
    source_only.pop("gold_evidence_chunk_cited")
    summary, rows = MODULE.summarize(
        [record("blind-001"), record("blind-002", False)],
        [source_only, rule("blind-002")],
        [judge("blind-001"), judge("blind-002")],
        expected_records=2,
    )
    assert rows[0]["citation_ok"] is False
    assert summary["gate"] == "FAIL"


def test_expected_unanswerable_composition_is_enforced():
    try:
        MODULE.summarize(
            [record("blind-001"), record("blind-002", False)],
            [rule("blind-001"), rule("blind-002")],
            [judge("blind-001"), judge("blind-002")],
            expected_records=2,
            expected_unanswerable=0,
        )
    except ValueError as exc:
        assert "expected 0 unanswerable" in str(exc)
    else:
        raise AssertionError("wrong answerable/unanswerable composition must fail")


def test_unanswerable_without_refusal_fails():
    summary, _ = MODULE.summarize(
        [record("blind-001"), record("blind-002", False)],
        [rule("blind-001"), rule("blind-002", ["missing_required_refusal"])],
        [judge("blind-001"), judge("blind-002")],
        expected_records=2,
    )
    assert summary["refusal_accuracy"] == 0
    assert summary["gate"] == "FAIL"


def test_missing_judgment_is_rejected():
    try:
        MODULE.summarize(
            [record("blind-001"), record("blind-002", False)],
            [rule("blind-001"), rule("blind-002")],
            [judge("blind-001")],
            expected_records=2,
        )
    except ValueError as exc:
        assert "ids mismatch" in str(exc)
    else:
        raise AssertionError("missing judgment must fail")


def test_portfolio_profile_uses_separate_published_thresholds():
    summary, _ = MODULE.summarize(
        [record("blind-001"), record("blind-002", False)],
        [rule("blind-001"), rule("blind-002")],
        [judge("blind-001"), judge("blind-002")],
        expected_records=2,
        profile="portfolio",
    )
    assert summary["gate"] == "PASS"
    assert summary["thresholds"]["overall_accuracy_min"] == 0.90
    assert summary["thresholds"]["citation_accuracy_min"] == 0.95
    assert summary["thresholds"]["refusal_accuracy_min"] == 1.0


def test_generation_profile_supports_answerable_only_g3():
    dataset = [record(f"dev-{index:03d}") for index in range(1, 19)]
    summary, _ = MODULE.summarize(
        dataset,
        [rule(row["id"]) for row in dataset],
        [judge(row["id"]) for row in dataset],
        expected_records=18,
        expected_unanswerable=0,
        profile="generation",
    )
    assert summary["gate"] == "PASS"
    assert summary["thresholds"]["overall_accuracy_min"] == 0.95
    assert summary["refusal_accuracy"] == 1.0
