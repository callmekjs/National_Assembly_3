# POL-6 여야 대립 구도 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** POL-5 행위자 입장 × POL-0 정당 모듈을 결합해 이슈별 정당 구도(정당별 의원 입장 분포 + 여야 보조 필드)를 `GET /issues/{id}/party-stances` API와 IssueView 패널로 제공한다.

**Architecture:** party.py 의 role 판정을 재사용 함수 `speaker_group` 으로 분리(기존 test_party 회귀로 무해 증명)하고, issues.py 에 순수 집계 함수 3개(`actor_group`·`party_composition`·`party_sides`)를 얹은 뒤 DB 조회 래퍼 `issue_party_stances` + FastAPI 라우트 + 프론트 패널을 붙인다. 사전집계 테이블 없음 — 요청 시 계산.

**Tech Stack:** FastAPI + psycopg2(RealDictCursor), React(IssueView.jsx), pytest 스타일 순수 테스트.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-11-pol6-party-stances-design.md` — 아래 값들은 스펙 verbatim.
- `stance_dist` 키 5개 고정: `support`, `oppose`, `concern`, `mixed`, `no_stance` (aggregate_stances 출력과 일치, 0 포함).
- `parties` 정렬: actor_count 내림차순 → party 명 오름차순, **"정부측"·"무소속/미상"은 맨 뒤** (이 순서).
- `side_by_period`: periods 배열 순서 대응, 의원 정당만. 위성정당은 SATELLITE_PARENT 로 모정당 기준 판정. "정부측"·"무소속/미상"은 `null`.
- 증인·참고인·진술인·국회 스태프는 구도에서 **제외**. 무소속·미상은 "무소속/미상" 행 통합.
- 행위자 그룹 = 이슈 내 최빈 role, 동률 우선순위 `assembly > government > witness > staff > unknown`.
- `LOW_QUALITY_ISSUES` 7개 verbatim: `martial-law`, `lee-jinsook-kcc`, `ytn-privatization`, `public-broadcasting`, `small-business`, `conscription-welfare`, `itaewon-disaster` → `mapping_quality: "low"`, 그 외 `"ok"`.
- 이슈 없음/판정 없음 → 함수 None → 라우트 404 (기존 stances 패턴 동일).
- `party_label` 판정 결과는 리팩터 전후 **불변** — tests/test_party.py 전체 통과가 증거.
- 테스트 관례: 파일 상단 `if __name__ == "__main__": sys.stdout = io.TextIOWrapper(...)`, `python tests/test_x.py` 와 pytest 둘 다 실행 가능.
- API 호출 검증은 `127.0.0.1` (localhost 는 Windows IPv6 우선 +2초).

---

### Task 1: `speaker_group` 분리 (party.py 타깃 리팩터)

**Files:**
- Modify: `backend/party.py` (party_label 의 role 게이트 부분, 현재 137-144행)
- Test: `tests/test_party.py` (test_speaker_group 추가)

**Interfaces:**
- Consumes: party.py 기존 상수 `ASSEMBLY_ROLES`, `STAFF_ROLES`, `WITNESS_ROLES`, `_NOMINEE_ROLE`, `_EXECUTIVE_ROLE`
- Produces: `speaker_group(role: str | None) -> str` — `"assembly" | "government" | "witness" | "staff" | "unknown"` 반환. Task 2 의 `actor_group` 이 소비.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_party.py` 끝에 추가 (파일의 기존 테스트 함수들과 같은 수준):

```python
def test_speaker_group():
    """role → 그룹 분류 (POL-6). party_label 게이트와 동일 판정의 재사용 함수."""
    from party import speaker_group
    assert speaker_group("위원") == "assembly"
    assert speaker_group("위원장") == "assembly"          # 국회 위원장 — exact 매치가 우선
    assert speaker_group("소위원장") == "assembly"
    assert speaker_group("보건복지부장관") == "government"
    assert speaker_group("금융위원장") == "government"     # 행정기관장 (위원장$ 패턴)
    assert speaker_group("증인") == "witness"
    assert speaker_group("참고인") == "witness"
    assert speaker_group("수석전문위원") == "staff"
    assert speaker_group("장관후보자") == "unknown"        # 후보자는 아직 행정부 아님
    assert speaker_group(None) == "unknown"
    assert speaker_group("기타직함") == "unknown"
```

파일 하단에 `if __name__ == "__main__":` 러너가 있으면 `test_speaker_group()` 호출을 추가.

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_party.py::test_speaker_group -q`
Expected: FAIL — `ImportError: cannot import name 'speaker_group'`

- [ ] **Step 3: 구현 — 함수 분리 + party_label 소비 전환**

`backend/party.py` 의 `party_label` 바로 위에 추가:

```python
def speaker_group(role: str | None) -> str:
    """발언 자격(role) → 그룹. party_label 게이트와 동일 판정의 재사용 함수 (POL-6).

    "assembly"(국회의원) | "government"(행정부) | "witness"(증인·참고인·진술인) |
    "staff"(국회 스태프) | "unknown"(미상·후보자·자격불명).
    판정 순서는 기존 party_label 과 동일 — 후보자 검사가 행정부 패턴보다 먼저
    ("위원장후보자" 가 위원장$ 에 오폭하지 않도록).
    """
    if role in ASSEMBLY_ROLES:
        return "assembly"
    if role is None:
        return "unknown"
    if role in STAFF_ROLES:
        return "staff"
    if role in WITNESS_ROLES:
        return "witness"
    if _NOMINEE_ROLE.search(role):
        return "unknown"
    if _EXECUTIVE_ROLE.search(role):
        return "government"
    return "unknown"
```

`party_label` 의 기존 게이트 블록(주석 "자격 불명(None)도 무표기 ..." 포함, `if role not in ASSEMBLY_ROLES:` ~ `return None` 8줄)을 아래로 교체 — 기존 주석은 speaker_group 이 아니라 이 자리에 유지:

```python
    # 자격 불명(None)도 무표기 — role=NULL 이 게이트를 우회해 의원과 동명인
    # 증인에게 정당이 붙을 수 있던 구멍 (2026-07-07 수정). 실측: role=NULL 은
    # 전체 0.12%(494청크), 의원 이름 일치 114청크 — 라벨 손실은 미미하고
    # "자격이 확인된 발언에만 라벨" 원칙이 구조적으로 보장된다.
    group = speaker_group(role)
    if group == "government":
        return "정부측"
    if group != "assembly":
        return None
```

- [ ] **Step 4: 회귀 + 신규 테스트 통과 확인**

Run: `python -m pytest tests/test_party.py -q`
Expected: 전체 PASS (기존 케이스 전부 + test_speaker_group). 기존 케이스 하나라도 깨지면 리팩터가 판정을 바꾼 것 — 교체 블록을 재검토.

- [ ] **Step 5: 커밋**

```bash
git add backend/party.py tests/test_party.py
git commit -m "refactor(pol6): role 판정을 speaker_group 으로 분리 — party_label 판정 불변"
```

---

### Task 2: 순수 집계 함수 3개 (issues.py)

**Files:**
- Modify: `backend/issues.py` (aggregate_stances 아래에 추가)
- Test: `tests/test_party_stances.py` (신규)

**Interfaces:**
- Consumes: `party.speaker_group` (Task 1), `party.RULING_PERIODS`, `party.SATELLITE_PARENT`
- Produces (Task 3 이 소비):
  - `LOW_QUALITY_ISSUES: frozenset[str]` — 7개 이슈 id
  - `actor_group(roles: list) -> str` — 최빈 그룹, 동률 우선순위
  - `party_composition(actors: list[dict]) -> list[dict]` — actors 원소는 `{"speaker", "party", "stance", "roles"}`. 반환 행: `{"party", "actor_count", "stance_dist", "actors"}` (정렬 규칙 적용)
  - `party_sides(parties: list[str]) -> dict` — `{"periods": [{"from","to","ruling"}], "sides": {party: ["여당"|"야당", ...]}}`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_party_stances.py` 신규:

```python
"""POL-6 여야 구도 순수 로직 테스트 — DB 없이 실행.
실행: python tests/test_party_stances.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import (  # noqa: E402
    LOW_QUALITY_ISSUES, actor_group, party_composition, party_sides,
)


def _actor(sp, party, stance, roles):
    return {"speaker": sp, "party": party, "stance": stance, "roles": roles}


def test_actor_group():
    assert actor_group(["위원", "위원"]) == "assembly"
    assert actor_group(["보건복지부장관"]) == "government"
    assert actor_group(["위원", "통일부장관"]) == "assembly"   # 동률 → assembly 우선 (겸직)
    assert actor_group(["증인"]) == "witness"
    assert actor_group([None]) == "unknown"
    assert actor_group(["증인", "증인", "위원"]) == "witness"  # 최빈 우선


def test_party_composition():
    actors = [
        _actor("김A", "더불어민주당", "support", ["위원"]),
        _actor("이B", "더불어민주당", "concern", ["위원"]),
        _actor("박C", "국민의힘", "oppose", ["위원"]),
        _actor("장관D", None, "support", ["보건복지부장관"]),
        _actor("증인E", None, "no_stance", ["증인"]),
        _actor("스태프F", None, "no_stance", ["수석전문위원"]),
        _actor("무소속G", "무소속", "mixed", ["위원"]),
        _actor("미상H", None, "no_stance", ["위원"]),
    ]
    rows = party_composition(actors)
    names = [r["party"] for r in rows]
    # 의원 정당 수 내림차순 → 특수행("정부측", "무소속/미상") 맨 뒤 고정 순서
    assert names == ["더불어민주당", "국민의힘", "정부측", "무소속/미상"], names
    dem = rows[0]
    assert dem["actor_count"] == 2
    assert dem["stance_dist"] == {"support": 1, "oppose": 0, "concern": 1, "mixed": 0, "no_stance": 0}
    assert dem["actors"] == [{"speaker": "김A", "stance": "support"},
                             {"speaker": "이B", "stance": "concern"}]
    # 증인·스태프는 어느 행에도 없음
    everyone = [a["speaker"] for r in rows for a in r["actors"]]
    assert "증인E" not in everyone and "스태프F" not in everyone
    # 무소속 + 정당 미상(의원 자격인데 members 미등록) 통합
    assert rows[-1]["actor_count"] == 2
    assert rows[2]["party"] == "정부측" and rows[2]["actor_count"] == 1


def test_party_sides():
    ps = party_sides(["더불어민주당", "국민의힘", "국민의미래"])
    assert len(ps["periods"]) == 2
    assert ps["periods"][0] == {"from": "2024-05-30", "to": "2025-06-03", "ruling": "국민의힘"}
    assert ps["periods"][1]["from"] == "2025-06-04" and ps["periods"][1]["to"] is None
    assert ps["periods"][1]["ruling"] == "더불어민주당"
    assert ps["sides"]["더불어민주당"] == ["야당", "여당"]
    assert ps["sides"]["국민의힘"] == ["여당", "야당"]
    assert ps["sides"]["국민의미래"] == ["여당", "야당"]   # 위성정당 → 모정당 기준


def test_low_quality_issues():
    assert "martial-law" in LOW_QUALITY_ISSUES
    assert len(LOW_QUALITY_ISSUES) == 7


if __name__ == "__main__":
    test_actor_group()
    test_party_composition()
    test_party_sides()
    test_low_quality_issues()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_party_stances.py`
Expected: FAIL — `ImportError: cannot import name 'LOW_QUALITY_ISSUES'`

- [ ] **Step 3: 구현**

`backend/issues.py` 상단 import 에 `from collections import Counter` 추가. `aggregate_stances` 아래에 추가:

```python
# POL-3 core 게이트(≥90%) 미달 7개 — 매핑 정밀도 경고 대상 (progress.md POL-3 후속 기록,
# core 86.2% 마감 2026-07-08). 게이트 재실행 시 이 목록도 갱신할 것.
LOW_QUALITY_ISSUES = frozenset({
    "martial-law", "lee-jinsook-kcc", "ytn-privatization", "public-broadcasting",
    "small-business", "conscription-welfare", "itaewon-disaster",
})

_COMP_STANCES = ("support", "oppose", "concern", "mixed", "no_stance")  # 행위자 대표 라벨
_GROUP_PRIORITY = ("assembly", "government", "witness", "staff", "unknown")  # 동률 우선순위
_SPECIAL_ROWS = ("정부측", "무소속/미상")  # 항상 맨 뒤, 이 순서


def actor_group(roles: list) -> str:
    """행위자의 이슈 내 role 목록 → 최빈 그룹. 동률이면 _GROUP_PRIORITY 순 (겸직 의원 우선)."""
    from party import speaker_group
    counts = Counter(speaker_group(r) for r in roles)
    return max(_GROUP_PRIORITY, key=lambda g: (counts.get(g, 0), -_GROUP_PRIORITY.index(g)))


def party_composition(actors: list[dict]) -> list[dict]:
    """행위자 목록 → 정당별 구도 행 (POL-6). actors 원소: speaker/party/stance/roles.

    assembly → 정당 행(무소속·미상은 "무소속/미상"), government → "정부측",
    witness·staff → 제외. 정렬: 수 내림차순 → 정당명, 특수행 맨 뒤."""
    rows: dict[str, dict] = {}
    for a in actors:
        g = actor_group(a["roles"])
        if g in ("witness", "staff"):
            continue
        if g == "government":
            key = "정부측"
        elif g == "assembly" and a["party"] and a["party"] != "무소속":
            key = a["party"]
        else:
            key = "무소속/미상"
        row = rows.setdefault(key, {"party": key, "actor_count": 0,
                                    "stance_dist": {s: 0 for s in _COMP_STANCES},
                                    "actors": []})
        row["actor_count"] += 1
        row["stance_dist"][a["stance"]] += 1
        row["actors"].append({"speaker": a["speaker"], "stance": a["stance"]})
    ordered = sorted((r for r in rows.values() if r["party"] not in _SPECIAL_ROWS),
                     key=lambda r: (-r["actor_count"], r["party"]))
    ordered += [rows[k] for k in _SPECIAL_ROWS if k in rows]
    return ordered


def party_sides(parties: list[str]) -> dict:
    """정권교체 구간 목록 + 정당별 여야 (POL-6 보조 필드). 위성정당은 모정당 기준."""
    from party import RULING_PERIODS, SATELLITE_PARENT
    periods = [{"from": s.isoformat(), "to": None if e.year == 9999 else e.isoformat(),
                "ruling": p} for s, e, p in RULING_PERIODS]
    sides = {party: ["여당" if SATELLITE_PARENT.get(party, party) == pr["ruling"] else "야당"
                     for pr in periods] for party in parties}
    return {"periods": periods, "sides": sides}
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_party_stances.py` 그리고 `python -m pytest tests/test_party_stances.py -q`
Expected: 둘 다 PASS — `all passed` / 4 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/issues.py tests/test_party_stances.py
git commit -m "feat(pol6): 정당 구도 순수 집계 — actor_group·party_composition·party_sides"
```

---

### Task 3: `issue_party_stances` + 라우트 + api.js

**Files:**
- Modify: `backend/issues.py` (issue_stances 아래에 추가)
- Modify: `backend/main.py` (import 줄 17행 + `/issues/{issue_id}/stances` 라우트 아래 신규 라우트)
- Modify: `frontend/src/api.js` (fetchStances 아래)

**Interfaces:**
- Consumes: Task 2 의 `LOW_QUALITY_ISSUES`·`actor_group`(간접)·`party_composition`·`party_sides`, 기존 `aggregate_stances`, `party.member_party`, `db.get_conn`
- Produces:
  - `issue_party_stances(issue_id: str) -> dict | None` — 스펙의 API 응답 dict (issue_id/title/mapping_quality/periods/parties)
  - `GET /issues/{issue_id}/party-stances` 라우트 (404 처리)
  - `fetchPartyStances(issueId)` (Task 4 가 소비)

- [ ] **Step 1: 구현 — 백엔드 함수**

`backend/issues.py` 의 `issue_stances` 함수 아래에 추가:

```python
def issue_party_stances(issue_id: str) -> dict | None:
    """이슈 정당 구도 (POL-6). 이슈 없거나 판정 데이터 없으면 None.

    발언 rows → 행위자(speaker)별 대표 라벨(aggregate_stances) + role 목록
    → party_composition 으로 정당 행 → side_by_period 보조 필드 부착."""
    from party import member_party
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("SELECT speaker, role, stance FROM issue_stances WHERE issue_id = %s",
                    (issue_id,))
        srows = cur.fetchall()
    if not srows:
        return None

    by_speaker: dict[str, list] = {}
    for r in srows:
        by_speaker.setdefault(r["speaker"], []).append(r)
    actors = [{"speaker": sp, "party": member_party(sp), "stance": aggregate_stances(rs),
               "roles": [r["role"] for r in rs]} for sp, rs in by_speaker.items()]

    parties = party_composition(actors)
    ps = party_sides([r["party"] for r in parties if r["party"] not in _SPECIAL_ROWS])
    for r in parties:
        r["side_by_period"] = ps["sides"].get(r["party"])  # 특수행은 None
    return {"issue_id": issue_id, "title": row["title"],
            "mapping_quality": "low" if issue_id in LOW_QUALITY_ISSUES else "ok",
            "periods": ps["periods"], "parties": parties}
```

- [ ] **Step 2: 라우트 + import**

`backend/main.py` 17행 import 를 다음으로 교체:

```python
from issues import issue_party_stances, issue_stances, issue_timeline, list_issues
```

`get_issue_stances` 라우트 아래에 추가:

```python
@app.get("/issues/{issue_id}/party-stances")
def get_issue_party_stances(issue_id: str):
    """쟁점 정당 구도 (POL-6) — 정당별 의원 입장 분포 + 여야 보조(정권교체 구간별)."""
    result = issue_party_stances(issue_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"입장 데이터 없음: {issue_id}")
    return result
```

- [ ] **Step 3: api.js 래퍼**

`frontend/src/api.js` 의 `fetchStances` 바로 아래, 인접 함수와 동일한 형태로 추가 (fetchStances 가 쓰는 호출 형태를 그대로 따를 것):

```javascript
export function fetchPartyStances(issueId) {
  return request(`/issues/${issueId}/party-stances`)
}
```

- [ ] **Step 4: 실DB 스모크 (Docker `national-assembly-db` 가동 필요)**

Run:

```bash
python -c "import sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); sys.path.insert(0,'backend'); from dotenv import load_dotenv; load_dotenv('.env'); from db import init_pool, close_pool; init_pool(); from issues import issue_party_stances; r=issue_party_stances('medical-reform'); print('quality:', r['mapping_quality']); print('periods:', len(r['periods'])); print('parties:', [(p['party'], p['actor_count'], p['side_by_period']) for p in r['parties']]); print('404:', issue_party_stances('no-such-issue')); low=issue_party_stances('martial-law'); print('low-quality:', low['mapping_quality'] if low else 'no-data'); close_pool()"
```

Expected:
- `quality: ok`, `periods: 2`
- `parties:` 목록에 "정부측" 행 존재 (의정갈등은 정부 발언 다수), 더불어민주당·국민의힘의 side_by_period 가 각각 `['야당', '여당']`·`['여당', '야당']`, 특수행은 `None`
- `404: None`
- `low-quality: low`

행위자 수 합계가 기존 `/stances` 의 actors 수에서 증인·스태프 제외분만큼 빠지는지 눈으로 확인 (검증 노트에 수치 기록).

- [ ] **Step 5: 전체 테스트 + 커밋**

Run: `python -m pytest tests/ -q` — Expected: 전체 PASS (기존 71 + 신규)

```bash
git add backend/issues.py backend/main.py frontend/src/api.js
git commit -m "feat(pol6): GET /issues/{id}/party-stances — 정당 구도 API + api.js 래퍼"
```

---

### Task 4: 프론트 여야 구도 패널 (IssueView)

**Files:**
- Modify: `frontend/src/components/IssueView.jsx`

**Interfaces:**
- Consumes: `fetchPartyStances(issueId)` (Task 3), 기존 `STANCE_KO`·`STANCE_COLOR` 상수
- Produces: IssueView 내 "여야 구도" 섹션 (행위자 입장 테이블 위)

- [ ] **Step 1: 구현**

`frontend/src/components/IssueView.jsx`:

(a) import 줄을 교체:

```javascript
import { fetchIssues, fetchTimeline, fetchStances, fetchPartyStances } from '../api'
```

(b) `STANCE_COLOR` 상수 아래에 컴포넌트 2개 추가:

```javascript
function PartyBar({ row }) {
  const total = Math.max(row.actor_count, 1)
  const badge = row.side_by_period
    ? (row.side_by_period[0] === row.side_by_period[1]
        ? row.side_by_period[0] : `${row.side_by_period[0]}→${row.side_by_period[1]}`)
    : null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '4px 0' }}>
      <div style={{ width: 170, fontSize: 13, flexShrink: 0 }}>
        {row.party}{' '}
        {badge && <span style={{ fontSize: 11, color: '#555', border: '1px solid #ccc', borderRadius: 4, padding: '0 4px' }}>{badge}</span>}
      </div>
      <div style={{ flex: 1, display: 'flex', height: 18, borderRadius: 3, overflow: 'hidden', background: '#f3f4f6' }}>
        {Object.entries(row.stance_dist).filter(([, v]) => v > 0).map(([s, v]) => (
          <div key={s} title={`${STANCE_KO[s]} ${v}명`}
               style={{ width: `${(v / total) * 100}%`, background: STANCE_COLOR[s] }} />
        ))}
      </div>
      <div style={{ width: 44, fontSize: 12, textAlign: 'right', flexShrink: 0 }}>{row.actor_count}명</div>
    </div>
  )
}

function PartyPanel({ data }) {
  if (!data) return <p>불러오는 중…</p>
  return (
    <div>
      {data.mapping_quality === 'low' && (
        <p style={{ color: '#d97706', fontSize: 12 }}>
          ⚠ 이 이슈의 청크 매핑 정밀도는 게이트 기준(90%) 미달 — 구도 수치 해석 주의
        </p>
      )}
      {data.parties.map(r => <PartyBar key={r.party} row={r} />)}
      <p style={{ fontSize: 11, color: '#666' }}>
        {Object.entries(STANCE_KO).map(([s, ko]) => (
          <span key={s} style={{ marginRight: 10 }}>
            <span style={{ color: STANCE_COLOR[s] }}>■</span> {ko}
          </span>
        ))}
      </p>
    </div>
  )
}
```

(c) `IssueView` 본체: state 와 fetch 추가 — `const [stances, setStances] = useState(null)` 아래에

```javascript
  const [partyStances, setPartyStances] = useState(null)
```

이슈 선택 useEffect 안 `setStances(null)` 뒤에 `setPartyStances(null)`, `fetchStances(...)` 줄 아래에

```javascript
    fetchPartyStances(sel).then(setPartyStances).catch(() => setPartyStances(null))
```

(주의: `.catch(e => setError(...))` 가 아님 — 판정 없는 이슈에서 404 가 페이지 전체 에러로 번지지 않게 패널만 비움)

(d) 렌더: `<h3>행위자 입장 ...` 줄 **위**에 추가:

```javascript
      <h3>여야 구도</h3>
      {partyStances ? <PartyPanel data={partyStances} /> : <p>구도 데이터 없음(판정된 이슈만 표시)</p>}
```

- [ ] **Step 2: 브라우저 확인 (백엔드 8000 + 프론트 5173)**

백엔드가 안 떠 있으면: `cd backend && python -m uvicorn main:app --port 8000` (백그라운드, --reload 금지 — Windows hang). 프론트는 `.claude/launch.json` 의 dev 서버 (5173).

확인 항목 — "쟁점 분석" 탭에서:
1. medical-reform: "여야 구도" 패널 렌더 — 정당 행 + 누적 막대 + 여야 배지("야당→여당" 형태), "정부측" 행 존재, 맨 뒤 배치
2. martial-law 선택: 경고 배너(⚠ 매핑 정밀도) 표시
3. 콘솔 에러 0 (read_console_messages)
4. 행위자 입장 테이블 등 기존 섹션 회귀 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/components/IssueView.jsx
git commit -m "feat(pol6): IssueView 여야 구도 패널 — 정당 누적 막대 + 여야 배지 + 저품질 경고"
```

---

### Task 5: 문서 — progress.md 기록

**Files:**
- Modify: `docs/progress.md` (3단계 로드맵 표 POL-6 행 + POL-7 구현 기록 아래 신규 섹션 + 3행 최종 업데이트 줄)

**Interfaces:**
- Consumes: Task 1~4 의 결과 (커밋·스모크 수치)
- Produces: 로드맵 상태 갱신

- [ ] **Step 1: 로드맵 표 갱신**

POL-6 행의 상태 셀 `⬜` 를 다음으로 교체:

```
✅ 2026-07-11 (GET /issues/{id}/party-stances — 정당 축 + 여야 보조, 정부측 행, 프론트 패널. 구현 기록 참조)
```

- [ ] **Step 2: 구현 기록 섹션 추가**

"### POL-7 구현 기록" 섹션 끝(다음 `##` 직전)에 추가:

```markdown
### POL-6 구현 기록 — 여야 대립 구도 (2026-07-11)

> spec: `docs/superpowers/specs/2026-07-11-pol6-party-stances-design.md`,
> plan: `docs/superpowers/plans/2026-07-11-pol6-party-stances.md`

- **설계**: 정당 축 기본 + 여야 보조(사용자 결정) — 구도는 정당별 의원 입장 분포로
  산출하고, 여야는 정권교체(2025-06-04) 구간별 `side_by_period` 필드로 표기
  ("야당→여당"). 교체 전/후 구도 분리·단일 여야 합산은 기각(정보 손실).
- **구현**: party.py role 판정을 `speaker_group` 으로 분리(판정 불변, test_party 회귀)
  → issues.py 순수 함수(`actor_group` 최빈 role·동률 의원 우선 / `party_composition`
  증인·스태프 제외, 정부측·무소속/미상 특수행 / `party_sides` 위성정당 모정당 기준)
  → `GET /issues/{id}/party-stances`(404, LOW_QUALITY_ISSUES 7개 `mapping_quality:
  "low"`) → IssueView 정당 누적 막대 패널(여야 배지·저품질 경고 배너).
- **주의**: 입장 판정은 POL-5 교차검증(67.5%) 품질 상속 — 방향 신뢰·세분류 ±1단계.
  구도 해석은 방향 중심으로. 사람 기준선(POL-7 잔여) 확보 시 재평가.
- 범위 밖: RAG 답변 주입(POL-8), 공수교대 시계열, 탈당 추적.
```

- [ ] **Step 3: 3행 최종 업데이트 줄 교체**

```
최종 업데이트: 2026-07-11 (POL-6 여야 구도 — 정당 구도 API + 프론트 패널. 다음: POL-8 통합 또는 POL-7 사람 기준선)
```

- [ ] **Step 4: 커밋**

```bash
git add docs/progress.md
git commit -m "docs(pol6): 구현 기록 + 로드맵 POL-6 완료"
```

---

## Self-Review

**1. Spec coverage:**
- API 응답 형식(issue_id/title/mapping_quality/periods/parties, side_by_period null 규칙, 정렬) → Task 2 정렬·Task 3 조립 ✅
- speaker_group 분리 + party_label 불변 회귀 → Task 1 ✅
- actor_group 최빈·동률 우선순위 → Task 2 ✅ (스펙 우선순위 verbatim)
- 정부측/무소속·미상/증인·스태프 처리 → Task 2 party_composition ✅
- 위성정당 모정당 기준 여야 → Task 2 party_sides + 테스트 ✅
- LOW_QUALITY_ISSUES 7개 + 경고 필드 → Task 2 상수 + Task 3 조립 + Task 4 배너 ✅
- 프론트(막대·배지·경고·기존 매트릭스 위 배치) → Task 4 ✅
- 테스트 요구(순수 함수·회귀·API 스모크·브라우저) → Task 1·2·3·4 각 스텝 ✅
- 문서 → Task 5 ✅

**2. Placeholder scan:** TBD/TODO 없음. 모든 코드 스텝에 실제 코드 포함.

**3. Type consistency:**
- `speaker_group(role) -> str` Task 1 정의 = Task 2 actor_group 소비 ✅
- actors 원소 `{"speaker","party","stance","roles"}` — Task 2 테스트 `_actor` = Task 3 조립부 ✅
- `party_composition` 반환 행 키(party/actor_count/stance_dist/actors) = Task 3 side_by_period 부착·Task 4 PartyBar 소비(row.party/actor_count/stance_dist/side_by_period) ✅
- `party_sides` 반환 `{"periods","sides"}` = Task 3 소비 ✅
- `_SPECIAL_ROWS` Task 2 정의 = Task 3 사용 ✅
- `fetchPartyStances` Task 3 정의 = Task 4 import ✅
