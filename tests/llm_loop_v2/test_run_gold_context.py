from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "llm_loop_v2" / "run_gold_context.py"
SPEC = importlib.util.spec_from_file_location("run_gold_context_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_gold_evidence_maps_to_product_hit_without_gold_answer():
    evidence = {
        "chunk_id": "chunk-1", "source_id": "source-1", "speaker": "홍길동",
        "role": "위원", "committee": "정무위", "date": "2025-01-01",
        "page_start": 3, "quote": "직접 근거 원문",
    }
    hit = MODULE.gold_hit(evidence)
    assert hit["chunk_id"] == "chunk-1"
    assert hit["snippet"] == "직접 근거 원문"
    assert "answer" not in hit and "required_claims" not in hit


def test_gold_context_applies_product_grounding_and_verification_downgrade(monkeypatch):
    monkeypatch.setattr(MODULE.grounding, "judge", lambda response: ("FULL", False))
    response = {"verification": {"flags": ["unsupported_number"]}}
    result = MODULE.apply_product_grounding(response)
    assert result["grounding"] == "PARTIAL"
    assert "ungrounded" not in result


def test_gold_context_preserves_ungrounded_warning(monkeypatch):
    monkeypatch.setattr(MODULE.grounding, "judge", lambda response: ("PARTIAL", True))
    result = MODULE.apply_product_grounding({"verification": {"flags": []}})
    assert result["grounding"] == "PARTIAL"
    assert result["ungrounded"] is True


def test_direct_gold_context_initializes_and_closes_db_pool(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(MODULE, "load_dotenv", lambda path: calls.append(("env", path)))
    monkeypatch.setattr(MODULE.db, "init_pool", lambda: calls.append(("init", None)))
    monkeypatch.setattr(MODULE.db, "close_pool", lambda: None)
    monkeypatch.setattr(MODULE.atexit, "register", lambda fn: calls.append(("close", fn)))
    MODULE.initialize_runtime(tmp_path)
    assert calls[0] == ("env", tmp_path / ".env")
    assert calls[1][0] == "init"
    assert calls[2][0] == "close"
