# 4단계-A 배포 방어선 — rate limit + 일별 비용 상한 (spec)

> 2026-07-11 브레인스토밍 확정. 목표: 공개 URL 배포(4-B)의 착수 조건인 **비용 공격
> 방어선**을 만든다 (fix_checklist 5순위 "공개 배포 시 비용 공격에 무방비" 해소).
> 월 예산 $0~5 전제 — OpenAI 비용이 유일한 변동비라 이걸 구조적으로 막는다.

## 전제

- 비용 발생 엔드포인트: `/query`·`/answer`(LLM 답변, 질의당 ~$0.001~0.01),
  `/search/vector`·`/search/hybrid`(질문 임베딩 1회, ~$0.00002). 나머지는 DB 조회만.
- CORS 는 이미 환경변수화됨(`BACKEND_CORS_ORIGINS`, main.py 47-53) — 배포 시 값만
  설정하면 됨. 이 스펙의 작업 아님.
- `VITE_API_URL` 도 이미 지원(api.js 3행) — 작업 아님.
- query_logs 에 usage JSON(est_cost_usd)이 질의마다 기록됨(/query 경로만).
- main.py 에 기존 http 미들웨어(요청 ID 로깅) 존재 — 공존해야 함.

## 사용자 결정 (2026-07-11)

1. **4단계 분해**: 4-A 방어선(이 스펙, 로컬 완결) → 4-B 배포 실행(별도 스펙, 계정
   작업 필요). 목표 = 공개 URL 포트폴리오, 월 $0~5.
2. 방어선 구성: **IP당 rate limit + 일별 OpenAI 비용 상한 + CORS 한정(기존)**.

## 설계 — 신규 모듈 `backend/guard.py`

의존성 추가 없이 자체 구현 (slowapi 기각 — 단순한 것을 설명 가능하게).

### RateLimiter (순수 클래스 — 테스트 대상)

```python
class RateLimiter:
    def __init__(self, per_min: int): ...
    def allow(self, key: str, now: float) -> bool: ...
```

- 슬라이딩 윈도우 60초: `dict[key] -> deque[timestamp]`. allow 호출 시 60초 지난
  항목 제거 → 남은 수 < per_min 이면 기록 후 True, 아니면 False.
- `now` 를 인자로 받아 순수하게 테스트 (기본값 없음 — 호출측이 time.time() 주입).
- 메모리 관리: 호출 시 해당 key 만 정리 (전역 GC 불필요 — IP 수천 개 수준 무해.
  단 deque 가 빈 key 는 dict 에서 제거).
- 프로세스 로컬(단일 인스턴스 전제 — Render free 1대. 다중 인스턴스는 범위 밖).

### 일별 비용 상한 (`daily_cost_exceeded`)

```python
def daily_cost_today() -> float: ...          # query_logs 오늘(UTC) est_cost_usd 합
def daily_cost_exceeded(limit: float) -> bool  # 60초 캐시로 DB 부하 방지
```

- SQL: `SELECT COALESCE(SUM((usage->>'est_cost_usd')::float), 0) FROM query_logs
  WHERE created_at >= date_trunc('day', now())` (usage NULL 행 제외 — COALESCE).
- 결과를 모듈 캐시 (값, 시각) 에 60초 보관 — 초과 판정 후에도 매 요청 DB 를 때리지
  않게. 한도의 ±60초 오버슛은 허용 오차(최대 분당 한도 × $0.01 수준).
- **알려진 한계(문서화)**: `/answer` 는 query_logs 를 안 남겨 비용 집계에서 빠짐.
  배포 프론트는 `/query` 만 사용하므로 실위험은 rate limit 이 흡수. 정밀 집계는
  범위 밖.

### 클라이언트 키 (`client_ip`)

- `X-Forwarded-For` 첫 값(프록시 뒤 실 IP — Render/Vercel 프록시 전제) →
  없으면 `request.client.host`. 스푸핑 한계는 알려진 트레이드오프로 문서화
  (직접 노출 아닌 플랫폼 프록시 뒤라 XFF 첫 값 신뢰 — 완벽 방어가 아니라 비용
  사고 방지가 목적).

## main.py 배선

- 기존 요청 ID 미들웨어에 **선행하는 별도 http 미들웨어** 추가 (guard 먼저):
  - 경로 분류: `LLM_PATHS = ("/query", "/answer")`, `EMBED_PATHS =
    ("/search/vector", "/search/hybrid")`, 나머지 = 일반.
  - 한도 (환경변수, 기본값): LLM `RATE_LIMIT_LLM_PER_MIN=5` / 임베딩·일반 공용
    `RATE_LIMIT_PER_MIN=60`. `0` 이면 해당 리미터 끔 (로컬 개발 기본은 켜짐 —
    한도가 넉넉해 개발에 지장 없음).
  - LLM 경로는 rate limit 통과 후 `daily_cost_exceeded(DAILY_COST_LIMIT_USD)`
    (기본 1.0, `0` 이면 끔) 검사.
  - 초과 응답: rate limit → **429** `{"detail": "요청이 너무 잦습니다. 1분 뒤 다시
    시도해주세요."}` / 비용 상한 → **429** `{"detail": "오늘의 무료 사용량이 모두
    소진되었습니다. 내일 다시 이용해주세요."}` (한국어 고정 — 프론트가 detail 을
    그대로 표시하는 기존 규약 재사용).
  - `/health` 는 항상 통과 (플랫폼 헬스체크).
- CORS·요청 ID 미들웨어는 무변경.

## .env.example 갱신

```
RATE_LIMIT_LLM_PER_MIN=5
RATE_LIMIT_PER_MIN=60
DAILY_COST_LIMIT_USD=1.0
```

(BACKEND_CORS_ORIGINS 는 기존 항목 유지.)

## 테스트

- 순수: `RateLimiter` — 한도 내 허용 / 초과 거부 / 61초 후 윈도우 회복 / 키 분리
  (다른 IP 독립) / per_min=0 처리(호출측에서 끔 — allow 호출 안 함).
- 순수: `client_ip` — XFF 첫 값 / XFF 없음 fallback / XFF 다중값 파싱.
- 비용 캐시: `daily_cost_exceeded` 60초 캐시 동작 (모킹 — DB 값 바꿔도 캐시 내
  동일 판정, 캐시 만료 후 갱신).
- TestClient: LLM 경로 연속 호출 → 6번째 429 + detail 한국어 확인 / 일반 경로는
  LLM 한도와 독립 / `/health` 무제한 / 비용 상한 모킹 초과 시 429.
- 기존 pytest 전체 회귀 (리미터 기본값이 기존 테스트를 깨지 않는지 — TestClient
  테스트가 많으면 테스트 환경에서 한도 상향 필요할 수 있음: conftest 나 테스트 내
  환경변수로 `RATE_LIMIT_PER_MIN=0` 설정하는 방식을 계획에서 확정).

## 정직한 처리 (문서화)

- 인메모리 리미터 = 단일 인스턴스 전제, 재시작 시 리셋. 포트폴리오 규모에 적정.
- XFF 스푸핑·분산 공격은 완전 방어 불가 — 최종 방어선은 일별 비용 상한.
- `/answer` 비용 미집계 한계 (위 참조).

## 범위 밖 (후속 = 4-B 배포 실행 스펙)

축소 코퍼스 생성·마이그레이션, Vercel/Render/Supabase 프로비저닝, 콜드스타트 안내
문구, HTTPS(플랫폼), grounding LLM 판정자 고도화, 모니터링·알림.
