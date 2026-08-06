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
import threading

# 재순위 모델 (2026-08-07 실측으로 채택). 6종 비교에서 nDCG@5 최고:
#   mini 0.697 / terra 0.720 / luna 0.751 / sol 0.768 / sol+low 0.778
# 답변 생성(answer.py)은 여전히 gpt-4o-mini — 재순위만 교체했다.
_MODEL = os.environ.get("RERANKER_MODEL") or "gpt-5.6-sol"
RERANK_CANDIDATES = 30           # 재순위에 넣을 후보 수 (RRF 상위)
# 사고량을 낮추는 쪽이 품질·지연 모두 낫다 (실측: sol 0.768→0.778, 20.0→12.3초/질의).
# 재순위는 후보 30개를 줄 세우는 단순 작업이라 깊은 추론이 필요 없고, 과도한 사고가
# 오히려 판단을 흐린다. "더 주면 낫다"가 반증된 두 번째 사례(첫째는 스니펫 1200자).
_REASONING_EFFORT = os.environ.get("RERANKER_EFFORT") or "low"   # 추론형 모델 전용
_MAX_DOC_CHARS = 600             # 후보당 스니펫 길이 — search_* 의 left(ch.text, N) 과 같아야 한다
                                 # (1200 시도 → nDCG@5 0.710→0.653, 비용 +59%. 되돌림)

_client = None

# 재순위 호출의 토큰 사용량 (2026-08-04) — 이걸 안 재면 query_logs 의
# est_cost_usd 가 답변 LLM 비용만 담고, guard 의 일별 상한($1)이 실지출의
# 절반만 보고 판단한다. 실측: 답변 $0.00078 / 재순위 $0.00146 per 질의.
# 요청 스레드 단위 저장 — FastAPI 동기 핸들러는 hybrid_search·generate_answer 가
# 같은 스레드에서 돌기 때문에 threading.local 로 충분하다.
_local = threading.local()


def last_usage() -> dict | None:
    """직전 rerank 호출의 토큰 사용량. 호출 안 됐거나 실패면 None."""
    return getattr(_local, "usage", None)


def reset_usage() -> None:
    """요청 시작 시 초기화 — 같은 스레드의 이전 요청 값이 새 나가지 않게."""
    _local.usage = None


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

앞으로 보낼 것:
- 질문이 특정 인물·기관·시점을 지목하면 그 대상의 발언을 우선한다.
- 질문이 요구한 것을 실제로 담은 발언 — 수치를 물으면 수치가 있는 것,
  입장을 물으면 주장이 담긴 것, 인용을 물으면 인용할 만한 문장이 있는 것.

뒤로 보낼 것:
- **사건·사안이 다른 근거.** 표현이 겹쳐도 다른 사건이면 뒤로 보낸다.
  예: "티메프 사태 피해자 구제" 질문에 전세사기 피해주택 매입 발언은 '피해자 구제'가
  겹쳐도 다른 사건이므로 뒤다. 이것이 가장 흔한 오배치다.
- **의사진행·절차 발언.** "예, 그렇습니다", "수고하셨습니다", 호명, 개회·산회 선언,
  의사일정 낭독, 인사말은 주제어가 들어 있어도 답이 아니므로 뒤로 보낸다.
- 주제만 비슷하고 대상이 다른 근거.

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
        # temperature=0 을 안 받는 모델이 있다 (gpt-5.6 계열은 기본값 1만 허용 —
        # 2026-08-06 실측 400 BadRequest). 지원 모델에만 붙여 결정성을 유지하고,
        # 그 외에는 생략해 호출 자체가 실패하지 않게 한다.
        kw = {"temperature": 0} if _MODEL.startswith(("gpt-4", "gpt-3")) else {}
        # 추론형 모델은 사고 토큰이 지연·비용을 지배한다. 재순위는 30개를 줄 세우는
        # 단순 작업이라 깊은 추론이 필요 없다 — 실험용 노브로 열어 둔다.
        if _REASONING_EFFORT and not _MODEL.startswith(("gpt-4", "gpt-3")):
            kw["reasoning_effort"] = _REASONING_EFFORT
        resp = _get_client().chat.completions.create(
            model=_MODEL,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": f"질문: {query}\n\n근거 목록:\n{docs}"}],
            **kw,
        )
        # 파싱보다 먼저 기록 — 파싱이 실패해 원순위로 폴백해도 호출 비용은 이미 나갔다
        _local.usage = {"input_tokens": resp.usage.prompt_tokens,
                        "output_tokens": resp.usage.completion_tokens}
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
