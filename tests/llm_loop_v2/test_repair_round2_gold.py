from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "repair_round2_gold.py"
SPEC = importlib.util.spec_from_file_location("repair_round2_gold_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def row(record_id: str):
    return {
        "id": record_id,
        "record": {
            "question": "기존 질문",
            "gold": {"answer": "기존 답", "required_claims": [], "forbidden_claims": [], "exact_values": []},
            "provenance": {},
        },
    }


def test_round2_repairs_remove_contamination_and_scope_questions():
    ids = set(MODULE.EXACT_REPLACEMENTS) | {
        "blind-026", "blind-044", "blind-080", "blind-090", "reserve-030"
    }
    repaired = MODULE.repair([row(record_id) for record_id in ids])
    by_id = {item["id"]: item["record"] for item in repaired}
    assert by_id["blind-026"]["question"].startswith("2025년 11월 27일")
    assert by_id["blind-044"]["question"].startswith("2025년 4월 23일")
    assert by_id["reserve-003"]["gold"]["exact_values"][-1]["value"] == "2212384"
    assert "1200만 원" not in by_id["blind-008"]["gold"]["answer"]
    assert "º" not in str(by_id["blind-080"])
    assert "향후 20년 이상" in by_id["blind-090"]["question"]
    assert "관동대지진" in by_id["reserve-030"]["question"]
    assert all("wait invalid" not in str(item) for item in repaired)
