"""재순위(reranker) — LLM(OpenAI) listwise rerank 로 하이브리드 상위 후보 재정렬.
(A+ 로드맵 기준 2)

동기 (2026-07-07 답변 평가셋 실측): 답변 실패 9건이 전부 comparison/
speaker_confusion/multi_chunk — 여러 근거를 종합할 때 관련 낮은 근거가
상위에 섞여 LLM 이 없는 입장을 지어내는 패턴. 더 정확한 근거를 위로
올리면(reranker) 이 환각을 줄일 수 있다는 가설.

방식 선택: 별도 rerank API(Cohere 등) 없이 이미 있는 OpenAI 키 재사용.
후보 30개를 번호 매겨 LLM 에 주고 관련도 순 번호 목록만 받는다(listwise).
전용 reranker 모델보다 정확도는 낮을 수 있으나 추가 의존성·키가 없다.

설계:
    - 기본 OFF. RERANKER_ENABLED=1 + OPENAI_API_KEY 있을 때만 동작.
    - 실패(파싱·네트워크)는 원래 순위로 폴백 — 검색이 멈추지 않게.
    - rerank_rank 를 결과에 남겨 디버그·평가에 활용.
"""

import json
import os
import re

_MODEL = "gpt-4o-mini"           # 재순위 판정 — 답변 생성과 동일 모델(추가 비용 최소)
RERANK_CANDIDATES = 30           # 재순위에 넣을 후보 수 (RRF 상위)
_MAX_DOC_CHARS = 600             # 후보당 스니펫 길이 (토큰·비용 보호)

_client = None


def is_enabled() -> bool:
    return os.environ.get("RERANKER_ENABLED") == "1" and bool(os.environ.get("OPENAI_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        from search_vector import _get_client as _oc
        _client = _oc()
    return _client


def _doc_line(i: int, hit: dict) -> str:
    who = hit.get("speaker") or ""
    role = hit.get("role") or ""
    com = hit.get("committee") or ""
    date = str(hit.get("meeting_date") or "")
    body = (hit.get("snippet") or hit.get("text") or "")[:_MAX_DOC_CHARS]
    return f"[{i}] ({com} {date}) {who} {role}: {body}".strip()


_SYSTEM = """당신은 검색 재순위 도우미다. 질문과 번호 매긴 근거 목록이 주어진다.
각 근거가 질문에 '직접' 답하는 데 얼마나 관련 있는지로 재정렬하라.
- 질문이 특정 인물·기관·시점을 지목하면 그 대상의 발언을 우선한다.
- 주제만 비슷하고 대상이 다른 근거는 뒤로 보낸다.
- 근거를 새로 만들거나 번호를 바꾸지 말고, 주어진 번호만 재배열한다.
반드시 아래 JSON 만 출력: {"order":[관련도 높은 순 번호 목록 전체]}"""


def rerank(query: str, hits: list[dict], limit: int) -> list[dict]:
    """hits 를 query 관련도로 재정렬해 상위 limit 개 반환.
    비활성/실패 시 원래 순서의 상위 limit 개를 그대로 반환 (무해 폴백)."""
    if not is_enabled() or len(hits) <= 1:
        return hits[:limit]

    candidates = hits[:RERANK_CANDIDATES]
    docs = "\n".join(_doc_line(i, h) for i, h in enumerate(candidates))
    try:
        resp = _get_client().chat.completions.create(
            model=_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": f"질문: {query}\n\n근거 목록:\n{docs}"}],
        )
        order = json.loads(resp.choices[0].message.content).get("order", [])
    except Exception as e:
        print(f"[reranker] 재순위 실패, 원순위 폴백: {type(e).__name__}: {e}")
        return hits[:limit]

    # 유효 번호만, 중복 제거, 누락분은 원순위로 보충 (LLM 이 일부를 빠뜨려도 안전)
    seen, ordered = set(), []
    for idx in order:
        if isinstance(idx, int) and 0 <= idx < len(candidates) and idx not in seen:
            seen.add(idx)
            ordered.append(idx)
    for idx in range(len(candidates)):
        if idx not in seen:
            ordered.append(idx)

    out = []
    for new_rank, idx in enumerate(ordered[:limit], start=1):
        hit = dict(candidates[idx])
        hit["rerank_rank"] = new_rank
        out.append(hit)
    return out
