# POL-9 의원 프로필 화면 + 분석 뷰 연결 — 설계 (spec)

> 2026-07-11 브레인스토밍 확정. 목표: POL-2 행위자 API 를 소비하는 **의원 프로필 탭**을
> 신설하고, 쟁점 분석(IssueView)과 양방향으로 연결해 3단계 데모 시연 동선을 완성한다
> (로드맵 POL-9 완료 기준: 브라우저 데모 시연).

## 전제

- POL-2 `GET /actors/{name}` (backend/actors.py `actor_profile`): name, display_name,
  party, party_history, totals(turns/meetings/first/last), by_committee, by_month,
  utterance_types(question/statement 비율), top_mentions(10), recent_utterances(5).
  별칭 확장(expand_aliases)으로 한자명 매칭.
- IssueView: 이슈 드롭다운 + 타임라인 차트 + 여야 구도 패널(POL-6) + 행위자 입장
  매트릭스(행 클릭 → 근거 펼침). App.jsx 탭: query / issues (11행 tab state).
- issue_stances: (issue_id, turn_id, speaker, role, stance) — 의원의 이슈별 입장
  역조회 재료. `aggregate_stances` 로 이슈별 대표 라벨.

## 사용자 결정 (2026-07-11)

1. **범위 = 프로필 + 매트릭스 연결 + 이슈별 입장** — ①새 "의원 프로필" 탭(이름 검색)
   ②IssueView 매트릭스 의원 클릭 → 프로필 이동 ③프로필에 이슈별 입장 섹션
   ④이슈 행 클릭 → 쟁점 분석 탭 해당 이슈로 역이동. 대시보드 개편은 범위 밖.
2. **이슈별 입장은 기존 응답 확장** — 별도 라우트 대신 `actor_profile` 응답에
   `issue_stances` 필드 추가 (프론트 호출 1번). react-router 도입·모달 프로필 기각.
3. **탭 전환 상태는 App 레벨 승격** — `selectedActor`(프로필 대상)·`selectedIssue`
   (쟁점 탭 초기 이슈)를 App.jsx 가 소유, 뷰들이 콜백으로 요청.

## 백엔드 — actors.py 확장 (유일한 백엔드 변경)

- `actor_issue_stances(variants: list[str]) -> list[dict]` — issue_stances 를 별칭
  목록으로 조회, 이슈별로 묶어 대표 라벨 + 카운트:

```sql
SELECT s.issue_id, i.title, s.stance, count(*) AS n
FROM issue_stances s JOIN issues i USING (issue_id)
WHERE s.speaker = ANY(%s)
GROUP BY s.issue_id, i.title, s.stance
ORDER BY s.issue_id
```

  파이썬에서 이슈별로 접어 `[{"issue_id", "title", "stance"(aggregate_stances 재사용),
  "counts": {support..none}, "total_turns"}]`, total_turns 내림차순 정렬.
  판정 없는 의원이면 `[]`.
- `actor_profile` 반환 dict 에 `"issue_stances": actor_issue_stances(variants)` 추가.
  기존 필드·시그니처 불변 (기존 test_actors 회귀로 보증).
- 집계 순수부(행 목록 → 이슈별 접기)는 DB 무관 함수로 분리해 단위 테스트:
  `fold_issue_stances(rows: list[dict]) -> list[dict]` — rows 원소
  `{"issue_id","title","stance","n"}`.

## 프론트

### App.jsx (상태 승격)

- `const [selectedActor, setSelectedActor] = useState(null)` /
  `const [selectedIssue, setSelectedIssue] = useState(null)`
- 탭 버튼 추가: `의원 프로필` (tab === 'actor').
- `openActor(name)`: setSelectedActor(name) + setTab('actor').
  `openIssue(issueId)`: setSelectedIssue(issueId) + setTab('issues').
- `<IssueView selectedIssue={selectedIssue} onActorClick={openActor} />`,
  `<ActorView actor={selectedActor} onIssueClick={openIssue} />`.

### ActorView.jsx (신규)

- 이름 검색 입력(엔터/버튼) + `fetchActor(name)` (`api.js` 신규, `/actors/{name}`,
  encodeURIComponent). 404 는 "발언 기록 없음" 안내 (페이지 에러 아님).
- `actor` prop 이 바뀌면 자동 조회 (매트릭스 클릭 진입).
- 섹션 구성 (위에서 아래):
  1. 헤더 — display_name + 정당 배지(party, 없으면 무표기) + 당적 이력(party_history)
  2. 통계 줄 — 발언 N턴 · 회의 N회 · 활동 기간 first~last
  3. 위원회 분포 — by_committee 가로 바 (턴 수 비례, 상위 전부)
  4. 월별 추이 — by_month 라인 (IssueView TimelineChart 패턴 축소 재사용, 단일 선)
  5. **이슈별 입장** — issue_stances 테이블: 이슈명 | 대표 입장(STANCE_KO 색) |
     발언 수. **행 클릭 → onIssueClick(issue_id)** (쟁점 탭 역이동).
     비어 있으면 "판정된 이슈 없음".
  6. 발언 유형 — question/statement 비율 한 줄
  7. 주요 언급 — top_mentions 태그 나열
  8. 최근 발언 — recent_utterances 스니펫 5건
- 스타일: 기존 인라인 스타일 관례 유지 (별도 CSS 파일 신설 금지).

### IssueView.jsx (연결)

- `StanceRow` 의 발언자 셀 클릭 → `onActorClick(actor.speaker)` (기존 행 클릭의
  근거 펼침과 충돌하지 않게 **이름 셀만** 별도 클릭, stopPropagation).
- `selectedIssue` prop 이 오면 드롭다운 초기값으로 반영 (역이동 진입).
  prop 변경 시에도 반영 (같은 탭 재진입).

## 테스트

- 백엔드 순수: `fold_issue_stances` — 이슈별 접기·대표 라벨(aggregate_stances 위임)·
  counts 5키·total_turns 정렬·빈 입력 [].
- 백엔드 스모크(실DB): 김윤(다수 판정 의원) → issue_stances 비어있지 않음 + 기존
  필드 불변 확인, 판정 없는 인물 → `issue_stances: []`, 미존재 인물 404.
- 기존 test_actors 전체 회귀 (응답 확장이 기존 필드를 깨지 않음).
- 프론트 브라우저: ①프로필 탭에서 이름 검색 → 8개 섹션 렌더 ②쟁점 매트릭스 의원
  클릭 → 프로필 이동 ③프로필 이슈 행 클릭 → 쟁점 탭 해당 이슈 ④404 인물 안내
  ⑤콘솔 에러 0 ⑥기존 질의·쟁점 탭 회귀 없음.

## 정직한 처리 (문서화)

- 이슈별 입장은 POL-5 판정 품질(교차검증 67.5%) 상속 — 프로필 이슈 테이블 하단에
  작은 주석 한 줄("입장은 LLM 자동 판정 — 방향 참고용").
- 당적 이력은 최종 스냅샷 한계(party.py) 상속.

## 범위 밖 (후속)

react-router URL 라우팅, 대시보드 카드 그리드 개편, 프로필 공유 링크, 의원 비교
화면, 인라인 스타일 → CSS 클래스 이관(POL-8 배지 포함 일괄).
