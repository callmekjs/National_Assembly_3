"""재순위(reranker) — Cohere Rerank 로 하이브리드 상위 후보를 재정렬 (A+ 로드맵 기준 2).

동기 (2026-07-07 답변 평가셋 실측): 답변 실패 9건이 전부 comparison/
speaker_confusion/multi_chunk — 여러 근거를 종합할 때 관련 낮은 근거가
상위에 섞여 LLM 이 없는 입장을 지어내는 패턴. 더 정확한 근거를 위로
올리면(reranker) 이 환각을 줄일 수 있다는 가설.

설계:
    - 기본 OFF. RERANKER_ENABLED=1 + COHERE_API_KEY 있을 때만 동작 (없으면 무해 통과).
    - 하이브리드 RRF 상위 RERANK_CANDIDATES 개를 재순위 → 상위 limit 개 선택.
    - 재순위 실패(네트워크·키 오류)는 원래 순위로 폴백 — 검색이 멈추지 않게.
    - rerank_score 를 결과에 남겨 디버그·평가에 활용.
"""

import os

_MODEL = "rerank-v3.5"            # 한국어 포함 다국어 지원
RERANK_CANDIDATES = 30           # 재순위에 넣을 후보 수 (RRF 상위)
_MAX_DOC_CHARS = 2000            # 문서당 길이 상한 (토큰·비용 보호)

_client = None


def is_enabled() -> bool:
    return os.environ.get("RERANKER_ENABLED") == "1" and bool(os.environ.get("COHERE_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        import cohere
        _client = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
    return _client


def _doc_text(hit: dict) -> str:
    """재순위용 문서 텍스트 — 발언자·위원회·본문(스니펫)으로 구성."""
    who = hit.get("speaker") or ""
    role = hit.get("role") or ""
    com = hit.get("committee") or ""
    body = (hit.get("snippet") or hit.get("text") or "")[:_MAX_DOC_CHARS]
    return f"[{com}] {who} {role}: {body}".strip()


def rerank(query: str, hits: list[dict], limit: int) -> list[dict]:
    """hits 를 query 관련도로 재정렬해 상위 limit 개 반환.

    비활성/실패 시 원래 순서의 상위 limit 개를 그대로 반환 (무해 폴백).
    """
    if not is_enabled() or len(hits) <= 1:
        return hits[:limit]

    candidates = hits[:RERANK_CANDIDATES]
    try:
        resp = _get_client().rerank(
            model=_MODEL,
            query=query,
            documents=[_doc_text(h) for h in candidates],
            top_n=min(limit, len(candidates)),
        )
    except Exception as e:
        # 네트워크·인증·쿼터 오류는 검색을 막지 않는다 — 원래 순위로 폴백
        print(f"[reranker] 재순위 실패, 원순위 폴백: {type(e).__name__}: {e}")
        return hits[:limit]

    reranked = []
    for r in resp.results:
        hit = dict(candidates[r.index])
        hit["rerank_score"] = round(r.relevance_score, 5)
        reranked.append(hit)
    return reranked
