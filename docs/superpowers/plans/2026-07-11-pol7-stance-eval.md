# POL-7 입장 판정 eval (블라인드 라벨) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** POL-5 입장 판정(gpt-4o-mini 5택)의 품질을 사람 블라인드 라벨 40건으로 검증해 일치도·혼동행렬 기준선을 기록하고, 프롬프트 변경마다 재실행 가능한 eval 자산을 만든다.

**Architecture:** 순수 계산 로직(라벨 파싱·일치도)을 DB·LLM 없이 단위 테스트 가능한 함수로 먼저 만들고, 그 위에 DB 조회를 얹은 얇은 CLI 2개(`stance_label_sheet.py` 블라인드 시트 생성 / `stance_eval.py` 일치도 계산)를 붙인다. 사람 라벨링은 두 CLI 사이의 수동 게이트다.

**Tech Stack:** Python 3, psycopg2(pool via `backend/db.py`), pytest 스타일 순수 테스트(`tests/test_*.py`), PostgreSQL `issue_stances`·`chunks` 테이블.

## Global Constraints

- 범위 = `medical-reform` 40건. seed=42 재현 가능 표본. 전체 24개 이슈 일반화 아님.
- **하드 게이트 없음** — 입장은 5택·주관적. 기준선(일치도)만 기록한다.
- 5택 라벨 토큰: `support`, `oppose`, `concern`, `neutral`, `none` (verbatim, 소문자).
- 라벨링은 **사용자가 직접** 한다(사람 게이트). LLM 판정은 시트에서 숨긴다(블라인드) — 앵커링 편향 방지.
- 시트 상단 rubric 5택 정의는 `scripts/build_issue_stance.py` 의 `_SYSTEM` 정의와 문구가 일치해야 한다(사람·LLM 동일 기준).
- 스크립트 표준 관례(기존 `scripts/*.py` 동일): 파일 상단에서 `if __name__ == "__main__": sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")`, `sys.path.insert(0, backend)`, `.env` 로드는 `main()` 안에서.
- 산출물 경로(고정):
  - 블라인드 시트: `data/issues/stance_labels_medical-reform.md`
  - eval 자산 JSON: `data/eval/stance_eval_medical-reform.json`
  - 리포트: `data/issues/stance_eval_report.md`

---

### Task 1: 라벨 시트 파서 (`parse_label_sheet`, 순수 함수)

**Files:**
- Create: `scripts/stance_eval.py`
- Test: `tests/test_stance_eval.py`

**Interfaces:**
- Consumes: 없음 (순수 문자열 처리)
- Produces:
  - `STANCES: tuple[str, ...]` = `("support","oppose","concern","neutral","none")`
  - `parse_label_sheet(text: str) -> dict[str, str]` — 라벨 파일 본문에서 `{turn_id: stance}` 추출. 각 항목은 백틱으로 감싼 turn_id 줄 다음에 `입장: <토큰>` 줄이 온다. 빈칸·허용밖 토큰은 제외(반환 dict 에 미포함). turn_id 하나당 최초 유효 라벨만.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_stance_eval.py`:

```python
"""POL-7 입장 eval 순수 로직 테스트 — DB·LLM 없이 실행.
실행: python tests/test_stance_eval.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stance_eval import parse_label_sheet  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


SHEET = """# 입장 블라인드 라벨 — medical-reform

> 안내문...
> - support: 지지

- `복지위_A_turn_0001` (2024-06-13) 이주영 위원
      입장: support
      안녕하십니까 발언 전문 ...

- `복지위_A_turn_0002` (2024-06-19) 김윤 위원
      입장: oppose
      의대 증원에 반대하는 ...

- `복지위_A_turn_0003` (2024-06-26) 서영석 위원
      입장:
      발언 전문 (미기입) ...

- `복지위_A_turn_0004` (2024-06-26) 안상훈 위원
      입장: supprt
      오타 토큰 발언 ...
"""


def test_parse_label_sheet():
    r = parse_label_sheet(SHEET)
    check("정상 2건 추출", r.get("복지위_A_turn_0001") == "support" and r.get("복지위_A_turn_0002") == "oppose", r)
    check("빈칸 제외", "복지위_A_turn_0003" not in r, r)
    check("허용밖 토큰 제외", "복지위_A_turn_0004" not in r, r)
    check("총 2건만", len(r) == 2, r)


if __name__ == "__main__":
    test_parse_label_sheet()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_stance_eval.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'stance_eval'` (또는 import 에러)

- [ ] **Step 3: 최소 구현**

`scripts/stance_eval.py`:

```python
"""POL-7 입장 판정 eval — 블라인드 사람 라벨 vs LLM 판정 일치도.

순수 로직(parse_label_sheet·agreement)은 DB·LLM 없이 테스트 가능.
CLI 는 라벨 파일 파싱 → issue_stances 조회 → 일치도·혼동행렬 → JSON·리포트 저장.

실행:
  python scripts/stance_eval.py --issue medical-reform
"""
import io
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

STANCES = ("support", "oppose", "concern", "neutral", "none")
_TURN_RE = re.compile(r"`([^`]+)`")
_LABEL_RE = re.compile(r"입장:\s*([A-Za-z]+)")


def parse_label_sheet(text: str) -> dict[str, str]:
    """라벨 파일 → {turn_id: stance}. 백틱 turn_id 줄 뒤 `입장: <토큰>` 줄을 짝짓는다.
    빈칸·허용밖 토큰은 제외(경고는 호출측에서 시트 항목 수와 비교해 판단)."""
    labels: dict[str, str] = {}
    cur: str | None = None
    for line in text.splitlines():
        m = _TURN_RE.search(line)
        if m:
            cur = m.group(1)
            continue
        lm = _LABEL_RE.search(line)
        if lm and cur is not None:
            tok = lm.group(1).lower()
            if tok in STANCES and cur not in labels:
                labels[cur] = tok
            cur = None  # `입장:` 줄을 소비 — 다음 turn_id 까지 대기
    return labels
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_stance_eval.py`
Expected: PASS — `all passed`

- [ ] **Step 5: 커밋**

```bash
git add scripts/stance_eval.py tests/test_stance_eval.py
git commit -m "feat(pol7): 블라인드 라벨 시트 파서 — turn_id·5택 추출, 빈칸·오타 제외"
```

---

### Task 2: 일치도·혼동행렬 계산 (`agreement`, 순수 함수)

**Files:**
- Modify: `scripts/stance_eval.py` (함수 추가)
- Test: `tests/test_stance_eval.py` (테스트 추가)

**Interfaces:**
- Consumes: `STANCES` (Task 1)
- Produces:
  - `agreement(human: dict[str, str], llm: dict[str, str]) -> dict` — 공통 turn_id 에서:
    - `"n"`: 공통 건수, `"agreement"`: 일치율(round 3) 또는 `None`(공통 0건),
    - `"matrix"`: `{human_stance: {llm_stance: count}}` 5×5 (모든 STANCES 키 존재, 0 포함),
    - `"disagreements"`: `[{"turn_id","human","llm"}, ...]` (사람!=LLM, turn_id 정렬).

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_stance_eval.py` 의 import 줄과 `__main__` 블록을 아래처럼 갱신하고 테스트 함수를 추가:

```python
from stance_eval import parse_label_sheet, agreement  # noqa: E402
```

```python
def test_agreement():
    # 완전 일치
    h = {"t1": "support", "t2": "oppose"}
    r = agreement(h, dict(h))
    check("완전 일치 1.0", r["agreement"] == 1.0 and r["n"] == 2, r)
    check("혼동행렬 대각 카운트", r["matrix"]["support"]["support"] == 1 and r["matrix"]["oppose"]["oppose"] == 1, r)
    check("불일치 없음", r["disagreements"] == [], r)

    # 부분 일치 — 공통 t1(일치)·t2(불일치), t3 는 사람만 → 제외
    human = {"t1": "support", "t2": "concern", "t3": "none"}
    llm = {"t1": "support", "t2": "oppose"}
    r2 = agreement(human, llm)
    check("공통 2건", r2["n"] == 2, r2)
    check("일치율 0.5", r2["agreement"] == 0.5, r2)
    check("혼동 concern→oppose", r2["matrix"]["concern"]["oppose"] == 1, r2)
    check("불일치 1건 t2", [d["turn_id"] for d in r2["disagreements"]] == ["t2"], r2)

    # 공통 0건 방어
    r3 = agreement({"a": "support"}, {"b": "oppose"})
    check("공통 0건 agreement None", r3["n"] == 0 and r3["agreement"] is None, r3)
```

`__main__` 블록:

```python
if __name__ == "__main__":
    test_parse_label_sheet()
    test_agreement()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_stance_eval.py`
Expected: FAIL — `ImportError: cannot import name 'agreement'`

- [ ] **Step 3: 최소 구현**

`scripts/stance_eval.py` 에 추가 (parse_label_sheet 아래):

```python
def agreement(human: dict[str, str], llm: dict[str, str]) -> dict:
    """공통 turn_id 에서 일치율 + 혼동행렬(사람→LLM) + 불일치 목록. 공통 0건이면 방어."""
    common = sorted(set(human) & set(llm))
    matrix = {h: {c: 0 for c in STANCES} for h in STANCES}
    disagreements = []
    agree = 0
    for t in common:
        h, l = human[t], llm[t]
        if h in matrix and l in matrix[h]:
            matrix[h][l] += 1
        if h == l:
            agree += 1
        else:
            disagreements.append({"turn_id": t, "human": h, "llm": l})
    n = len(common)
    return {
        "n": n,
        "agreement": round(agree / n, 3) if n else None,
        "matrix": matrix,
        "disagreements": disagreements,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_stance_eval.py`
Expected: PASS — `all passed`

- [ ] **Step 5: 커밋**

```bash
git add scripts/stance_eval.py tests/test_stance_eval.py
git commit -m "feat(pol7): 일치도·5x5 혼동행렬 계산 — 공통 turn_id 방어 포함"
```

---

### Task 3: 블라인드 라벨 시트 생성기 (`stance_label_sheet.py`)

**Files:**
- Create: `scripts/stance_label_sheet.py`
- Test: `tests/test_stance_label_sheet.py`

**Interfaces:**
- Consumes: `backend/db.py` 의 `init_pool`, `close_pool`, `get_conn` (DB), `.env`
- Produces:
  - `sample_turns(rows: list, n: int = 40, seed: int = 42) -> list` — `rows` 를 seed 로 재현 가능하게 최대 n 건 표본추출 후 첫 원소(turn_id) 기준 정렬. `len(rows) <= n` 이면 전체를 정렬만.
  - `render_sheet(issue_id: str, picked: list, total: int) -> str` — 블라인드 라벨 시트 마크다운. `picked` 원소 = `(turn_id, speaker, role, date, text)`. 각 항목에 `입장: ` 빈칸 포함, **stance 는 절대 미출력**. 상단 rubric 5택 정의 포함.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_stance_label_sheet.py`:

```python
"""POL-7 블라인드 시트 생성 순수 로직 테스트 — DB 없이 실행."""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stance_label_sheet import sample_turns, render_sheet  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_sample_turns():
    rows = [(f"t{i:03d}", "sp", "role", "2024-06-13", "text") for i in range(100)]
    a = sample_turns(rows, n=40, seed=42)
    b = sample_turns(rows, n=40, seed=42)
    check("40건", len(a) == 40, len(a))
    check("재현성(같은 seed 같은 결과)", a == b)
    check("turn_id 정렬", [r[0] for r in a] == sorted(r[0] for r in a))
    small = [(f"t{i}", "sp", "r", "d", "x") for i in range(5)]
    check("표본<n 이면 전체", len(sample_turns(small, n=40)) == 5)


def test_render_sheet_is_blind():
    picked = [("복지위_A_turn_0001", "이주영 위원", "위원", "2024-06-13",
               "지지 발언 원문")]
    md = render_sheet("medical-reform", picked, total=212)
    check("turn_id 출력", "복지위_A_turn_0001" in md)
    check("입장 빈칸 존재", "입장:" in md)
    check("rubric support 정의 포함", "support:" in md)
    check("표본/전체 표기", "212" in md)
    # 블라인드: 시트 텍스트에 판정 stance 토큰이 라벨 문맥으로 새지 않아야 함
    check("판정 라벨 문자열 미노출", "판정:" not in md)


if __name__ == "__main__":
    test_sample_turns()
    test_render_sheet_is_blind()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_stance_label_sheet.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'stance_label_sheet'`

- [ ] **Step 3: 최소 구현**

`scripts/stance_label_sheet.py`:

```python
"""POL-7 블라인드 라벨 시트 생성 — 사용자가 직접 5택을 매길 시트.

issue_stances(medical-reform) 의 turn 을 seed=42 로 40건 표본추출하되 **LLM 판정 stance 를
숨긴다**(블라인드). 상단에 build_issue_stance._SYSTEM 과 동일한 5택 정의(rubric)를 넣어
사람·LLM 이 같은 기준으로 판정하게 한다.

실행: python scripts/stance_label_sheet.py --issue medical-reform
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

# build_issue_stance._SYSTEM 의 5택 정의와 문구 일치 (사람·LLM 동일 기준)
_RUBRIC = [
    "- support: 쟁점의 정책·조치를 지지·찬성한다.",
    "- oppose: 반대·철회를 주장한다.",
    "- concern: 방향엔 동의하나 부작용·속도·방식에 우려를 표한다 (조건부).",
    "- neutral: 입장 없이 다룬다 (사실 확인·질의·중계).",
    "- none: 입장 판정 불가 (순수 절차·인사·다른 주제 경유).",
]


def sample_turns(rows: list, n: int = SAMPLE_N, seed: int = RNG_SEED) -> list:
    """seed 재현 가능 표본 최대 n 건 → turn_id(첫 원소) 정렬."""
    picked = rows if len(rows) <= n else random.Random(seed).sample(rows, n)
    return sorted(picked, key=lambda r: r[0])


def render_sheet(issue_id: str, picked: list, total: int) -> str:
    """블라인드 라벨 시트 마크다운. stance 는 출력하지 않는다."""
    lines = [
        f"# 입장 블라인드 라벨 — {issue_id}",
        "",
        "> 각 발언을 읽고 `입장:` 뒤에 support|oppose|concern|neutral|none 중 하나를 적으세요.",
        "> LLM 판정은 숨겨져 있습니다 (블라인드). 아래 5택 정의를 기준으로 판정하세요.",
        *[f"> {r}" for r in _RUBRIC],
        f"> 표본 {len(picked)} / 전체 {total}",
        "",
    ]
    for turn_id, speaker, role, date, text in picked:
        lines += [
            f"- `{turn_id}` ({date}) {speaker or ''} {role or ''}".rstrip(),
            "      입장: ",
            f"      {text}",
            "",
        ]
    return "\n".join(lines)


def fetch_stance_turns(issue_id: str) -> list:
    """issue_stances 의 turn — (turn_id, speaker, role, date, 500자 텍스트). stance 는 조회하되
    시트엔 넣지 않음(표본 모집단 = 판정된 turn)."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, c.meeting_date::text,
                   left(string_agg(c.text, ' ' ORDER BY c.chunk_index), 500)
            FROM issue_stances s JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, c.meeting_date
        """, (issue_id,))
        return cur.fetchall()


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    init_pool()
    rows = fetch_stance_turns(args.issue)
    close_pool()
    if not rows:
        print(f"[FAIL] issue_stances 비어 있음: {args.issue} — POL-5 먼저 실행"); sys.exit(1)
    picked = sample_turns(rows)
    md = render_sheet(args.issue, picked, total=len(rows))
    out = ROOT / "data" / "issues" / f"stance_labels_{args.issue}.md"
    out.write_text(md, encoding="utf-8")
    print(f"저장: {out} — 표본 {len(picked)}/{len(rows)}. 사용자가 `입장:` 40건 기입 후 stance_eval.py 실행")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_stance_label_sheet.py`
Expected: PASS — `all passed`

- [ ] **Step 5: 실제 시트 생성 (DB 필요)**

Docker Desktop + `national-assembly-db` 실행 확인 후:

Run: `python scripts/stance_label_sheet.py --issue medical-reform`
Expected: `저장: ...\data\issues\stance_labels_medical-reform.md — 표본 40/<전체>`

검증: 생성된 `data/issues/stance_labels_medical-reform.md` 를 열어 (a) 40개 항목, (b) 각 항목에 `입장: ` 빈칸, (c) rubric 5줄, (d) **어디에도 `판정:`/stance 값이 안 보임**(블라인드) 확인.

- [ ] **Step 6: 커밋**

```bash
git add scripts/stance_label_sheet.py tests/test_stance_label_sheet.py data/issues/stance_labels_medical-reform.md
git commit -m "feat(pol7): 블라인드 라벨 시트 생성기 — seed42 40건, 판정 숨김, 동일 rubric"
```

---

### Task 4: eval CLI 배선 (`stance_eval.py` main — LLM 판정 조회·저장)

**Files:**
- Modify: `scripts/stance_eval.py` (`fetch_llm_stances` + `main` 추가)
- Test: `tests/test_stance_eval.py` (round-trip 통합 확인 추가)

**Interfaces:**
- Consumes: `parse_label_sheet`, `agreement` (Task 1·2), `backend/db.py`
- Produces:
  - `fetch_llm_stances(issue_id: str) -> dict[str, str]` — `issue_stances` 에서 `{turn_id: stance}`.
  - `write_outputs(issue_id, human, result, out_json, out_md) -> None` — eval 자산 JSON + 리포트 md 저장.
  - `main()` — 라벨 파일 파싱 → LLM 판정 조회 → agreement → 저장·출력.

- [ ] **Step 1: 실패 테스트 작성 (write_outputs round-trip, DB 무관)**

`tests/test_stance_eval.py` 상단 import 에 `write_outputs` 추가하고 테스트 함수 추가. import 는 지연(파일 I/O 만) — DB 는 건드리지 않음:

```python
from stance_eval import parse_label_sheet, agreement, write_outputs  # noqa: E402
```

```python
def test_write_outputs_roundtrip(tmp_path=None):
    import json, tempfile, os
    d = tempfile.mkdtemp()
    out_json = Path(d) / "stance_eval_x.json"
    out_md = Path(d) / "report.md"
    human = {"t1": "support", "t2": "concern"}
    result = agreement(human, {"t1": "support", "t2": "oppose"})
    write_outputs("medical-reform", human, result, out_json, out_md)
    saved = json.loads(out_json.read_text(encoding="utf-8"))
    check("JSON issue_id", saved["issue_id"] == "medical-reform", saved)
    check("JSON seed 42", saved["rng_seed"] == 42, saved)
    check("JSON labels 보존", saved["labels"] == human, saved)
    md = out_md.read_text(encoding="utf-8")
    check("리포트 일치율 표기", "일치율" in md)
    check("리포트 혼동행렬 표기", "혼동행렬" in md)
    os.remove(out_json); os.remove(out_md)
```

`__main__` 블록에 `test_write_outputs_roundtrip()` 추가.

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_stance_eval.py`
Expected: FAIL — `ImportError: cannot import name 'write_outputs'`

- [ ] **Step 3: 최소 구현**

`scripts/stance_eval.py` 하단에 추가 (import 에 `argparse`, `json`, `datetime` 필요 — 상단 import 블록에 `import argparse`, `import json` 추가):

```python
def fetch_llm_stances(issue_id: str) -> dict[str, str]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT turn_id, stance FROM issue_stances WHERE issue_id = %s", (issue_id,))
        return {t: s for t, s in cur.fetchall()}


def write_outputs(issue_id, human, result, out_json, out_md) -> None:
    """eval 자산 JSON(재실행용) + 사람 판독 리포트 md 저장."""
    out_json.write_text(json.dumps(
        {"issue_id": issue_id, "rng_seed": 42, "labels": human},
        ensure_ascii=False, indent=1), encoding="utf-8")

    agr = result["agreement"]
    lines = [f"# 입장 판정 eval — {issue_id}", "",
             f"- 공통 {result['n']}건, **일치율 {agr if agr is not None else 'N/A'}** (하드 게이트 없음, 기준선)",
             "", "## 혼동행렬 (행=사람, 열=LLM)", "",
             "| 사람\\LLM | " + " | ".join(STANCES) + " |",
             "|" + "---|" * (len(STANCES) + 1)]
    for h in STANCES:
        lines.append(f"| {h} | " + " | ".join(str(result['matrix'][h][c]) for c in STANCES) + " |")
    lines += ["", f"## 불일치 {len(result['disagreements'])}건", ""]
    for d in result["disagreements"]:
        lines.append(f"- `{d['turn_id']}` 사람={d['human']} / LLM={d['llm']}")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    from db import init_pool, close_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    root = Path(__file__).parent.parent
    labels_path = root / "data" / "issues" / f"stance_labels_{args.issue}.md"
    if not labels_path.exists():
        print(f"[FAIL] 라벨 파일 없음: {labels_path} — stance_label_sheet.py 먼저 실행"); sys.exit(1)
    human = parse_label_sheet(labels_path.read_text(encoding="utf-8"))
    if not human:
        print(f"[FAIL] 기입된 라벨 0건 — `입장:` 뒤에 5택을 기입하세요"); sys.exit(1)

    init_pool()
    llm = fetch_llm_stances(args.issue)
    close_pool()

    result = agreement(human, llm)
    out_json = root / "data" / "eval" / f"stance_eval_{args.issue}.json"
    out_md = root / "data" / "issues" / "stance_eval_report.md"
    write_outputs(args.issue, human, result, out_json, out_md)

    n_sheet = len(_TURN_RE.findall(labels_path.read_text(encoding="utf-8")))
    skipped = n_sheet - len(human)
    if skipped > 0:
        print(f"[WARN] 시트 {n_sheet}건 중 {skipped}건 미기입/무효 제외")
    print(f"[OK] 공통 {result['n']}건 일치율 {result['agreement']} — {out_json.name}, {out_md.name} 저장")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_stance_eval.py`
Expected: PASS — `all passed`

- [ ] **Step 5: 커밋**

```bash
git add scripts/stance_eval.py tests/test_stance_eval.py
git commit -m "feat(pol7): eval CLI — LLM 판정 조회·일치도·혼동행렬 리포트·재실행 자산 저장"
```

---

### Task 5: ★ 사람 라벨링 + 기준선 실행 (수동 게이트)

> **이 태스크는 사용자가 직접 40건을 라벨링해야 완료된다.** 코드가 아니라 데이터 산출물(eval 자산 + 리포트)이 결과물이다. LLM 이 대신 라벨하면 POL-7 의 목적(사람 검증)이 무효가 되므로 자동화 금지.

**Files:**
- Modify: `data/issues/stance_labels_medical-reform.md` (사용자가 `입장:` 40칸 기입)
- Create: `data/eval/stance_eval_medical-reform.json`, `data/issues/stance_eval_report.md` (스크립트 산출)

- [ ] **Step 1: 사용자에게 라벨링 요청**

사용자에게 `data/issues/stance_labels_medical-reform.md` 를 열어 각 항목의 `입장: ` 뒤에 `support`/`oppose`/`concern`/`neutral`/`none` 중 하나를 기입하도록 안내한다. LLM 판정을 보지 않고 발언 원문만으로 판정(블라인드). 40건 전부 기입.

- [ ] **Step 2: 기입 완료 확인**

Run: `python -c "import sys; sys.path.insert(0,'scripts'); from stance_eval import parse_label_sheet; print(len(parse_label_sheet(open('data/issues/stance_labels_medical-reform.md',encoding='utf-8').read())))"`
Expected: `40` (미만이면 미기입/오타 항목을 사용자에게 다시 안내)

- [ ] **Step 3: eval 실행 (기준선 계산)**

Docker `national-assembly-db` 실행 확인 후:

Run: `python scripts/stance_eval.py --issue medical-reform`
Expected: `[OK] 공통 40건 일치율 0.XXX — stance_eval_medical-reform.json, stance_eval_report.md 저장`

- [ ] **Step 4: 기준선 판독**

`data/issues/stance_eval_report.md` 를 열어 (a) 일치율, (b) 혼동행렬에서 약한 경계(예상: concern↔oppose, neutral↔none), (c) 불일치 항목을 확인한다. 이 값이 POL-5 의 medical-reform 기준선이다.

- [ ] **Step 5: 커밋 + POL-5 승격 판단**

```bash
git add data/issues/stance_labels_medical-reform.md data/eval/stance_eval_medical-reform.json data/issues/stance_eval_report.md
git commit -m "eval(pol7): medical-reform 입장 판정 기준선 — 블라인드 라벨 40건 일치율 기록"
```

기준선을 근거로 `docs/progress.md` 와 memory 의 POL-5 상태(🔶→✅ 또는 개선 필요)를 갱신한다. 혼동행렬에서 드러난 약한 경계는 POL-5 프롬프트 정의 보강의 후속 근거로 남긴다(범위 밖).

---

## Self-Review

**1. Spec coverage:**
- 블라인드 라벨 시트 생성(`stance_label_sheet.py`, seed=42, 40건, 판정 숨김, rubric) → Task 3 ✅
- 사용자 라벨링(사람 게이트) → Task 5 Step 1 ✅
- 일치도 + 혼동행렬 계산(`stance_eval.py`) → Task 2 + Task 4 ✅
- eval 자산 JSON 저장(재실행 가능) → Task 4 `write_outputs` ✅
- 리포트 md(일치도·혼동행렬·불일치) → Task 4 `write_outputs` ✅
- 순수 함수 `parse_label_sheet`·`agreement` 테스트 → Task 1·2 ✅
- 저장 형식 `{issue_id, rng_seed, labels}` → Task 4 write_outputs 와 일치 ✅
- 하드 게이트 없음 문서화 → 리포트 "하드 게이트 없음, 기준선" 문구 + Global Constraints ✅

**2. Placeholder scan:** 모든 step 에 실제 코드·명령·기대출력 포함. TBD/TODO 없음.

**3. Type consistency:**
- `STANCES` 튜플 Task 1 정의 → Task 2·3·4 일관 사용 ✅
- `parse_label_sheet(text)->dict`, `agreement(human,llm)->dict` 시그니처 Task 간 일치 ✅
- `render_sheet`/`sample_turns` 원소 형식 `(turn_id, speaker, role, date, text)` — Task 3 `fetch_stance_turns` SELECT 순서와 일치 ✅
- `result["matrix"][h][c]`·`result["disagreements"]` 키 Task 2 반환 구조와 Task 4 소비 일치 ✅
- eval JSON 키 `issue_id`/`rng_seed`/`labels` — Task 4 write 와 테스트 assert 일치 ✅
