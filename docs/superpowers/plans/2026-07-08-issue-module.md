# POL-3 쟁점 모듈 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 22대 국회 주요 이슈 20~30개 사전과 이슈↔청크 매핑을 구축해 POL-4~6(타임라인·입장·여야 구도)의 공통 기반을 만든다.

**Architecture:** 코퍼스 탐사로 이슈 후보 추출 → 사용자 확정(사람 게이트) → 이슈별 하이브리드 검색으로 후보 청크 수집 → 저점수 컷 → gpt-4o-mini 배치 관련도 판정 → `issue_chunks` 적재 → 스팟체크 정밀도 게이트. 스펙: `docs/issue_module_spec.md`.

**Tech Stack:** Python, psycopg2(+pgvector), OpenAI gpt-4o-mini(판정)·text-embedding-3-small(검색 축), FastAPI, pytest.

## Global Constraints

- 신뢰 원칙: **누락 > 오염** — 확신 없으면 매핑하지 않는다 (스펙 "전제")
- **bill_refs 사용 금지** (POL-1 판정 — 스펙 "전제")
- 단위 테스트는 **LLM·DB 없이 순수 로직만** — DB 필요 테스트는 test_api.py 패턴(HAS_DB skip)
- 스크립트는 `if __name__ == "__main__":` 에서만 stdout 재래핑 (import 부작용 금지 — 2026-07-06 수정 관례)
- 스크립트의 backend 모듈 사용은 `sys.path.insert(0, .../backend)` (build_members.py 패턴)
- DB 적재는 이슈 단위 DELETE+재삽입, 적재 후 행수 검증 (jsonl_to_postgres 패턴)
- OpenAI 일시 오류(RateLimit/Timeout/Connection/5xx)만 재시도 (embeddings_v1 패턴)
- 커밋 메시지는 한국어, `feat:`/`test:`/`docs:` 프리픽스 (git log 관례)
- 저점수 컷 임계값은 `.env` `GROUNDING_SIM_THRESHOLD`(기본 0.4) 재사용 — 새 임계값 발명 금지
- 매핑 버전 상수 `MAP_VERSION = "v1.0"` — 방법이 바뀌면 올린다

---

### Task 1: DB 스키마 — issues · issue_chunks

**Files:**
- Modify: `db/schema.sql` (파일 끝, 보안 원칙 주석 위 아님 — members 테이블 다음)

**Interfaces:**
- Produces: `issues(issue_id PK, title, type, description, seed, created_at)`, `issue_chunks(issue_id, chunk_id, turn_id, vec_score, kw_hit, judge, map_version, mapped_at, PK(issue_id, chunk_id))` — Task 6 적재, Task 8 API 가 이 스키마에 의존

- [ ] **Step 1: schema.sql 에 테이블 2개 추가**

`db/schema.sql` 끝(members 테이블 블록 다음)에 추가:

```sql
-- 8. 쟁점 사전 (POL-3). data/issues/issues_seed.json 을 build_issue_map.py 가 적재한다.
CREATE TABLE IF NOT EXISTS issues (
  issue_id    TEXT PRIMARY KEY,       -- 슬러그: martial-law, ai-basic-act
  title       TEXT NOT NULL,          -- 표시명: 12·3 비상계엄
  type        TEXT NOT NULL,          -- event | policy
  description TEXT NOT NULL,          -- LLM 관련도 판정의 기준문
  seed        JSONB NOT NULL,         -- keywords/queries/anchor_meetings 원본
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- 9. 이슈↔청크 매핑 (POL-3). 검색 확장 + LLM 판정 통과분만 저장 (누락 > 오염).
--    turn_id 동시 저장: POL-4 집계는 turn 단위 (청크 분할 중복 방지 — actors.py 교훈)
CREATE TABLE IF NOT EXISTS issue_chunks (
  issue_id    TEXT NOT NULL REFERENCES issues(issue_id) ON DELETE CASCADE,
  chunk_id    TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  turn_id     TEXT,
  vec_score   REAL,                   -- 후보 수집 시 벡터 유사도 (키워드 단독 편입이면 NULL)
  kw_hit      BOOLEAN,                -- 키워드 매치 여부 (디버깅)
  judge       TEXT NOT NULL,          -- 편입 근거: 현재 'llm_relevant' 단일
  map_version TEXT NOT NULL,          -- 매핑 방법 버전 (재매핑 추적)
  mapped_at   TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (issue_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_issue_chunks_chunk ON issue_chunks(chunk_id);
```

- [ ] **Step 2: 스키마 적용 + 확인**

임시 스크립트로 적용 (schema.sql 은 IF NOT EXISTS 라 반복 안전):

```powershell
$code = @'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))
from db import init_pool, close_pool, get_conn
init_pool()
sql = Path("db/schema.sql").read_text(encoding="utf-8")
with get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    cur.execute("SELECT to_regclass('issues'), to_regclass('issue_chunks')")
    print(cur.fetchone())
close_pool()
'@
Set-Content -Path apply_schema_tmp.py -Value $code -Encoding utf8
python apply_schema_tmp.py
Remove-Item apply_schema_tmp.py
```

Expected: `('issues', 'issue_chunks')`

- [ ] **Step 3: Commit**

```powershell
git add db/schema.sql
git commit -m "feat(pol3): issues·issue_chunks 스키마 — 쟁점 사전 + 매핑 테이블"
```

---

### Task 2: 이슈 후보 탐사 — 순수 로직 (TDD)

**Files:**
- Create: `scripts/issue_candidates.py` (순수 함수 부분만 — main 은 Task 3)
- Test: `tests/test_issue_candidates.py`

**Interfaces:**
- Produces (Task 3 이 사용):
  - `detect_spikes(rows: list[tuple], ratio: float = 1.8) -> list[dict]` — rows 는 `(committee: str, month: 'YYYY-MM', turn_count: int)`, 반환 `[{"committee","month","count","median","ratio"}]` ratio 내림차순
  - `top_agenda_lines(lines: list[str], top_n: int = 40) -> list[tuple[str, int]]` — (정규화된 안건 줄, 빈도) 빈도 내림차순
  - `parse_topics(content: str) -> list[str]` — LLM 요약 JSON 응답 → 주제 목록, 실패 시 `[]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_issue_candidates.py`:

```python
"""이슈 후보 탐사 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_candidates.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from issue_candidates import detect_spikes, parse_topics, top_agenda_lines  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_detect_spikes_basic():
    # 국방위 2024-12 가 중앙값(100)의 3배 — 스파이크로 잡혀야 한다 (계엄 자기 검증과 동일 구조)
    rows = [("국방위", "2024-10", 100), ("국방위", "2024-11", 100),
            ("국방위", "2024-12", 300), ("국방위", "2025-01", 110)]
    spikes = detect_spikes(rows, ratio=1.8)
    check("스파이크 1건", len(spikes) == 1, spikes)
    check("월 식별", spikes[0]["month"] == "2024-12", spikes[0])
    check("배율 계산", spikes[0]["ratio"] == 3.0, spikes[0])


def test_detect_spikes_below_ratio():
    rows = [("과방위", "2024-10", 100), ("과방위", "2024-11", 100),
            ("과방위", "2024-12", 150)]
    check("1.8배 미만은 비스파이크", detect_spikes(rows, ratio=1.8) == [])


def test_detect_spikes_needs_three_months():
    # 월 2개뿐이면 중앙값이 무의미 — 판단 불가로 제외
    rows = [("기재위", "2026-05", 10), ("기재위", "2026-06", 100)]
    check("월 3 미만 위원회 제외", detect_spikes(rows) == [])


def test_top_agenda_lines():
    lines = ["  방송법 일부개정법률안  ", "방송법 일부개정법률안", "인사청문요청안",
             "짧다", "방송법 일부개정법률안"]
    top = top_agenda_lines(lines, top_n=2)
    check("빈도 1위", top[0] == ("방송법 일부개정법률안", 3), top)
    check("8자 미만 제외", all("짧다" != t[0] for t in top), top)


def test_parse_topics():
    check("정상", parse_topics('{"topics": ["AI 기본법", "계엄"]}') == ["AI 기본법", "계엄"])
    check("topics 아님", parse_topics('{"other": 1}') == [])
    check("JSON 아님", parse_topics("주제: 계엄") == [])
    check("문자열 아닌 항목 걸러냄", parse_topics('{"topics": ["a", 3]}') == ["a"])


if __name__ == "__main__":
    for fn in [test_detect_spikes_basic, test_detect_spikes_below_ratio,
               test_detect_spikes_needs_three_months, test_top_agenda_lines, test_parse_topics]:
        fn()
    print("ALL PASS")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_issue_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'issue_candidates'`

- [ ] **Step 3: 순수 함수 구현**

`scripts/issue_candidates.py` (main 없이 함수부만 — main 은 Task 3 에서 추가):

```python
"""이슈 후보 탐사 (POL-3 단계 1) — 코퍼스에서 이슈 후보 40~60개를 근거와 함께 추출.

네 가지 신호를 교차한다 (docs/issue_module_spec.md):
  1. 시계열 스파이크 — 위원회×월 발언량 급증 (사건형 후보)
  2. agenda 빈발 줄 — 안건 섹션 반복 의제 (정책형 후보)
  3. LLM 표본 요약 — 위원회×분기 표본 청크의 반복 주제
  4. query_logs — 실사용 질문 교차

산출: data/issues/candidates_report.md (사용자 검수용 — 확정은 사람이 한다)
실행: python scripts/issue_candidates.py
"""

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "issues"
REPORT = OUT_DIR / "candidates_report.md"
NORMALIZED_DIR = ROOT / "data" / "v1" / "normalized"

SPIKE_RATIO = 1.8       # 월 발언량 ≥ 위원회 월 중앙값 × 이 배율 → 스파이크
SAMPLE_PER_CELL = 30    # 위원회×분기당 LLM 요약 표본 청크 수
MIN_AGENDA_LEN = 8      # 이보다 짧은 안건 줄은 잡음 ("산회" 등)
_MODEL = "gpt-4o-mini"


def detect_spikes(rows: list[tuple], ratio: float = SPIKE_RATIO) -> list[dict]:
    """(committee, 'YYYY-MM', turn_count) 목록 → 스파이크 목록 (ratio 내림차순).

    중앙값 기준 — 평균은 스파이크 자신에게 끌려간다. 월이 3개 미만인 위원회는
    중앙값이 무의미하므로 판단하지 않는다.
    """
    by_com = defaultdict(list)
    for com, month, cnt in rows:
        by_com[com].append((month, cnt))
    out = []
    for com, months in by_com.items():
        if len(months) < 3:
            continue
        med = median(c for _, c in months)
        if med <= 0:
            continue
        for month, cnt in months:
            if cnt >= ratio * med:
                out.append({"committee": com, "month": month, "count": cnt,
                            "median": med, "ratio": round(cnt / med, 2)})
    out.sort(key=lambda s: s["ratio"], reverse=True)
    return out


def top_agenda_lines(lines: list[str], top_n: int = 40) -> list[tuple[str, int]]:
    """agenda 섹션 줄들 → (정규화 줄, 빈도) 상위 top_n. 짧은 줄(의사진행 잡음)은 제외."""
    counter = Counter(
        " ".join(line.split())
        for line in lines
        if len(" ".join(line.split())) >= MIN_AGENDA_LEN
    )
    return counter.most_common(top_n)


def parse_topics(content: str) -> list[str]:
    """LLM 요약 응답 '{"topics": [...]}' → 주제 목록. 형식 위반은 빈 목록 (후보 누락 무해)."""
    try:
        topics = json.loads(content).get("topics")
    except (json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(topics, list):
        return []
    return [t for t in topics if isinstance(t, str)]
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_issue_candidates.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```powershell
git add scripts/issue_candidates.py tests/test_issue_candidates.py
git commit -m "feat(pol3): 이슈 후보 탐사 순수 로직 — 스파이크·agenda 빈발·주제 파싱"
```

---

### Task 3: 이슈 후보 탐사 — 본체 실행 + 리포트

**Files:**
- Modify: `scripts/issue_candidates.py` (main 추가)
- Create(실행 산출물): `data/issues/candidates_report.md`

**Interfaces:**
- Consumes: Task 2 의 `detect_spikes` / `top_agenda_lines` / `parse_topics`, `db.get_conn`, `search_vector._get_client`
- Produces: `data/issues/candidates_report.md` — Task 4(사람 게이트)의 입력

- [ ] **Step 1: main 구현**

`scripts/issue_candidates.py` 에 추가:

```python
def _monthly_counts() -> list[tuple]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT co.name, to_char(c.meeting_date, 'YYYY-MM') AS month,
                   count(DISTINCT c.turn_id)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            GROUP BY co.name, month
            ORDER BY co.name, month
        """)
        return cur.fetchall()


def _agenda_lines() -> list[str]:
    """normalized.jsonl 의 agenda 세그먼트 줄 수집 (767개 source 전체)."""
    lines = []
    for f in sorted(NORMALIZED_DIR.glob("*/normalized.jsonl")):
        for raw in f.read_text(encoding="utf-8").splitlines():
            page = json.loads(raw)
            for seg in page.get("segments", []):
                if seg.get("section_type") == "agenda":
                    lines.extend(seg["text"].splitlines())
    return lines


def _quarter_cells() -> list[tuple]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT co.name, to_char(date_trunc('quarter', c.meeting_date), 'YYYY-"Q"Q') AS q
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            GROUP BY co.name, q ORDER BY co.name, q
        """)
        return cur.fetchall()


def _sample_texts(committee: str, quarter: str, n: int) -> list[str]:
    """위원회×분기 무작위 표본 (is_short 제외, 500자 절단). seed 는 SQL setseed 로 고정."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT setseed(0.42)")
        cur.execute("""
            SELECT left(c.text, 500)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            WHERE co.name = %s
              AND to_char(date_trunc('quarter', c.meeting_date), 'YYYY-"Q"Q') = %s
              AND NOT c.is_short
            ORDER BY random() LIMIT %s
        """, (committee, quarter, n))
        return [r[0] for r in cur.fetchall()]


_SUMMARY_SYSTEM = """당신은 국회 회의록 발언 표본에서 반복되는 쟁점을 뽑는 도우미다.
발언 표본이 주어지면, 반복적으로 다뤄지는 사건·정책 주제 3~5개를 짧은 명사구로 뽑아라.
의사진행(개의·산회·표결)·인사말은 주제가 아니다.
반드시 아래 JSON 만 출력: {"topics": ["주제1", "주제2", ...]}"""


def _summarize_cell(client, committee: str, quarter: str, texts: list[str]) -> list[str]:
    docs = "\n---\n".join(texts)
    resp = client.chat.completions.create(
        model=_MODEL, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _SUMMARY_SYSTEM},
                  {"role": "user", "content": f"[{committee} {quarter} 표본]\n{docs}"}],
    )
    return parse_topics(resp.choices[0].message.content)


def _user_questions() -> list[str]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT question FROM query_logs ORDER BY question")
        return [r[0] for r in cur.fetchall()]


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client

    init_pool()
    print("[1/4] 시계열 스파이크...")
    spikes = detect_spikes(_monthly_counts())
    print(f"      {len(spikes)}건")

    print("[2/4] agenda 빈발 줄...")
    agenda_top = top_agenda_lines(_agenda_lines())

    print("[3/4] LLM 표본 요약 (위원회×분기)...")
    client = _get_client()
    topic_counter = Counter()
    cells = _quarter_cells()
    for i, (com, q) in enumerate(cells, 1):
        texts = _sample_texts(com, q, SAMPLE_PER_CELL)
        if not texts:
            continue
        for t in _summarize_cell(client, com, q, texts):
            topic_counter[(com, t)] += 1
        print(f"      {i}/{len(cells)} {com} {q}")

    print("[4/4] query_logs 교차...")
    questions = _user_questions()
    close_pool()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# 이슈 후보 리포트 (POL-3 단계 1 — 사용자 검수용)", "",
             "> 아래 신호를 보고 20~30개를 확정해 issues_seed.json 을 만든다.",
             "", "## 1. 시계열 스파이크 (사건형 후보)", "",
             "| 위원회 | 월 | 발언(turn) | 위원회 월 중앙값 | 배율 |", "|---|---|---|---|---|"]
    lines += [f"| {s['committee']} | {s['month']} | {s['count']} | {s['median']} | {s['ratio']} |"
              for s in spikes]
    lines += ["", "## 2. agenda 빈발 의제 (정책형 후보)", ""]
    lines += [f"- ({n}회) {t}" for t, n in agenda_top]
    lines += ["", "## 3. LLM 표본 요약 반복 주제 (위원회, 등장 분기 수)", ""]
    lines += [f"- {com}: {t} ({n}분기)"
              for (com, t), n in topic_counter.most_common(80)]
    lines += ["", "## 4. 실사용 질문 (query_logs)", ""]
    lines += [f"- {q}" for q in questions]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {REPORT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 회귀 확인 (순수 로직 무손상)**

Run: `pytest tests/test_issue_candidates.py -v`
Expected: 5 passed

- [ ] **Step 3: 실행**

Run: `python scripts/issue_candidates.py`
Expected: `[1/4]`~`[4/4]` 진행 후 `저장: ...candidates_report.md`. LLM 비용 ~수십 센트 (셀 ~70개 × 표본 30 × 500자).

**자기 검증 (스펙):** 리포트의 스파이크 표에 국방위/행안위 2024-12(계엄) 가 있어야 한다. 없으면 신호 로직 오류 — detect_spikes 나 SQL 을 의심할 것.

- [ ] **Step 4: Commit**

```powershell
git add scripts/issue_candidates.py data/issues/candidates_report.md
git commit -m "feat(pol3): 이슈 후보 탐사 실행 — 4신호 교차 리포트 (사용자 검수용)"
```

---

### Task 4: [사람 게이트] 이슈 확정 → issues_seed.json

**Files:**
- Create: `data/issues/issues_seed.json`

**Interfaces:**
- Consumes: `data/issues/candidates_report.md` (Task 3)
- Produces: `issues_seed.json` — Task 6 매핑의 입력. 형식은 아래 예시가 규범 (Task 5 의 `load_seed` 가 검증)

- [ ] **Step 1: 사용자에게 리포트 제시 + 이슈 확정 요청**

candidates_report.md 를 요약해 보여주고, 20~30개 선정을 요청한다. **이 단계는 사람 작업 — 사용자 응답 없이 진행 금지** (스펙 "구현 순서 1"). Claude 가 후보 기반 초안(제목·유형·설명·시드)을 제안하고 사용자가 가감·수정하는 방식을 권장.

- [ ] **Step 2: issues_seed.json 작성**

형식 (이슈당, 전 필드 필수 — anchor_meetings 만 빈 배열 허용):

```json
[
  {
    "issue_id": "martial-law",
    "title": "12·3 비상계엄",
    "type": "event",
    "description": "2024년 12월 3일 비상계엄 선포와 해제, 그 경위·책임·후속 조치(수사, 탄핵 정국 포함)를 둘러싼 국회 논의.",
    "seed_keywords": ["비상계엄", "계엄령", "계엄 해제", "12·3"],
    "seed_queries": [
      "12월 3일 비상계엄 선포 경위에 대한 국회 논의",
      "비상계엄 관련 군과 경찰의 역할과 책임"
    ],
    "anchor_meetings": ["국방위_20241210_52412_52412"]
  }
]
```

anchor_meetings 는 "이 회의는 반드시 매핑에 잡혀야 한다"는 대표 회의 source_id 1~2개 — `SELECT source_id, meeting_date FROM meetings WHERE ...` 로 실존 확인 후 기입 (재현율 참고 체크의 기준, Task 7).

- [ ] **Step 3: Commit**

```powershell
git add data/issues/issues_seed.json
git commit -m "data(pol3): 22대 이슈 사전 확정 — 사용자 검수 N개 (사건형/정책형)"
```

---

### Task 5: 매핑 파이프라인 — 순수 로직 (TDD)

**Files:**
- Create: `scripts/build_issue_map.py` (순수 함수부 — 수집·판정·적재는 Task 6)
- Test: `tests/test_issue_map.py`

**Interfaces:**
- Produces (Task 6·7 이 사용):
  - `MAP_VERSION = "v1.0"`
  - `load_seed(path: Path) -> list[dict]` — 필수 필드·타입·issue_id 중복 검증, 위반 시 `ValueError`
  - `cut_candidates(cands: dict[str, dict], threshold: float) -> dict[str, dict]` — `{chunk_id: {"vec_score": float|None, "kw_hit": bool, ...}}` 에서 (kw_hit 또는 vec_score ≥ threshold) 만 통과
  - `make_batches(items: list, size: int = 20) -> list[list]`
  - `parse_judge_response(content: str, batch_size: int) -> list[int] | None` — None 은 "배치 재시도" 신호

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_issue_map.py`:

```python
"""이슈 매핑 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_map.py  (pytest 도 지원)
"""
import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_issue_map import (  # noqa: E402
    cut_candidates, load_seed, make_batches, parse_judge_response,
)


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


_VALID_ISSUE = {
    "issue_id": "test-issue", "title": "테스트", "type": "event",
    "description": "테스트 이슈.", "seed_keywords": ["키워드"],
    "seed_queries": ["질문 하나"], "anchor_meetings": [],
}


def _write_seed(tmp_path: Path, issues) -> Path:
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_seed_valid(tmp_path):
    issues = load_seed(_write_seed(tmp_path, [_VALID_ISSUE]))
    check("정상 로드", len(issues) == 1 and issues[0]["issue_id"] == "test-issue")


def test_load_seed_rejects_missing_field(tmp_path):
    bad = {k: v for k, v in _VALID_ISSUE.items() if k != "description"}
    try:
        load_seed(_write_seed(tmp_path, [bad]))
        check("필수 필드 누락 거부", False)
    except ValueError:
        check("필수 필드 누락 거부", True)


def test_load_seed_rejects_dup_id(tmp_path):
    try:
        load_seed(_write_seed(tmp_path, [_VALID_ISSUE, dict(_VALID_ISSUE)]))
        check("issue_id 중복 거부", False)
    except ValueError:
        check("issue_id 중복 거부", True)


def test_load_seed_rejects_bad_type(tmp_path):
    bad = dict(_VALID_ISSUE, type="both")
    try:
        load_seed(_write_seed(tmp_path, [bad]))
        check("type 은 event|policy 만", False)
    except ValueError:
        check("type 은 event|policy 만", True)


def test_cut_candidates():
    cands = {
        "c1": {"vec_score": 0.55, "kw_hit": False},   # 유사도 통과
        "c2": {"vec_score": 0.20, "kw_hit": True},    # 키워드 매치로 통과
        "c3": {"vec_score": 0.20, "kw_hit": False},   # 둘 다 미달 → 컷
        "c4": {"vec_score": None, "kw_hit": False},   # 벡터 무점수·키워드 없음 → 컷
        "c5": {"vec_score": 0.40, "kw_hit": False},   # 경계값 = 통과 (이상)
    }
    kept = cut_candidates(cands, threshold=0.4)
    check("컷 결과", sorted(kept) == ["c1", "c2", "c5"], sorted(kept))


def test_make_batches():
    check("20개 분할", [len(b) for b in make_batches(list(range(45)), size=20)] == [20, 20, 5])
    check("빈 입력", make_batches([], size=20) == [])


def test_parse_judge_response():
    check("정상", parse_judge_response('{"relevant": [0, 2]}', 5) == [0, 2])
    check("빈 목록도 정상", parse_judge_response('{"relevant": []}', 5) == [])
    check("JSON 아님 → None", parse_judge_response("0, 2번이 관련", 5) is None)
    check("relevant 없음 → None", parse_judge_response('{"order": [1]}', 5) is None)
    check("범위 밖 번호는 버림", parse_judge_response('{"relevant": [0, 99]}', 5) == [0])
    check("정수 아닌 항목은 버림", parse_judge_response('{"relevant": [0, "1"]}', 5) == [0])


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_load_seed_valid(tmp)
        test_load_seed_rejects_missing_field(tmp)
        test_load_seed_rejects_dup_id(tmp)
        test_load_seed_rejects_bad_type(tmp)
    test_cut_candidates()
    test_make_batches()
    test_parse_judge_response()
    print("ALL PASS")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_issue_map.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_issue_map'`

- [ ] **Step 3: 순수 함수 구현**

`scripts/build_issue_map.py`:

```python
"""이슈↔청크 매핑 파이프라인 (POL-3 단계 2).

흐름 (docs/issue_module_spec.md — 사용자 결정 3):
  issues_seed.json → 이슈별 [후보 수집(하이브리드 축 직접 호출) → 저점수 컷
  → gpt-4o-mini 배치 관련도 판정] → issues·issue_chunks 적재 (통과분만 — 누락 > 오염)

실행:
  python scripts/build_issue_map.py --dry-run     # 후보 수·예상 비용만
  python scripts/build_issue_map.py               # 전체 이슈 매핑
  python scripts/build_issue_map.py --issue martial-law   # 단일 이슈 재실행 (시드 수정 시)
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"

MAP_VERSION = "v1.0"     # 매핑 방법 버전 — 수집·컷·판정 방식이 바뀌면 올린다
BATCH_SIZE = 20          # LLM 판정 배치 크기
DOC_CHARS = 600          # 판정에 보여줄 청크 발췌 길이 (reranker 와 동일)
PER_QUERY_VEC = 100      # seed_query 당 벡터 후보 수 (hnsw.ef_search=100 이 상한)
PER_KEYWORD_KW = 300     # seed_keyword 당 키워드 후보 수
_MODEL = "gpt-4o-mini"

_REQUIRED = ("issue_id", "title", "type", "description",
             "seed_keywords", "seed_queries", "anchor_meetings")


def load_seed(path: Path) -> list[dict]:
    """issues_seed.json 로드 + 검증. 시드 오류는 매핑 전체를 오염시키므로 즉시 실패."""
    issues = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for i, issue in enumerate(issues):
        for f in _REQUIRED:
            if f not in issue:
                raise ValueError(f"이슈 #{i}: 필수 필드 '{f}' 누락")
        if issue["type"] not in ("event", "policy"):
            raise ValueError(f"{issue['issue_id']}: type 은 event|policy (got {issue['type']!r})")
        if not issue["seed_keywords"] or not issue["seed_queries"]:
            raise ValueError(f"{issue['issue_id']}: seed_keywords·seed_queries 는 비울 수 없음")
        if issue["issue_id"] in seen:
            raise ValueError(f"issue_id 중복: {issue['issue_id']}")
        seen.add(issue["issue_id"])
    return issues


def cut_candidates(cands: dict[str, dict], threshold: float) -> dict[str, dict]:
    """저점수 컷 (1차 필터) — grounding 사전차단과 같은 기준:
    키워드 매치도 없고 벡터 유사도도 임계값 미만이면 LLM 판정에 보낼 가치가 없다."""
    return {
        cid: c for cid, c in cands.items()
        if c["kw_hit"] or (c["vec_score"] is not None and c["vec_score"] >= threshold)
    }


def make_batches(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def parse_judge_response(content: str, batch_size: int) -> list[int] | None:
    """판정 응답 → 관련 번호 목록. 구조 자체가 틀리면 None(재시도 신호),
    개별 항목 오류(범위 밖·비정수)는 그 항목만 버린다 (누락 우선)."""
    try:
        nums = json.loads(content).get("relevant")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(nums, list):
        return None
    return [n for n in nums if isinstance(n, int) and 0 <= n < batch_size]
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_issue_map.py -v`
Expected: 8 passed

- [ ] **Step 5: 전체 회귀**

Run: `pytest tests/ -q`
Expected: 전부 passed (DB 없는 환경이면 일부 skip)

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_issue_map.py tests/test_issue_map.py
git commit -m "feat(pol3): 매핑 순수 로직 — 시드 검증·저점수 컷·배치·판정 파싱 (TDD)"
```

---

### Task 6: 매핑 파이프라인 — 수집·판정·적재 + 실행

**Files:**
- Modify: `scripts/build_issue_map.py` (수집·판정·적재·CLI 추가)

**Interfaces:**
- Consumes: Task 5 순수 함수, `search_keyword.keyword_search(q, committee=None, date_from=None, date_to=None, limit)`, `search_vector.vector_search(q, ..., limit)` (hit 에 `chunk_id`·`score`), `search_vector._get_client()`, `db.get_conn`, `grounding` 의 `GROUNDING_SIM_THRESHOLD` env
- Produces: DB `issues`·`issue_chunks` 적재 — Task 7 스팟체크·Task 8 API 의 데이터

- [ ] **Step 1: 수집·판정·적재 구현**

`scripts/build_issue_map.py` 에 추가:

```python
def collect_candidates(issue: dict) -> dict[str, dict]:
    """시드 → 후보 합집합 {chunk_id: {"vec_score": 최대 유사도|None, "kw_hit": bool}}.

    hybrid_search 를 쓰지 않는 이유: limit 컷·turn dedup·reranker 가 걸려 있어
    '넓은 후보 수집'에 부적합 — 두 축을 직접 호출한다 (재현율 담당).
    """
    from search_keyword import keyword_search
    from search_vector import vector_search
    cands: dict[str, dict] = {}
    for q in issue["seed_queries"]:
        for hit in vector_search(q, limit=PER_QUERY_VEC):
            c = cands.setdefault(hit["chunk_id"], {"vec_score": None, "kw_hit": False})
            s = hit.get("score")
            if s is not None and (c["vec_score"] is None or s > c["vec_score"]):
                c["vec_score"] = round(float(s), 4)
    for kw in issue["seed_keywords"]:
        for hit in keyword_search(kw, limit=PER_KEYWORD_KW):
            c = cands.setdefault(hit["chunk_id"], {"vec_score": None, "kw_hit": False})
            c["kw_hit"] = True
    return cands


def fetch_texts(chunk_ids: list[str]) -> dict[str, dict]:
    """판정용 청크 본문·메타 일괄 조회 — 검색 응답의 snippet(200자)은 판정엔 부족."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.chunk_id, c.turn_id, c.speaker, c.role, co.name, c.meeting_date,
                   left(c.text, %s)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            WHERE c.chunk_id = ANY(%s)
        """, (DOC_CHARS, chunk_ids))
        return {r[0]: {"turn_id": r[1], "speaker": r[2], "role": r[3],
                       "committee": r[4], "date": str(r[5]), "text": r[6]}
                for r in cur.fetchall()}


_JUDGE_SYSTEM = """당신은 국회 회의록 발언이 특정 쟁점과 관련 있는지 판정하는 도우미다.
쟁점 정의와 번호 매긴 발언 목록이 주어진다. 각 발언에 대해:
- 쟁점의 사건·정책·대상을 실질적으로 다루면(질의·답변·주장·보고) 관련이다.
- 단어만 스치듯 지나가는 발언, 안건 목록 낭독, 의사진행 발언(개의·산회·표결 처리)은 무관이다.
- 확신이 없으면 무관으로 판정한다 — 누락이 오염보다 낫다.
반드시 아래 JSON 만 출력: {"relevant": [관련 있는 발언 번호 목록]}"""

_TRANSIENT = None  # 지연 로드 (openai import 비용)


def _transient_errors():
    global _TRANSIENT
    if _TRANSIENT is None:
        from openai import (APIConnectionError, APITimeoutError,
                            InternalServerError, RateLimitError)
        _TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
    return _TRANSIENT


def _judge_batch(client, issue: dict, batch: list[tuple[str, dict]]) -> list[int] | None:
    """배치 1개 판정. 형식 위반 1회 재시도, 일시 오류는 지수 백오프 (embeddings_v1 패턴)."""
    docs = "\n".join(
        f"[{i}] ({m['committee']} {m['date']}) {m['speaker'] or ''} {m['role'] or ''}: {m['text']}"
        for i, (_, m) in enumerate(batch)
    )
    user = (f"쟁점: {issue['title']}\n정의: {issue['description']}\n\n발언 목록:\n{docs}")
    for attempt in range(2):          # 형식 위반 재시도 1회
        delay = 2
        while True:                   # 일시 오류 재시도
            try:
                resp = client.chat.completions.create(
                    model=_MODEL, temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _JUDGE_SYSTEM},
                              {"role": "user", "content": user}],
                )
                break
            except _transient_errors():
                time.sleep(delay)
                delay = min(delay * 2, 60)
        result = parse_judge_response(resp.choices[0].message.content, len(batch))
        if result is not None:
            return result
    return None  # 2회 모두 형식 위반 → 배치 제외 (누락 우선)


def store_mapping(issue: dict, rows: list[tuple]) -> int:
    """이슈 단위 DELETE+재삽입 + 행수 검증 (jsonl_to_postgres 패턴). rows:
    (chunk_id, turn_id, vec_score, kw_hit)."""
    from db import get_conn
    from psycopg2.extras import execute_values
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO issues (issue_id, title, type, description, seed)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (issue_id) DO UPDATE SET
              title = EXCLUDED.title, type = EXCLUDED.type,
              description = EXCLUDED.description, seed = EXCLUDED.seed
        """, (issue["issue_id"], issue["title"], issue["type"], issue["description"],
              json.dumps(issue, ensure_ascii=False)))
        cur.execute("DELETE FROM issue_chunks WHERE issue_id = %s", (issue["issue_id"],))
        execute_values(cur, """
            INSERT INTO issue_chunks
              (issue_id, chunk_id, turn_id, vec_score, kw_hit, judge, map_version)
            VALUES %s
        """, [(issue["issue_id"], cid, tid, vs, kh, "llm_relevant", MAP_VERSION)
              for cid, tid, vs, kh in rows])
        cur.execute("SELECT count(*) FROM issue_chunks WHERE issue_id = %s",
                    (issue["issue_id"],))
        n = cur.fetchone()[0]
    if n != len(rows):
        raise RuntimeError(f"{issue['issue_id']}: 행수 불일치 (기대 {len(rows)}, DB {n})")
    return n


def _est_cost_usd(n_candidates: int) -> float:
    """판정 입력 비용 추정 — 후보당 발췌 600자 ≈ 540토큰(한국어 ~0.9tok/자) + 오버헤드."""
    input_tokens = n_candidates * (DOC_CHARS * 0.9 + 60)
    return input_tokens / 1e6 * 0.15


def process_issue(client, issue: dict, threshold: float, dry_run: bool) -> dict:
    t0 = time.time()
    cands = collect_candidates(issue)
    kept = cut_candidates(cands, threshold)
    if dry_run:
        return {"issue_id": issue["issue_id"], "candidates": len(cands),
                "after_cut": len(kept), "est_cost": round(_est_cost_usd(len(kept)), 3)}
    meta = fetch_texts(list(kept))
    items = [(cid, meta[cid]) for cid in kept if cid in meta]
    relevant_ids, dropped = [], 0
    for batch in make_batches(items):
        result = _judge_batch(client, issue, batch)
        if result is None:
            dropped += 1
            continue
        relevant_ids += [batch[i][0] for i in result]
    rows = [(cid, meta[cid]["turn_id"], kept[cid]["vec_score"], kept[cid]["kw_hit"])
            for cid in relevant_ids]
    n = store_mapping(issue, rows)
    return {"issue_id": issue["issue_id"], "candidates": len(cands),
            "after_cut": len(kept), "mapped": n, "dropped_batches": dropped,
            "secs": round(time.time() - t0, 1)}


def main():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="후보 수·예상 비용만")
    ap.add_argument("--issue", help="단일 이슈만 재실행 (issue_id)")
    args = ap.parse_args()

    issues = load_seed(SEED_PATH)
    if args.issue:
        issues = [i for i in issues if i["issue_id"] == args.issue]
        if not issues:
            print(f"[FAIL] issue_id 없음: {args.issue}")
            sys.exit(1)

    threshold = float(os.environ.get("GROUNDING_SIM_THRESHOLD", "0.4"))
    init_pool()
    client = None if args.dry_run else _get_client()

    failures = []
    total_cost = 0.0
    for issue in issues:
        try:
            r = process_issue(client, issue, threshold, args.dry_run)
        except Exception as e:
            failures.append((issue["issue_id"], f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {issue['issue_id']}: {type(e).__name__}: {e}")
            continue
        total_cost += r.get("est_cost", 0)
        print(f"[{'DRY' if args.dry_run else 'OK'}] {json.dumps(r, ensure_ascii=False)}")
    close_pool()

    if args.dry_run:
        print(f"예상 판정 입력 비용 합계: ~${total_cost:.2f}")
    if failures:  # 조용한 유실 금지 — 실패 이슈를 남기고 비정상 종료
        print(f"[FAIL] {len(failures)}개 이슈 실패: {[f[0] for f in failures]}")
        sys.exit(1)
    print("전체 완료")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 순수 로직 회귀**

Run: `pytest tests/test_issue_map.py -v`
Expected: 8 passed

- [ ] **Step 3: dry-run 으로 규모·비용 확인**

Run: `python scripts/build_issue_map.py --dry-run`
Expected: 이슈별 `{"issue_id": ..., "candidates": N, "after_cut": M, "est_cost": ...}` + 합계. 합계가 스펙 추정($2~4)의 2배를 넘으면 **실행 중단하고 사용자에게 보고** (PER_KEYWORD_KW 축소 검토).

- [ ] **Step 4: 단일 이슈 파일럿 → 전체 실행**

```powershell
python scripts/build_issue_map.py --issue martial-law   # 파일럿 1개로 동작 확인
python scripts/build_issue_map.py                        # 전체
```

Expected: 이슈별 `[OK] {..., "mapped": N, "dropped_batches": 0, ...}` 후 `전체 완료`. dropped_batches 가 0 이 아니면 개수와 함께 보고 (판정 프롬프트 점검 신호).

- [ ] **Step 5: 적재 확인**

```powershell
$code = @'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "backend"))
from db import init_pool, close_pool, get_conn
init_pool()
with get_conn() as conn, conn.cursor() as cur:
    cur.execute("""SELECT issue_id, count(*), count(DISTINCT turn_id)
                   FROM issue_chunks GROUP BY issue_id ORDER BY 2 DESC""")
    for r in cur.fetchall():
        print(r)
close_pool()
'@
Set-Content -Path check_map_tmp.py -Value $code -Encoding utf8
python check_map_tmp.py
Remove-Item check_map_tmp.py
```

Expected: 전 이슈에 1행 이상. 0행 이슈가 있으면 시드(키워드·질문)를 의심.

- [ ] **Step 6: Commit**

```powershell
git add scripts/build_issue_map.py
git commit -m "feat(pol3): 이슈 매핑 실행 — 수집·컷·LLM 판정·적재 (이슈 N개, 청크 M개)"
```

---

### Task 7: 스팟체크 — 정밀도 게이트

**Files:**
- Create: `scripts/issue_spotcheck.py`
- Test: `tests/test_issue_map.py` (표본 함수 1건 추가)
- Create(실행 산출물): `data/issues/spotcheck_report.md`

**Interfaces:**
- Consumes: DB `issue_chunks`·`chunks`·`issues` (Task 6), `issues_seed.json` 의 `anchor_meetings`
- Produces: 판독용 리포트 + 앵커 포함 여부 — **이슈 평균 정밀도 ≥90% 게이트** (스펙 "단계 3")

- [ ] **Step 1: 표본 함수 테스트 추가**

`tests/test_issue_map.py` 에 추가 (import 줄에 `sample_rows` 추가 — `from issue_spotcheck import sample_rows`):

```python
def test_sample_rows_deterministic():
    from issue_spotcheck import sample_rows
    rows = list(range(100))
    a, b = sample_rows(rows, n=10, seed=42), sample_rows(rows, n=10, seed=42)
    check("seed 고정 재현", a == b and len(a) == 10, (a, b))
    check("표본보다 적으면 전부", sample_rows([1, 2], n=10, seed=42) == [1, 2])
```

Run: `pytest tests/test_issue_map.py -v` → Expected: FAIL (`No module named 'issue_spotcheck'`)

- [ ] **Step 2: 구현**

`scripts/issue_spotcheck.py`:

```python
"""이슈 매핑 스팟체크 (POL-3 단계 3) — 정밀도 게이트의 재료를 만든다.

  1. 이슈당 무작위 10청크(seed 고정) 발췌 → 판독용 마크다운 (사람이 O/X 판독)
  2. anchor_meetings 포함 여부 — 재현율 참고 체크 (게이트 아님, 경보 신호)

게이트 (docs/issue_module_spec.md): 이슈 평균 정밀도 ≥90%.
미달 이슈는 description/시드 보정 후 build_issue_map.py --issue 재실행.

실행: python scripts/issue_spotcheck.py
"""

import io
import json
import random
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"
REPORT = ROOT / "data" / "issues" / "spotcheck_report.md"

SAMPLE_N = 10
RNG_SEED = 42


def sample_rows(rows: list, n: int = SAMPLE_N, seed: int = RNG_SEED) -> list:
    """seed 고정 무작위 표본 — 재실행해도 같은 표본 (enrichment_audit 패턴)."""
    if len(rows) <= n:
        return list(rows)
    return random.Random(seed).sample(rows, n)


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool, get_conn

    issues = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    init_pool()
    lines = ["# 이슈 매핑 스팟체크 (판독용)", "",
             "> 각 발언이 이슈와 '실질적으로' 관련 있으면 [O], 스치는 언급·무관이면 [X] 로 표기.",
             "> 게이트: 이슈 평균 정밀도 ≥90% — 미달 이슈는 시드 보정 후 --issue 재실행.", ""]
    with get_conn() as conn, conn.cursor() as cur:
        for issue in issues:
            iid = issue["issue_id"]
            cur.execute("SELECT chunk_id FROM issue_chunks WHERE issue_id = %s ORDER BY chunk_id",
                        (iid,))
            all_ids = [r[0] for r in cur.fetchall()]
            picked = sample_rows(all_ids)
            lines += [f"## {issue['title']} (`{iid}`) — 매핑 {len(all_ids)}청크, 표본 {len(picked)}", ""]
            if picked:
                cur.execute("""
                    SELECT c.chunk_id, co.name, c.meeting_date, c.speaker, c.role,
                           left(c.text, 400)
                    FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
                    WHERE c.chunk_id = ANY(%s)
                """, (picked,))
                for cid, com, d, sp, role, text in cur.fetchall():
                    lines += [f"- [ ] `{cid}` ({com} {d}) {sp or ''} {role or ''}:",
                              f"      {text}", ""]
            # 앵커 회의 포함 여부 (재현율 경보)
            for src in issue.get("anchor_meetings", []):
                cur.execute("""
                    SELECT count(*) FROM issue_chunks ic
                    JOIN chunks c ON c.chunk_id = ic.chunk_id
                    WHERE ic.issue_id = %s AND c.source_id = %s
                """, (iid, src))
                n = cur.fetchone()[0]
                mark = "OK" if n > 0 else "**MISS**"
                lines += [f"- 앵커 {src}: {mark} ({n}청크)", ""]
    close_pool()
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {REPORT} — 사람 판독 후 정밀도 계산")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 테스트 통과 + 실행**

```powershell
pytest tests/test_issue_map.py -v      # Expected: 전부 passed
python scripts/issue_spotcheck.py      # Expected: 저장: ...spotcheck_report.md
```

- [ ] **Step 4: [사람 게이트] 판독 → 정밀도 집계**

리포트의 표본을 원문 대조 판독 (Claude 가 근거 대조 초벌 판독 → 사용자 확인 — answer_eval 검수 방식). 이슈별 정밀도 = O 수 / 표본 수:
- **평균 ≥90% + 앵커 MISS 0** → 통과, 다음 Task
- 미달 이슈 → description·시드 보정 → `python scripts/build_issue_map.py --issue <id>` → 해당 이슈만 재판독 (루프)

- [ ] **Step 5: Commit**

```powershell
git add scripts/issue_spotcheck.py tests/test_issue_map.py data/issues/spotcheck_report.md
git commit -m "eval(pol3): 매핑 스팟체크 — 이슈 평균 정밀도 N% (게이트 90% 통과), 앵커 MISS 0"
```

---

### Task 8: GET /issues + 스모크

**Files:**
- Modify: `backend/main.py` (엔드포인트 1개 — `/actors/{name}` 근처)
- Modify: `tests/test_api.py` (테스트 1건 추가)

**Interfaces:**
- Consumes: DB `issues`·`issue_chunks` (Task 6)
- Produces: `GET /issues` → `{"issues": [{issue_id, title, type, description, chunk_count, turn_count}]}` — POL-9 프론트·POL-4 의 진입점

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_api.py` 에 추가 (기존 check/HAS_DB 관례 그대로):

```python
def test_issues_list():
    """이슈 목록 — 사전이 비어 있어도 200 + issues 키 (스키마만 보장)."""
    if not HAS_DB:
        print(_SKIP_MSG)
        return
    r = client.get("/issues")
    check("issues: 200", r.status_code == 200, r.status_code)
    body = r.json()
    check("issues: 목록 키", isinstance(body.get("issues"), list), body)
    if body["issues"]:
        first = body["issues"][0]
        check("issues: 필드", all(k in first for k in
              ("issue_id", "title", "type", "description", "chunk_count", "turn_count")), first)
```

Run: `pytest tests/test_api.py::test_issues_list -v`
Expected: FAIL — 404 (엔드포인트 없음)

- [ ] **Step 2: 엔드포인트 구현**

`backend/main.py` 의 `/actors/{name}` 엔드포인트 아래에 추가:

```python
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
```

- [ ] **Step 3: 테스트 + 전체 회귀**

```powershell
pytest tests/test_api.py -v    # Expected: test_issues_list 포함 전부 passed
pytest tests/ -q               # Expected: 전부 passed/skip — 퇴행 없음
```

- [ ] **Step 4: 스모크 — 집계 정합**

서버 기동 후 (`uvicorn main:app --app-dir backend`), `/issues` 의 chunk_count 합이 Task 6 Step 5 의 issue_chunks 집계와 일치하는지 확인:

```powershell
curl.exe -s http://127.0.0.1:8000/issues
```

Expected: 전 이슈 목록 + 청크·turn 수 (Task 6 Step 5 결과와 일치)

- [ ] **Step 5: Commit**

```powershell
git add backend/main.py tests/test_api.py
git commit -m "feat(pol3): GET /issues — 쟁점 사전 목록 API + 계층 테스트"
```

---

### Task 9: 문서화 마감

**Files:**
- Modify: `docs/progress.md` (POL-3 구현 기록 + 로드맵 표 ⬜→✅ + 최종 업데이트 줄)

**Interfaces:**
- Consumes: Task 1~8 의 실측 수치 (이슈 수·매핑 청크 수·정밀도·비용·소요)

- [ ] **Step 1: progress.md 갱신**

3단계 로드맵 표에서 POL-3 상태를 ✅ (완료 기준 충족 시에만), POL-2 구현 기록 아래에 "POL-3 구현 기록 (2026-07-08)" 섹션 추가 — 기존 기록 형식대로: 사용자 결정 3건, 이슈 수·매핑 규모, 스팟체크 정밀도 실측, dropped_batches·비용 실측, 알려진 한계(스냅샷·재현율 미측정·판정 비결정성). 문서 상단 "최종 업데이트" 줄도 갱신.

- [ ] **Step 2: 전체 회귀 최종 확인**

Run: `pytest tests/ -q`
Expected: 전부 passed/skip

- [ ] **Step 3: Commit**

```powershell
git add docs/progress.md
git commit -m "docs: POL-3 쟁점 모듈 완료 기록 — 이슈 N개, 매핑 M청크, 정밀도 P%"
```

---

## Self-Review 결과 (작성 후 점검)

1. **스펙 커버리지**: 산출물 4종(seed json=T4, 테이블=T1, 스크립트 3종=T2/3/5/6/7, API=T8), 단계 1~3(T2~4 / T5~6 / T7), 완료 기준 4항(정밀도 게이트=T7, 앵커=T7, 단위테스트=T2/5/7, API 스모크=T8) — 전부 대응 태스크 있음
2. **플레이스홀더**: 코드 블록 전부 실행 가능한 실코드. "사람 게이트" 2곳(T4, T7 Step 4)은 의도된 사용자 개입이며 절차를 명시함
3. **타입 일관성**: `load_seed`/`cut_candidates`/`make_batches`/`parse_judge_response`/`sample_rows` 시그니처가 테스트·본체·소비 태스크에서 동일. `store_mapping` rows 튜플 순서 = INSERT 컬럼 순서 확인
