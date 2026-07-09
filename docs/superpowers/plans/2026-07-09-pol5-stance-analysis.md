# POL-5 입장 분석 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** medical-reform 이슈의 core 발언을 5택 입장 판정하고, 행위자별 입장 매트릭스를 `GET /issues/{id}/stances` 로 제공하며, 최소 프론트 뷰(타임라인 + 입장표)로 브라우저에서 확인한다.

**Architecture:** `scripts/build_issue_stance.py`(LLM turn 판정, build_issue_map 패턴) → `issue_stances` 테이블 → `backend/issues.py` 집계 함수 + 라우트 → `frontend` IssueView. 파일럿 1개 이슈(medical-reform).

**Tech Stack:** Python(psycopg2, openai), FastAPI, PostgreSQL, React+Vite, pytest, vitest.

## Global Constraints

- 발언 레벨 입장 5택: `support | oppose | concern | neutral | none`. 행위자 레벨: `support | oppose | concern | mixed | no_stance`.
- 판정 단위 = turn. 대상 = `issue_chunks.judge='llm_core'` 의 DISTINCT turn_id. turn 텍스트는 그 turn의 core chunk 텍스트를 chunk 순서로 이어 붙임(medical-reform은 212 turn 중 211개가 1 chunk).
- 집계 규칙: 입장 발언(support/oppose/concern)만 방향 카운트. 0개→`no_stance`. 최다 카운트가 대표(concern 포함). support·oppose 둘 다 있고 각각 입장발언의 ⅓ 이상이면 `mixed`. 카운트+근거 항상 노출.
- LLM: gpt-4o-mini, temperature=0, JSON 출력, 배치 20. 형식 위반 1회 재시도 후 보류(누락 우선). 멱등.
- turn 단위 집계는 chunks.turn_id(권위). party 는 POL-0 `party.member_party(speaker)`.
- 순수 로직 테스트: DB·LLM 없이, `if __name__=="__main__"` 가드 + `check()` assert (tests/ 관례).
- Windows: 127.0.0.1, uvicorn `--reload` 금지(백그라운드 기동 후 스모크 끝나면 종료), `python -X utf8`.
- 커밋 트레일러: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

- **Modify** `db/schema.sql` — `issue_stances` 테이블 추가.
- **Create** `scripts/build_issue_stance.py` — turn 5택 LLM 판정·저장(build_issue_map/issue_tier_pass 패턴).
- **Create** `tests/test_issue_stance.py` — 판정 응답 파싱 순수 테스트.
- **Modify** `backend/issues.py` — `aggregate_stances`(순수) + `issue_stances`(DB 집계) 추가.
- **Modify** `tests/test_issue_timeline.py` 아님 → **Create** `tests/test_stance_aggregate.py` — 집계 규칙 테스트.
- **Modify** `backend/main.py` — `GET /issues/{id}/stances` 라우트.
- **Create** `scripts/issue_stance_spotcheck.py` — 판독용 리포트(게이트·POL-7 시작점).
- **Modify** `frontend/src/api.js` — `fetchTimeline`, `fetchStances`.
- **Create** `frontend/src/components/IssueView.jsx` — 드롭다운 + 타임라인 SVG + 입장표.
- **Modify** `frontend/src/App.jsx` — 탭 토글.
- **Modify** `docs/progress.md` — 로드맵 POL-5 기록.

---

## Task 1: 스키마 + 판정 스크립트 (build_issue_stance.py) + 파싱 테스트

**Files:**
- Modify: `db/schema.sql`
- Create: `scripts/build_issue_stance.py`
- Test: `tests/test_issue_stance.py`

**Interfaces:**
- Produces: `parse_stance_response(content: str, batch_size: int) -> list[str] | None` — JSON `{"stances":[...]}` 파싱. 길이 불일치·구조 오류면 None(재시도 신호). 각 원소는 5택 중 하나여야 하며 아니면 None.

- [ ] **Step 1: 스키마 추가.** `db/schema.sql` 의 `issue_chunks` 블록(CREATE INDEX ... idx_issue_chunks_chunk 줄) 바로 뒤에 추가:
```sql

-- 10. 이슈↔행위자 입장 (POL-5). core turn 을 LLM 5택 판정 (support|oppose|concern|neutral|none).
CREATE TABLE IF NOT EXISTS issue_stances (
  issue_id    TEXT NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
  turn_id     TEXT NOT NULL,
  speaker     TEXT,
  role        TEXT,
  stance      TEXT NOT NULL,   -- support | oppose | concern | neutral | none
  judge_model TEXT NOT NULL,
  map_version TEXT NOT NULL,
  mapped_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (issue_id, turn_id)
);
```

- [ ] **Step 2: 파싱 테스트 작성** (`tests/test_issue_stance.py`):
```python
"""POL-5 입장 판정 파싱 순수 테스트 — DB·LLM 없이 실행.
실행: python tests/test_issue_stance.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_issue_stance import parse_stance_response  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_parse_stance_response():
    ok = '{"stances":["support","oppose","concern","neutral","none"]}'
    check("정상 5개", parse_stance_response(ok, 5) == ["support", "oppose", "concern", "neutral", "none"])
    check("길이 불일치 → None", parse_stance_response('{"stances":["support"]}', 3) is None)
    check("허용 밖 토큰 → None", parse_stance_response('{"stances":["yes","no"]}', 2) is None)
    check("JSON 아님 → None", parse_stance_response("support, oppose", 2) is None)
    check("stances 키 없음 → None", parse_stance_response('{"x":[]}', 0) is None)


if __name__ == "__main__":
    test_parse_stance_response()
    print("all passed")
```

- [ ] **Step 3: 테스트 실패 확인.** Run: `python -X utf8 tests/test_issue_stance.py` → FAIL `ModuleNotFoundError: No module named 'build_issue_stance'`.

- [ ] **Step 4: 스크립트 구현** (`scripts/build_issue_stance.py`):
```python
"""이슈↔행위자 입장 판정 (POL-5). core turn 을 5택 LLM 판정해 issue_stances 에 적재.

흐름 (docs/superpowers/specs/2026-07-09-pol5-stance-analysis-design.md):
  issue_chunks.judge='llm_core' 의 DISTINCT turn_id → turn 텍스트(core chunk 이어붙임)
  → gpt-4o-mini 배치 5택 판정 → issue_stances upsert (턴 단위, 멱등).

실행:
  python scripts/build_issue_stance.py --issue medical-reform --dry-run
  python scripts/build_issue_stance.py --issue medical-reform
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from build_issue_map import (  # noqa: E402
    MAX_TRANSIENT_RETRIES, _transient_errors, load_seed, make_batches,
)

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"
MAP_VERSION = "s1.0"
BATCH_SIZE = 20
_MODEL = "gpt-4o-mini"
_STANCES = ("support", "oppose", "concern", "neutral", "none")

_SYSTEM = """당신은 국회 회의록 발언에서 발언자가 특정 쟁점에 취하는 입장을 판정하는 도우미다.
쟁점 정의와 번호 매긴 발언 목록이 주어진다. 각 발언을 다섯 중 하나로 분류한다:
- support: 쟁점의 정책·조치를 지지·찬성한다.
- oppose: 반대·철회를 주장한다.
- concern: 방향엔 동의하나 부작용·속도·방식에 우려를 표한다 (조건부).
- neutral: 입장 없이 다룬다 (사실 확인·질의·중계).
- none: 입장 판정 불가 (순수 절차·인사·다른 주제 경유).
확신이 없으면 neutral 또는 none 으로. 반드시 아래 JSON 만 출력, 발언 수와 같은 길이:
{"stances": ["support"|"oppose"|"concern"|"neutral"|"none", ...]}"""


def parse_stance_response(content: str, batch_size: int) -> list[str] | None:
    """응답 → 입장 목록. 길이 불일치·허용 밖 토큰·구조 오류면 None(재시도 신호)."""
    try:
        arr = json.loads(content).get("stances")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(arr, list) or len(arr) != batch_size:
        return None
    if any(s not in _STANCES for s in arr):
        return None
    return arr


def fetch_core_turns(issue_id: str) -> list[dict]:
    """이슈의 core turn 목록 — turn_id, speaker, role, date, 이어붙인 텍스트.
    core chunk 를 turn 별로 chunk_index 순 이어붙인다."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.turn_id, c.speaker, c.role, c.meeting_date::text AS date,
                   string_agg(c.text, ' ' ORDER BY c.chunk_index) AS text
            FROM issue_chunks ic JOIN chunks c ON c.chunk_id = ic.chunk_id
            WHERE ic.issue_id = %s AND ic.judge = 'llm_core'
            GROUP BY c.turn_id, c.speaker, c.role, c.meeting_date
            ORDER BY c.turn_id
        """, (issue_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _classify_batch(client, issue, batch, model=_MODEL):
    docs = "\n".join(f"[{i}] {t['speaker'] or ''} {t['role'] or ''}: {t['text'][:600]}"
                     for i, t in enumerate(batch))
    user = f"쟁점: {issue['title']}\n정의: {issue['description']}\n\n발언 목록:\n{docs}"
    for _ in range(2):
        delay = 2
        for retry in range(MAX_TRANSIENT_RETRIES):
            try:
                resp = client.chat.completions.create(
                    model=model, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _SYSTEM},
                              {"role": "user", "content": user}])
                break
            except _transient_errors() as e:
                if retry == MAX_TRANSIENT_RETRIES - 1:
                    raise
                print(f"[retry] {type(e).__name__} — {delay}s")
                time.sleep(delay); delay = min(delay * 2, 60)
        result = parse_stance_response(resp.choices[0].message.content, len(batch))
        if result is not None:
            return result
    return None


def store_stances(issue_id, rows, model):
    """turn 단위 upsert (멱등). rows: [(turn_id, speaker, role, stance), ...]"""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        for turn_id, speaker, role, stance in rows:
            cur.execute("""
                INSERT INTO issue_stances (issue_id, turn_id, speaker, role, stance,
                                           judge_model, map_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (issue_id, turn_id) DO UPDATE SET
                    stance=EXCLUDED.stance, speaker=EXCLUDED.speaker, role=EXCLUDED.role,
                    judge_model=EXCLUDED.judge_model, map_version=EXCLUDED.map_version,
                    mapped_at=now()
            """, (issue_id, turn_id, speaker, role, stance, model, MAP_VERSION))
    return len(rows)


def classify_issue(client, issue, judge_model=_MODEL, dry_run=False):
    turns = fetch_core_turns(issue["issue_id"])
    if dry_run:
        return {"issue_id": issue["issue_id"], "core_turns": len(turns)}
    stored, dropped = [], 0
    for batch in make_batches(turns, BATCH_SIZE):
        result = _classify_batch(client, issue, batch, judge_model)
        if result is None:
            dropped += len(batch)
            print(f"[WARN] 배치 보류 {len(batch)}건")
            continue
        for t, s in zip(batch, result):
            stored.append((t["turn_id"], t["speaker"], t["role"], s))
    n = store_stances(issue["issue_id"], stored, judge_model)
    if n + dropped != len(turns):
        raise RuntimeError(f"행수 불일치 {n}+{dropped} != {len(turns)}")
    return {"issue_id": issue["issue_id"], "core_turns": len(turns), "stored": n, "dropped": dropped}


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--judge-model", default=_MODEL)
    args = ap.parse_args()
    issues = [i for i in load_seed(SEED_PATH) if i["issue_id"] == args.issue]
    if not issues:
        print(f"[FAIL] issue_id 없음: {args.issue}"); sys.exit(1)
    init_pool()
    client = None if args.dry_run else _get_client()
    print(f"MAP_VERSION={MAP_VERSION}")
    r = classify_issue(client, issues[0], args.judge_model, args.dry_run)
    print(f"[{'DRY' if args.dry_run else 'OK'}] {json.dumps(r, ensure_ascii=False)}")
    close_pool()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 파싱 테스트 통과 확인.** Run: `python -X utf8 tests/test_issue_stance.py` → `all passed`. 그리고 `python -m pytest tests/test_issue_stance.py -q` → 1 passed.

- [ ] **Step 6: Commit.**
```bash
git add db/schema.sql scripts/build_issue_stance.py tests/test_issue_stance.py
git commit -m "feat(pol5): issue_stances 스키마 + 입장 판정 스크립트 (5택, TDD 파싱)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 스키마 적용 + medical-reform 입장 판정 실행

**Files:** (실행 태스크 — 코드 변경 없음, DB 데이터 생성)

- [ ] **Step 1: 스키마 적용.** Run:
```bash
docker exec -i national-assembly-db psql -U postgres -d national_assembly < db/schema.sql
```
Expected: `CREATE TABLE` (issue_stances) 포함, 에러 없음(기존 테이블은 IF NOT EXISTS 로 무해).

- [ ] **Step 2: dry-run 으로 대상 수 확인.** Run: `python -X utf8 scripts/build_issue_stance.py --issue medical-reform --dry-run`
Expected: `[DRY] {"issue_id": "medical-reform", "core_turns": 212}`

- [ ] **Step 3: 실판정 실행.** Run: `python -X utf8 scripts/build_issue_stance.py --issue medical-reform`
Expected: `[OK] {"issue_id": "medical-reform", "core_turns": 212, "stored": <n>, "dropped": <212-n>}` (dropped 는 형식위반 보류분, 보통 0). 비용 ~$0.1 미만.

- [ ] **Step 4: DB 적재 검증.** Run:
```bash
docker exec national-assembly-db psql -U postgres -d national_assembly -c "SELECT stance, count(*) FROM issue_stances WHERE issue_id='medical-reform' GROUP BY stance ORDER BY count(*) DESC;"
```
Expected: support/oppose/concern/neutral/none 분포 출력, 합계 = stored 수. (5택이 모두 나올 필요는 없으나 support·oppose·concern 이 섞여 나와야 정상 — 전부 neutral 이면 프롬프트 재점검.)

- [ ] **Step 5: 원장 기록만** (커밋할 코드 없음). 판정 결과 요약을 진행 노트에 남긴다.

---

## Task 3: 백엔드 집계 로직 (aggregate_stances + issue_stances DB 함수)

**Files:**
- Modify: `backend/issues.py`
- Test: `tests/test_stance_aggregate.py`

**Interfaces:**
- Produces:
  - `aggregate_stances(rows: list[dict]) -> str` — 한 행위자의 발언 stance 목록(dict에 "stance" 키)에서 행위자 레벨 라벨(`support|oppose|concern|mixed|no_stance`) 산출.
  - `issue_stances(issue_id: str) -> dict | None` — 이슈 없거나 판정 데이터 없으면 None. 있으면 `{"issue_id","title","actors":[...]}`.

- [ ] **Step 1: 집계 테스트 작성** (`tests/test_stance_aggregate.py`):
```python
"""POL-5 행위자 집계 규칙 순수 테스트.
실행: python tests/test_stance_aggregate.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import aggregate_stances  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def rows(*stances):
    return [{"stance": s} for s in stances]


def test_aggregate_stances():
    check("입장 발언 0 → no_stance", aggregate_stances(rows("neutral", "none")) == "no_stance")
    check("빈 목록 → no_stance", aggregate_stances([]) == "no_stance")
    check("단일 support", aggregate_stances(rows("support", "support", "neutral")) == "support")
    check("concern 대표 가능", aggregate_stances(rows("concern", "concern", "support")) == "concern")
    check("혼재(각 ⅓ 이상)", aggregate_stances(rows("support", "support", "oppose", "oppose")) == "mixed")
    check("혼재 아님(oppose 1/5 미만)", aggregate_stances(rows("support", "support", "support", "support", "oppose")) == "support")


if __name__ == "__main__":
    test_aggregate_stances()
    print("all passed")
```

- [ ] **Step 2: 테스트 실패 확인.** Run: `python -X utf8 tests/test_stance_aggregate.py` → FAIL `ImportError: cannot import name 'aggregate_stances'`.

- [ ] **Step 3: 집계 + DB 함수 구현.** `backend/issues.py` 끝에 추가:
```python
_STANCE_DIRS = ("support", "oppose", "concern")  # 방향(입장) 발언만 카운트


def aggregate_stances(rows: list[dict]) -> str:
    """한 행위자의 발언 stance 목록 → 행위자 레벨 라벨.
    입장 발언(support/oppose/concern)만 카운트. 0개면 no_stance. 최다가 대표.
    support·oppose 둘 다 있고 각각 입장발언의 1/3 이상이면 mixed."""
    counts = {s: 0 for s in _STANCE_DIRS}
    for r in rows:
        if r["stance"] in counts:
            counts[r["stance"]] += 1
    total = sum(counts.values())
    if total == 0:
        return "no_stance"
    if counts["support"] > 0 and counts["oppose"] > 0 \
            and counts["support"] >= total / 3 and counts["oppose"] >= total / 3:
        return "mixed"
    return max(_STANCE_DIRS, key=lambda s: counts[s])


def issue_stances(issue_id: str) -> dict | None:
    """이슈 행위자 입장 매트릭스. 이슈 없거나 판정 데이터 없으면 None.
    발언별 stance 를 speaker 로 묶어 집계(aggregate_stances) + 입장별 카운트 + 근거 인용."""
    from party import member_party
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, s.stance, c.meeting_date::text AS date,
                   min(c.chunk_id) AS chunk_id, left(min(c.text), 160) AS snippet
            FROM issue_stances s
            JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, s.stance, c.meeting_date
            ORDER BY s.speaker, c.meeting_date
        """, (issue_id,))
        stance_rows = cur.fetchall()
    if not stance_rows:
        return None

    by_speaker: dict[str, list] = {}
    for r in stance_rows:
        by_speaker.setdefault(r["speaker"], []).append(r)

    actors = []
    for speaker, rs in by_speaker.items():
        counts = {s: 0 for s in ("support", "oppose", "concern", "neutral", "none")}
        for r in rs:
            counts[r["stance"]] = counts.get(r["stance"], 0) + 1
        label = aggregate_stances(rs)
        # 근거: 대표 라벨을 뒷받침하는 발언(혼재면 support+oppose 양쪽), 없으면 전부
        support_set = {"support", "oppose"} if label == "mixed" else {label}
        cites = [r for r in rs if r["stance"] in support_set] or rs
        actors.append({
            "speaker": speaker,
            "party": member_party(speaker),
            "stance": label,
            "counts": counts,
            "citations": [{"turn_id": r["turn_id"], "stance": r["stance"], "date": r["date"],
                           "chunk_id": r["chunk_id"], "snippet": r["snippet"]} for r in cites],
        })
    actors.sort(key=lambda a: sum(a["counts"].values()), reverse=True)
    return {"issue_id": issue_id, "title": row["title"], "actors": actors}
```

- [ ] **Step 4: 테스트 통과 확인.** Run: `python -X utf8 tests/test_stance_aggregate.py` → `all passed`. `python -m pytest tests/test_stance_aggregate.py -q` → 1 passed.

- [ ] **Step 5: Commit.**
```bash
git add backend/issues.py tests/test_stance_aggregate.py
git commit -m "feat(pol5): 행위자 입장 집계 로직 + issue_stances DB 함수 (TDD)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: API 라우트 + 실 DB 스모크

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: import 확장.** `backend/main.py` 의 `from issues import issue_timeline, list_issues` 를 아래로 교체:
```python
from issues import issue_stances, issue_timeline, list_issues
```

- [ ] **Step 2: 라우트 추가.** `get_issue_timeline` 함수 블록 바로 아래에 추가:
```python
@app.get("/issues/{issue_id}/stances")
def get_issue_stances(issue_id: str):
    """쟁점 행위자 입장 매트릭스 (POL-5) — 발언 5택 판정 → 행위자 집계 + 근거."""
    result = issue_stances(issue_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"입장 데이터 없음: {issue_id}")
    return result
```

- [ ] **Step 3: 서버 기동 후 스모크** (Windows, --reload 금지, 스모크 후 종료, 127.0.0.1):
```bash
cd backend && python -m uvicorn main:app --port 8000 &
sleep 4
curl -s "http://127.0.0.1:8000/issues/medical-reform/stances" | python -X utf8 -c "import json,sys; d=json.load(sys.stdin); print('title', d['title']); print('actors', len(d['actors'])); a=d['actors'][0]; print('top', a['speaker'], a['stance'], a['counts']); print('sum_ok', sum(a['counts'].values())==len(a['citations']) or len(a['citations'])>0)"
curl -s -o /dev/null -w "404:%{http_code}\n" "http://127.0.0.1:8000/issues/no-such/stances"
```
Expected: `title 의정 갈등·의대 정원`, `actors 37` 부근, top 행위자의 speaker·stance·counts 출력, `404:404`. 확인 후 서버 종료(taskkill /F + netstat 확인).

- [ ] **Step 4: 전체 회귀.** Run: `python -m pytest tests/ -q` → 기존 63 + 신규 2 = `65 passed`.

- [ ] **Step 5: Commit.**
```bash
git add backend/main.py
git commit -m "feat(pol5): GET /issues/{id}/stances 라우트 — 행위자 입장 매트릭스

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: 스팟체크 리포트 (게이트 · POL-7 시작점)

**Files:**
- Create: `scripts/issue_stance_spotcheck.py`

- [ ] **Step 1: 스크립트 작성** (`scripts/issue_stance_spotcheck.py`):
```python
"""POL-5 입장 판정 스팟체크 — 판독용 리포트 (게이트·POL-7 시작점).

입장은 5택·주관적이라 하드 게이트(≥90%)는 비현실적. 무작위 N개 판정을 발언 원문과
대조 판독하는 리포트를 만들어 일치도 기준선을 측정한다(사람 판독 후 계산).

실행: python scripts/issue_stance_spotcheck.py --issue medical-reform
"""
import argparse
import io
import random
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SAMPLE_N = 40
RNG_SEED = 42


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool, get_conn
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    init_pool()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, s.stance, c.meeting_date::text,
                   left(string_agg(c.text, ' ' ORDER BY c.chunk_index), 500)
            FROM issue_stances s JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, s.stance, c.meeting_date
        """, (args.issue,))
        allrows = cur.fetchall()
    close_pool()
    picked = allrows if len(allrows) <= SAMPLE_N else random.Random(RNG_SEED).sample(allrows, SAMPLE_N)
    picked.sort(key=lambda r: r[0])
    lines = [f"# 입장 판정 스팟체크 — {args.issue} (판독용)", "",
             "> 판정이 맞으면 [O], 틀리면 [X] 로 표기하고 옳은 입장을 적어라.",
             "> 입장: support(찬성)/oppose(반대)/concern(우려)/neutral(중립)/none(입장없음)",
             f"> 표본 {len(picked)} / 전체 {len(allrows)}", ""]
    for turn_id, sp, role, stance, date, text in picked:
        lines += [f"- [ ] `{turn_id}` ({date}) {sp or ''} {role or ''} — 판정: **{stance}**",
                  f"      {text}", ""]
    out = ROOT / "data" / "issues" / f"stance_spotcheck_{args.issue}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out} — 사람 판독 후 일치도 계산")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행.** Run: `python -X utf8 scripts/issue_stance_spotcheck.py --issue medical-reform`
Expected: `저장: .../stance_spotcheck_medical-reform.md — 사람 판독 후 일치도 계산`. 파일에 표본 40건(또는 전체 미만) 판독 항목이 있어야 함.

- [ ] **Step 3: Commit.**
```bash
git add scripts/issue_stance_spotcheck.py data/issues/stance_spotcheck_medical-reform.md
git commit -m "eval(pol5): 입장 판정 스팟체크 리포트 — 일치도 기준선·POL-7 시작점

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: 프론트 뷰 (api.js + IssueView + App 탭)

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/IssueView.jsx`
- Modify: `frontend/src/App.jsx`

**Interfaces:**
- Consumes: `GET /issues`, `/issues/{id}/timeline`, `/issues/{id}/stances`.

- [ ] **Step 1: api.js 함수 추가.** `frontend/src/api.js` 의 `export function postQuery(...)` 위에 추가:
```javascript
export function fetchIssues() {
  return request('/issues')
}

export function fetchTimeline(issueId) {
  return request(`/issues/${encodeURIComponent(issueId)}/timeline`)
}

export function fetchStances(issueId) {
  return request(`/issues/${encodeURIComponent(issueId)}/stances`)
}
```

- [ ] **Step 2: IssueView 컴포넌트 작성** (`frontend/src/components/IssueView.jsx`):
```jsx
import { useEffect, useState } from 'react'
import { fetchIssues, fetchTimeline, fetchStances } from '../api'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

function TimelineChart({ months }) {
  if (!months || months.length === 0) return <p>타임라인 데이터 없음</p>
  const W = 640, H = 200, pad = 30
  const maxC = Math.max(...months.map(m => m.corpus_turns), 1)
  const maxM = Math.max(...months.map(m => m.mapped_core_turns), 1)
  const x = i => pad + i * (W - 2 * pad) / Math.max(months.length - 1, 1)
  const yC = v => H - pad - v / maxC * (H - 2 * pad)
  const yM = v => H - pad - v / maxM * (H - 2 * pad)
  const line = (fy, key) => months.map((m, i) => `${x(i).toFixed(1)},${fy(m[key]).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="이슈 월별 발언 추이">
      <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={line(yC, 'corpus_turns')} />
      <polyline fill="none" stroke="#d97706" strokeWidth="1.5" strokeDasharray="5 3" points={line(yM, 'mapped_core_turns')} />
      <text x={pad} y={H - 8} fontSize="11" fill="#666">{months[0].month}</text>
      <text x={W - pad} y={H - 8} fontSize="11" fill="#666" textAnchor="end">{months[months.length - 1].month}</text>
      <text x={pad} y={16} fontSize="11" fill="#2563eb">— 코퍼스 발언량(좌축 정규화)</text>
      <text x={pad} y={30} fontSize="11" fill="#d97706">-- 매핑 core(우축 정규화)</text>
    </svg>
  )
}

function StanceRow({ actor }) {
  const [open, setOpen] = useState(false)
  const c = actor.counts
  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: 'pointer' }}>
        <td>{actor.speaker}</td>
        <td>{actor.party || '—'}</td>
        <td><span style={{ color: STANCE_COLOR[actor.stance], fontWeight: 600 }}>{STANCE_KO[actor.stance]}</span></td>
        <td style={{ fontSize: 12 }}>찬{c.support}·반{c.oppose}·우{c.concern}·중{c.neutral}·무{c.none}</td>
        <td>{open ? '▲' : '▼'}</td>
      </tr>
      {open && actor.citations.map(cit => (
        <tr key={cit.turn_id}><td colSpan="5" style={{ fontSize: 12, color: '#444', padding: '4px 12px' }}>
          [{STANCE_KO[cit.stance] || cit.stance} · {cit.date}] {cit.snippet}…
        </td></tr>
      ))}
    </>
  )
}

export default function IssueView() {
  const [issues, setIssues] = useState([])
  const [sel, setSel] = useState('medical-reform')
  const [timeline, setTimeline] = useState(null)
  const [stances, setStances] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => { fetchIssues().then(d => setIssues(d.issues)).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    if (!sel) return
    setError(null); setTimeline(null); setStances(null)
    fetchTimeline(sel).then(setTimeline).catch(e => setError(e.message))
    fetchStances(sel).then(setStances).catch(e => setError(e.message))
  }, [sel])

  return (
    <div>
      <label>이슈: <select value={sel} onChange={e => setSel(e.target.value)}>
        {issues.map(i => <option key={i.issue_id} value={i.issue_id}>{i.title}</option>)}
      </select></label>
      {error && <p style={{ color: '#dc2626' }}>{error}</p>}
      <h3>월별 발언 추이</h3>
      {timeline ? <TimelineChart months={timeline.months} /> : <p>불러오는 중…</p>}
      <h3>행위자 입장 {stances ? `(${stances.actors.length}명)` : ''}</h3>
      {stances ? (
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th>발언자</th><th>정당</th><th>입장</th><th>발언 수</th><th></th></tr></thead>
          <tbody>{stances.actors.map(a => <StanceRow key={a.speaker} actor={a} />)}</tbody>
        </table>
      ) : <p>입장 데이터 없음(판정된 이슈만 표시)</p>}
    </div>
  )
}
```

- [ ] **Step 3: App.jsx 에 탭 토글 추가.** `frontend/src/App.jsx` 상단 import 에 추가:
```javascript
import IssueView from './components/IssueView'
```
그리고 `function App()` 의 상태 선언부에 `const [tab, setTab] = useState('query')` 를 추가한 뒤, 최상위 반환 JSX 의 제목(첫 헤더) 바로 아래에 탭 버튼과 분기를 삽입한다. 기존 질의 UI 전체를 `{tab === 'query' && ( ... )}` 로 감싸고, 그 뒤에 `{tab === 'issues' && <IssueView />}` 를 둔다. 탭 버튼:
```jsx
<div style={{ marginBottom: 12 }}>
  <button onClick={() => setTab('query')} disabled={tab === 'query'}>질의</button>
  <button onClick={() => setTab('issues')} disabled={tab === 'issues'}>쟁점 분석</button>
</div>
```
(기존 질의 UI 를 감싸는 것 외에 그 내부 로직·핸들러는 변경하지 않는다.)

- [ ] **Step 4: 빌드·lint 확인.** Run: `cd frontend && npm run build`
Expected: 빌드 성공(에러 0). `npm run lint` 가 있으면 통과.

- [ ] **Step 5: 실제 화면 확인 (사람 검증).** 백엔드(8000)와 `cd frontend && npm run dev`(5173) 를 띄우고 브라우저에서 "쟁점 분석" 탭 → medical-reform 선택 → 타임라인 2선 + 입장표(행 클릭 시 근거 펼침)가 보이는지 확인. **이 스텝이 이번 사이클의 목적("브라우저에서 직접 확인")이다.** 확인 후 dev 서버 종료.

- [ ] **Step 6: Commit.**
```bash
git add frontend/src/api.js frontend/src/components/IssueView.jsx frontend/src/App.jsx
git commit -m "feat(pol5): 프론트 쟁점 분석 뷰 — 타임라인 차트 + 입장 매트릭스 (POL-9 축소판)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: 문서 갱신

**Files:**
- Modify: `docs/progress.md`

- [ ] **Step 1: 로드맵 표 POL-5 행 갱신.** `docs/progress.md` 로드맵 표 POL-5 행 상태 칸 `⬜` 을 아래로 교체:
```
🔶 파일럿 (2026-07-09 medical-reform: 입장판정+매트릭스 API+프론트 뷰. 전체 확장·하드게이트·POL-7 잔여)
```

- [ ] **Step 2: 최종 업데이트 줄 교체.** `docs/progress.md:3` 을:
```
최종 업데이트: 2026-07-09 (POL-5 입장 분석 파일럿 — medical-reform + 프론트 뷰. 다음: POL-6 여야 구도 or POL-5 전체 확장)
```

- [ ] **Step 3: POL-5 구현 기록 추가.** "POL-4 구현 기록" 섹션 뒤에 추가:
```markdown
#### POL-5 구현 기록 — 입장 분석 파일럿 (2026-07-09)

- 파일럿 = medical-reform. core 212 turn 을 gpt-4o-mini 5택 판정
  (support/oppose/concern/neutral/none) → issue_stances 저장 → 행위자 집계.
- 집계: 입장 발언(찬반우려)만 카운트, 0개면 no_stance, 최다 대표, 찬반 각 ⅓↑면 mixed.
  카운트+근거 항상 노출(단일 라벨은 편의). 여야는 POL-6 로 미룸(정권교체 시점성).
- `GET /issues/{id}/stances` + 프론트 IssueView(타임라인 차트 + 입장표, POL-9 축소판) —
  브라우저에서 직접 확인.
- 게이트: 입장은 5택·주관적이라 하드 게이트 대신 스팟체크 일치도 기준선
  (stance_spotcheck_medical-reform.md) = POL-7 라벨 시작점.
- 범위 밖: 전체 24이슈 확장, 하드 게이트·임계값, POL-7 정식 라벨, 여야 구도(POL-6),
  시계열 입장.
```

- [ ] **Step 4: Commit.**
```bash
git add docs/progress.md
git commit -m "docs(pol5): 입장 분석 파일럿 기록 + 로드맵 POL-5 🔶

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review 체크 결과

- **Spec coverage**: 5택 판정→Task1 스크립트. turn 단위→fetch_core_turns(string_agg). issue_stances 저장→Task1 스키마+store. 집계 규칙(no_stance/대표/mixed)→Task3 aggregate_stances+테스트. API 매트릭스(counts+citations+party)→Task3 issue_stances+Task4 라우트. 게이트/POL-7 시작점→Task5. 프론트 뷰(드롭다운·타임라인·입장표)→Task6. 여야 POL-6 유보·party만→Task3. 문서→Task7. ✅
- **Placeholder scan**: 모든 코드 스텝에 완전한 코드·명령·기대 출력. Task6 Step3 App.jsx 만 "감싸기" 서술형이나, 기존 파일 구조 의존이라 구체 위치를 지정(제목 아래·기존 UI를 tab==='query'로 감쌈). ✅
- **Type consistency**: `parse_stance_response(str,int)->list[str]|None`(Task1), `aggregate_stances(list[dict])->str`·`issue_stances(str)->dict|None`(Task3), `fetchTimeline/fetchStances`(Task6) — 정의·사용 일치. stance 어휘: 발언 5택 vs 행위자 5종(mixed/no_stance) 스펙과 일치. ✅
- **주의**: Task2·3 는 Task1 스키마+데이터에 의존(순서 고정). Task4 는 Task3, Task6 는 Task4 에 의존.
