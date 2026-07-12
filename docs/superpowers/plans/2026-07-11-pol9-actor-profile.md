# POL-9 의원 프로필 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** POL-2 행위자 API 를 소비하는 "의원 프로필" 탭을 신설하고 쟁점 분석 뷰와 양방향 연결(매트릭스 의원 클릭→프로필, 프로필 이슈 행 클릭→쟁점 탭)해 3단계 데모 동선을 완성한다.

**Architecture:** 백엔드는 actors.py 한 곳만 확장 — `actor_profile` 응답에 이슈별 입장(`issue_stances`) 필드 추가(순수부 `fold_issue_stances` 분리, `aggregate_stances` 재사용). 프론트는 신규 `ActorView.jsx`(8개 섹션) + App.jsx 상태 승격(`selectedActor`·`selectedIssue`) + IssueView 이름 셀 클릭 연결. 라우터 도입 없음.

**Tech Stack:** FastAPI, psycopg2(RealDictCursor), React(App.jsx/ActorView.jsx/IssueView.jsx), pytest 스타일 순수 테스트.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-11-pol9-actor-profile-design.md` — 아래 값 verbatim.
- `actor_profile` 기존 필드·시그니처 **불변** — 기존 tests/test_actors.py 회귀 + 스모크로 보증. `issue_stances` 필드만 추가.
- `fold_issue_stances` 반환: `[{"issue_id","title","stance","counts","total_turns"}]`, counts 5키(support/oppose/concern/neutral/none, 0 포함), 대표 stance 는 `issues.aggregate_stances` 재사용(support/oppose/concern/mixed/no_stance), total_turns 내림차순. 빈 입력 → [].
- 프론트 STANCE_KO/COLOR 값은 IssueView 와 동일 (support 파랑 #2563eb / oppose 빨강 #dc2626 / concern 주황 #d97706 / mixed 보라 #7c3aed / no_stance 회색 #6b7280).
- 매트릭스 **이름 셀만** 클릭 → 프로필 (행 클릭의 근거 펼침과 충돌 금지 — stopPropagation).
- 이슈별 입장 테이블 하단 주석 verbatim: `입장은 LLM 자동 판정 — 방향 참고용`
- 404 인물은 페이지 에러가 아니라 패널 내 안내 문구.
- 인라인 스타일 관례 유지, 새 CSS 파일 금지. react-router 금지.
- 테스트 관례: check() 헬퍼(tests/test_actors.py 기존), python 직접 실행 + pytest.

---

### Task 1: 백엔드 — 이슈별 입장 필드 (`fold_issue_stances` + `actor_issue_stances`)

**Files:**
- Modify: `backend/actors.py` (import 1줄 + 함수 2개 + actor_profile 반환 1줄)
- Test: `tests/test_actors.py` (test_fold_issue_stances 추가)

**Interfaces:**
- Consumes: `issues.aggregate_stances`(기존), `db.get_conn`, `expand_aliases`(기존)
- Produces: `actor_profile` 응답에 `"issue_stances": [{"issue_id","title","stance","counts","total_turns"}]` (Task 2 ActorView 가 소비)

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_actors.py` 끝에 추가 (기존 check() 스타일):

```python
def test_fold_issue_stances():
    from actors import fold_issue_stances
    rows = [
        {"issue_id": "a", "title": "이슈A", "stance": "support", "n": 3},
        {"issue_id": "a", "title": "이슈A", "stance": "concern", "n": 1},
        {"issue_id": "b", "title": "이슈B", "stance": "neutral", "n": 5},
    ]
    r = fold_issue_stances(rows)
    check("발언수 내림차순 (b 5 > a 4)", [x["issue_id"] for x in r] == ["b", "a"], r)
    a = next(x for x in r if x["issue_id"] == "a")
    check("대표 라벨 support (3>1)", a["stance"] == "support", a)
    check("counts 5키 0 포함",
          a["counts"] == {"support": 3, "oppose": 0, "concern": 1, "neutral": 0, "none": 0}, a)
    check("total_turns 4", a["total_turns"] == 4, a)
    b = next(x for x in r if x["issue_id"] == "b")
    check("입장발언 0 → no_stance", b["stance"] == "no_stance", b)
    check("빈 입력 []", fold_issue_stances([]) == [])
```

파일 하단 `__main__` 러너가 있으면 호출 추가.

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_actors.py::test_fold_issue_stances -q`
Expected: FAIL — `ImportError: cannot import name 'fold_issue_stances'`

- [ ] **Step 3: 구현**

`backend/actors.py` 상단 import 블록에 추가:

```python
from issues import aggregate_stances
```

`build_party_history` 아래에 추가:

```python
_STANCE_KEYS = ("support", "oppose", "concern", "neutral", "none")


def fold_issue_stances(rows: list[dict]) -> list[dict]:
    """(issue_id, title, stance, n) 집계 행 → 이슈별 대표 라벨 + 카운트. 순수 함수.

    대표 라벨은 issues.aggregate_stances 재사용 — 쟁점 매트릭스(POL-5)와 동일 규칙이라
    두 화면의 라벨이 어긋나지 않는다. 정렬은 발언 수 내림차순."""
    by_issue: dict[str, dict] = {}
    for r in rows:
        it = by_issue.setdefault(r["issue_id"], {
            "issue_id": r["issue_id"], "title": r["title"],
            "counts": {s: 0 for s in _STANCE_KEYS},
        })
        it["counts"][r["stance"]] += r["n"]
    out = []
    for it in by_issue.values():
        flat = [{"stance": s} for s, n in it["counts"].items() for _ in range(n)]
        out.append({**it, "stance": aggregate_stances(flat),
                    "total_turns": sum(it["counts"].values())})
    out.sort(key=lambda x: -x["total_turns"])
    return out


def actor_issue_stances(variants: list[str]) -> list[dict]:
    """의원의 이슈별 입장 (POL-9) — issue_stances 역조회, 별칭 목록으로 매칭."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT s.issue_id, i.title, s.stance, count(*) AS n
            FROM issue_stances s JOIN issues i USING (issue_id)
            WHERE s.speaker = ANY(%s)
            GROUP BY s.issue_id, i.title, s.stance
            ORDER BY s.issue_id
        """, (variants,))
        return fold_issue_stances(cur.fetchall())
```

`actor_profile` 의 반환 dict 에서 `"recent_utterances": recent,` 줄 다음에 추가:

```python
        "issue_stances": actor_issue_stances(variants),
```

(주의: 반환 dict 는 `with get_conn()` 블록 밖 — actor_issue_stances 가 자체 커넥션을 풀에서 받는다. 기존 필드·순서는 건드리지 않는다.)

- [ ] **Step 4: 순수 테스트 + 기존 회귀**

Run: `python -m pytest tests/test_actors.py -q`
Expected: 전체 PASS (기존 + 신규)

- [ ] **Step 5: 실DB 스모크 (Docker `national-assembly-db` 가동)**

Run:

```bash
python -c "import sys,io; sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8'); sys.path.insert(0,'backend'); from dotenv import load_dotenv; load_dotenv('.env'); from db import init_pool, close_pool; init_pool(); from actors import actor_profile; p=actor_profile('김윤'); print('기존 키:', sorted(p.keys())); print('이슈별 입장 수:', len(p['issue_stances'])); print('상위 3:', [(i['issue_id'], i['stance'], i['total_turns']) for i in p['issue_stances'][:3]]); print('미존재:', actor_profile('없는사람123')); close_pool()"
```

Expected: 기존 키에 `issue_stances` 추가된 목록(기존 10개 필드 전부 존재), 김윤의 이슈별 입장 ≥1건(medical-reform 포함), `미존재: None`.

- [ ] **Step 6: 전체 테스트 + 커밋**

Run: `python -m pytest tests/ -q` — Expected: 전체 PASS

```bash
git add backend/actors.py tests/test_actors.py
git commit -m "feat(pol9): 행위자 프로필에 이슈별 입장 — fold_issue_stances·aggregate_stances 재사용"
```

---

### Task 2: ActorView.jsx 신규 + api.js

**Files:**
- Create: `frontend/src/components/ActorView.jsx`
- Modify: `frontend/src/api.js` (fetchActor 추가 — fetchPartyStances 아래)

**Interfaces:**
- Consumes: `GET /actors/{name}` 응답 (Task 1 확장분 포함), `request()` 래퍼
- Produces: `<ActorView actor={string|null} onIssueClick={fn(issueId)} />` — Task 3 의 App.jsx 가 소비. `fetchActor(name)`.

- [ ] **Step 1: api.js 래퍼 추가** (fetchPartyStances 바로 아래, 동일 형태)

```javascript
export function fetchActor(name) {
  return request(`/actors/${encodeURIComponent(name)}`)
}
```

- [ ] **Step 2: ActorView.jsx 작성**

`frontend/src/components/ActorView.jsx` 전체:

```jsx
import { useEffect, useState } from 'react'
import { fetchActor } from '../api'

const STANCE_KO = { support: '찬성', oppose: '반대', concern: '우려', mixed: '혼재', no_stance: '무입장' }
const STANCE_COLOR = { support: '#2563eb', oppose: '#dc2626', concern: '#d97706', mixed: '#7c3aed', no_stance: '#6b7280' }

function MonthLine({ months }) {
  if (!months || months.length < 2) return null
  const W = 640, H = 110, pad = 24
  const max = Math.max(...months.map(m => m.turns), 1)
  const x = i => pad + i * (W - 2 * pad) / (months.length - 1)
  const y = v => H - pad + 6 - v / max * (H - 2 * pad)
  const pts = months.map((m, i) => `${x(i).toFixed(1)},${y(m.turns).toFixed(1)}`).join(' ')
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="월별 발언 추이">
      <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={pts} />
      <text x={pad} y={H - 4} fontSize="11" fill="#666">{months[0].month}</text>
      <text x={W - pad} y={H - 4} fontSize="11" fill="#666" textAnchor="end">{months[months.length - 1].month}</text>
    </svg>
  )
}

export default function ActorView({ actor, onIssueClick }) {
  const [input, setInput] = useState(actor || '')
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  async function load(name) {
    const q = (name || '').trim()
    if (!q) return
    setErr(null); setProfile(null); setLoading(true)
    try { setProfile(await fetchActor(q)) } catch (e) { setErr(e.message) } finally { setLoading(false) }
  }
  useEffect(() => { if (actor) { setInput(actor); load(actor) } }, [actor])

  const maxCommittee = profile ? Math.max(...profile.by_committee.map(c => c.turns), 1) : 1

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <input value={input} onChange={e => setInput(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && load(input)}
               placeholder="의원 이름 (예: 김윤)" style={{ padding: '6px 8px', marginRight: 8 }} />
        <button onClick={() => load(input)} disabled={loading}>{loading ? '조회 중…' : '조회'}</button>
      </div>
      {err && <p style={{ color: '#6b7280' }}>{err}</p>}
      {profile && (
        <div>
          <h3 style={{ marginBottom: 4 }}>
            {profile.display_name || profile.name}{' '}
            {profile.party && <span style={{ fontSize: 13, color: '#555', border: '1px solid #ccc', borderRadius: 4, padding: '0 6px' }}>{profile.party}</span>}
          </h3>
          {profile.party_history.length > 0 && (
            <p style={{ fontSize: 12, color: '#666', margin: '2px 0 8px' }}>
              {profile.party_history.map(h => `${h.period}: ${h.label || '—'}`).join(' / ')}
            </p>
          )}
          <p style={{ fontSize: 13 }}>
            발언 {profile.totals.turns.toLocaleString()}턴 · 회의 {profile.totals.meetings}회 · {profile.totals.first} ~ {profile.totals.last}
          </p>

          <h4>위원회 분포</h4>
          {profile.by_committee.map(c => (
            <div key={c.committee} style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 0' }}>
              <div style={{ width: 150, fontSize: 12, flexShrink: 0 }}>{c.committee}</div>
              <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 3, height: 14 }}>
                <div style={{ width: `${(c.turns / maxCommittee) * 100}%`, background: '#2563eb', height: 14, borderRadius: 3 }} />
              </div>
              <div style={{ width: 60, fontSize: 12, textAlign: 'right' }}>{c.turns}턴</div>
            </div>
          ))}

          <h4>월별 발언 추이</h4>
          <MonthLine months={profile.by_month} />

          <h4>이슈별 입장</h4>
          {profile.issue_stances.length > 0 ? (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={{ textAlign: 'left' }}>이슈</th><th>입장</th><th>발언 수</th></tr></thead>
                <tbody>
                  {profile.issue_stances.map(s => (
                    <tr key={s.issue_id} onClick={() => onIssueClick(s.issue_id)} style={{ cursor: 'pointer' }}
                        title="클릭하면 쟁점 분석으로 이동">
                      <td>{s.title}</td>
                      <td style={{ textAlign: 'center', color: STANCE_COLOR[s.stance], fontWeight: 600 }}>{STANCE_KO[s.stance]}</td>
                      <td style={{ textAlign: 'center', fontSize: 12 }}>{s.total_turns}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p style={{ fontSize: 11, color: '#888' }}>입장은 LLM 자동 판정 — 방향 참고용</p>
            </>
          ) : <p style={{ fontSize: 13, color: '#666' }}>판정된 이슈 없음</p>}

          <h4>발언 유형</h4>
          <p style={{ fontSize: 13 }}>
            {Object.entries(profile.utterance_types).map(([k, v]) => `${k === 'question' ? '질의' : '진술'} ${(v * 100).toFixed(0)}%`).join(' · ') || '—'}
          </p>

          <h4>주요 언급 기관</h4>
          <p style={{ fontSize: 13 }}>
            {profile.top_mentions.map(m => `${m.org}(${m.count})`).join(', ') || '—'}
          </p>

          <h4>최근 발언</h4>
          {profile.recent_utterances.map(u => (
            <p key={u.chunk_id} style={{ fontSize: 12, color: '#444', margin: '4px 0' }}>
              [{u.date} · {u.committee}] {u.snippet}…
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: 빌드 성공 (미사용 컴포넌트지만 문법·import 검증)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/ActorView.jsx frontend/src/api.js
git commit -m "feat(pol9): ActorView 의원 프로필 컴포넌트 — 8섹션 + 이슈별 입장 테이블"
```

---

### Task 3: App 통합 + IssueView 연결 + 브라우저 E2E

**Files:**
- Modify: `frontend/src/App.jsx` (import·state·탭·렌더)
- Modify: `frontend/src/components/IssueView.jsx` (props 2개 + 이름 셀 클릭 + selectedIssue 반영)

**Interfaces:**
- Consumes: Task 2 의 `ActorView`
- Produces: 탭 3개 동작 + 양방향 이동

- [ ] **Step 1: App.jsx 수정**

(a) import 추가: `import ActorView from './components/ActorView'`

(b) state 줄(`const [tab, setTab] = useState('query')`) 아래에:

```javascript
  const [selectedActor, setSelectedActor] = useState(null)
  const [selectedIssue, setSelectedIssue] = useState(null)

  function openActor(name) { setSelectedActor(name); setTab('actor') }
  function openIssue(issueId) { setSelectedIssue(issueId); setTab('issues') }
```

(c) 탭 버튼 줄(쟁점 분석 버튼 다음)에:

```javascript
        <button onClick={() => setTab('actor')} disabled={tab === 'actor'}>의원 프로필</button>
```

(d) `{tab === 'issues' && <IssueView />}` 를 교체:

```javascript
      {tab === 'issues' && <IssueView selectedIssue={selectedIssue} onActorClick={openActor} />}
      {tab === 'actor' && <ActorView actor={selectedActor} onIssueClick={openIssue} />}
```

- [ ] **Step 2: IssueView.jsx 연결**

(a) 시그니처 교체: `export default function IssueView({ selectedIssue, onActorClick })`

(b) `const [sel, setSel] = useState('medical-reform')` 아래에 prop 반영 추가:

```javascript
  useEffect(() => { if (selectedIssue) setSel(selectedIssue) }, [selectedIssue])
```

(c) `StanceRow` 에 `onActorClick` prop 전달: 호출부를
`<StanceRow key={a.speaker} actor={a} onActorClick={onActorClick} />` 로, 컴포넌트 시그니처를 `function StanceRow({ actor, onActorClick })` 로.

(d) StanceRow 의 발언자 셀 `<td>{actor.speaker}</td>` 를 교체 (이름 셀만 클릭, 행 클릭 근거 펼침과 충돌 방지):

```javascript
        <td onClick={e => { e.stopPropagation(); onActorClick && onActorClick(actor.speaker) }}
            style={{ color: '#2563eb', textDecoration: 'underline', cursor: 'pointer' }}
            title="의원 프로필 보기">{actor.speaker}</td>
```

- [ ] **Step 3: 브라우저 E2E (백엔드 8000 + 프론트 5173)**

백엔드가 안 떠 있거나 **Task 1 이전 코드로 떠 있으면** 재시작 (`--reload` 금지). 체크리스트:

1. "의원 프로필" 탭 → "김윤" 검색 → 8개 섹션 렌더 (헤더·정당 배지·통계·위원회 바·월별 라인·**이슈별 입장 테이블 + "LLM 자동 판정" 주석**·발언 유형·언급·최근 발언)
2. 이슈별 입장에서 의정갈등 행 클릭 → **쟁점 분석 탭으로 이동 + 해당 이슈 선택됨** (구도 패널·매트릭스가 그 이슈로 렌더)
3. 쟁점 매트릭스에서 임의 의원 이름 클릭 → **프로필 탭 이동 + 자동 조회** (행 근거 펼침이 같이 열리지 않아야 함 — stopPropagation 확인)
4. "없는사람123" 검색 → 패널 내 안내 문구 (페이지 에러 아님)
5. 콘솔 에러 0, 기존 질의 탭 회귀 없음

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/App.jsx frontend/src/components/IssueView.jsx
git commit -m "feat(pol9): 탭 통합 — 매트릭스↔프로필 양방향 이동 (상태 승격)"
```

---

### Task 4: 문서 — progress.md (3단계 마감 기록)

**Files:**
- Modify: `docs/progress.md`

**Interfaces:**
- Consumes: Task 1~3 결과
- Produces: 로드맵 POL-9 ✅ + 3단계 마감 선언

- [ ] **Step 1: 로드맵 표 POL-9 행 상태 셀 `⬜` 교체**

```
✅ 2026-07-11 (의원 프로필 탭 + 매트릭스↔프로필 양방향 + 이슈별 입장. 구현 기록 참조. **3단계 전 항목 종료** — POL-5·7 사람검증만 🔶)
```

- [ ] **Step 2: "### POL-8 구현 기록" 섹션 끝(다음 `##` 직전)에 추가**

```markdown
### POL-9 구현 기록 — 의원 프로필 화면 (2026-07-11)

> spec: `docs/superpowers/specs/2026-07-11-pol9-actor-profile-design.md`,
> plan: `docs/superpowers/plans/2026-07-11-pol9-actor-profile.md`

- **설계**: 새 "의원 프로필" 탭 — POL-2 `/actors/{name}` 소비 + 응답에 `issue_stances`
  필드 확장(actors.py 유일 백엔드 변경, fold_issue_stances 순수부 + aggregate_stances
  재사용으로 매트릭스와 라벨 규칙 일치). 탭 간 이동은 App.jsx 상태 승격
  (selectedActor·selectedIssue) — react-router 기각(데모 단계 과설계).
- **동선**: 쟁점 매트릭스 의원 이름 클릭(이름 셀만, stopPropagation) → 프로필 자동
  조회 / 프로필 이슈 행 클릭 → 쟁점 탭 해당 이슈. 브라우저 왕복 E2E 확인.
- **정직 표기**: 이슈별 입장 테이블에 "입장은 LLM 자동 판정 — 방향 참고용" 주석
  (POL-5 교차검증 67.5% 품질 상속).
- 범위 밖: URL 라우팅, 대시보드 카드 개편, 의원 비교, 인라인 스타일 CSS 이관.
```

- [ ] **Step 3: 3행 최종 업데이트 줄 교체**

```
최종 업데이트: 2026-07-11 (POL-9 의원 프로필 — **3단계 정치분석 전 항목 종료**. 잔여: POL-5·7 사람 기준선(🔶→✅ 열쇠). 다음: 4단계 신뢰 도구)
```

- [ ] **Step 4: 커밋**

```bash
git add docs/progress.md
git commit -m "docs(pol9): 구현 기록 + 3단계 마감 — 로드맵 POL-9 완료"
```

---

## Self-Review

**1. Spec coverage:**
- issue_stances 응답 확장 + 순수부 분리 + 별칭 매칭 → Task 1 ✅ (SQL 스펙 verbatim)
- ActorView 8섹션 + 404 안내 + LLM 판정 주석 → Task 2 ✅
- App 상태 승격 + 탭 + 양방향 콜백 → Task 3 ✅
- 이름 셀만 클릭·stopPropagation → Task 3 Step 2(d) ✅
- selectedIssue prop 반영 (역이동 진입) → Task 3 Step 2(b) ✅
- 테스트 요구 (순수·회귀·스모크·브라우저 6항목) → Task 1·3 ✅
- 문서 → Task 4 ✅

**2. Placeholder scan:** TBD 없음, 모든 코드 스텝 실코드.

**3. Type consistency:**
- `fold_issue_stances` rows 원소 `{"issue_id","title","stance","n"}` — Task 1 테스트·SQL SELECT 별칭 일치 ✅
- 응답 `issue_stances[].{issue_id,title,stance,counts,total_turns}` — Task 2 ActorView 소비(`s.title/s.stance/s.total_turns`) ✅
- `party_history[].{period,label}` — actors.py build_party_history 실반환과 Task 2 렌더 일치 ✅
- `by_month[].{month,turns}` / `by_committee[].{committee,turns}` — actor_profile 실반환과 MonthLine·바 렌더 일치 ✅
- `fetchActor` Task 2 정의 = ActorView import ✅ / `onIssueClick`·`onActorClick`·`actor`·`selectedIssue` prop 이름 Task 2·3 일치 ✅
