"""
벡터(의미) 검색 (RAG-3).

흐름: 질문 → OpenAI 임베딩(text-embedding-3-small, 1536차원)
     → embeddings_openai HNSW 인덱스 코사인 유사도 검색 → 필터 적용

주의:
  - 필터(위원회 등)와 HNSW 를 함께 쓰면 인덱스가 후보를 좁게 잡아 결과가 부족할 수
    있다 → hnsw.ef_search 를 높여 후보 폭을 넓힌다 (pgvector 권장 방식)
  - 검색 대상 임베딩은 ETL-8 에서 생성한 것과 같은 모델이어야 한다 (차원·의미 공간 일치)
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from psycopg2.extras import RealDictCursor

from db import get_conn

load_dotenv(Path(__file__).parent.parent / ".env")

EMBEDDING_MODEL = "text-embedding-3-small"
EF_SEARCH = 100          # HNSW 탐색 폭 (기본 40 — 필터 병용 대비 상향)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """OpenAI 클라이언트 지연 초기화 (서버 기동 시점엔 키만 확인)."""
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(".env 에 OPENAI_API_KEY 가 없습니다.")
        _client = OpenAI(api_key=api_key)
    return _client


def embed_query(q: str) -> str:
    """질문을 임베딩해 pgvector 리터럴 문자열로 반환."""
    resp = _get_client().embeddings.create(model=EMBEDDING_MODEL, input=[q])
    vec = resp.data[0].embedding
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def vector_search(
    q: str,
    committee: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    speaker: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """의미 검색. 유사도(0~1) 내림차순 결과 반환 (RAG-4 하이브리드의 다른 한 축).

    committee 는 약칭 하나 또는 목록 — 복수 위원회 질문(2026-07-03) 대응.
    """
    qvec = embed_query(q)

    where, params = [], []
    if committee:
        where.append("co.name = ANY(%s)")
        params.append([committee] if isinstance(committee, str) else list(committee))
    if date_from:
        where.append("ch.meeting_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("ch.meeting_date <= %s")
        params.append(date_to)
    if speaker:
        where.append("ch.speaker = %s")
        params.append(speaker)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        SELECT ch.chunk_id, ch.source_id, ch.speaker, ch.role,
               co.name AS committee, ch.meeting_date,
               ch.page_start, ch.is_short,
               left(ch.text, 200) AS snippet,
               1 - (e.embedding <=> %s::vector) AS score
        FROM embeddings_openai e
        JOIN chunks ch ON ch.chunk_id = e.chunk_id
        JOIN committees co ON co.committee_id = ch.committee_id
        {where_sql}
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET LOCAL hnsw.ef_search = %s", (EF_SEARCH,))
        cur.execute(sql, [qvec] + params + [qvec, limit])
        return cur.fetchall()
