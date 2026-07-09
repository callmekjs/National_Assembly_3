# POL-5 입장(stance) 분석 — 설계 (spec)

> 2026-07-09 브레인스토밍 확정. 목표: 이슈별 **행위자 입장 매트릭스**를 LLM 판정으로 만들고,
> 최소 프론트 뷰에서 타임라인(POL-4)과 함께 브라우저로 직접 확인한다. 파일럿 1개 이슈
> (medical-reform) = 로드맵 POL-5 완료 기준("이슈 1개의 행위자별 입장+근거 매트릭스").

## 전제 (기존 실측·결정)

- **입장 판정은 LLM 필수** — POL-1 판정: `stance_signals`(규칙 기반)는 neutral 97.9%로 사용 불가
  (한국어 활용형 미매치). 규칙 기반 재시도 안 함.
- **소비 대상은 core 만** — POL-3 지침: POL-5/6은 `issue_chunks.judge='llm_core'` 만 소비(순도).
- **집계는 turn 단위** — chunk 분할이 한 발언의 입장을 가르는 것 방지(actors.py·POL-4 교훈).
- **신뢰 원칙** — 모든 입장에 근거 발언 인용 필수. 판정 불가는 누락(none)으로, 오염보다 우선.

## 사용자 결정 (2026-07-09)

1. **파일럿 이슈 = medical-reform**(의정 갈등·의대 정원). 의대증원 찬반이 선명해 5택 체계 검증에 최적.
   core 212 turn / 37 speaker. martial-law 등 규탄·절차 편중 이슈는 파일럿 부적합.
2. **판정 단위 = 발언별(turn) → 행위자 집계.** 발언별 판정이 근거 인용이 자연스럽고 POL-7 라벨도
   객관적(POL-3 방식 일관). 행위자별 통합 판정(상충 종합·근거 정밀도·게이트 난이도)은 기각.
3. **입장 5택**: support(찬성) / oppose(반대) / concern(우려) / neutral(중립) / none(입장없음).
   - concern 별도: "찬성하나 속도·부작용 우려" 류를 반대로 뭉개면 왜곡. 조건부 입장 보존.
   - neutral(다루되 입장 유보: 사실 질의·중계) vs none(판정 불가: 순수 절차·인사·타주제 경유) 구분 —
     core는 "실질 논의"지 "입장 표명"이 아니므로 입장 없는 core 발언이 존재.
4. **행위자 집계 규칙**: 발언 레벨은 5택 영문 토큰(support/oppose/concern/neutral/none),
   **행위자 레벨 입장 어휘**는 별도로 `support | oppose | concern | mixed | no_stance` 5종.
   규칙: 입장 발언(support/oppose/concern)만 방향 카운트. 0개면 `no_stance`.
   그 외 최다 카운트가 대표(concern도 대표 가능). support·oppose 둘 다 있고 서로 비슷하면
   (각각 입장발언의 ⅓ 이상) `mixed` + 양쪽 근거. **단일 라벨은 편의, 진실은
   카운트+근거** — 매트릭스는 입장별 카운트와 근거 인용을 항상 노출.
5. **여야(alignment)는 POL-5에 미포함** — POL-6(여야 구도)의 몫. 입장 발언이 정권교체
   (2025-06-04)를 걸치면 여야 갈리는 시점 모호성 있어 POL-6에서 처리. POL-5는 party 까지.
6. **시간 변화 미모델링(파일럿 YAGNI)** — 단일 라벨은 시계열 입장 진화를 안 담음. 근거 인용의
   날짜로 눈으로는 보임. 시계열 입장은 POL-4 결합 여지로 남김.
7. **프론트 뷰 포함** — POL-9 축소판(이슈 상세 화면)을 앞당겨, 브라우저에서 타임라인+입장 확인.

## 판정 (scripts/build_issue_stance.py)

build_issue_map.py 패턴. medical-reform 의 core turn(issue_chunks.judge='llm_core' 의
DISTINCT turn_id)을 대상으로:

1. **대상 수집**: core turn_id 목록 + 각 turn 발언 전문. 전문은 answer.py 의 turn 복원 방식
   (`_fetch_texts`/`_assemble_turn`) 또는 build_issue_map 의 텍스트 조회를 turn 단위로 집계해 사용.
2. **배치 LLM 판정**: gpt-4o-mini(이슈별 judge_model 존중), temperature=0, JSON 출력.
   프롬프트 = 이슈 title+description(기준) + 발언 전문(발췌) → 5택 1개. 배치 20.
   "발언자가 이슈의 정책·조치에 대해 취하는 태도"를 판정하되, 사실 질의·중계는 neutral,
   절차·인사·타주제 경유는 none 으로 명시.
3. **저장**: `issue_stances` 에 turn 단위 upsert. 멱등(재실행 시 재판정·덮어쓰기).
   행수 검증(대상 == 판정+보류), 형식 위반 배치는 재시도 1회 후 보류(누락 우선).

## 저장 (db/schema.sql)

```sql
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

## API (GET /issues/{id}/stances)

```json
{
  "issue_id": "medical-reform",
  "title": "의정 갈등·의대 정원",
  "actors": [
    {"speaker": "이주영", "party": "개혁신당", "stance": "concern",
     "counts": {"support":0,"oppose":0,"concern":2,"neutral":1,"none":0},
     "citations": [
       {"turn_id": "...", "stance": "concern", "date": "2024-06-13",
        "chunk_id": "...", "snippet": "..."}
     ]}
  ]
}
```

- `backend/issues.py` 에 `issue_stances(issue_id) -> dict | None`(집계 4번 규칙) 추가,
  main.py 얇은 라우트 + 404(이슈 없거나 판정 데이터 없으면). party 는 POL-0 `party.member_party`.
- 행위자 정렬 = 입장 발언 수 많은 순.
- citations: 각 행위자의 대표 입장을 뒷받침하는 turn(혼재면 양쪽), 대표 chunk_id + 날짜 + 발췌.

## 게이트 (scripts/issue_stance_spotcheck.py)

- 무작위 N개(seed 고정) 판정을 발언 원문과 대조 판독용 리포트로. **정직한 한계**: 입장은
  5택·주관적이라 POL-3 관련도(2택) 의 ≥90% 하드 게이트는 비현실적. **일치도(agreement)
  기준선을 측정·기록**하고, 이 스팟체크가 **POL-7 라벨 세트(한 이슈분 30~50건)의 출발점**.
- 파일럿에선 "판정이 말이 되는지" 확인 수준. 하드 게이트/임계값은 실제 숫자를 보고 POL-7 에서 확정.

## 프론트 뷰 (frontend/ — POL-9 축소판)

- `api.js`: `fetchTimeline(id)`, `fetchStances(id)` 추가.
- 새 컴포넌트 `IssueView`: 이슈 선택 드롭다운(24개, 기본 medical-reform) + 두 렌더:
  1. **타임라인 차트** — corpus/core 2선. 외부 차트 라이브러리 없이 경량 인라인 SVG
     (프로젝트 의존성 최소 원칙).
  2. **입장 매트릭스 표** — 행위자 | 정당 | 대표 입장(색상) | 입장별 카운트 | 근거(펼침 인용).
- 기존 RAG 질의 화면과 탭/토글로 분리. 데이터 없으면 안내 문구.

## 테스트

- 순수 로직(DB·LLM 없이): 입장 응답 파싱(정상·부분·형식위반), 행위자 집계 규칙
  (무입장·단일 대표·혼재 경계·concern 대표), 카운트 합산.
- API 스모크: medical-reform curl → actors 배열·counts 합 정합, 없는 이슈 404.
- 프론트: api.js 스모크(기존 vitest 관례), IssueView 렌더 스모크.

## 범위 밖 (파일럿 이후)

전체 24이슈 입장 판정, 하드 게이트·임계값, POL-7 정식 라벨 세트, 여야 구도(POL-6),
시계열 입장 진화, 대시보드 전체(POL-9).

## 알려진 한계 (문서화)

- **입장 판정은 5택·주관적** — 경계(concern vs oppose, neutral vs none)가 모호. 하드 게이트 대신
  일치도 기준선 + POL-7 라벨로 관리. 단일 라벨보다 카운트+근거가 진실.
- **core 상한 상속** — POL-3 매핑이 분기 층화·정밀도 상한이라 입장 표본도 그 부분집합. 절대 분포 아님.
- **party 스냅샷** — 최종 당적(탈당 미추적), 여야 시점성은 POL-6.
