from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "render_gold_review.py"
SPEC = importlib.util.spec_from_file_location("render_gold_review_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_review_packet_starts_every_item_pending():
    answerable = [{"record": {
        "id": "blind-001", "category": "fact", "question": "무엇인가?", "answerable": True,
        "gold": {
            "answer": "정답", "required_claims": ["정답"], "forbidden_claims": ["오답"],
            "exact_values": [
                {"type": "number", "value": "10명"},
                {"type": "person", "value": "홍길동"},
            ],
            "required_exact_values": [{"type": "number", "value": "10명"}],
            "reference_values": [{"type": "person", "value": "홍길동"}],
            "evidence": [{"committee": "정무위원회", "date": "2024-01-01", "speaker": "홍길동", "role": "위원", "quote": "충분히 긴 원문입니다."}],
        },
    }}]
    negative = [{
        "id": "blind-091", "category": "unanswerable", "question": "발언했는가?", "answerable": False,
        "gold": {
            "answer": None, "required_claims": [], "forbidden_claims": ["발언했다"],
            "exact_values": [], "evidence": [], "refusal_reason": "발언 없음",
        },
    }]
    proof = [{"id": "blind-091", "speaker_chunk_count_in_target": 0}]
    markdown, checklist = MODULE.render(answerable, negative, proof)
    assert "blind-001" in markdown and "blind-091" in markdown
    assert "답변 필수값" in markdown and "원문 참고값" in markdown
    assert {"required_values_match_question", "reference_values_supported"}.issubset(checklist[0]["checks"])
    assert all(item["status"] == "pending" for item in checklist)
    assert all(not any(item["checks"].values()) for item in checklist)
