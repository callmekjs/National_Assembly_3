"""배포용 축소 코퍼스 생성·이전 (4단계-B).

이슈 매핑 청크가 속한 turn 전체(+같은 회의 인접 ±N turn)를 골라 원격(Supabase)으로
직접 복사한다. 실측 행단가(2026-07-11): 청크 2.3KB, 임베딩 21.0KB(HNSW 포함)/6.5KB(생략).

**2026-08-07 실측으로 기본 전략을 바꿨다.** 배포본 검색 점수가 로컬보다 낮아
(strict@5 15/18 vs 18/18) 원인을 재보니, 라벨된 근거의 절반이 축소본에 없었다.
그 근거가 '어디에' 있는지 세어 보니:

    쟁점 turn 이 있는 같은 회의 안   95.1%
    쟁점 turn 이 없는 회의            4.9%

거리 분포는 넓게 퍼져 있다 — ±5 로 26%, ±20 으로 51%, ±60 까지 가야 100%.
그래서 인접 창을 조금 넓히는 것으로는 회수가 안 된다. 규칙별 실측(보정계수 0.83):

    규칙            청크      HNSW 유    HNSW 무    근거 회수
    씨앗만         6,692     139MB      60MB       47.2%
    ±1            18,001     352MB     141MB       49.3%
    ±5            50,726     970MB     374MB       60.3%

**인덱스를 유지하면 500MB 를 다 써도 회수율이 2%p 밖에 안 오른다.** 인덱스를 빼면
7.6배 많은 내용에 +13%p 인데, 순차 스캔 비용은 실측 환산 0.06초(50,726행)로 무시할
수준이다 — 벡터 검색 왕복이 이미 3.7초이고 그 대부분은 임베딩 API 호출이다.
그래서 기본값을 **±5 · HNSW 생략**으로 둔다. HNSW 는 '있으면 좋은 것'이 아니라
이 규모에서는 **용량을 근거와 맞바꾸는 선택**이었다.

실행:
  python scripts/make_deploy_corpus.py --dry-run      # 대상 산출·사이즈 추정만 (원격 불필요)
  python scripts/make_deploy_corpus.py                # DEPLOY_DATABASE_URL 로 복사 (빈 DB 전제)
  python scripts/make_deploy_corpus.py --wipe-remote  # 원격 대상 테이블 TRUNCATE 후 재적재
  옵션: --neighbors N (기본 5)  --index/--no-index (기본 --no-index)  --limit-mb (기본 450)
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
LIMIT_MB = 450.0            # Supabase 무료 500MB 에서 여유 50MB (query_logs·WAL)
CHUNK_ROW_KB = 2.3          # 실측: chunks 935MB / 42만 행
EMB_ROW_KB_INDEXED = 21.0   # 실측: embeddings 8.6GB(HNSW 포함) / 42만 행
EMB_ROW_KB_RAW = 6.5        # 1536 float + 행 오버헤드
# 추정식은 실측보다 크게 나온다. 배포본 6,692행이 추정 167MB 인데 실측 139MB —
# 계수 0.83. 추정만 믿고 한도를 잡으면 쓸 수 있는 용량을 20% 남기고 버리게 된다.
SIZE_CALIBRATION = 0.83
DEFAULT_NEIGHBORS = 5       # 근거 회수율 실측으로 정함 (모듈 docstring 표 참조)
_TURN_ID = re.compile(r"^(?P<src>.+_turn_)(?P<no>\d+)$")

# 전량 복사 소형 테이블 (FK 순서 — committees 가 meetings·chunks 의 부모)
FULL_TABLES = ("committees", "meetings", "speakers", "members", "issues", "issue_stances")


def expand_neighbor_turn_ids(turn_ids: set, k: int = 1) -> set:
    """turn 집합 + 같은 회의 인접 ±k (answer.neighbor_turn_ids 와 동일 규칙, 자릿수 보존).
    패턴 밖 id 는 그대로 둔다. 첫 turn(0001)의 이전(-1)은 만들지 않는다.

    k 는 기본 1 로 둔다 — 이 함수의 계약(±1)에 기대는 호출부가 있고, 넓히기는
    호출측이 명시적으로 요구할 일이다."""
    out = set(turn_ids)
    for tid in turn_ids:
        m = _TURN_ID.match(tid)
        if not m:
            continue
        src, no = m.group("src"), m.group("no")
        n, width = int(no), len(no)
        for d in range(1, k + 1):
            if n - d >= 1:  # answer.py 와 동일 — turn 번호는 0001 시작
                out.add(f"{src}{n - d:0{width}d}")
            out.add(f"{src}{n + d:0{width}d}")
    return out


def estimate_mb(n_chunks: int, with_index: bool, calibrated: bool = False) -> float:
    """행단가 기반 추정. calibrated=True 면 실측 보정계수를 적용한다.

    보정 없이 쓰면 20% 과대 추정이라 한도 판단이 보수적으로 치우친다 — 실제로
    쓸 수 있는 용량을 남기고 버리게 된다. 기본값을 False 로 둔 것은 기존
    호출부(테스트 포함)의 계약을 바꾸지 않기 위해서다."""
    emb = EMB_ROW_KB_INDEXED if with_index else EMB_ROW_KB_RAW
    mb = n_chunks * (CHUNK_ROW_KB + emb) / 1024
    return mb * SIZE_CALIBRATION if calibrated else mb


# choose_scope(폴백 캐스케이드: 인접+인덱스 → 인접 제외 → 인덱스 생략)는 제거했다.
#
# 그 자동 폴백이 이번 문제의 원인이었다. 인접 ±1 이 옛 한도(350MB)를 넘자 조용히
# '인접 제외'로 떨어졌고, 그 결과 배포본이 씨앗 turn 만 담은 채로 몇 주를 돌았다.
# 로그에는 선택 결과가 찍혔지만 **인덱스를 지키려고 근거를 버렸다는 사실**은
# 아무 데도 드러나지 않았다. 자동 폴백은 '실패하지 않는 것'처럼 보이게 만들면서
# 트레이드오프를 사람 눈에서 감춘다.
#
# 지금은 인접 창과 인덱스 여부를 인자로 명시하고, 한도를 넘으면 **멈추고 사람에게
# 되묻는다**. 무엇을 포기할지는 스크립트가 조용히 정할 일이 아니다.


def fetch_targets(cur, neighbors: int = 1) -> tuple:
    """(core turn 집합, 인접 포함 turn 집합, 각 chunk 수). 로컬 DB 기준."""
    cur.execute("""
        SELECT DISTINCT c.turn_id FROM issue_chunks ic JOIN chunks c USING (chunk_id)
    """)
    core_turns = {r[0] for r in cur.fetchall()}
    with_neighbors = expand_neighbor_turn_ids(core_turns, neighbors)

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
    ap.add_argument("--neighbors", type=int, default=DEFAULT_NEIGHBORS,
                    help=f"인접 turn 창 ±N (기본 {DEFAULT_NEIGHBORS})")
    ap.add_argument("--index", dest="index", action="store_true", default=False,
                    help="HNSW 생성 (기본: 생략 — 용량을 근거와 맞바꾸지 않는다)")
    ap.add_argument("--no-index", dest="index", action="store_false")
    ap.add_argument("--limit-mb", type=float, default=LIMIT_MB)
    args = ap.parse_args()

    init_pool()
    with get_conn() as lconn, lconn.cursor() as lcur:
        core_turns, nb_turns, n_core, n_nb = fetch_targets(lcur, args.neighbors)
        est = estimate_mb(n_nb, with_index=args.index, calibrated=True)
        scope = {"neighbors": True, "index": args.index,
                 "n_chunks": n_nb, "est_mb": round(est, 1)}
        print(f"core turn {len(core_turns):,} / +인접(±{args.neighbors}) turn {len(nb_turns):,}")
        print(f"청크: core {n_core:,} / +인접 {n_nb:,}")
        print(f"선택: 인접 ±{args.neighbors}, HNSW={'생성' if scope['index'] else '생략'}, "
              f"청크 {scope['n_chunks']:,}개, 추정 {scope['est_mb']}MB "
              f"(보정계수 {SIZE_CALIBRATION} 적용, 한도 {args.limit_mb}MB)")
        if scope["est_mb"] > args.limit_mb:
            print(f"[FAIL] 한도 초과 — --neighbors 를 줄이거나 --limit-mb 를 조정할 것")
            sys.exit(1)
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
