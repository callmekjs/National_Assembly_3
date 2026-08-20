from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import search_hybrid  # noqa: E402


def _hit(chunk_id: str, *, score: float = 0.8) -> dict:
    return {
        "chunk_id": chunk_id,
        "speaker": "홍길동",
        "role": "위원",
        "committee": "정무위",
        "meeting_date": "2024-01-01",
        "page_start": 1,
        "snippet": "근거",
        "is_short": False,
        "score": score,
    }


def test_hybrid_search_records_stage_rank_trace_without_changing_results(monkeypatch):
    keyword = [_hit("chunk-a"), _hit("chunk-b")]
    vector = [_hit("chunk-b", score=0.91), _hit("chunk-c", score=0.82)]
    monkeypatch.setattr(search_hybrid, "keyword_search", lambda *args, **kwargs: keyword)
    monkeypatch.setattr(search_hybrid, "vector_search", lambda *args, **kwargs: vector)
    monkeypatch.setattr(search_hybrid, "reranker_enabled", lambda: False)

    results = search_hybrid.hybrid_search("질문입니다", committee="정무위", limit=2)

    assert [item["chunk_id"] for item in results] == ["chunk-b", "chunk-a"]
    trace = results.trace
    assert [item["chunk_id"] for item in trace["keyword_candidates"]] == ["chunk-a", "chunk-b"]
    assert [item["chunk_id"] for item in trace["vector_candidates"]] == ["chunk-b", "chunk-c"]
    assert trace["rrf_candidates"][0]["chunk_id"] == "chunk-b"
    assert [item["chunk_id"] for item in trace["final_results"]] == ["chunk-b", "chunk-a"]
    assert trace["filters"] == {
        "committees": ["정무위"], "date_from": None, "date_to": None,
    }
