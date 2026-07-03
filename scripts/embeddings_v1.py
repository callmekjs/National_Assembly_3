"""
[8] embeddings_v1
chunks.embed_text 를 OpenAI 임베딩으로 변환해 embeddings_openai(pgvector)에 저장한다. (ETL-8)

필수 환경 변수 (.env):
    OPENAI_API_KEY   — OpenAI API 키
    DATABASE_URL     — PostgreSQL 연결 문자열

원칙 (마스터 설계 문서 3-4, 4-4 반영):
    - 증분 처리: 이미 embeddings_openai 에 있는 chunk_id 는 건너뛴다
      → 중단돼도 재실행하면 이어서 처리 (배치 단위 커밋)
    - rate limit / 일시 오류는 지수 백오프로 재시도
    - 모든 행에 embedding_model 명시 저장

실행:
    python scripts/embeddings_v1.py               # 전체 (미임베딩 청크만)
    python scripts/embeddings_v1.py 기재위         # source_id 접두사 필터
    python scripts/embeddings_v1.py --dry-run     # 대상 수·예상 비용만 출력
    python scripts/embeddings_v1.py --limit 1000  # 테스트: 처음 N개만
"""

import io
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from openai import OpenAI
from openai import APIError, APITimeoutError, RateLimitError, APIConnectionError

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent

EMBEDDING_MODEL   = "text-embedding-3-small"
EMBEDDING_DIM     = 1536
EMBEDDING_VERSION = "v1.0"

# 배치 구성: 요청당 토큰 한도(300k)를 넘지 않도록 문자 수 기준으로 자른다
BATCH_MAX_ITEMS = 800       # 요청당 최대 텍스트 수
BATCH_MAX_CHARS = 120_000   # 요청당 최대 총 문자 수 (~한국어 150k 토큰 안전선)

MAX_RETRIES = 6             # 지수 백오프 재시도 횟수 (2,4,8,16,32,64초)

PRICE_PER_1M_TOKENS = 0.02  # text-embedding-3-small


def fetch_pending(cur, prefix_filters: list[str]) -> list[tuple[str, str]]:
    """아직 임베딩되지 않은 (chunk_id, embed_text) 목록."""
    sql = """
        SELECT c.chunk_id, c.embed_text
        FROM chunks c
        LEFT JOIN embeddings_openai e ON e.chunk_id = c.chunk_id
        WHERE e.chunk_id IS NULL
          AND c.embed_text IS NOT NULL AND trim(c.embed_text) <> ''
    """
    params: list = []
    if prefix_filters:
        conds = " OR ".join("c.source_id LIKE %s" for _ in prefix_filters)
        sql += f" AND ({conds})"
        params = [f"{p}%" for p in prefix_filters]
    sql += " ORDER BY c.chunk_id"
    cur.execute(sql, params)
    return cur.fetchall()


def make_batches(rows: list[tuple[str, str]]):
    """(chunk_id, text) 목록을 아이템 수·문자 수 한도로 배치 분할."""
    batch, chars = [], 0
    for cid, text in rows:
        if batch and (len(batch) >= BATCH_MAX_ITEMS or chars + len(text) > BATCH_MAX_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append((cid, text))
        chars += len(text)
    if batch:
        yield batch


def embed_with_retry(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """임베딩 API 호출. rate limit/일시 오류는 지수 백오프로 재시도."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            # index 순서 보장
            data = sorted(resp.data, key=lambda d: d.index)
            return [d.embedding for d in data]
        except (RateLimitError, APITimeoutError, APIConnectionError, APIError) as e:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"    [재시도 {attempt + 1}/{MAX_RETRIES}] {type(e).__name__} — {wait}초 대기")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def insert_embeddings(cur, batch: list[tuple[str, str]], vectors: list[list[float]]) -> None:
    rows = [
        (cid, "[" + ",".join(f"{v:.7f}" for v in vec) + "]", EMBEDDING_MODEL)
        for (cid, _), vec in zip(batch, vectors)
    ]
    execute_values(
        cur,
        "INSERT INTO embeddings_openai (chunk_id, embedding, model) VALUES %s "
        "ON CONFLICT (chunk_id) DO NOTHING",
        rows,
        template="(%s, %s::vector, %s)",
        page_size=500,
    )


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY")
    db_url  = os.environ.get("DATABASE_URL")
    if not api_key:
        print("[ERROR] .env 에 OPENAI_API_KEY 가 없습니다."); sys.exit(1)
    if not db_url:
        print("[ERROR] .env 에 DATABASE_URL 이 없습니다."); sys.exit(1)

    args    = sys.argv[1:]
    dry_run = "--dry-run" in args
    limit   = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    filters = [a for a in args if not a.startswith("--") and not a.isdigit()]

    conn = psycopg2.connect(db_url)
    conn.autocommit = False

    with conn.cursor() as cur:
        pending = fetch_pending(cur, filters)
        cur.execute("SELECT count(*) FROM embeddings_openai")
        already = cur.fetchone()[0]

    if limit is not None:
        pending = pending[:limit]
        print(f"[--limit] 처음 {limit:,}개만 처리합니다.")

    total_chars  = sum(len(t) for _, t in pending)
    est_tokens   = int(total_chars * 1.0)  # 한국어 대략 1토큰/자 내외
    est_cost     = est_tokens / 1e6 * PRICE_PER_1M_TOKENS

    print(f"모델: {EMBEDDING_MODEL} ({EMBEDDING_DIM}차원)")
    print(f"이미 임베딩됨: {already:,}개 (스킵)")
    print(f"대상: {len(pending):,}개 청크 / {total_chars:,}자")
    print(f"예상 비용: ~${est_cost:.2f}")

    if dry_run:
        conn.close(); return
    if not pending:
        print("처리할 청크가 없습니다."); conn.close(); return

    client = OpenAI(api_key=api_key)
    t0 = time.time()
    done = 0

    for batch in make_batches(pending):
        vectors = embed_with_retry(client, [t for _, t in batch])
        if len(vectors) != len(batch):
            conn.rollback()
            print(f"[ERROR] 응답 개수 불일치: 요청 {len(batch)} vs 응답 {len(vectors)}")
            sys.exit(1)
        with conn.cursor() as cur:
            insert_embeddings(cur, batch, vectors)
        conn.commit()  # 배치 단위 커밋 → 중단돼도 여기까지는 저장됨
        done += len(batch)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        remain = (len(pending) - done) / rate if rate > 0 else 0
        print(f"  {done:,}/{len(pending):,}  ({done/len(pending)*100:.1f}%)  "
              f"{rate:,.0f}청크/초  남은 예상 {remain/60:.1f}분")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM embeddings_openai")
        final = cur.fetchone()[0]
    conn.close()

    print(f"\n임베딩 완료 — embeddings_openai 총 {final:,}행  (소요 {(time.time()-t0)/60:.1f}분)")
    print("다음 단계: HNSW 인덱스 생성 (검색 속도용)")


if __name__ == "__main__":
    main()
