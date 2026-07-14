"""배포용 축소 코퍼스 생성·이전 (4단계-B).

이슈 매핑 청크가 속한 turn 전체(+같은 회의 인접 ±1 turn)를 골라 원격(Supabase)으로
직접 복사한다. 목표 ≤350MB — 초과 시 폴백: ①인접 turn 제외 ②HNSW 생략.
실측 행단가(2026-07-11): 청크 2.3KB, 임베딩 21.0KB(HNSW 포함)/6.5KB(생략).

실행:
  python scripts/make_deploy_corpus.py --dry-run      # 대상 산출·사이즈 추정만 (원격 불필요)
  python scripts/make_deploy_corpus.py                # DEPLOY_DATABASE_URL 로 복사 (빈 DB 전제)
  python scripts/make_deploy_corpus.py --wipe-remote  # 원격 대상 테이블 TRUNCATE 후 재적재
"""
import argparse
import io
import os
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
LIMIT_MB = 350.0
CHUNK_ROW_KB = 2.3          # 실측: chunks 935MB / 42만 행
EMB_ROW_KB_INDEXED = 21.0   # 실측: embeddings 8.6GB(HNSW 포함) / 42만 행
EMB_ROW_KB_RAW = 6.5        # 1536 float + 행 오버헤드
_TURN_ID = re.compile(r"^(?P<src>.+_turn_)(?P<no>\d+)$")

# 전량 복사 소형 테이블 (FK 순서 — committees 가 meetings·chunks 의 부모)
FULL_TABLES = ("committees", "meetings", "speakers", "members", "issues", "issue_stances")


def expand_neighbor_turn_ids(turn_ids: set) -> set:
    """turn 집합 + 같은 회의 인접 ±1 (answer.neighbor_turn_ids 와 동일 규칙, 자릿수 보존).
    패턴 밖 id 는 그대로 둔다. 첫 turn(0001)의 이전(-1)은 만들지 않는다."""
    out = set(turn_ids)
    for tid in turn_ids:
        m = _TURN_ID.match(tid)
        if not m:
            continue
        src, no = m.group("src"), m.group("no")
        n, width = int(no), len(no)
        if n > 1:  # answer.py 와 동일 — turn 번호는 0001 시작, 0001 의 이전은 없음
            out.add(f"{src}{n - 1:0{width}d}")
        out.add(f"{src}{n + 1:0{width}d}")
    return out


def estimate_mb(n_chunks: int, with_index: bool) -> float:
    emb = EMB_ROW_KB_INDEXED if with_index else EMB_ROW_KB_RAW
    return n_chunks * (CHUNK_ROW_KB + emb) / 1024


def choose_scope(n_with_neighbors: int, n_core_only: int, limit_mb: float = LIMIT_MB) -> dict:
    """폴백 캐스케이드: 인접+인덱스 → 인접 제외 → 인덱스도 생략 (스펙 순서)."""
    for neighbors, index in ((True, True), (False, True), (False, False)):
        n = n_with_neighbors if neighbors else n_core_only
        est = estimate_mb(n, with_index=index)
        if est <= limit_mb:
            return {"neighbors": neighbors, "index": index, "n_chunks": n, "est_mb": round(est, 1)}
    # 전부 초과 — 마지막 조합을 그대로 반환하되 초과 표식 (호출측이 중단 판단)
    return {"neighbors": False, "index": False, "n_chunks": n_core_only,
            "est_mb": round(estimate_mb(n_core_only, with_index=False), 1)}


def fetch_targets(cur) -> tuple:
    """(core turn 집합, 인접 포함 turn 집합, 각 chunk 수). 로컬 DB 기준."""
    cur.execute("""
        SELECT DISTINCT c.turn_id FROM issue_chunks ic JOIN chunks c USING (chunk_id)
    """)
    core_turns = {r[0] for r in cur.fetchall()}
    with_neighbors = expand_neighbor_turn_ids(core_turns)

    def count_chunks(turns: set) -> int:
        cur.execute("SELECT count(*) FROM chunks WHERE turn_id = ANY(%s)", (list(turns),))
        return cur.fetchone()[0]

    return core_turns, with_neighbors, count_chunks(core_turns), count_chunks(with_neighbors)


def copy_table(lcur, rcur, table: str, where: str = "", params: tuple = ()) -> int:
    """로컬 → 원격 한 테이블 복사 (컬럼 자동, 배치 1000). 반환 = 복사 행수."""
    from psycopg2.extras import execute_values
    lcur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = 'public' ORDER BY ordinal_position", (table,))
    meta = lcur.fetchall()
    cols = [c for c, _ in meta]
    collist = ", ".join(cols)
    # vector·jsonb 는 텍스트 직렬화로 이식 — 문자열 리터럴은 원격 컬럼 타입으로 암시 캐스팅됨
    sel = ", ".join(f"{c}::text" if c == "embedding" or dt == "jsonb" else c
                    for c, dt in meta)
    lcur.execute(f"SELECT {sel} FROM {table} {where}", params)
    n = 0
    while True:
        rows = lcur.fetchmany(1000)
        if not rows:
            break
        execute_values(rcur, f"INSERT INTO {table} ({collist}) VALUES %s", rows)
        n += len(rows)
    return n


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool, get_conn
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wipe-remote", action="store_true")
    args = ap.parse_args()

    init_pool()
    with get_conn() as lconn, lconn.cursor() as lcur:
        core_turns, nb_turns, n_core, n_nb = fetch_targets(lcur)
        scope = choose_scope(n_nb, n_core)
        print(f"core turn {len(core_turns):,} / +인접 turn {len(nb_turns):,}")
        print(f"청크: core {n_core:,} / +인접 {n_nb:,}")
        print(f"선택: 인접={'포함' if scope['neighbors'] else '제외'}, "
              f"HNSW={'생성' if scope['index'] else '생략'}, "
              f"청크 {scope['n_chunks']:,}개, 추정 {scope['est_mb']}MB (한도 {LIMIT_MB}MB)")
        if scope["est_mb"] > LIMIT_MB:
            print("[FAIL] 최소 구성도 한도 초과 — 스펙 재검토 필요"); sys.exit(1)
        if args.dry_run:
            print("[DRY] 원격 복사 생략")
        else:
            remote_url = os.environ.get("DEPLOY_DATABASE_URL")
            if not remote_url:
                print("[FAIL] DEPLOY_DATABASE_URL 미설정 (.env)"); sys.exit(1)
            import psycopg2
            turns = list(nb_turns if scope["neighbors"] else core_turns)
            rconn = psycopg2.connect(remote_url)
            rconn.autocommit = False
            try:
                with rconn.cursor() as rcur:
                    rcur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    schema_sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
                    rcur.execute(schema_sql)
                    if args.wipe_remote:
                        for t in ("embeddings_openai", "chunks", "issue_chunks",
                                  *reversed(FULL_TABLES)):
                            rcur.execute(f"TRUNCATE {t} CASCADE")
                    report = {}
                    for t in FULL_TABLES:
                        report[t] = copy_table(lcur, rcur, t)
                    report["chunks"] = copy_table(
                        lcur, rcur, "chunks", "WHERE turn_id = ANY(%s)", (turns,))
                    report["issue_chunks"] = copy_table(
                        lcur, rcur, "issue_chunks",
                        "WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE turn_id = ANY(%s))",
                        (turns,))
                    report["embeddings_openai"] = copy_table(
                        lcur, rcur, "embeddings_openai",
                        "WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE turn_id = ANY(%s))",
                        (turns,))
                    if scope["index"]:
                        print("HNSW 생성 중 (수만 행 — 수 분)…")
                        rcur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_embeddings_openai_hnsw
                            ON embeddings_openai USING hnsw (embedding vector_cosine_ops)
                        """)
                    # 행수 검증 — 원격 count 와 대조
                    for t, n in report.items():
                        rcur.execute(f"SELECT count(*) FROM {t}")
                        rn = rcur.fetchone()[0]
                        flag = "OK" if rn >= n else "MISMATCH"
                        print(f"  [{flag}] {t:20s} 복사 {n:,} / 원격 {rn:,}")
                        if rn < n:
                            raise RuntimeError(f"{t} 행수 불일치")
                rconn.commit()
                print("[OK] 이전 완료")
            except Exception:
                rconn.rollback()
                raise
            finally:
                rconn.close()
    close_pool()


if __name__ == "__main__":
    main()
