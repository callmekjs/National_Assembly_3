from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "prepare_judge_inputs.py"
SPEC = importlib.util.spec_from_file_location("prepare_judge_inputs_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def record(row_id="blind-001", answerable=True):
    return {
        "id": row_id, "answerable": answerable, "question": "질문",
        "gold": {
            "answer": "정답" if answerable else None,
            "required_claims": ["필수"] if answerable else [],
            "forbidden_claims": ["금지"], "exact_values": [],
            "refusal_reason": None if answerable else "발언 없음",
        },
    }


def result(row_id="blind-001", chunk_id="chunk-1"):
    return {"id": row_id, "response": {
        "answer": "후보 [1]", "grounding": "FULL",
        "citations": [{"chunk_id": chunk_id}],
    }}


def test_build_inputs_includes_full_evidence():
    rows = MODULE.build_inputs([record()], [result()], {"chunk-1": {"chunk_id": "chunk-1", "text": "전문"}})
    assert rows[0]["cited_evidence"][0]["text"] == "전문"
    assert rows[0]["answerable"] is True


def test_build_inputs_uses_all_chunks_actually_shown_to_answer_model():
    candidate = result(chunk_id="chunk-1")
    candidate["response"]["citations"][0]["support_chunk_ids"] = ["chunk-1", "chunk-2"]
    candidate["response"]["citations"][0]["n"] = 3
    full = {
        "chunk-1": {"chunk_id": "chunk-1", "text": "앞 조각"},
        "chunk-2": {"chunk_id": "chunk-2", "text": "정답 조각"},
    }
    rows = MODULE.build_inputs([record()], [candidate], full)
    evidence = rows[0]["cited_evidence"][0]
    assert evidence["text"] == "앞 조각 정답 조각"
    assert evidence["support_chunk_ids"] == ["chunk-1", "chunk-2"]
    assert evidence["n"] == 3


def test_build_inputs_preserves_required_and_reference_value_roles():
    item = record()
    item["gold"]["required_exact_values"] = [{"type": "number", "value": "10명"}]
    item["gold"]["reference_values"] = [{"type": "number", "value": "93조 원"}]
    item["gold"]["exact_values"] = [
        *item["gold"]["required_exact_values"],
        *item["gold"]["reference_values"],
    ]
    rows = MODULE.build_inputs([item], [result()], {"chunk-1": {"chunk_id": "chunk-1", "text": "전문"}})
    assert rows[0]["required_exact_values"] == [{"type": "number", "value": "10명"}]
    assert rows[0]["reference_values"] == [{"type": "number", "value": "93조 원"}]


def test_missing_cited_chunk_is_hard_failure():
    try:
        MODULE.build_inputs([record()], [result()], {})
    except ValueError as exc:
        assert "missing from DB" in str(exc)
    else:
        raise AssertionError("missing evidence must fail")


def test_unanswerable_requires_matching_proof_when_requested():
    negative = record("blind-091", False)
    negative_result = {"id": "blind-091", "response": {"answer": "확인 불가", "grounding": "NONE", "citations": []}}
    try:
        MODULE.build_inputs([negative], [negative_result], {}, [])
    except ValueError as exc:
        assert "proof ids mismatch" in str(exc)
    else:
        raise AssertionError("negative proof must be present")


def test_unanswerable_proof_is_forwarded():
    negative = record("blind-091", False)
    negative_result = {"id": "blind-091", "response": {"answer": "확인 불가", "grounding": "NONE", "citations": []}}
    proof = {"id": "blind-091", "speaker_chunk_count_in_target": 0}
    rows = MODULE.build_inputs([negative], [negative_result], {}, [proof])
    assert rows[0]["negative_control_proof"] == proof


def test_unanswerable_exact_values_are_scope_metadata_not_required_answer_text():
    negative = record("blind-091", False)
    negative["gold"]["exact_values"] = [
        {"type": "person", "value": "안태준"},
        {"type": "date", "value": "2025-11-17"},
    ]
    negative_result = {
        "id": "blind-091",
        "response": {"answer": "제공된 회의록에서 확인할 수 없습니다.", "grounding": "REFUSED", "citations": []},
    }
    proof = {"id": "blind-091", "speaker_chunk_count_in_target": 0}
    rows = MODULE.build_inputs([negative], [negative_result], {}, [proof])
    assert rows[0]["exact_values"] == negative["gold"]["exact_values"]
    assert rows[0]["required_exact_values"] == []


def test_answerable_subset_ignores_extra_negative_proofs():
    proof = {"id": "blind-091", "speaker_chunk_count_in_target": 0}
    rows = MODULE.build_inputs(
        [record()], [result()], {"chunk-1": {"chunk_id": "chunk-1", "text": "전문"}}, [proof]
    )
    assert rows[0]["negative_control_proof"] is None
