"""
하이브리드 검색 (RAG-4) — 키워드 축 + 벡터 축을 RRF 로 융합.

왜 하이브리드인가 (ETL-8 실측):
  - 벡터 단독: 주제형("공영방송 지배구조")은 정확하나 고유명사형("AI 기본법"·"티메프")은
    데이터에 229·528청크가 있는데도 못 찾음
  - 키워드 단독: 고유명사는 정확하나 표현이 다른 주제 질문("정부 입장 변화")에 약함

융합: Weighted RRF (Reciprocal Rank Fusion, k=60)
  - 두 축의 점수 눈금이 달라(키워드 정수 vs 코사인 0~1) 직접 합산 불가
  - 순위만 사용: score = Σ weight × 1/(k + rank)
  - 국회 회의록은 인물명·기관명·법안명·사건명 등 고유명사 질문 비중이 높아
    키워드 축을 살짝 더 신뢰한다 (KEYWORD_WEIGHT 1.2 — 2026-07-02 개선)
  - 질문 유형별 자동 가중치는 아직 하지 않는다 (고정 가중치 먼저, eval 후 판단)

후처리:
  - is_short 페널티 ×0.9 ("예." 같은 의사진행 발언의 상위 독식 방지)
  - 동일 turn 중복 제거 (긴 발언이 쪼개진 조각들 중 최고 순위만)

디버그 필드: found_in, kw_rank, vec_rank, rrf_before_penalty, rrf(최종)
"""

import re
from concurrent.futures import ThreadPoolExecutor

from query_parser import extract_filters
from reranker import (RERANK_CANDIDATES, is_enabled as reranker_enabled, rerank,
                      reset_usage as reset_reranker_usage)
from search_keyword import keyword_search
from search_vector import vector_search

# 두 축 병렬 실행용 (2026-07-07): 순차 호출이 응답의 큰 몫이었다 — 실측 평균
# 3.08s → 병렬 후 아래 hybrid_search 참조. 워커 2면 충분 (요청당 축 2개).
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="search")

RRF_K = 60
KEYWORD_WEIGHT = 1.2  # 키워드 축 가중치 (고유명사 중심 도메인 특성)
VECTOR_WEIGHT = 1.0   # 벡터 축 가중치
SHORT_PENALTY = 0.9   # is_short 청크의 최종 점수 배율
K_EACH = 30           # 각 축에서 가져올 후보 수

# 진영 균형이 필요한 질문. answer._PARTY_QUESTION 과 달리 "소속"은 넣지 않는다 —
# "○○ 위원의 소속 위원회는?" 같은 단일 인물 질문까지 균형 배분에 걸리기 때문.
_SIDE_QUESTION = re.compile(r"여야|여당|야당|진영|정당별|당별")


def _balance_by_side(ranked: list[dict], limit: int) -> list[dict]:
    """여야 비교 질문용 — 진영별 상한으로 근거를 나눠 담는다.

    왜 필요한가 (2026-08-07 실측, 평가셋 n010):
        "티메프 사태의 정부 책임을 두고 여야 시각이 어떻게 달랐나" 질문에서 정무위
        결과 3건이 **전부 더불어민주당**이었다. 코퍼스에는 같은 위원회에 국민의힘
        발언이 74건/7명 있었는데도 상위에 한 진영만 올라와, 비교 자체가 불가능한
        근거를 받은 LLM 이 다른 위원회 발언을 끌어다 메웠다.
        발언량이 많은 쪽이 상위를 독식하는 것은 위원회에서 이미 겪은 문제이고
        (_balance_by_committee, 2026-07-03), 진영도 같은 구조다.

    committee 균형과 같은 규칙: 순위는 유지하고 자리만 배분한다. 한쪽 진영에 근거가
    부족하면 다른 쪽이 넘겨받으므로, 한 진영만 말한 사안에서 억지 균형을 만들지 않는다.
    """
    from party import party_bloc         # 순환 import 방지 — 이 경로에서만 필요

    bloc_of = {e["chunk_id"]: party_bloc(e.get("speaker"), e.get("role")) for e in ranked}
    blocs: set[str] = {b for b in bloc_of.values() if b}
    if len(blocs) < 2:                    # 애초에 한쪽뿐이면 손대지 않는다
        return ranked[:limit]

    rank_of = {e["chunk_id"]: i for i, e in enumerate(ranked)}
    quota = max(1, limit // (len(blocs) + 1))   # +1: 정부측·증인 몫을 남긴다
    count = {b: 0 for b in blocs}
    picked, leftover = [], []
    for e in ranked:
        b = bloc_of[e["chunk_id"]]
        if b and count[b] < quota:
            picked.append(e)
            count[b] += 1
        else:
            leftover.append(e)
    for e in leftover:
        if len(picked) >= limit:
            break
        picked.append(e)
    picked.sort(key=lambda e: rank_of[e["chunk_id"]])
    return picked[:limit]


def _balance_by_committee(ranked: list[dict], committees: list[str], limit: int) -> list[dict]:
    """들어온 순위를 유지하되 위원회별 상한(quota)으로 근거를 나눠 담는다.

    각 위원회가 quota 만큼 우선 확보하고, 남는 자리는 전체 순위대로 채운다
    (한 위원회에 근거가 부족하면 다른 위원회가 자리를 넘겨받음).

    마지막 정렬 키는 **입력 순서**다. rrf 로 재정렬하면 상위에서 리랭커가 매긴
    순서가 통째로 버려진다 — 리랭커 9위가 [2]번 자리에 오던 문제(2026-08-04 실측).
    [n] 번호는 LLM 의 주의 배분에 직결되므로 무해하지 않고, 단일 주제 경로
    (rerank 순 유지)와 동작이 갈리는 비일관성도 생긴다.
    입력 순서를 쓰면 리랭커 ON=리랭커 순 / OFF=RRF 순이 자동으로 따라온다.
    """
    rank_of = {e["chunk_id"]: i for i, e in enumerate(ranked)}
    quota = max(1, limit // len(committees))
    count = {c: 0 for c in committees}
    picked, leftover = [], []
    for e in ranked:
        c = e.get("committee")
        if c in count and count[c] < quota:
            picked.append(e)
            count[c] += 1
        else:
            leftover.append(e)
    for e in leftover:
        if len(picked) >= limit:
            break
        picked.append(e)
    picked.sort(key=lambda e: rank_of[e["chunk_id"]])
    return picked[:limit]


def hybrid_search(
    q: str,
    committee: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """키워드+벡터 RRF 융합 검색. RAG-6 답변 생성의 근거 공급원."""
    # 재순위 비용 집계 초기화 — 이 검색에서 실제로 쓴 값만 이번 질의에 달리게
    reset_reranker_usage()

    # 질문에서 날짜·위원회 추출 → 필터로 변환 (호출자가 명시하지 않은 경우만)
    # eval 실측: 날짜는 본문에 없어 필터 없이는 date_based 질문 Recall 0.00
    cleaned_q, auto_committees, auto_from, auto_to = extract_filters(q)
    committees = [committee] if committee else auto_committees  # 명시 필터가 우선
    date_from = date_from or auto_from
    date_to = date_to or auto_to

    # 두 축 병렬 실행 — 키워드(trgm 스캔)와 벡터(임베딩 API + HNSW)는 독립적
    kw_future = _executor.submit(
        keyword_search, cleaned_q, committees, date_from, date_to, K_EACH
    )
    vec_hits = vector_search(cleaned_q, committees, date_from, date_to, limit=K_EACH)
    kw_hits = kw_future.result()

    # Weighted RRF 합산 (chunk_id 기준)
    fused: dict[str, dict] = {}
    for source_name, weight, hits in (
        ("keyword", KEYWORD_WEIGHT, kw_hits),
        ("vector", VECTOR_WEIGHT, vec_hits),
    ):
        for rank, hit in enumerate(hits, start=1):
            cid = hit["chunk_id"]
            entry = fused.setdefault(
                cid,
                {**hit, "rrf": 0.0, "found_in": [], "kw_rank": None, "vec_rank": None,
                 "vec_score": None},
            )
            entry["rrf"] += weight * (1.0 / (RRF_K + rank))
            entry["found_in"].append(source_name)
            entry["kw_rank" if source_name == "keyword" else "vec_rank"] = rank
            if source_name == "vector":
                # 벡터 축 원점수(코사인 유사도) 보존 — RAG-7 Grounding 사전차단의 근거
                entry["vec_score"] = round(hit["score"], 4)

    # is_short 페널티 (페널티 전 점수는 디버그용으로 보존)
    for entry in fused.values():
        entry["rrf_before_penalty"] = entry["rrf"]
        if entry.get("is_short"):
            entry["rrf"] *= SHORT_PENALTY

    # 동일 turn 중복 제거 — 같은 발언의 조각 중 RRF 최고만 유지
    # (chunk_id = {turn_id}_chunk_{nnn} 형식)
    best_per_turn: dict[str, dict] = {}
    for entry in fused.values():
        turn_id = entry["chunk_id"].rsplit("_chunk_", 1)[0]
        cur = best_per_turn.get(turn_id)
        if cur is None or entry["rrf"] > cur["rrf"]:
            best_per_turn[turn_id] = entry

    ranked = sorted(best_per_turn.values(), key=lambda e: e["rrf"], reverse=True)

    # 복수 위원회 질문이면 위원회별 근거 균형 배분 — 발언량 많은 위원회가
    # 상위를 독식하면 "A위와 B위 비교" 질문이 반쪽 답변이 된다 (2026-07-03 실측)
    if committees and len(committees) > 1:
        # 재순위(켜져 있으면) 후 균형 배분 — 각 위원회 quota 는 유지하되 관련도 반영
        pool = rerank(cleaned_q, ranked, RERANK_CANDIDATES) if reranker_enabled() else ranked
        results = _balance_by_committee(pool, committees, limit)
    elif _SIDE_QUESTION.search(q):
        # 여야·정당 비교 질문 — 진영별 균형 배분 (위원회 균형과 같은 이유).
        # 위원회를 지목하지 않은 질문은 위 분기를 안 타므로 여기서 처리한다.
        pool = rerank(cleaned_q, ranked, RERANK_CANDIDATES) if reranker_enabled() else ranked
        results = _balance_by_side(pool, limit)
    elif reranker_enabled():
        # 단일 주제: RRF 상위 후보를 재순위해 상위 limit 선택
        results = rerank(cleaned_q, ranked, limit)
    else:
        results = ranked[:limit]

    # 응답 정리: 융합 근거를 남기고 원 점수(score)는 혼동 방지 위해 제거
    for e in results:
        e["rrf"] = round(e["rrf"], 5)
        e["rrf_before_penalty"] = round(e["rrf_before_penalty"], 5)
        e["found_in"] = "+".join(sorted(set(e["found_in"])))
        e.pop("score", None)
    return results
