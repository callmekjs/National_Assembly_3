# RAG-7 스펙 — `/query` 통합 (검색 + 답변 + Grounding 판정 + 로그)

> 2026-07-03 설계 확정. 목표: **curl 한 번에 답변+출처+신뢰등급** (2단계 완료 기준).
> RAG-6의 answer 모듈을 감싸서 Grounding 판정과 query_logs 저장을 붙인다.

## Grounding 판정 — `backend/grounding.py` (신규, 순수 함수)

판정 방식: **규칙 기반 + 유사도 사전차단** (LLM 판정자는 4단계 고도화 때 재검토).

**threshold는 하드코딩 금지 — .env 설정값으로:**
- `.env`와 `.env.example`에 `GROUNDING_SIM_THRESHOLD=0.4` 추가
- 코드에서 `os.environ.get("GROUNDING_SIM_THRESHOLD", "0.4")`로 읽고 float 변환
- 근거: 무작위 유사도 기준선 0.386 실측 → 0.4는 경험적 초기값이라 조정 가능해야 함

**LLM 호출 전 (사전차단, 비용 0):**
1. 검색 결과 0건 → **NONE**
2. 벡터 최고 유사도 < threshold **AND** 키워드 매치 0건 → **REFUSED** (LLM 호출 생략)
   - 키워드 매치가 있으면 차단하지 않는다 — 고유명사 질문은 벡터 유사도가
     낮아도 정답일 수 있음 (ETL-8 실측)

**LLM 답변 후 (RAG-6 응답의 신호로 판정):**

| cited_numbers | 거절 문구 포함 | 판정 |
|---|---|---|
| 있음 | 없음 | FULL |
| 있음 | 있음 | PARTIAL |
| 없음 | 있음 | REFUSED |
| 없음 | 없음 (무인용 주장 = 프롬프트 위반) | PARTIAL + ungrounded 경고 플래그 |

- 거절 문구 감지는 exact match 금지 — **"확인할 수 없" 부분 문자열 매칭**
  (RAG-6 스모크 실측: LLM이 어순을 바꿔 거절함)
- `invalid_citations` 비어있지 않으면 FULL이어도 PARTIAL로 강등

## 전제: answer.py 프롬프트 1줄 보강

RAG-6 스모크 관찰 ②(대상 없는 꼬리 문장 "이 부분은 … 확인할 수 없습니다")를 막지 않으면
멀쩡한 답변이 대부분 PARTIAL로 강등된다. 공통 규칙에 추가:
"질문이 요구한 내용을 근거로 모두 답할 수 있으면 확인 불가 문구를 덧붙이지 않는다."
보강 후 기존 스모크 질문 몇 개로 꼬리 문장이 사라졌는지 재확인.

## 이중 검색 방지 (선조회 주입)

사전차단은 LLM 호출 전에 검색 결과가 필요한데 `generate_answer`도 내부에서 검색한다.
- `generate_answer(question, mode, ..., hits=None)` 로 시그니처 확장 —
  hits가 주어지면 내부 검색을 건너뛰고 그대로 사용
- `search_hybrid.py`는 벡터 축 원점수(유사도)를 `vec_score` 필드로 결과에 보존
  (기존 응답 필드는 유지, 추가만 — API 호환)

## query_logs — `db/schema.sql`에 추가 + 실제 DB에 CREATE

```sql
CREATE TABLE query_logs (
  query_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question   TEXT NOT NULL,
  mode       TEXT NOT NULL,
  committee  TEXT, date_from DATE, date_to DATE,
  answer     TEXT NOT NULL,
  grounding  TEXT NOT NULL,           -- FULL/PARTIAL/REFUSED/NONE
  citations  JSONB NOT NULL DEFAULT '[]',
  invalid_citations JSONB NOT NULL DEFAULT '[]',
  usage      JSONB,                    -- 토큰·비용 (사전차단 시 NULL)
  latency_ms INT,
  created_at TIMESTAMPTZ DEFAULT now(),
  rating INT, feedback_comment TEXT, feedback_at TIMESTAMPTZ
);
```

- 로그 저장 실패해도 답변은 정상 반환 (try/except 격리 — 로그는 부가 기능)
- `/feedback` 은 query_id 로 해당 행 UPDATE (rating, feedback_comment, feedback_at)
  - 존재하지 않는 query_id 면 404

## /query 엔드포인트 (main.py 스텁 교체)

- 요청: `{question, mode: "qa"|"report" (기본 qa), committee?, date_from?, date_to?}`
  - 기존 스텁의 speaker 필드는 제거 (하이브리드 검색이 지원하지 않음)
- 흐름: hybrid_search 1회 → 사전차단 판정 → 통과 시 generate_answer(hits 주입)
  → 사후 판정 → query_logs 저장 → 응답
- 응답: `{query_id, answer, grounding, mode, citations, sources, cited_numbers, invalid_citations, usage, latency_ms}`
  - 무인용 주장 케이스는 응답에 `ungrounded: true` 필드 추가 (프론트 경고 표시용, 평소엔 생략)
  - 사전차단(NONE/REFUSED)이어도 query_id 발급 + 로그 저장 (answer는 고정 문구)
- `/answer` 는 디버그용으로 유지 (grounding·로그 없는 원시 호출)

## 테스트 (`tests/test_grounding.py` — LLM·DB 없는 순수 로직)

- 검색 0건 → NONE
- 벡터 <threshold + 키워드 0건 → 사전차단 REFUSED
- 벡터 <threshold 이지만 키워드 매치 있음 → 차단 안 함
- 인용 있음+거절 문구 없음 → FULL
- 인용 있음+거절 문구 있음 → PARTIAL
- 인용 없음+거절 문구 있음 → REFUSED (어순 변형 문구 포함)
- 인용 없음+거절 문구 없음 → PARTIAL + ungrounded 플래그
- invalid_citations 있으면 PARTIAL 강등
- threshold 를 env 로 바꾸면 판정이 바뀌는지 (설정값 동작 확인)

## 스모크 검증 (구현 후)

- eval 셋 unanswerable 4문항 → REFUSED(또는 NONE) 4/4 기대
- 정상 6문항(유형 다양하게) → FULL 위주 기대, 꼬리 문장 제거 확인
- query_logs 행 적재 확인 (grounding 분포 출력)
- /feedback 호출 → rating 반영 확인
- 완료 기준: curl 한 번에 답변+출처+신뢰등급

## 주의

- 기존 코드 스타일 유지 (모듈 상단 docstring에 설계 근거).
- LangChain 금지, 큰 의존성 추가 금지.
- 서버: `cd backend && python -m uvicorn main:app --port 8000` (--reload 금지),
  호출은 127.0.0.1. DB는 도커 컨테이너 `national-assembly-db` (꺼져 있으면 시작).
- 완료 후 docs/progress.md에 RAG-7 구현 기록 + 2단계 완료 기준 체크 갱신.
