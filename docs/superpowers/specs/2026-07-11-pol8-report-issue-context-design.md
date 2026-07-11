# POL-8 분석 통합 — report 브리핑 이슈 분석 주입 (spec)

> 2026-07-11 브레인스토밍 확정. 목표: report 모드 브리핑에 이슈 분석 데이터
> (구도 POL-6 · 타임라인 POL-4 · 주요 행위자 POL-5)를 주입해 "이슈 X의 여야 입장
> 차이" 류 질문에 근거 있는 구도 응답을 만든다 (로드맵 POL-8 완료 기준).

## 전제

- report 모드: `backend/answer.py` `generate_answer` — 근거 블록(build_source_block)
  조립 후 `build_user_message` 로 LLM user 메시지 구성. 근거 블록은 프롬프트 주입
  방어용 명시 경계(===== 근거 블록 =====)로 감싸져 있음.
- 이슈 데이터: `issues` 테이블 `seed` JSONB 에 seed_keywords. `issue_party_stances`
  (POL-6 구도), `issue_timeline`(POL-4, mapped_core_turns 정밀 축), `issue_stances`
  테이블(행위자 발언 수).
- 입장 판정 품질 = POL-5 교차검증 67.5% (방향 신뢰·세분류 ±1단계) — 구도 요약은
  방향 중심 서술 지시.

## 사용자 결정 (2026-07-11)

1. **이슈 감지 = 시드 키워드 매칭** — 질문에 seed_keywords 부분일치, 매칭 키워드 수
   최다 이슈 1개. **동률이면 주입 생략**(모호). LLM 분류·명시 파라미터는 기각(비용/범위).
2. **report 모드만** 주입. qa 는 무변경.
3. **주입 내용 3종 (컴팩트 텍스트)**: 구도(정당별 한 줄 + 정부측), 타임라인 피크
   (mapped_core_turns 상위 3개 월), 주요 행위자(발언 수 상위 5명 + 대표 입장).
4. **별도 경계 블록** `===== 이슈 분석 데이터 =====` — 근거 블록과 분리.
   LLM 지시: 개요·쟁점별 정리에 활용하되 "코퍼스 분석 기준"으로 표기, 발언 인용은
   여전히 [n]만. 분석 수치는 DB 결정적 계산이라 인용 번호 없음.
5. **응답 필드 `issue_context`**: `{"issue_id", "title"} | null` — 프론트 배지용.
   query_logs 는 기존 source_block 경로에 분석 블록 포함(별도 컬럼 없음).
6. `mapping_quality: low` 이슈면 분석 블록에 경고 한 줄 포함.
7. "분석 API 정리"(로드맵 문구)는 **범위 밖** — 이미 issues.py 응집.

## 구조 — 신규 모듈 `backend/issue_context.py`

answer.py(456줄)·issues.py 비대화 방지 + 책임 분리(감지·조립은 브리핑 전용).

- `detect_issue(question: str, index: list[dict]) -> dict | None` — **순수**.
  index 원소 `{"issue_id","title","seed_keywords"}`. 질문에 `in` 부분일치하는
  키워드 수를 세어 최다 이슈 반환. 0개 또는 최다 동률이면 None.
- `load_issue_index() -> list[dict]` — `SELECT issue_id, title, seed FROM issues`
  1회 조회 후 **모듈 캐시** (party.py `_load_map` 패턴). seed_keywords 없으면 [].
- `top_actors(issue_id: str, limit: int = 5) -> list[dict]` — issue_stances 에서
  `SELECT speaker, count(*) ... GROUP BY speaker ORDER BY count DESC, speaker LIMIT 5`.
- `build_issue_block(party_data: dict, timeline: dict, actors: list[dict]) -> str`
  — **순수** 조립. 형식:

```
[이슈: 의정 갈등·의대 정원]
(코퍼스 분석 기준 — 아래 수치는 회의록 자동 분석 결과다. 개요·쟁점별 정리에
 활용하되 "코퍼스 분석 기준"으로 표기하고, 발언 인용 근거는 [n] 본문만 쓴다.
 입장 세분류(찬성/우려 경계)는 오차가 있으니 방향(찬반) 중심으로 서술한다.)
⚠ 이 이슈의 자동 매핑 정밀도는 기준 미달 — 수치 해석 주의   ← low 일 때만
- 구도: 더불어민주당 12명(찬7·반0·우3·혼1·무1) [야당→여당] / 국민의힘 …
  / 정부측 4명(…) / 무소속·미상 …
- 발언 피크: 2024-06(31턴), 2024-07(28턴), 2025-07(19턴)
- 주요 행위자: 김윤(9턴, 찬성), 박민수(정부측, 8턴, …) …
```

  - 구도 줄: party_composition 행 순서 그대로, stance_dist 를 `찬N·반N·우N·혼N·무N`
    축약, side_by_period 는 `[야당→여당]`(같으면 하나). actors 대표 입장은
    party_data 의 actors 에서 조회(발언 수는 top_actors 결과).
  - 피크: mapped_core_turns 상위 3개 월 내림차순, 전부 0이면 corpus_turns 로 대체,
    그것도 없으면 줄 생략.
- `issue_context_for(question: str) -> tuple[str, dict] | None` — 배선 래퍼:
  detect → None 이면 None. 감지되면 `issue_party_stances`·`issue_timeline`·
  `top_actors` 조회 → `build_issue_block` → `(block, {"issue_id","title"})`.
  party_stances 가 None(판정 없는 이슈)이면 None (주입 생략 — 구도 없는 분석
  블록은 반쪽).

## answer.py 배선 (최소 변경)

- `build_user_message(question, block, issue_block: str = "")` — issue_block 이
  있으면 근거 블록 **앞**에 별도 경계로 삽입:

```
===== 이슈 분석 데이터 시작 =====
{issue_block}
===== 이슈 분석 데이터 끝 =====
```

- `generate_answer`: `cfg` 로드 후 `mode == "report"` 이면
  `issue_context_for(question)` 호출(예외는 잡아서 주입 생략 — 분석 실패가
  브리핑 실패로 번지지 않게, WARN 로그). 반환 dict 에 `"issue_context"` 필드
  (감지 없으면 None). qa 경로는 호출 자체를 안 함.
- `source_block` 저장값(query_logs)은 기존 그대로 근거 블록만 — 분석 블록은
  응답 `issue_context` 로 추적 가능(정확 재현 필요하면 결정적 재계산 가능).

## 프론트 (한 줄 배지)

- `AnswerPanel.jsx`: 응답에 `issue_context` 가 있으면 답변 상단에
  `📊 이슈 분석 반영: {title}` 작은 배지 한 줄. 없으면 무표시.

## 테스트

- 순수: `detect_issue`(단일 매칭·최다 우선·동률 None·무매칭 None·빈 키워드 무시),
  `build_issue_block`(구도 줄 형식·피크 정렬·corpus 대체·low 경고 유무·행위자 줄).
- 배선: 실DB 스모크 — `issue_context_for("의대 정원 증원 논의 정리해줘")` 가
  medical-reform 블록 반환, 비이슈 질문("국정감사 일정")은 None.
- E2E 스팟체크(LLM 비용 발생): report 모드 이슈 질문 1건 실행 — `issue_context`
  필드 확인 + 브리핑에 "코퍼스 분석 기준" 서술이 녹았는지 육안 확인. qa 모드
  동일 질문 → `issue_context: null` 확인.
- 회귀: 기존 pytest 전체(76) + 비이슈 report 질문 1건이 기존과 동일 구조인지 확인.

## 정직한 처리 (문서화)

- 분석 수치는 POL-5 판정 품질(교차검증 67.5%)을 상속 — 블록 지시문에 "방향 중심
  서술" 명시로 완화. 사람 기준선 확보 시 재평가.
- 키워드 감지는 보수적 — 시드 키워드 밖 표현("의사 파업 브리핑")은 놓치고 일반
  브리핑으로 동작(오탐 없음 우선). 재현율 개선은 후속.

## 범위 밖 (후속)

LLM 이슈 분류(재현율), qa 모드 주입, 다중 이슈 동시 주입, 분석 블록의 query_logs
별도 컬럼, 답변 eval 전체 재실행(스팟체크로 대체), 분석 API 문서 정리.
