from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "approve_codex_source_review.py"
SPEC = importlib.util.spec_from_file_location("approve_source_review_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def review(row_id: str, status: str = "approved") -> dict:
    return {
        "id": row_id,
        "status": status,
        "reviewer": "Codex source review",
        "reviewed_at": "2026-08-18T16:00:00+09:00",
        "checks": {
            "question_scope": True,
            "answer_supported": True,
            "claims_match_question": True,
            "required_values_match_question": True,
            "reference_values_supported": True,
        },
        "note": "원문과 질문을 문항별로 대조했다.",
    }


def test_explicit_per_item_reviews_are_required():
    rows = MODULE.validate_review_rows({"blind-001", "blind-002"}, [review("blind-001"), review("blind-002")])
    assert set(rows) == {"blind-001", "blind-002"}


def test_pending_or_missing_review_cannot_be_auto_approved():
    try:
        MODULE.validate_review_rows({"blind-001", "blind-002"}, [review("blind-001", "pending")])
    except ValueError as exc:
        assert "ids mismatch" in str(exc) or "not approved" in str(exc)
    else:
        raise AssertionError("pending or missing per-item review must fail")


def test_legacy_single_exact_value_check_is_not_enough_for_new_review():
    row = review("blind-001")
    row["checks"] = {
        "question_scope": True,
        "answer_supported": True,
        "claims_match_question": True,
        "exact_values_supported": True,
    }
    try:
        MODULE.validate_review_rows({"blind-001"}, [row])
    except ValueError as exc:
        assert "checks" in str(exc)
    else:
        raise AssertionError("partition roles must be reviewed separately")
