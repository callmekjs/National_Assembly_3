from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "score_deterministic.py"
SPEC = importlib.util.spec_from_file_location("score_deterministic_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def unanswerable_record():
    return {
        "id": "blind-091",
        "answerable": False,
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {"evidence": [], "exact_values": []},
    }


def test_unanswerable_requires_explicit_refusal():
    scored = MODULE.score(unanswerable_record(), {
        "http_status": 200,
        "response": {"answer": "관련 정책을 설명하겠습니다.", "citations": []},
    })
    assert "missing_required_refusal" in scored["auto_fail_reasons"]


def test_unanswerable_refusal_with_no_citations_can_continue_to_semantic_review():
    scored = MODULE.score(unanswerable_record(), {
        "http_status": 200,
        "response": {"answer": "제공된 회의록에서 확인할 수 없습니다.", "citations": []},
    })
    assert scored["stage1_verdict"] == "SEMANTIC_REVIEW_REQUIRED"


def test_exact_date_accepts_korean_format():
    assert MODULE.exact_value_present(
        {"type": "date", "value": "2024-01-02"},
        "2024년 1월 2일 회의에서 밝혔다.",
    )


def test_context_person_is_left_to_semantic_judge():
    record = {
        "id": "blind-001",
        "answerable": True,
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {
            "evidence": [{"source_id": "source-1", "chunk_id": "source-1_turn_1", "speaker": "홍길동"}],
            "exact_values": [{"type": "person", "value": "홍길동"}],
        },
    }
    result = {
        "http_status": 200,
        "response": {
            "answer": "지원 확대가 필요하다고 밝혔다. [1]",
            "citations": [{
                "chunk_id": "source-1_turn_1", "speaker": "홍길동",
                "committee": "정무위", "date": "2024-01-01",
            }],
        },
    }
    assert MODULE.score(record, result)["stage1_verdict"] == "SEMANTIC_REVIEW_REQUIRED"


def test_full_committee_name_citation_matches_short_filter():
    record = _answerable_numeric_record(required=[], reference=[])
    record["gold"]["evidence"][0]["committee"] = "정무위원회"
    result = _cited_result("정답 내용을 설명했습니다. [1]")
    result["response"]["citations"][0]["committee"] = "정무위원회"
    scored = MODULE.score(record, result)
    assert "wrong_committee_citation" not in scored["auto_fail_reasons"]


def test_korean_number_synonyms_are_accepted():
    assert MODULE.number_present("몇백억", "별도 항목에 수백억 원을 편성하자고 했다.")
    assert MODULE.number_present("열 번", "무인기가 열 차례 이상 넘어갔다고 말했다.")
    assert MODULE.number_present("열 번 이상", "최소 열 차례 이상 탐지됐어야 한다.")


def test_different_korean_number_is_not_accepted():
    assert not MODULE.number_present("열 번", "아홉 차례 탐지됐다고 말했다.")


def test_native_counter_and_digit_counter_are_equivalent():
    assert MODULE.number_present("세 대", "필요 없다고 지적한 차량은 3대였습니다.")
    assert MODULE.number_present("3명", "세 명이 합의했다고 설명했습니다.")
    assert MODULE.number_present("두 배", "법정 상한의 2배라고 설명했습니다.")


def test_sino_korean_compound_counter_and_digits_are_equivalent():
    assert MODULE.number_present("백이십 분", "국민 100명과 전문가 20명으로 구성했습니다.")
    assert MODULE.number_present("백이십 분", "국민참여단은 총 120명입니다.")
    assert not MODULE.number_present("백이십 분", "국민참여단은 100명입니다.")


def test_explicit_hanja_hangul_speaker_alias_is_accepted():
    record = _answerable_numeric_record(required=[], reference=[])
    record["gold"]["evidence"][0]["speaker"] = "柳榮夏"
    result = _cited_result("학도 의용군으로 참전했다고 말했습니다. [1]")
    result["response"]["citations"][0]["speaker"] = "柳榮夏(유영하)"
    scored = MODULE.score(record, result)
    assert "gold_speaker_not_cited" not in scored["auto_fail_reasons"]


def test_partial_speaker_name_is_not_accepted():
    assert not MODULE.speaker_matches("김민", "김민수")


def test_short_and_full_year_are_equivalent_inside_number_expression():
    assert MODULE.number_present("25년도 제2회", "2025년도 제2회 추가경정예산안입니다.")


def test_native_number_syllable_inside_normal_word_is_not_a_number():
    assert not MODULE.number_present("3명", "세부 내용을 설명했습니다.")
    assert not MODULE.number_present("3", "세계 시장에 진출했습니다.")


def _answerable_numeric_record(*, required, reference):
    return {
        "id": "blind-001",
        "answerable": True,
        "filters": {"committee": "정무위", "date_from": "2024-01-01", "date_to": "2024-01-01"},
        "gold": {
            "evidence": [{"source_id": "source-1", "chunk_id": "source-1_turn_1", "speaker": "홍길동"}],
            "exact_values": [*required, *reference],
            "required_exact_values": required,
            "reference_values": reference,
        },
    }


def _cited_result(answer):
    return {
        "http_status": 200,
        "response": {
            "answer": answer,
            "citations": [{
                "chunk_id": "source-1_turn_1", "speaker": "홍길동",
                "committee": "정무위", "date": "2024-01-01",
            }],
        },
    }


def test_required_exact_number_is_still_a_hard_requirement():
    value = {"type": "number", "value": "93조 원"}
    scored = MODULE.score(
        _answerable_numeric_record(required=[value], reference=[]),
        _cited_result("재정 부담이 커질 수 있다고 지적했습니다. [1]"),
    )
    assert "missing_exact_number" in scored["auto_fail_reasons"]


def test_reference_number_is_not_a_deterministic_answer_requirement():
    value = {"type": "number", "value": "93조 원"}
    scored = MODULE.score(
        _answerable_numeric_record(required=[], reference=[value]),
        _cited_result("재정 부담이 커질 수 있다고 지적했습니다. [1]"),
    )
    assert "missing_exact_number" not in scored["auto_fail_reasons"]


def test_same_document_but_wrong_evidence_chunk_is_not_accepted():
    record = _answerable_numeric_record(required=[], reference=[])
    record["gold"]["evidence"][0]["chunk_id"] = "source-1_turn_2"
    scored = MODULE.score(
        record,
        _cited_result("정답 내용을 설명했습니다. [1]"),
    )
    assert "gold_evidence_not_cited" in scored["auto_fail_reasons"]
    assert scored["gold_source_cited"] is True
    assert scored["gold_evidence_chunk_cited"] is False


def test_gold_chunk_in_model_visible_turn_context_is_accepted():
    record = _answerable_numeric_record(required=[], reference=[])
    record["gold"]["evidence"][0]["chunk_id"] = "source-1_turn_0001_chunk_002"
    result = _cited_result("정답 내용을 설명했습니다. [1]")
    result["response"]["citations"][0]["chunk_id"] = "source-1_turn_0001_chunk_001"
    result["response"]["citations"][0]["support_chunk_ids"] = [
        "source-1_turn_0001_chunk_001", "source-1_turn_0001_chunk_002"
    ]
    scored = MODULE.score(record, result)
    assert "gold_evidence_not_cited" not in scored["auto_fail_reasons"]
    assert scored["gold_evidence_chunk_cited"] is True
