from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


VALIDATOR = load("validate_authored_gold_test", ROOT / "scripts" / "llm_loop_v2" / "validate_authored_gold.py")
MATERIALIZER = load("materialize_blind_test", ROOT / "scripts" / "llm_loop_v2" / "materialize_blind.py")
AUTHOR = load("author_gold_test", ROOT / "scripts" / "llm_loop_v2" / "author_gold.py")


def source(record_id: str = "blind-001") -> dict:
    return {
        "id": record_id,
        "split": record_id.split("-", 1)[0],
        "category": "fact",
        "committee_filter": "정무위",
        "candidate_id": "candidate-001",
        "chunk_id": "chunk-001",
        "source_id": "source-001",
        "speaker": "홍길동",
        "role": "위원",
        "committee": "정무위원회",
        "date": "2024-01-02",
        "page_start": 3,
        "text": "홍길동 위원은 지원 대상이 10명이라고 설명하고 추가 확대는 아직 결정되지 않았다고 말했다.",
    }


def result(src: dict) -> dict:
    return {
        "id": src["id"],
        "candidate_id": src["candidate_id"],
        "record": {
            "schema_version": "2.0", "id": src["id"], "split": src["split"],
            "category": src["category"],
            "question": "2024년 1월 2일 정무위원회에서 홍길동 위원이 밝힌 지원 대상은 몇 명인가?",
            "mode": "qa", "answerable": True,
            "filters": {"committee": "정무위", "date_from": "2024-01-02", "date_to": "2024-01-02"},
            "gold": {
                "answer": "홍길동 위원은 지원 대상이 10명이라고 설명했다.",
                "required_claims": ["지원 대상은 10명이다"],
                "forbidden_claims": ["추가 확대가 확정됐다"],
                "exact_values": [
                    {"type": "person", "value": "홍길동"},
                    {"type": "date", "value": "2024-01-02"},
                    {"type": "number", "value": "10명"},
                ],
                "evidence": [{
                    "chunk_id": src["chunk_id"], "source_id": src["source_id"],
                    "quote": src["text"], "speaker": src["speaker"], "role": src["role"],
                    "committee": src["committee"], "date": src["date"], "page_start": src["page_start"],
                }],
                "refusal_reason": None,
            },
            "provenance": {
                "selection_method": "source_first_v2",
                "created_at": datetime.now().astimezone().isoformat(),
                "review_status": "source_checked",
            },
        },
    }


def test_matching_authored_result_passes_hard_checks():
    src = source()
    report = VALIDATOR.validate_rows([src], [result(src)], require_complete=True)
    assert report["status"] == "PASS"
    assert report["review_flags"] == []


def test_new_authoring_keeps_required_and_reference_values_separate():
    src = source()
    authored = {
        "question": "2024년 1월 2일 정무위원회에서 홍길동 위원이 밝힌 지원 대상은 몇 명인가?",
        "answer": "지원 대상은 10명이다.",
        "required_claims": ["지원 대상은 10명이다"],
        "forbidden_claims": ["추가 확대가 확정됐다"],
        "required_exact_values": [{"type": "number", "value": "10명"}],
        "reference_values": [{"type": "person", "value": "홍길동"}],
    }
    gold = AUTHOR.record_from(src, authored)["gold"]
    assert gold["required_exact_values"] == authored["required_exact_values"]
    assert gold["reference_values"] == authored["reference_values"]
    assert gold["exact_values"] == [
        {"type": "number", "value": "10명"},
        {"type": "person", "value": "홍길동"},
    ]


def test_changed_evidence_is_rejected():
    src = source()
    row = result(src)
    row["record"]["gold"]["evidence"][0]["quote"] += " 변조"
    report = VALIDATOR.validate_rows([src], [row], require_complete=True)
    assert report["status"] == "FAIL"
    assert "source mismatch" in report["hard_errors"][0]["error"]


def test_unsupported_exact_value_is_rejected():
    src = source()
    row = result(src)
    row["record"]["gold"]["exact_values"].append({"type": "number", "value": "99명"})
    report = VALIDATOR.validate_rows([src], [row], require_complete=True)
    assert report["status"] == "FAIL"
    assert "unsupported exact_values" in report["hard_errors"][0]["error"]


def test_instruction_contamination_in_required_claim_is_rejected():
    src = source()
    row = result(src)
    row["record"]["gold"]["required_claims"] = [
        "지원 대상은 10명이어야 한다: If malformed? Actually JSON string"
    ]
    report = VALIDATOR.validate_rows([src], [row], require_complete=True)
    assert report["status"] == "FAIL"
    assert "claim contamination" in report["hard_errors"][0]["error"]


def test_overlong_required_claim_is_rejected():
    src = source()
    row = result(src)
    row["record"]["gold"]["required_claims"] = ["정상 주장 " * 40]
    report = VALIDATOR.validate_rows([src], [row], require_complete=True)
    assert report["status"] == "FAIL"
    assert "too_long" in report["hard_errors"][0]["error"]


def test_combining_mark_contamination_in_claim_is_rejected():
    src = source()
    row = result(src)
    row["record"]["gold"]["required_claims"] = ["지원 대상은 10명이다.\u0304\u0304\u0304"]
    report = VALIDATOR.validate_rows([src], [row], require_complete=True)
    assert report["status"] == "FAIL"
    assert "combining_mark_contamination" in report["hard_errors"][0]["error"]


def test_synthesis_schema_allows_eight_partitioned_claims():
    assert AUTHOR.SCHEMA["schema"]["properties"]["required_claims"]["maxItems"] == 8


def test_hanja_speaker_has_hangul_display_name():
    repo = Path(__file__).parents[2]
    mapping = AUTHOR.load_speaker_display_names(repo)
    assert mapping[AUTHOR.unicodedata.normalize("NFKC", "\uf9c9榮夏")] == "유영하"
    assert mapping[AUTHOR.unicodedata.normalize("NFKC", "\uf9e1憲昇")] == "이헌승"


def test_hangul_speaker_alias_satisfies_hanja_question_scope():
    src = source()
    src["speaker"] = "\uf9c9榮夏"
    row = result(src)
    row["record"]["question"] = "2024년 1월 2일 정무위원회에서 유영하 위원의 지원 대상은 몇 명인가?"
    row["record"]["gold"]["evidence"][0]["speaker"] = src["speaker"]
    report = VALIDATOR.validate_rows([src], [row])
    assert report["review_flags"] == []


def test_missing_question_scope_is_flagged_for_review():
    src = source()
    row = result(src)
    row["record"]["question"] = "지원 대상은 몇 명인가?"
    report = VALIDATOR.validate_rows([src], [row])
    assert report["status"] == "PASS"
    assert report["review_flags"]


def test_metadata_year_in_answer_is_supported():
    src = source()
    row = result(src)
    row["record"]["gold"]["answer"] = "2024년 회의에서 홍길동 위원은 지원 대상이 10명이라고 설명했다."
    report = VALIDATOR.validate_rows([src], [row])
    assert report["review_flags"] == []


def test_date_inside_source_text_is_supported_exact_value():
    src = source()
    src["text"] += " 과거 기준일은 2022년 6월 6일이었다."
    row = result(src)
    row["record"]["gold"]["evidence"][0]["quote"] = src["text"]
    row["record"]["gold"]["exact_values"].append({"type": "date", "value": "2022년 6월 6일"})
    report = VALIDATOR.validate_rows([src], [row])
    assert report["status"] == "PASS"


def test_short_and_full_year_are_equivalent_in_answer_review():
    src = source()
    src["text"] += " 22년도 예산을 설명했다."
    assert VALIDATOR._unsupported_answer_numbers("2022년도 예산이었다.", src) == []


def test_last_year_is_resolved_from_meeting_date():
    src = source()
    src["text"] += " 작년 12월까지 근무했다."
    assert VALIDATOR._unsupported_answer_numbers("2023년까지 근무했다.", src) == []


def test_materializer_requires_all_explicit_approvals():
    src = source()
    try:
        MATERIALIZER.materialize([src], [result(src)], [], set())
    except ValueError as exc:
        assert "exact 120" in str(exc)
    else:
        raise AssertionError("incomplete materialization must fail")


def negative_record():
    return {
        "id": "blind-091",
        "question": "2024-01-02 정무위원회 회의에서 홍길동 위원은 어떤 발언을 했는가?",
        "filters": {"committee": "정무위", "date_from": "2024-01-02", "date_to": "2024-01-02"},
    }


def negative_proof():
    return {
        "id": "blind-091", "committee": "정무위원회", "committee_filter": "정무위",
        "date": "2024-01-02", "speaker": "홍길동",
        "target_source_ids": ["source-1"], "target_chunk_count": 10,
        "speaker_chunk_count_in_target": 0,
    }


def test_negative_control_filter_must_match_proof():
    records = [negative_record() for _ in range(10)]
    proofs = [negative_proof() for _ in range(10)]
    for index, (record, proof) in enumerate(zip(records, proofs), 91):
        record["id"] = proof["id"] = f"blind-{index:03d}"
    records[0]["filters"]["committee"] = "정무위원회"
    try:
        MATERIALIZER.validate_negative_controls(records, proofs)
    except ValueError as exc:
        assert "filter" in str(exc)
    else:
        raise AssertionError("full-name API filter must not pass")


def test_negative_control_requires_zero_speaker_chunks():
    records = [negative_record() for _ in range(10)]
    proofs = [negative_proof() for _ in range(10)]
    for index, (record, proof) in enumerate(zip(records, proofs), 91):
        record["id"] = proof["id"] = f"blind-{index:03d}"
    proofs[0]["speaker_chunk_count_in_target"] = 1
    try:
        MATERIALIZER.validate_negative_controls(records, proofs)
    except ValueError as exc:
        assert "present" in str(exc)
    else:
        raise AssertionError("answerable negative control must fail")


def test_dev_negative_controls_support_exact_two_records():
    records = [negative_record(), negative_record()]
    proofs = [negative_proof(), negative_proof()]
    for index, (record, proof) in enumerate(zip(records, proofs), 19):
        record["id"] = proof["id"] = f"dev-{index:03d}"
    MATERIALIZER.validate_negative_controls(records, proofs, expected_count=2)


def test_approval_requires_all_four_checks(tmp_path):
    path = tmp_path / "approvals.jsonl"
    path.write_text(json.dumps({
        "id": "blind-001", "status": "approved", "reviewer": "reviewer",
        "reviewed_at": "2026-08-18T01:00:00+09:00",
        "checks": {
            "question_scope": True, "answer_supported": True,
            "claims_match_question": False, "exact_values_supported": True,
        },
    }) + "\n", encoding="utf-8")
    try:
        MATERIALIZER.approved_ids(path)
    except ValueError as exc:
        assert "all review checks" in str(exc)
    else:
        raise AssertionError("unchecked approval must fail")


def test_negative_source_cannot_overlap_answerable_queue():
    dev = []
    queue = [{"source_id": "same-source"}]
    proofs = [{"target_source_ids": ["same-source"]}]
    try:
        MATERIALIZER.validate_negative_source_disjoint(dev, queue, proofs)
    except ValueError as exc:
        assert "answerable" in str(exc)
    else:
        raise AssertionError("negative source overlap must fail")
