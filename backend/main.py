import datetime
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAIError
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, Field

from actors import actor_profile
from answer import MODE_CONFIG, NO_EVIDENCE, generate_answer
from db import init_pool, close_pool, get_conn
from grounding import judge, pre_gate
from search_keyword import keyword_search
from search_vector import vector_search
from search_hybrid import hybrid_search


# 구조화 로깅 — print 는 레벨·시각이 없어 운영 중 추적 불가 (A+ 로드맵 기준 7)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("assembly_rag")

# query_logs 저장 실패 카운터 — 로그는 부가 기능이라 답변은 반환하지만,
# 조용히 계속 실패하면 평가 재료가 새는 것 — /health 로 노출해 가시화
_log_failures = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작: connection pool 준비 / 종료: 반납
    init_pool()
    yield
    close_pool()


app = FastAPI(title="국회 RAG API", version="0.2.0", lifespan=lifespan)

# CORS 허용 출처 — 기본은 Vite dev server (localhost/127.0.0.1 어느 쪽으로 열어도
# 동작하게 둘 다). 배포 시 .env 의 BACKEND_CORS_ORIGINS(쉼표 구분)로 교체.
_cors_env = os.environ.get("BACKEND_CORS_ORIGINS", "")
CORS_ORIGINS = (
    [o.strip() for o in _cors_env.split(",") if o.strip()]
    or ["http://localhost:5173", "http://127.0.0.1:5173"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    """요청 ID 부여 + 요청 단위 구조화 로그 — 이상 응답을 로그에서 역추적 가능하게."""
    rid = uuid.uuid4().hex[:8]
    request.state.request_id = rid
    t0 = time.time()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    logger.info(
        "rid=%s %s %s -> %d (%.0fms)",
        rid, request.method, request.url.path, response.status_code,
        (time.time() - t0) * 1000,
    )
    return response


# 날짜는 date 타입으로 받아 잘못된 값("2025-13-01" 등)을 422 로 거른다 — str 로 두면
# SQL 까지 흘러가 500. question 길이 제한은 임베딩 API 한도(8,192토큰)·비용 방어.
class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    mode: Literal["qa", "report"] = "qa"
    committee: str | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None


class AnswerRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    mode: Literal["qa", "report"] = "qa"
    committee: str | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None


class FeedbackRequest(BaseModel):
    query_id: str
    rating: int = Field(ge=1, le=5)  # 프론트: 👍=5, 👎=1
    comment: str | None = Field(None, max_length=2000)


@app.get("/health")
def health():
    """서버 + DB 상태 확인. DB 장애 시에도 200 으로 상태를 알린다 (모니터링용)."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks")
            chunks = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM embeddings_openai")
            embeddings = cur.fetchone()[0]
        return {
            "status": "ok",
            "db": "ok",
            "chunks": chunks,
            "embeddings": embeddings,
            "log_failures": _log_failures,  # query_logs 저장 실패 누적 (0 이 정상)
        }
    except Exception as e:
        return {"status": "degraded", "db": "error", "detail": type(e).__name__}


def _log_query(req: QueryRequest, result: dict, grounding: str, latency_ms: int,
               source_block: str | None = None) -> str | None:
    """query_logs 1행 저장 → query_id. 로그는 부가 기능 — 실패해도 답변은 반환한다.

    source_block: LLM 에 실제로 들어간 근거 블록 — 이상 답변 사후 재현·품질 평가 재료.
    """
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO query_logs
                  (question, mode, committee, date_from, date_to,
                   answer, grounding, citations, invalid_citations, usage, latency_ms,
                   source_block)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING query_id
                """,
                (
                    req.question, result["mode"], req.committee, req.date_from, req.date_to,
                    result["answer"], grounding,
                    json.dumps(result["citations"], ensure_ascii=False),
                    json.dumps(result["invalid_citations"], ensure_ascii=False),
                    json.dumps(result["usage"], ensure_ascii=False) if result["usage"] else None,
                    latency_ms,
                    source_block,
                ),
            )
            return str(cur.fetchone()[0])
    except Exception:
        global _log_failures
        _log_failures += 1
        logger.exception("query_logs 저장 실패 (답변은 정상 반환, 누적 %d회)", _log_failures)
        return None


@app.post("/query")
def query(req: QueryRequest):
    """RAG 파이프라인 통합 (RAG-7) — curl 한 번에 답변+출처+신뢰등급.

    흐름: 하이브리드 검색 1회 → Grounding 사전차단 → (통과 시) 답변 생성(hits 재사용)
         → 사후 판정 → query_logs 저장
    """
    t0 = time.time()
    try:
        # 벡터 검색의 질문 임베딩도 OpenAI 호출 — 장애 시 500 이 아니라 502 로
        hits = hybrid_search(
            req.question, req.committee, req.date_from, req.date_to,
            limit=MODE_CONFIG[req.mode]["limit"],
        )
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"임베딩 호출 실패: {type(e).__name__}")

    gate = pre_gate(hits)
    if gate is not None:
        # NONE(검색 0건) / REFUSED(유사도 미달 + 키워드 0건) — LLM 호출 없이 고정 문구
        result = {
            "answer": NO_EVIDENCE, "mode": req.mode,
            "sources": [], "citations": [], "cited_numbers": [], "invalid_citations": [],
            "usage": None,
        }
        grounding, ungrounded = gate, False
    else:
        try:
            result = generate_answer(
                req.question, req.mode, req.committee, req.date_from, req.date_to, hits=hits
            )
        except OpenAIError as e:
            raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {type(e).__name__}")
        grounding, ungrounded = judge(result)

    latency_ms = int((time.time() - t0) * 1000)
    # 근거 블록은 로그에만 저장 — API 응답에 그대로 내보내면 응답이 수십 KB 로 불어난다
    source_block = result.pop("source_block", None)
    query_id = _log_query(req, result, grounding, latency_ms, source_block)
    logger.info("query_id=%s mode=%s grounding=%s latency_ms=%d", query_id, req.mode, grounding, latency_ms)

    response = {"query_id": query_id, "grounding": grounding, "latency_ms": latency_ms, **result}
    if ungrounded:
        response["ungrounded"] = True  # 무인용 주장 경고 (프론트 표시용)
    return response


@app.get("/committees")
def get_committees():
    """위원회 목록 + 회의 수. (9행 — 필터 화면의 위원회 선택지)"""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.committee_id, c.name, c.full_name, c.policy_domain,
                   count(m.source_id) AS meeting_count
            FROM committees c
            LEFT JOIN meetings m ON m.committee_id = c.committee_id
            GROUP BY c.committee_id, c.name, c.full_name, c.policy_domain
            ORDER BY meeting_count DESC
            """
        )
        return {"committees": cur.fetchall()}


@app.get("/meetings")
def get_meetings(
    committee: str | None = Query(None, description="위원회 약칭 (예: 과방위)"),
    date_from: datetime.date | None = Query(None, description="YYYY-MM-DD"),
    date_to: datetime.date | None = Query(None, description="YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """회의 목록. 위원회·기간 필터 지원."""
    conds, params = [], []
    if committee:
        conds.append("c.name = %s")
        params.append(committee)
    if date_from:
        conds.append("m.meeting_date >= %s")
        params.append(date_from)
    if date_to:
        conds.append("m.meeting_date <= %s")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT m.source_id, c.name AS committee, m.meeting_date, m.file_name
            FROM meetings m
            JOIN committees c ON c.committee_id = m.committee_id
            {where}
            ORDER BY m.meeting_date DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        return {"meetings": cur.fetchall()}


@app.get("/speakers")
def get_speakers(
    committee: str | None = Query(None, description="위원회 약칭 (예: 과방위)"),
    q: str | None = Query(None, description="이름 부분 검색"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """발언자 목록 (발언 수 순). 위원회·이름 필터 지원."""
    conds, params = [], []
    if committee:
        conds.append("c.name = %s")
        params.append(committee)
    if q:
        conds.append("s.name ILIKE %s")
        params.append(f"%{q}%")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT s.name, s.role, c.name AS committee, s.utterance_count
            FROM speakers s
            LEFT JOIN committees c ON c.committee_id = s.committee_id
            {where}
            ORDER BY s.utterance_count DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        return {"speakers": cur.fetchall()}


@app.get("/search/keyword")
def search_keyword_endpoint(
    q: str = Query(..., min_length=2, description="검색어"),
    committee: str | None = Query(None, description="위원회 약칭"),
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """키워드 검색 (RAG-2). 하이브리드 검색의 한 축 — 디버그·검증용 노출."""
    results = keyword_search(q, committee, date_from, date_to, limit)
    return {"query": q, "count": len(results), "results": results}


@app.get("/search/vector")
def search_vector_endpoint(
    q: str = Query(..., min_length=2, description="검색어 (의미 검색)"),
    committee: str | None = Query(None, description="위원회 약칭"),
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    speaker: str | None = Query(None, description="발언자 이름 (정확 일치)"),
    limit: int = Query(20, ge=1, le=100),
):
    """벡터(의미) 검색 (RAG-3). 하이브리드 검색의 다른 한 축 — 디버그·검증용 노출."""
    try:
        results = vector_search(q, committee, date_from, date_to, speaker, limit)
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"임베딩 호출 실패: {type(e).__name__}")
    return {"query": q, "count": len(results), "results": results}


@app.get("/search/hybrid")
def search_hybrid_endpoint(
    q: str = Query(..., min_length=2, description="검색어"),
    committee: str | None = Query(None, description="위원회 약칭"),
    date_from: datetime.date | None = None,
    date_to: datetime.date | None = None,
    limit: int = Query(10, ge=1, le=50),
):
    """하이브리드 검색 (RAG-4) — 키워드+벡터 RRF 융합. /query 의 검색 엔진."""
    try:
        results = hybrid_search(q, committee, date_from, date_to, limit)
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"임베딩 호출 실패: {type(e).__name__}")
    return {"query": q, "count": len(results), "results": results}


@app.post("/answer")
def answer_endpoint(req: AnswerRequest):
    """답변 생성 (RAG-6) — qa: 간결 답변 / report: 정책 브리핑. RAG-7 /query 가 이걸 감싼다."""
    try:
        return generate_answer(req.question, req.mode, req.committee, req.date_from, req.date_to)
    except OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {type(e).__name__}")


@app.get("/actors/{name}")
def get_actor(name: str):
    """행위자 프로필 (POL-2) — 발언 통계·정당 여야 이력·주요 언급 기관·최근 발언."""
    profile = actor_profile(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"발언 기록 없음: {name}")
    return profile


@app.get("/issues")
def list_issues():
    """쟁점 사전 목록 (POL-3). 상세·타임라인은 POL-4 에서 확장한다."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.issue_id, i.title, i.type, i.description,
                   count(ic.chunk_id)          AS chunk_count,
                   count(DISTINCT ic.turn_id)  AS turn_count
            FROM issues i LEFT JOIN issue_chunks ic USING (issue_id)
            GROUP BY i.issue_id, i.title, i.type, i.description
            ORDER BY chunk_count DESC
        """)
        return {"issues": cur.fetchall()}


@app.get("/citations/{chunk_id}")
def get_citation(chunk_id: str):
    """출처 원문 조회 — 발언 전문 + 앞뒤 맥락 + 원본 PDF 페이지. (신뢰 설계의 핵심)"""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ch.chunk_id, ch.turn_id, ch.chunk_index, ch.chunk_total,
                   ch.speaker, ch.role, ch.text,
                   ch.context_before, ch.context_after,
                   ch.page_start, ch.page_end,
                   ch.meeting_date, co.name AS committee, co.full_name AS committee_full,
                   m.file_name, ch.source_id,
                   ch.policy_domain, ch.bill_refs, ch.utterance_type, ch.mentions
            FROM chunks ch
            JOIN meetings m ON m.source_id = ch.source_id
            JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.chunk_id = %s
            """,
            (chunk_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"chunk_id not found: {chunk_id}")
    return row


@app.post("/feedback")
def post_feedback(req: FeedbackRequest):
    """답변 평가 저장 (RAG-7) — query_logs 해당 행에 rating UPDATE."""
    try:
        uuid.UUID(req.query_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"query_id 형식이 UUID 가 아닙니다: {req.query_id}")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE query_logs
            SET rating = %s, feedback_comment = %s, feedback_at = now()
            WHERE query_id = %s::uuid
            """,
            (req.rating, req.comment, req.query_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"query_id not found: {req.query_id}")
    return {"status": "ok", "query_id": req.query_id}
