"""
벡터(의미) 검색 (RAG-3).

흐름: 질문 → OpenAI 임베딩(text-embedding-3-small)
     → embeddings_openai HNSW 인덱스 코사인 유사도 검색 → 필터 적용

차원은 저장된 벡터에서 읽어 맞춘다(embedding_dims). 배포본은 용량 제약으로
512차원을 쓰고 로컬 전체 코퍼스는 1536차원이라, 상수로 박으면 한쪽이 깨진다.

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
from functools import lru_cache

load_dotenv(Path(__file__).parent.parent / ".env")

EMBEDDING_MODEL = "text-embedding-3-small"
EF_SEARCH = 100          # HNSW 탐색 폭 (기본 40 — 필터 병용 대비 상향)

# 질의 벡터의 차원은 **저장된 벡터에 맞춰야** 한다. 어긋나면 pgvector 가 매 질의마다
# 오류를 내 검색이 통째로 죽는다. 상수로 박아 두면 DB 를 바꿀 때 코드 배포와 데이터
# 적재 사이에 반드시 깨지는 구간이 생긴다 — 어느 쪽을 먼저 해도 그렇다.
# 그래서 **DB 에서 읽는다.** 코드가 데이터를 따라가므로 순서 문제가 사라진다.
# 환경변수로 덮어쓸 수 있게 둔 것은 빈 테이블에 처음 적재할 때를 위해서다.
_DEFAULT_DIMS = 1536


_dims_cache: int | None = None


def embedding_dims() -> int:
    """저장된 임베딩의 차원. **성공한 조회만 캐시한다.**

    예전에는 @lru_cache(maxsize=1) 이 걸려 있었다. 그러면 조회 실패 시 돌려주는
    기본값 1536 까지 캐시되어, 콜드스타트 중 DB 가 한 번만 삐끗해도 512차원 배포본에
    1536차원 질의 벡터를 계속 만들었다 — 이후 전 질의가 pgvector 차원 불일치로 500 이
    되고, DB 가 회복돼도 프로세스 재시작 전까지 풀리지 않았다. /health 는 DB 장애에도
    200 을 주는 설계라 자동 재시작도 걸리지 않는다 (감사 2026-08-14).

    실패는 캐시하지 않으므로 다음 호출에서 다시 조회한다.
    """
    global _dims_cache
    if _dims_cache is not None:
        return _dims_cache

    override = os.environ.get("EMBEDDING_DIMENSIONS")
    if override:
        _dims_cache = int(override)
        return _dims_cache

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT vector_dims(embedding) FROM embeddings_openai LIMIT 1")
            row = cur.fetchone()
            if row:
                dims = int(row[0])
                # 장애 구간에 기본값으로 만들어진 질의 벡터가 캐시에 남아 있으면
                # 그 질문들만 계속 깨진다 — 차원이 확정되는 순간 버린다
                embed_query.cache_clear()
                _dims_cache = dims
                return dims
    except Exception:
        pass

    # 조회 실패 또는 빈 테이블 — 기본값을 쓰되 **캐시하지 않는다**
    return _DEFAULT_DIMS


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


@lru_cache(maxsize=256)
def embed_query(q: str) -> str:
    """질문을 임베딩해 pgvector 리터럴 문자열로 반환.

    같은 질문 재질의(재시도·데모 반복·eval)에 API 호출 생략 — lru_cache 는
    스레드 안전, 임베딩은 모델 고정이라 결과 불변 (2026-07-07, A+ 기준 6).
    """
    dims = embedding_dims()
    kw = {} if dims == _DEFAULT_DIMS else {"dimensions": dims}
    resp = _get_client().embeddings.create(model=EMBEDDING_MODEL, input=[q], **kw)
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
               -- 600자: 리랭커가 보는 길이(reranker._MAX_DOC_CHARS 와 같아야 함).
               -- 1200자로 늘려봤으나 nDCG@5 가 0.710→0.653 으로 떨어지고 비용은 1.6배가 돼
               -- 되돌렸다 (2026-08-06 실측). 후보 30개를 한 번에 주는 listwise 방식이라
               -- 후보당 길이를 늘리면 총 입력이 커져 판단이 흐려지는 것으로 보인다.
               -- 주의: 이 주석에 퍼센트 기호를 쓰지 말 것 — psycopg2 가 SQL 문자열 안의
               -- 그 기호를 파라미터 자리표시자로 해석해 IndexError 가 난다 (여기서 겪음).
               left(ch.text, 600) AS snippet,
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
