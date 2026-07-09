# POL-4 쟁점 타임라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 이슈별 월별 발언 추이를 병행 2축(코퍼스 직접 집계 + 매핑 표본 core/전체)으로 반환하는 `GET /issues/{id}/timeline` API를 만든다.

**Architecture:** `backend/issues.py` 신설(actors.py 패턴 — 모듈에 순수 집계 함수, main.py는 얇은 라우트 + 404). 두 SQL(코퍼스 ILIKE 볼륨 + 매핑 조인 볼륨)을 파이썬에서 month 키로 병합하고 갭을 0으로 채워 반환. main.py의 인라인 `list_issues`도 이 모듈로 이관.

**Tech Stack:** FastAPI, psycopg2(RealDictCursor), PostgreSQL(pg_trgm ILIKE), pytest.

## Global Constraints

- turn 단위 집계: `count(DISTINCT turn_id)` — 청크 분할 중복 카운트 방지 (actors.py 교훈). 매핑 쪽은 `chunks.turn_id`(NOT NULL 권위) 사용, `issue_chunks.turn_id`(nullable) 아님.
- ILIKE 패턴은 `search_keyword._like_escape` 재사용 — `%`·`_` 이스케이프.
- 순수 로직 테스트 우선(DB·LLM 없이), 파일은 `if __name__ == "__main__"` 가드 + `check()` assert 패턴 (tests/ 관례).
- 읽기 전용, 기존 `get_conn()` 풀 사용.
- Windows: API 호출은 `127.0.0.1`. 서버 실행 `cd backend && python -m uvicorn main:app --port 8000` (--reload 금지, 수동 재시작).
- 커밋 트레일러: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## File Structure

- **Create** `backend/issues.py` — 순수 로직(`build_keyword_patterns`, `merge_months`) + DB 집계(`issue_timeline`, `list_issues`).
- **Modify** `backend/main.py` — `GET /issues/{id}/timeline` 라우트 추가; 기존 `GET /issues` 를 `issues.list_issues` 위임으로 교체; import 정리.
- **Create** `tests/test_issue_timeline.py` — 순수 로직 테스트(패턴 빌더, 월 병합·갭 채우기).

---

## Task 1: 순수 로직 — 키워드 패턴 빌더 + 월 병합/갭 채우기

**Files:**
- Create: `backend/issues.py`
- Test: `tests/test_issue_timeline.py`

**Interfaces:**
- Produces:
  - `build_keyword_patterns(keywords: list[str]) -> list[str]` — 각 키워드를 `%...%` ILIKE 패턴으로(이스케이프 포함). 빈 리스트면 `[]`.
  - `merge_months(corpus: dict[str,int], mapped: dict[str,tuple[int,int]]) -> list[dict]` — corpus는 `{month: corpus_turns}`, mapped는 `{month: (mapped_turns, mapped_core_turns)}`. 두 계열 합집합의 최소~최대 월 사이 모든 달을 포함(빈 달은 0), month 오름차순 정렬. 각 원소 `{"month","corpus_turns","mapped_turns","mapped_core_turns"}`. 양쪽 다 비면 `[]`.

- [ ] **Step 1: Write the failing test**

`tests/test_issue_timeline.py`:
```python
"""POL-4 타임라인 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_timeline.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import build_keyword_patterns, merge_months  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_build_keyword_patterns():
    check("기본 패턴", build_keyword_patterns(["계엄"]) == ["%계엄%"])
    check("% 이스케이프", build_keyword_patterns(["50%"]) == ["%50\\%%"])
    check("_ 이스케이프", build_keyword_patterns(["a_b"]) == ["%a\\_b%"])
    check("빈 입력", build_keyword_patterns([]) == [])


def test_merge_months_basic():
    corpus = {"2024-12": 1478, "2025-01": 241}
    mapped = {"2024-12": (166, 75), "2025-01": (24, 15)}
    out = merge_months(corpus, mapped)
    check("2개월 정렬", [m["month"] for m in out] == ["2024-12", "2025-01"], out)
    check("첫 달 값", out[0] == {"month": "2024-12", "corpus_turns": 1478,
                                 "mapped_turns": 166, "mapped_core_turns": 75}, out[0])


def test_merge_months_gap_fill():
    # 2024-12 와 2025-03 사이 1·2월은 빈 달 → 0 으로 채움
    out = merge_months({"2024-12": 10, "2025-03": 5}, {})
    months = [m["month"] for m in out]
    check("갭 채움", months == ["2024-12", "2025-01", "2025-02", "2025-03"], months)
    check("빈 달 0", out[1] == {"month": "2025-01", "corpus_turns": 0,
                                "mapped_turns": 0, "mapped_core_turns": 0}, out[1])


def test_merge_months_one_sided_and_empty():
    # 매핑만 있는 달(코퍼스 0), 코퍼스만 있는 달(매핑 0) 이 합집합 범위에 포함
    out = merge_months({"2025-02": 3}, {"2024-12": (2, 1)})
    check("합집합 범위", [m["month"] for m in out] == ["2024-12", "2025-01", "2025-02"], out)
    check("매핑만 달", out[0] == {"month": "2024-12", "corpus_turns": 0,
                                  "mapped_turns": 2, "mapped_core_turns": 1}, out[0])
    check("코퍼스만 달", out[2] == {"month": "2025-02", "corpus_turns": 3,
                                    "mapped_turns": 0, "mapped_core_turns": 0}, out[2])
    check("양쪽 빈 입력", merge_months({}, {}) == [])


if __name__ == "__main__":
    test_build_keyword_patterns()
    test_merge_months_basic()
    test_merge_months_gap_fill()
    test_merge_months_one_sided_and_empty()
    print("all passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -X utf8 tests/test_issue_timeline.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'issues'` (아직 backend/issues.py 없음)

- [ ] **Step 3: Write minimal implementation**

`backend/issues.py` (순수 로직 부분만 — DB 함수는 Task 2·3에서 추가):
```python
"""쟁점 API (POL-3 목록 + POL-4 타임라인).

타임라인 설계 (docs/superpowers/specs/2026-07-09-pol4-issue-timeline-design.md):
  이슈별 월별 발언 추이를 병행 2축으로 반환한다.
  - corpus_turns: 시드 키워드로 전체 chunks ILIKE 검색한 월별 turn 수 (재현율 축,
    키워드 노이즈 포함 — 두 선 간격이 "스침 많은 달"을 드러냄)
  - mapped_turns / mapped_core_turns: issue_chunks 매핑의 월별 turn 수 (정밀도 축,
    분기 상한 있음). core 만 POL-5·POL-6 이 소비.
  집계는 turn 단위(actors.py 교훈). 매핑은 chunks.turn_id(NOT NULL 권위) 사용.
"""

from psycopg2.extras import RealDictCursor

from db import get_conn
from search_keyword import _like_escape


def build_keyword_patterns(keywords: list[str]) -> list[str]:
    """시드 키워드 → ILIKE 부분일치 패턴 (내용 이스케이프, 양끝 % 와일드카드)."""
    return [f"%{_like_escape(k)}%" for k in keywords]


def _month_range(months: list[str]) -> list[str]:
    """'YYYY-MM' 목록의 최소~최대 사이 모든 달을 오름차순으로. 빈 목록이면 []."""
    if not months:
        return []
    lo, hi = min(months), max(months)
    y, m = int(lo[:4]), int(lo[5:7])
    hy, hm = int(hi[:4]), int(hi[5:7])
    out = []
    while (y, m) <= (hy, hm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def merge_months(corpus: dict, mapped: dict) -> list[dict]:
    """두 월별 집계를 합집합 범위로 병합 + 빈 달 0 채움. month 오름차순."""
    all_months = list(corpus.keys()) + list(mapped.keys())
    rows = []
    for month in _month_range(all_months):
        mt, mc = mapped.get(month, (0, 0))
        rows.append({
            "month": month,
            "corpus_turns": corpus.get(month, 0),
            "mapped_turns": mt,
            "mapped_core_turns": mc,
        })
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -X utf8 tests/test_issue_timeline.py`
Expected: 모든 `[PASS]` 출력 후 `all passed`

pytest 수집도 확인: `python -m pytest tests/test_issue_timeline.py -q`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add backend/issues.py tests/test_issue_timeline.py
git commit -m "feat(pol4): 타임라인 순수 로직 — 키워드 패턴·월 병합·갭 채우기 (TDD)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: DB 집계 — issue_timeline + list_issues 이관

**Files:**
- Modify: `backend/issues.py` (Task 1이 만든 파일에 함수 추가)

**Interfaces:**
- Consumes: `build_keyword_patterns`, `merge_months` (Task 1), `get_conn`, `RealDictCursor`.
- Produces:
  - `issue_timeline(issue_id: str) -> dict | None` — 이슈 없으면 None. 있으면 `{"issue_id","title","months":[...]}`. months는 `merge_months` 결과.
  - `list_issues() -> dict` — `{"issues":[...]}`. main.py 인라인 쿼리와 동일 컬럼(issue_id,title,type,description,chunk_count,turn_count,core_chunk_count).

- [ ] **Step 1: Append implementation to `backend/issues.py`**

Task 1 파일 끝에 추가:
```python
def issue_timeline(issue_id: str) -> dict | None:
    """이슈 월별 발언 추이 (병행 2축). 이슈 미존재 시 None → 라우트에서 404.

    corpus: 시드 키워드 ILIKE 로 전체 chunks 월별 turn 수 (키워드 없으면 건너뜀).
    mapped: issue_chunks 조인 월별 turn 수(전체/core), chunks.turn_id 사용.
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title, seed FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        keywords = (row["seed"] or {}).get("keywords", [])

        corpus: dict[str, int] = {}
        patterns = build_keyword_patterns(keywords)
        if patterns:
            cur.execute(
                """
                SELECT to_char(meeting_date, 'YYYY-MM') AS month,
                       count(DISTINCT turn_id) AS corpus_turns
                FROM chunks
                WHERE text ILIKE ANY(%s)
                GROUP BY 1
                """,
                (patterns,),
            )
            corpus = {r["month"]: r["corpus_turns"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT to_char(c.meeting_date, 'YYYY-MM') AS month,
                   count(DISTINCT c.turn_id) AS mapped_turns,
                   count(DISTINCT c.turn_id) FILTER (WHERE ic.judge = 'llm_core')
                       AS mapped_core_turns
            FROM issue_chunks ic JOIN chunks c ON c.chunk_id = ic.chunk_id
            WHERE ic.issue_id = %s
            GROUP BY 1
            """,
            (issue_id,),
        )
        mapped = {r["month"]: (r["mapped_turns"], r["mapped_core_turns"])
                  for r in cur.fetchall()}

    return {"issue_id": issue_id, "title": row["title"],
            "months": merge_months(corpus, mapped)}


def list_issues() -> dict:
    """쟁점 사전 목록 (POL-3). main.py 인라인에서 이관 — 이슈 API 응집."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.issue_id, i.title, i.type, i.description,
                   count(ic.chunk_id)          AS chunk_count,
                   count(DISTINCT ic.turn_id)  AS turn_count,
                   count(*) FILTER (WHERE ic.judge = 'llm_core') AS core_chunk_count
            FROM issues i LEFT JOIN issue_chunks ic USING (issue_id)
            GROUP BY i.issue_id, i.title, i.type, i.description
            ORDER BY chunk_count DESC, issue_id
        """)
        return {"issues": cur.fetchall()}
```

- [ ] **Step 2: Import 스모크 — 구문·의존성 확인**

Run: `cd backend && python -c "import issues; print('ok', hasattr(issues,'issue_timeline'), hasattr(issues,'list_issues'))"`
Expected: `ok True True`

- [ ] **Step 3: Commit**

```bash
git add backend/issues.py
git commit -m "feat(pol4): issue_timeline DB 집계 + list_issues 이관

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 라우트 연결 + 실 DB 스모크

**Files:**
- Modify: `backend/main.py` (import 추가, `/issues` 위임 교체, `/issues/{id}/timeline` 추가)

**Interfaces:**
- Consumes: `issues.issue_timeline`, `issues.list_issues` (Task 2).

- [ ] **Step 1: main.py import 추가**

`backend/main.py:16` 의 `from actors import actor_profile` 아래에 추가:
```python
from issues import issue_timeline, list_issues
```

- [ ] **Step 2: 기존 `/issues` 라우트를 위임으로 교체**

`backend/main.py` 의 현재 블록:
```python
@app.get("/issues")
def list_issues():
    """쟁점 사전 목록 (POL-3). 상세·타임라인은 POL-4 에서 확장한다."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.issue_id, i.title, i.type, i.description,
                   count(ic.chunk_id)          AS chunk_count,
                   count(DISTINCT ic.turn_id)  AS turn_count,
                   count(*) FILTER (WHERE ic.judge = 'llm_core') AS core_chunk_count
            FROM issues i LEFT JOIN issue_chunks ic USING (issue_id)
            GROUP BY i.issue_id, i.title, i.type, i.description
            ORDER BY chunk_count DESC, issue_id
        """)
        return {"issues": cur.fetchall()}
```
을 아래로 교체 (함수명 충돌 방지 위해 라우트 핸들러명은 `get_issues`):
```python
@app.get("/issues")
def get_issues():
    """쟁점 사전 목록 (POL-3)."""
    return list_issues()


@app.get("/issues/{issue_id}/timeline")
def get_issue_timeline(issue_id: str):
    """쟁점 월별 발언 추이 (POL-4) — 병행 2축(코퍼스 직접 + 매핑 core/전체)."""
    result = issue_timeline(issue_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"쟁점 없음: {issue_id}")
    return result
```

- [ ] **Step 3: 서버 기동 후 실 DB 스모크 (계엄 이슈)**

서버를 백그라운드로 띄우고(수동 재시작 방식) 확인:
```bash
cd backend && python -m uvicorn main:app --port 8000 &
sleep 4
curl -s "http://127.0.0.1:8000/issues/martial-law/timeline" | python -X utf8 -c "import json,sys; d=json.load(sys.stdin); m={x['month']:x for x in d['months']}; print('title', d['title']); print('2024-12', m['2024-12'])"
```
Expected 출력:
```
title 12·3 비상계엄과 탄핵 정국
2024-12 {'month': '2024-12', 'corpus_turns': 1478, 'mapped_turns': 166, 'mapped_core_turns': 75}
```
(스펙 실측과 일치. mapped 수치는 등급화 상태에 따라 다를 수 있으나 corpus_turns 1478 은 고정.)

- [ ] **Step 4: 404 + 기존 /issues 회귀 스모크**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8000/issues/no-such-issue/timeline"
curl -s "http://127.0.0.1:8000/issues" | python -X utf8 -c "import json,sys; print('issues', len(json.load(sys.stdin)['issues']))"
```
Expected:
```
404
issues 24
```
확인 후 서버 종료(TaskStop 또는 프로세스 종료).

- [ ] **Step 5: 전체 테스트 스위트 회귀**

Run: `python -m pytest tests/ -q`
Expected: 기존 58 passed + 신규 4 = `62 passed` (경고 1 무방)

- [ ] **Step 6: Commit**

```bash
git add backend/main.py
git commit -m "feat(pol4): GET /issues/{id}/timeline 라우트 + /issues 위임 — 병행 2축 추이

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: 문서 갱신 — progress.md 로드맵 + POL-4 기록

**Files:**
- Modify: `docs/progress.md` (로드맵 표 POL-4 행, 최종 업데이트 줄, POL-4 구현 기록 추가)

- [ ] **Step 1: 로드맵 표 POL-4 행을 ✅ 로**

`docs/progress.md` 로드맵 표에서 POL-4 행의 상태 칸 `⬜` 을 아래로 교체:
```
✅ 2026-07-09 (GET /issues/{id}/timeline — 병행 2축 코퍼스+매핑, 계엄 스모크 일치)
```

- [ ] **Step 2: 최종 업데이트 줄 갱신**

`docs/progress.md:3` 을 교체:
```
최종 업데이트: 2026-07-09 (POL-4 타임라인 API 완료 — 병행 2축. 다음: POL-5 입장 분석)
```

- [ ] **Step 3: POL-4 구현 기록 추가**

progress.md 의 "POL-3 마감" 섹션 뒤에 추가:
```markdown
#### POL-4 구현 기록 — 쟁점 타임라인 (2026-07-09)

- `GET /issues/{id}/timeline` — 이슈별 월별 발언 추이를 **병행 2축**으로 반환:
  corpus_turns(시드 키워드 ILIKE 전체 코퍼스 볼륨·재현율 축) + mapped_turns/
  mapped_core_turns(매핑 표본·정밀도 축, 분기 상한). 모두 turn 단위.
- **병행 근거**(설계 spec 참조): 매핑은 분기 층화라 월별 볼륨 비례 안 함 — 계엄 실측
  2024-12 코퍼스 1478 vs 매핑 166(11%) vs core 75(5%), 한산한 달은 포착률 20~25% 로
  피크 압축. 매핑 단독은 "가짜 성장 곡선"(최종리뷰 Critical), 코퍼스 단독은 키워드
  노이즈 → 두 선 병행으로 간격이 "스침 많은 달"을 드러냄
- 구조: `backend/issues.py` 신설(순수 로직 build_keyword_patterns·merge_months +
  DB issue_timeline·list_issues), main.py 얇은 라우트. list_issues 이관(응집)
- 테스트: 순수 로직 4건(패턴 이스케이프·월 병합·갭 채우기·합집합 범위) + 계엄 스모크
- 한계: 코퍼스 축은 키워드 노이즈 포함(정밀 볼륨 아님), 매핑 축은 상한(절대 볼륨 아님).
  POL-5/6 은 core 만 소비. 위원회 분포·주요 회의는 POL-8 로 미룸
```

- [ ] **Step 4: Commit**

```bash
git add docs/progress.md
git commit -m "docs(pol4): 타임라인 완료 기록 + 로드맵 POL-4 ✅ 전환

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review 체크 결과

- **Spec coverage**: 병행 2축(corpus/mapped/core) → Task 2 쿼리. 월 갭 채우기(합집합 범위) → Task 1 merge_months. 키워드 노이즈 노출 → Task 2(시드 키워드 그대로) + 문서. 404 → Task 3. turn 단위/chunks.turn_id → Global Constraints + Task 2. list_issues 이관 → Task 2·3. 테스트 → Task 1·3. 범위 밖(위원회·회의·UI) → 계획에 미포함(의도적). ✅ 누락 없음.
- **Placeholder scan**: 모든 코드 스텝에 실제 코드·명령·기대 출력 포함. "적절히 처리" 류 없음. ✅
- **Type consistency**: `build_keyword_patterns(list[str])->list[str]`, `merge_months(dict,dict)->list[dict]`, `issue_timeline(str)->dict|None`, `list_issues()->dict` — Task 1 정의와 Task 2·3 사용 일치. mapped dict 값은 `(turns, core)` 튜플로 Task 1 테스트·Task 2 생성 동일. ✅
