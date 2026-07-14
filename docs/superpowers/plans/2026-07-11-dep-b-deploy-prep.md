# 4단계-B 배포 준비 (코드 완결분) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배포에 필요한 코드 전부를 완성해 "계정 연결 + 버튼"만 남긴다 — 축소 코퍼스 생성기(dry-run 검증), 콜드스타트 UX, 배포 설정·런북.

**Architecture:** 신규 `scripts/make_deploy_corpus.py` — 순수부(인접 turn 확장·사이즈 추정·범위 결정)와 DB부(대상 산출·원격 복사·검증)를 분리. 오늘은 로컬 **dry-run까지만 실행**(원격 복사는 다음 세션에 DEPLOY_DATABASE_URL 로). 프론트는 App.jsx에 헬스 ping 배너 + 푸터 표기. 배포 절차는 README 런북으로 문서화.

**Tech Stack:** psycopg2(원격 직접 연결), execute_values 배치 복사(기존 jsonl_to_postgres 패턴), React.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-11-dep-b-deploy-design.md` — 아래 값 verbatim.
- 축소 규칙: 이슈 매핑 청크의 turn 전체 + 같은 회의 인접 ±1 turn. 목표 **≤350MB**, 초과 시 폴백 순서: ①인접 turn 제외 → ②HNSW 생략(추가 레버, 로그 명시).
- 사이즈 추정 상수(실측 2026-07-11): 청크 행 **2.3KB**, 임베딩 행(HNSW 포함 전량 기준) **21.0KB**, 인덱스 생략 시 임베딩 행 **6.5KB**.
- 인접 turn 계산은 `backend/answer.py` `neighbor_turn_ids` 와 동일 규칙 (`_turn_\d+` 순번 ±1, 자릿수 보존).
- 원격 접속: `DEPLOY_DATABASE_URL` (로컬 .env 전용 — .env.example 에 주석으로만 추가, 값 커밋 금지).
- 복사 테이블: 전량 = committees, meetings, speakers, members, issues, issue_stances / 부분 = issue_chunks(대상 chunk 존재분), chunks(대상), embeddings_openai(대상). query_logs 는 스키마만(빈 상태).
- 푸터 문구 verbatim: `데모 코퍼스: 24개 쟁점 관련 발언 부분집합 (전체 42만 청크는 로컬 데모) — 의원 프로필 통계도 이 부분집합 기준입니다.`
- 콜드스타트 배너: 로드 시 `/health` ping(타임아웃 90초), 대기 중 `무료 서버를 깨우는 중입니다 (최대 1분)…`, 실패 시 `서버 연결 실패 — 잠시 후 새로고침해주세요.`
- **오늘 실행 범위**: dry-run·프론트 로컬 검증·문서까지. 원격 복사·호스팅 프로비저닝은 다음 세션(README 런북이 그 대본).
- 스크립트·테스트 관례: `if __name__ == "__main__"` stdout 래핑, check() 헬퍼, python 직접 실행/pytest 겸용.

---

### Task 1: `make_deploy_corpus.py` — 순수부 + dry-run

**Files:**
- Create: `scripts/make_deploy_corpus.py`
- Test: `tests/test_deploy_corpus.py`

**Interfaces:**
- Consumes: `backend/db.py`(로컬), `psycopg2`(원격 직접)
- Produces:
  - `expand_neighbor_turn_ids(turn_ids: set[str]) -> set[str]` — 원본 + 인접 ±1 (자릿수 보존)
  - `estimate_mb(n_chunks: int, with_index: bool) -> float`
  - `choose_scope(n_with_neighbors: int, n_core_only: int, limit_mb: float = 350.0) -> dict` — `{"neighbors": bool, "index": bool, "n_chunks": int, "est_mb": float}` (폴백 캐스케이드)
  - CLI: `--dry-run` / `--wipe-remote` / (기본) 원격 복사

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_deploy_corpus.py`:

```python
"""배포 코퍼스 생성기 순수 로직 테스트 — DB 없이 실행.
실행: python tests/test_deploy_corpus.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from make_deploy_corpus import choose_scope, estimate_mb, expand_neighbor_turn_ids  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_expand_neighbor_turn_ids():
    r = expand_neighbor_turn_ids({"복지위_20240613_52087_52087_turn_0047"})
    check("원본 포함", "복지위_20240613_52087_52087_turn_0047" in r)
    check("이전 turn (자릿수 보존)", "복지위_20240613_52087_52087_turn_0046" in r, r)
    check("다음 turn", "복지위_20240613_52087_52087_turn_0048" in r, r)
    check("3개 정확히", len(r) == 3, r)
    r0 = expand_neighbor_turn_ids({"A_turn_0000"})
    check("turn_0000 은 이전 없음 (음수 미생성)", r0 == {"A_turn_0000", "A_turn_0001"}, r0)
    check("패턴 밖 id 는 그대로", expand_neighbor_turn_ids({"weird"}) == {"weird"})
    # 인접끼리 겹치면 합집합
    r2 = expand_neighbor_turn_ids({"A_turn_0001", "A_turn_0002"})
    check("겹침 합집합", r2 == {"A_turn_0000", "A_turn_0001", "A_turn_0002", "A_turn_0003"}, r2)


def test_estimate_mb():
    # 실측 상수: 청크 2.3KB + 임베딩(인덱스 포함) 21.0KB → 행당 23.3KB
    check("인덱스 포함 추정", abs(estimate_mb(10_000, with_index=True) - 10_000 * 23.3 / 1024) < 0.01)
    check("인덱스 생략 추정", abs(estimate_mb(10_000, with_index=False) - 10_000 * 8.8 / 1024) < 0.01)


def test_choose_scope():
    # 인접 포함이 350MB 이내면 그대로 (인덱스 포함)
    s = choose_scope(n_with_neighbors=10_000, n_core_only=6_000)
    check("여유 시 인접+인덱스", s["neighbors"] and s["index"], s)
    # 인접 포함이 초과, core-only 는 이내 → 인접 제외
    s2 = choose_scope(n_with_neighbors=20_000, n_core_only=14_000)
    check("초과 시 인접 제외", not s2["neighbors"] and s2["index"], s2)
    check("n_chunks 는 선택 범위 기준", s2["n_chunks"] == 14_000, s2)
    # core-only 도 인덱스 포함 초과 → 인덱스 생략 레버
    s3 = choose_scope(n_with_neighbors=40_000, n_core_only=30_000)
    check("최후 레버 인덱스 생략", not s3["neighbors"] and not s3["index"], s3)
    check("est_mb 동봉", s3["est_mb"] > 0, s3)


if __name__ == "__main__":
    test_expand_neighbor_turn_ids()
    test_estimate_mb()
    test_choose_scope()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_deploy_corpus.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'make_deploy_corpus'`

- [ ] **Step 3: 구현**

`scripts/make_deploy_corpus.py`:

```python
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
    패턴 밖 id 는 그대로 둔다. turn_0000 의 이전(-1)은 만들지 않는다."""
    out = set(turn_ids)
    for tid in turn_ids:
        m = _TURN_ID.match(tid)
        if not m:
            continue
        src, no = m.group("src"), m.group("no")
        n, width = int(no), len(no)
        if n > 0:
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
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position", (table,))
    cols = [r[0] for r in lcur.fetchall()]
    collist = ", ".join(cols)
    # embedding(vector) 은 텍스트 직렬화로 이식 — 원격에서 ::vector 캐스팅
    sel = ", ".join(f"{c}::text" if c == "embedding" else c for c in cols)
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
            print("[DRY] 원격 복사 생략"); close_pool(); return

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
                        CREATE INDEX IF NOT EXISTS embeddings_openai_hnsw
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
```

- [ ] **Step 4: 순수 테스트 통과**

Run: `python tests/test_deploy_corpus.py` 그리고 `python -m pytest tests/test_deploy_corpus.py -q`
Expected: `all passed` / 3 passed

- [ ] **Step 5: 실DB dry-run (오늘의 핵심 검증)**

Run: `python scripts/make_deploy_corpus.py --dry-run`
Expected: core/인접 turn 수 + 청크 수 + `선택: … 추정 xxx MB (한도 350.0MB)` + `[DRY] 원격 복사 생략`. 추정치가 350MB 이내인 구성이 선택되는지 확인, **출력 전문을 리포트에 기록** (다음 세션 실복사의 기준선).

- [ ] **Step 6: .env.example 주석 추가**

`.env.example` 끝에:

```
# 배포(4단계-B) — 원격 Supabase 접속 문자열 (make_deploy_corpus.py 전용, 절대 커밋 금지)
# DEPLOY_DATABASE_URL=postgresql://...supabase.co:5432/postgres
```

- [ ] **Step 7: 전체 테스트 + 커밋**

Run: `python -m pytest tests/ -q` — Expected: 전체 PASS

```bash
git add scripts/make_deploy_corpus.py tests/test_deploy_corpus.py .env.example
git commit -m "feat(dep-b): 축소 코퍼스 생성기 — 이슈 turn+인접, 350MB 폴백 캐스케이드, dry-run 검증"
```

---

### Task 2: 프론트 콜드스타트 배너 + 푸터 표기

**Files:**
- Modify: `frontend/src/api.js` (pingHealth 추가)
- Modify: `frontend/src/App.jsx` (배너 state·useEffect·렌더 + 푸터 문구)

**Interfaces:**
- Consumes: 기존 `request(path, options, timeoutMs)` 3번째 인자
- Produces: 로드 시 헬스 확인 배너 (깨어 있으면 즉시 사라짐)

- [ ] **Step 1: api.js 에 추가** (fetchActor 아래)

```javascript
export function pingHealth() {
  // 콜드스타트(Render free 슬립) 대비 — 최대 90초 대기
  return request('/health', {}, 90000)
}
```

- [ ] **Step 2: App.jsx 수정**

(a) import 줄에 pingHealth 추가: `import { postQuery, pingHealth } from './api'`
(b) `useState` import 를 `import { useEffect, useState } from 'react'` 로.
(c) state 블록에 추가:

```javascript
  const [serverStatus, setServerStatus] = useState('checking') // checking | ok | down
```

(d) state 아래 useEffect:

```javascript
  useEffect(() => {
    pingHealth().then(() => setServerStatus('ok')).catch(() => setServerStatus('down'))
  }, [])
```

(e) `<header>` 바로 아래 배너 렌더:

```javascript
      {serverStatus === 'checking' && (
        <div style={{ background: '#fef3c7', color: '#92400e', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 14 }}>
          무료 서버를 깨우는 중입니다 (최대 1분)…
        </div>
      )}
      {serverStatus === 'down' && (
        <div style={{ background: '#fee2e2', color: '#991b1b', padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 14 }}>
          서버 연결 실패 — 잠시 후 새로고침해주세요.
        </div>
      )}
```

(f) 푸터 `<small>` 안에 줄 추가 (기존 내용 뒤, `<br />` 로 구분):

```javascript
          <br />
          데모 코퍼스: 24개 쟁점 관련 발언 부분집합 (전체 42만 청크는 로컬 데모) — 의원
          프로필 통계도 이 부분집합 기준입니다.
```

- [ ] **Step 3: 브라우저 확인** (백엔드 8000 + 프론트 5173)

1. 정상 기동 상태: 배너가 잠깐 노란색 → 사라짐(ok), 푸터에 데모 코퍼스 문구
2. 백엔드 끈 상태로 새로고침: 노란 배너 유지 → (90초 대기 없이 확인하려면 백엔드 끄면 즉시 fetch 실패 → 빨간 배너) — 빨간 배너 전환 확인 후 백엔드 재기동
3. 콘솔 에러 0, 질의·쟁점·프로필 탭 회귀 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/api.js frontend/src/App.jsx
git commit -m "feat(dep-b): 콜드스타트 배너 + 데모 코퍼스 푸터 표기"
```

---

### Task 3: 배포 설정·런북 (runtime 핀 + README)

**Files:**
- Create: `backend/.python-version` (내용: `3.12`)
- Modify: `backend/requirements.txt` (검증 — 부족분 추가)
- Modify: `README.md` (배포 섹션 신설)

**Interfaces:**
- Consumes: Task 1·2 결과물 (런북이 참조)
- Produces: 다음 세션 실행 대본

- [ ] **Step 1: backend/requirements.txt 검증**

파일을 열어 다음이 전부 있는지 확인, 없으면 추가 (버전은 현재 로컬 설치 기준 유지):
`fastapi`, `uvicorn`, `psycopg2-binary`, `openai`, `python-dotenv`, `pydantic`.
확인 명령: `cd backend && python -c "import fastapi, uvicorn, psycopg2, openai, dotenv, pydantic; print('ok')"`

- [ ] **Step 2: backend/.python-version 생성** (내용 한 줄: `3.12`)

- [ ] **Step 3: README 배포 섹션 추가** (기존 "데이터 파이프라인 실행" 섹션 뒤)

```markdown
## 공개 배포 (4단계-B 런북)

무료 3-스택: **Vercel**(프론트) + **Render free**(백엔드) + **Supabase free**(DB).
배포 코퍼스는 이슈 중심 축소본 — `scripts/make_deploy_corpus.py` 가 생성 (전체 9.6GB
중 이슈 관련 turn만 ≤350MB, 검색 eval 수치 R@5 0.983 은 전체 코퍼스 로컬 측정 기준).

### 순서 (사용자 체크리스트)

1. **Supabase**: 프로젝트 생성 (리전 Northeast Asia) → Settings > Database 의
   Connection string(URI) 복사 → 로컬 `.env` 에 `DEPLOY_DATABASE_URL=...` 추가
2. **코퍼스 이전** (로컬에서): `python scripts/make_deploy_corpus.py` — 행수 검증
   리포트 `[OK]` 확인 (dry-run 먼저: `--dry-run`)
3. **Render**: New Web Service → GitHub 저장소 연결 → Root Directory `backend`,
   Build `pip install -r requirements.txt`, Start
   `uvicorn main:app --host 0.0.0.0 --port $PORT`, Health Check Path `/health`
   - 환경변수: `DATABASE_URL`(Supabase URI), `OPENAI_API_KEY`,
     `BACKEND_CORS_ORIGINS`(Vercel 도메인, 배포 후 갱신), `RERANKER_ENABLED=1`,
     `PYTHON_VERSION=3.12.10`
4. **Vercel**: Add New Project → 같은 저장소 → Root Directory `frontend` →
   환경변수 `VITE_API_URL`(Render URL) → Deploy → 도메인을 Render 의
   `BACKEND_CORS_ORIGINS` 에 반영(재배포)
5. **스모크 6항목**: `/health` 200(행수=축소본) / report 질의 1건(issue_context 포함)
   / 쟁점 탭 24개 이슈 / 프로필 김윤 / 연속 6회 질의 → 429 / 15분 방치 후 콜드스타트 배너

### 운영 방어선 (기본값)

IP당 LLM 분당 5회·일반 60회, 일별 OpenAI 비용 상한 $1 (초과 시 한국어 안내).
상세: `docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md`
```

- [ ] **Step 4: 커밋**

```bash
git add backend/.python-version backend/requirements.txt README.md
git commit -m "chore(dep-b): 배포 설정 — python 핀·requirements 검증·README 런북"
```

---

### Task 4: 문서 — progress.md (4-B 준비 완료 기록)

**Files:**
- Modify: `docs/progress.md`

- [ ] **Step 1: "### 4단계-A 구현 기록" 섹션 끝(다음 `##` 직전)에 추가**

```markdown
### 4단계-B 준비 기록 — 배포 코드 완결 (2026-07-11 심야)

> spec: `docs/superpowers/specs/2026-07-11-dep-b-deploy-design.md`,
> plan: `docs/superpowers/plans/2026-07-11-dep-b-deploy-prep.md`

- **실측 근거**: 전체 DB 9.6GB(임베딩+HNSW 8.6GB) — 무료 500MB 에 위원회 1개도 불가.
  이슈 매핑 청크 6,557개가 24개 이슈 전부를 커버 → **이슈 중심 축소본** 채택
  (위원회 컷은 이슈를 깨고, 이슈 컷은 전부 살림).
- **생성기**: `scripts/make_deploy_corpus.py` — 이슈 turn+인접 ±1, 350MB 폴백
  캐스케이드(인접 제외→HNSW 생략), 원격 직접 복사+행수 검증. dry-run 실측 기록은
  구현 리포트 참조.
- **UX**: 콜드스타트 배너(/health ping 90초) + 푸터 정직 표기(부분집합 데모).
- **잔여(다음 세션, README 런북이 대본)**: Supabase 생성→코퍼스 이전→Render→Vercel→
  스모크 6항목. 사용자 계정 작업 ~30분.
```

- [ ] **Step 2: 3행 최종 업데이트 줄 교체**

```
최종 업데이트: 2026-07-11 (4단계-B 배포 코드 완결 — 축소 코퍼스 생성기·콜드스타트 UX·런북. 잔여: 계정 연결 + 버튼, README 런북 참조)
```

- [ ] **Step 3: 커밋**

```bash
git add docs/progress.md
git commit -m "docs(dep-b): 배포 준비 완료 기록 — 잔여는 계정 연결 런북"
```

---

## Self-Review

**1. Spec coverage:**
- 축소 규칙·폴백 캐스케이드·행단가 상수 → Task 1 (choose_scope 스펙 순서) ✅
- 인접 turn 규칙(answer.py 동일·자릿수 보존·0 경계) → Task 1 expand + 테스트 ✅
- 원격 복사(테이블 목록·FK 순서·vector 텍스트 직렬화·행수 검증·wipe 플래그·트랜잭션 롤백) → Task 1 main ✅
- DEPLOY_DATABASE_URL 커밋 금지 → .env.example 주석만 ✅
- 콜드스타트 배너 문구·90초·실패 전환 → Task 2 verbatim ✅
- 푸터 문구 verbatim → Task 2 ✅
- Python 핀·requirements·런북(계정 체크리스트+스모크 6항목+eval 각주) → Task 3 ✅
- 오늘 범위 = dry-run까지 (원격 복사는 다음 세션) → Global Constraints + Task 1 Step 5 ✅

**2. Placeholder scan:** TBD 없음.

**3. Type consistency:**
- `expand_neighbor_turn_ids(set)->set` / `estimate_mb(n, with_index)` / `choose_scope(...)->dict{neighbors,index,n_chunks,est_mb}` — Task 1 구현·테스트 일치 ✅
- `pingHealth()` Task 2 정의 = App.jsx 사용 ✅
- README 런북의 명령·env 이름이 실제 코드(main.py env, make_deploy_corpus CLI)와 일치 ✅
