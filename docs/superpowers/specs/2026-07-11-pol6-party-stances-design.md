# POL-6 여야 대립 구도 — 설계 (spec)

> 2026-07-11 브레인스토밍 확정. 목표: POL-5 행위자 입장 × POL-0 정당 모듈을 결합해
> 이슈별 **정당 구도**(정당별 입장 분포)를 구조화 API + 프론트 패널로 제공한다.
> 여야는 정권교체(2025-06-04) 구간별 **보조 필드**로 표기한다.

## 전제

- POL-5: `issue_stances`(24이슈 3,270 판정, 🔶 교차검증 67.5%) + `issues.py`
  `issue_stances()`(행위자별 대표 라벨 `aggregate_stances` + party + counts + 근거).
- POL-0: `party.py` — `member_party`(NFKC 매칭, 동명이인 방어), `RULING_PERIODS`
  (2024-05-30~2025-06-03 국민의힘 / 2025-06-04~ 더불어민주당), `SATELLITE_PARENT`
  (여야 판정 전용, 표기는 위성정당 그대로), role 게이트 정규식(의원/정부측/증인/스태프).
- POL-5 입장 판정 품질은 교차검증 수준 — 방향(찬반 진영) 신뢰 가능, 세분류 ±1단계 오차
  (POL-7 기준선). 구도는 방향 중심 집계라 사용 가능 판단.

## 사용자 결정 (2026-07-11)

1. **산출 형태 = 구조화 API + 프론트** — `GET /issues/{id}/party-stances` 신설 +
   IssueView 패널. RAG 답변 주입은 POL-8 로 미룸.
2. **정당 축 기본 + 여야 보조** — 구도는 정당별로 산출(정보 손실 없음), 여야는
   정권교체 구간별 보조 필드(`side_by_period`). 교체 전/후 구도 분리·단일 여야 합산은 기각.
3. **집계 단위 = 의원(행위자)** — `aggregate_stances` 대표 라벨을 정당으로 묶음
   ("민주당 의원 12명: 찬성 7·우려 3·혼재 2"). 발언 카운트는 기존 `/stances` 보조.
4. **정부측 별도 행** — 정당 구도에서 빼되 "정부측" 행으로 표시 (정부 vs 야당 구도
   이슈에서 필수). 증인·참고인·진술인·국회 스태프는 구도에서 **제외**.
5. **무소속·미상 = "무소속/미상" 행** — 버리지 않음.
6. **POL-3 게이트 미달 7개 이슈** (martial-law, lee-jinsook-kcc, ytn-privatization,
   public-broadcasting, small-business, conscription-welfare, itaewon-disaster):
   API 제공하되 `mapping_quality: "low"` 경고 필드. 숨기지 않고 정직 표기.
7. **mixed·no_stance 분포에 포함** — stance_dist 키: support/oppose/concern/mixed/no_stance.

## API

`GET /issues/{issue_id}/party-stances` — 이슈 없거나 판정 없으면 404.

```json
{
  "issue_id": "medical-reform", "title": "의정 갈등·의대 정원",
  "mapping_quality": "ok",
  "periods": [
    {"from": "2024-05-30", "to": "2025-06-03", "ruling": "국민의힘"},
    {"from": "2025-06-04", "to": null, "ruling": "더불어민주당"}
  ],
  "parties": [
    {"party": "더불어민주당",
     "side_by_period": ["야당", "여당"],
     "actor_count": 12,
     "stance_dist": {"support": 7, "oppose": 0, "concern": 3, "mixed": 1, "no_stance": 1},
     "actors": [{"speaker": "김윤", "stance": "support"}]},
    {"party": "정부측", "side_by_period": null, "actor_count": 3, "stance_dist": {},
     "actors": []}
  ]
}
```

- `parties` 정렬: actor_count 내림차순, 동률은 party 명. "정부측"·"무소속/미상"은 맨 뒤.
- `side_by_period`: periods 배열 순서 대응. 의원 정당만, 위성정당은 SATELLITE_PARENT
  로 모정당 기준 판정(표기는 위성정당 그대로). 무소속·정부측·미상은 null.
- `actors`: speaker + 대표 stance 만 (근거·카운트는 기존 `/stances` 로 드릴다운).

## 집계 로직 (순수 함수 — 테스트 대상)

- `speaker_group(role: str | None) -> str` — `"assembly" | "government" | "witness" |
  "staff" | "unknown"`. **party.py 의 기존 role 판정(ASSEMBLY_ROLES·STAFF_ROLES·
  WITNESS_ROLES·_NOMINEE_ROLE·_EXECUTIVE_ROLE)을 재사용 가능한 함수로 분리**
  (현재 `party_label` 내부에 매몰 — 타깃 리팩터). `party_label` 은 이 함수를 소비하도록
  변경하되 판정 결과 불변(기존 test_party 회귀로 보증).
- 행위자 그룹 판정: 이슈 내 해당 행위자 발언들의 **최빈 role** 기준. 동률 우선순위
  assembly > government > witness > staff > unknown (겸직: 정동영 의원 겸 장관 사례).
  후보자 role 은 unknown 그룹(기존 규칙 유지).
- `party_composition(actors: list[dict]) -> list[dict]` — `issue_stances()` 의 actors
  (speaker/party/stance/counts + rows 의 role)를 받아 정당별 행으로 묶음.
  assembly → `member_party` 결과(None 이면 "무소속/미상"), government → "정부측",
  witness·staff → 제외, unknown → "무소속/미상".
- `party_sides(parties: list[str]) -> dict` — RULING_PERIODS 에서 periods 목록 +
  정당별 side_by_period. 순수 함수(날짜표 하드코딩 재사용).
- `LOW_QUALITY_ISSUES` 상수 셋 — issues.py, POL-3 게이트 결과 출처 주석 필수.

## 프론트 (IssueView)

- 기존 입장 매트릭스 **위**에 "여야 구도" 패널 추가.
- 정당별 가로 누적 막대: stance_dist 를 색으로 (기존 stance 색 팔레트 재사용),
  행 왼쪽에 정당명 + 여야 배지("야당→여당" 화살표 표기, null 이면 배지 없음),
  오른쪽에 의원 수.
- 정부측·무소속/미상 행도 표시 (막대 동일, 배지 없음).
- mapping_quality low 이슈는 패널 상단 경고 배너 한 줄.

## 테스트

- 순수 함수: `speaker_group`(의원/정부/증인/스태프/후보자/None), `party_composition`
  (정당 묶기·정부측·미상·증인 제외·겸직 최빈 role·동률 의원 우선),
  `party_sides`(전/후 여야·위성정당 모정당·무소속 null).
- `party_label` 회귀: 기존 test_party 전체 통과 (리팩터 무해 증명).
- API: medical-reform 실데이터 스모크(정부측 행 존재 확인), 404, low-quality 이슈
  `mapping_quality: "low"` 확인.
- 프론트: 브라우저 확인 (패널 렌더·배지·경고 배너).

## 범위 밖 (후속)

- RAG 답변 주입("여야 입장 차이" 질문 근거 블록) — POL-8.
- 정권교체 전/후 입장 변화 시계열 (공수 교대 관찰) — 후속 분석.
- 임기 중 탈당 추적 (party.py 기존 한계 상속).
- 발언 단위 정당 집계 모드.
