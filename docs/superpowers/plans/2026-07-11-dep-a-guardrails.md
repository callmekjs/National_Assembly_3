# 4단계-A 배포 방어선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 공개 배포의 착수 조건인 비용 방어선 — IP당 rate limit(1차) + 일별 OpenAI 비용 상한(2차)을 의존성 추가 없이 구현한다.

**Architecture:** 신규 `backend/guard.py` 에 순수 로직(RateLimiter 슬라이딩 윈도우, client_ip, 비용 캐시 — 전부 시각/조회 주입으로 DB·시계 없이 테스트)을 만들고, main.py 에 http 미들웨어 하나로 배선. 테스트 환경은 신규 `tests/conftest.py` 가 한도를 꺼서 기존 스위트를 보호하고, 429 경로는 리미터 객체 교체(monkeypatch)로 검증.

**Tech Stack:** FastAPI 미들웨어(JSONResponse), stdlib only (collections.deque), pytest.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md` — 아래 값 verbatim.
- **의존성 추가 금지** (slowapi 등 기각 — stdlib 만).
- 환경변수·기본값: `RATE_LIMIT_LLM_PER_MIN=5` / `RATE_LIMIT_PER_MIN=60` / `DAILY_COST_LIMIT_USD=1.0`, 각각 `0`이면 해당 방어 끔.
- LLM 경로 = `/query`, `/answer` (LLM 리미터 + 비용 상한). 그 외 경로 = 일반 리미터. **`/health` 는 항상 통과**.
- 429 detail 문구 verbatim: rate limit → `요청이 너무 잦습니다. 1분 뒤 다시 시도해주세요.` / 비용 상한 → `오늘의 무료 사용량이 모두 소진되었습니다. 내일 다시 이용해주세요.`
- 비용 합산 SQL: `query_logs` 의 `created_at >= date_trunc('day', now())` + `usage->>'est_cost_usd'` 합 (usage NULL 제외), 60초 캐시.
- 클라이언트 키: `X-Forwarded-For` 첫 값 → 없으면 직접 연결 주소.
- guard 미들웨어는 기존 요청 ID 미들웨어보다 **먼저 실행** (코드상 request_log_middleware **아래**에 등록 — FastAPI 는 나중 등록이 바깥에서 먼저 실행됨). 429 응답에 X-Request-ID 없는 것은 허용 트레이드오프.
- 기존 테스트 보호: `tests/conftest.py` 신규 — main import 전에 한도 env 를 0 으로. (스펙의 "계획에서 확정" 항목의 확정안.)
- 스펙 대비 확정 이탈 1건(계획에서 문서화): 빈 deque 의 dict 키 제거는 생략 — 고유 IP 수만큼만 자라는 유계 메모리(포트폴리오 규모 무해)라 단순성 우선.
- 테스트 관례: check() 헬퍼 + `if __name__ == "__main__"` 러너, python 직접 실행/pytest 겸용.

---

### Task 1: guard.py 순수부 (`RateLimiter` + `client_ip` + 비용 캐시)

**Files:**
- Create: `backend/guard.py`
- Test: `tests/test_guard.py` (신규)

**Interfaces:**
- Consumes: stdlib 만 (db 는 `daily_cost_today` 안에서 지연 import)
- Produces (Task 2 가 소비):
  - `RateLimiter(per_min: int)` — `.per_min` 속성, `.allow(key: str, now: float) -> bool`
  - `client_ip(xff: str | None, fallback: str) -> str`
  - `daily_cost_today() -> float` (DB), `daily_cost_exceeded(limit: float, now: float | None = None, fetch=None) -> bool` (60초 캐시, now·fetch 주입은 테스트용), `reset_cost_cache() -> None`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_guard.py` 신규:

```python
"""배포 방어선(guard) 순수 로직 테스트 — DB·시계 없이 실행 (주입 방식).
실행: python tests/test_guard.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from guard import RateLimiter, client_ip, daily_cost_exceeded, reset_cost_cache  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_rate_limiter():
    rl = RateLimiter(3)
    check("한도 내 3회 허용", all(rl.allow("a", t) for t in (0.0, 1.0, 2.0)))
    check("4번째 거부", not rl.allow("a", 3.0))
    check("다른 키는 독립", rl.allow("b", 3.0))
    check("60초 경과 후 회복", rl.allow("a", 60.5))       # t=0.0 항목이 윈도우 밖
    check("경계: 정확히 60초는 만료", rl.allow("a", 61.0) is True)
    rl2 = RateLimiter(1)
    check("59.9초는 아직 윈도우 안", rl2.allow("x", 0.0) and not rl2.allow("x", 59.9))
    check("per_min 속성 노출", rl.per_min == 3)


def test_client_ip():
    check("XFF 첫 값", client_ip("1.2.3.4, 5.6.7.8", "9.9.9.9") == "1.2.3.4")
    check("XFF 단일 값 공백 정리", client_ip("  1.2.3.4  ", "9.9.9.9") == "1.2.3.4")
    check("XFF 없음 → fallback", client_ip(None, "9.9.9.9") == "9.9.9.9")
    check("XFF 빈 문자열 → fallback", client_ip("", "9.9.9.9") == "9.9.9.9")
    check("XFF 쉼표만 → fallback", client_ip(" , ", "9.9.9.9") == "9.9.9.9")


def test_daily_cost_cache():
    reset_cost_cache()
    calls = {"n": 0}
    def fetch_1usd():
        calls["n"] += 1
        return 1.5
    check("한도 초과 판정", daily_cost_exceeded(1.0, now=0.0, fetch=fetch_1usd) is True)
    check("캐시 내 재조회 없음", daily_cost_exceeded(1.0, now=30.0, fetch=fetch_1usd) is True and calls["n"] == 1)
    check("캐시 만료 후 재조회", daily_cost_exceeded(1.0, now=61.0, fetch=fetch_1usd) is True and calls["n"] == 2)
    reset_cost_cache()
    check("한도 미만 통과", daily_cost_exceeded(2.0, now=0.0, fetch=lambda: 0.3) is False)
    reset_cost_cache()


if __name__ == "__main__":
    test_rate_limiter()
    test_client_ip()
    test_daily_cost_cache()
    print("all passed")
```

- [ ] **Step 2: 실패 확인**

Run: `python tests/test_guard.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'guard'`

- [ ] **Step 3: 구현**

`backend/guard.py` 신규:

```python
"""배포 방어선 (4단계-A) — IP rate limit(1차) + 일별 OpenAI 비용 상한(2차).

의존성 없이 자체 구현. 인메모리 슬라이딩 윈도우 — 단일 인스턴스 전제(Render free 1대),
재시작 시 리셋. XFF 스푸핑·분산 공격의 완전 방어가 목적이 아니라 **비용 사고 방지**가
목적 — 1차가 뚫려도 2차(비용 상한)가 지갑을 지킨다.
스펙: docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md
"""
import time
from collections import deque

_WINDOW_SEC = 60.0
_COST_CACHE_SEC = 60.0

_cost_cache: tuple[float, float] | None = None  # (계산 시각, 오늘 합계 USD)


class RateLimiter:
    """키(IP)별 슬라이딩 윈도우 카운터. now 주입으로 시계 없이 테스트 가능.

    메모리: 키 딕셔너리는 고유 IP 수만큼만 자람(유계) — 빈 키 GC 는 생략(단순성).
    """

    def __init__(self, per_min: int):
        self.per_min = per_min
        self._hits: dict[str, deque] = {}

    def allow(self, key: str, now: float) -> bool:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= _WINDOW_SEC:
            q.popleft()
        if len(q) >= self.per_min:
            return False
        q.append(now)
        return True


def client_ip(xff: str | None, fallback: str) -> str:
    """X-Forwarded-For 첫 값(플랫폼 프록시 전제) → 없으면 직접 연결 주소."""
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return fallback


def daily_cost_today() -> float:
    """오늘(UTC 자정 기준) query_logs 누적 비용 USD. usage NULL(사전차단) 행 제외."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(SUM((usage->>'est_cost_usd')::float), 0)
            FROM query_logs
            WHERE created_at >= date_trunc('day', now()) AND usage IS NOT NULL
        """)
        return float(cur.fetchone()[0])


def daily_cost_exceeded(limit: float, now: float | None = None, fetch=None) -> bool:
    """오늘 비용 >= limit 인가. 60초 캐시로 요청마다 DB 를 때리지 않는다.

    ±60초 오버슛은 허용 오차(분당 한도 × 질의당 ~$0.01 수준). now·fetch 는 테스트 주입용.
    /answer 는 query_logs 미기록이라 집계 밖 — 배포 프론트는 /query 만 사용(스펙 한계 참조).
    """
    global _cost_cache
    if now is None:
        now = time.time()
    if fetch is None:
        fetch = daily_cost_today
    if _cost_cache is None or now - _cost_cache[0] >= _COST_CACHE_SEC:
        _cost_cache = (now, fetch())
    return _cost_cache[1] >= limit


def reset_cost_cache() -> None:
    """테스트·수동 운영용 캐시 초기화."""
    global _cost_cache
    _cost_cache = None
```

- [ ] **Step 4: 통과 확인**

Run: `python tests/test_guard.py` 그리고 `python -m pytest tests/test_guard.py -q`
Expected: `all passed` / 3 passed

- [ ] **Step 5: 커밋**

```bash
git add backend/guard.py tests/test_guard.py
git commit -m "feat(dep-a): guard 순수부 — 슬라이딩 윈도우 리미터·client_ip·비용 캐시"
```

---

### Task 2: main.py 배선 + conftest + .env.example + 429 테스트

**Files:**
- Modify: `backend/main.py` (import + 상수/리미터 + guard 미들웨어)
- Create: `tests/conftest.py`
- Modify: `.env.example`
- Test: `tests/test_api.py` (429 케이스 추가)

**Interfaces:**
- Consumes: Task 1 의 `RateLimiter`·`client_ip`·`daily_cost_exceeded`
- Produces: 모든 요청이 guard 미들웨어 통과. 테스트가 monkeypatch 할 module 속성: `main._llm_limiter`, `main._general_limiter`, `main.DAILY_COST_LIMIT_USD`.

- [ ] **Step 1: conftest 작성 (기존 스위트 보호 — main import 전에 한도 끔)**

`tests/conftest.py` 신규:

```python
"""pytest 전역 설정 — 배포 방어선(guard) 한도를 테스트에서 끈다.

main.py 가 import 시점에 한도 env 를 읽으므로, 어떤 테스트 모듈보다 먼저 실행되는
conftest 에서 0(끔)으로 고정 — 기존 TestClient 스위트가 연속 호출로 429 를 맞아
무작위 실패하는 것을 방지. 429 경로 자체는 test_api 가 리미터 객체 교체로 검증.
"""
import os

os.environ.setdefault("RATE_LIMIT_LLM_PER_MIN", "0")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("DAILY_COST_LIMIT_USD", "0")
```

(주의: `setdefault` — 실제 배포/로컬 서버 실행에는 영향 없음, pytest 전용.)

- [ ] **Step 2: 실패 테스트 작성 (429 경로)**

`tests/test_api.py` 끝에 추가 (기존 check()·HAS_DB 스킵 관례):

```python
def test_guard_rate_limit_and_cost():
    if not HAS_DB:
        print(_SKIP_MSG); return
    import guard
    # LLM 리미터를 2회/분으로 교체 — /query 는 사전차단 경로(검색 0건 질문)라 LLM 미호출
    saved_limiter, saved_cost = main._llm_limiter, main.DAILY_COST_LIMIT_USD
    main._llm_limiter = guard.RateLimiter(2)
    main.DAILY_COST_LIMIT_USD = 0  # 비용 상한은 이 케이스에서 끔
    try:
        body = {"question": "zzqqxx 존재하지않는 검색어 9999", "mode": "qa"}
        r1 = client.post("/query", json=body)
        r2 = client.post("/query", json=body)
        r3 = client.post("/query", json=body)
        check("2회까지 정상", r1.status_code == 200 and r2.status_code == 200,
              (r1.status_code, r2.status_code))
        check("3번째 429", r3.status_code == 429, r3.status_code)
        check("429 한국어 detail", "요청이 너무 잦습니다" in r3.json()["detail"], r3.json())
        check("일반 경로는 LLM 한도와 독립", client.get("/issues").status_code == 200)
        check("/health 무제한", client.get("/health").status_code == 200)
    finally:
        main._llm_limiter = saved_limiter
        main.DAILY_COST_LIMIT_USD = saved_cost

    # 비용 상한 — fetch 주입 대신 캐시를 직접 심어 검증 (DB 값 무관 결정적)
    saved_cost = main.DAILY_COST_LIMIT_USD
    main.DAILY_COST_LIMIT_USD = 0.5
    guard._cost_cache = (float("inf"), 9.99)   # 만료되지 않는 캐시에 초과값
    try:
        r = client.post("/query", json={"question": "비용 상한 테스트", "mode": "qa"})
        check("비용 초과 429", r.status_code == 429, r.status_code)
        check("소진 안내 문구", "무료 사용량" in r.json()["detail"], r.json())
    finally:
        main.DAILY_COST_LIMIT_USD = saved_cost
        guard.reset_cost_cache()
```

`__main__` 러너가 있으면 호출 추가.

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_api.py::test_guard_rate_limit_and_cost -q`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_llm_limiter'`

- [ ] **Step 4: main.py 배선**

(a) import 블록에 추가:

```python
from guard import RateLimiter, client_ip, daily_cost_exceeded
```

(b) CORS `app.add_middleware(...)` 블록 아래에 상수·리미터:

```python
# 배포 방어선 (4단계-A) — 0 이면 해당 방어 끔 (테스트는 conftest 가 끔)
RATE_LIMIT_LLM_PER_MIN = int(os.environ.get("RATE_LIMIT_LLM_PER_MIN", "5"))
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "60"))
DAILY_COST_LIMIT_USD = float(os.environ.get("DAILY_COST_LIMIT_USD", "1.0"))
_llm_limiter = RateLimiter(RATE_LIMIT_LLM_PER_MIN)
_general_limiter = RateLimiter(RATE_LIMIT_PER_MIN)
_LLM_PATHS = ("/query", "/answer")   # LLM 호출 경로 — 강한 한도 + 비용 상한
```

(c) `request_log_middleware` 함수 **아래**에 guard 미들웨어 등록 (나중 등록 = 바깥에서 먼저 실행 → 한도 초과 요청은 로깅 미들웨어에 닿기 전에 차단. 429 에 X-Request-ID 없음은 허용):

```python
@app.middleware("http")
async def guard_middleware(request: Request, call_next):
    """배포 방어선 (4단계-A) — IP rate limit + 일별 비용 상한. /health 는 통과."""
    path = request.url.path
    if path != "/health":
        ip = client_ip(request.headers.get("x-forwarded-for"),
                       request.client.host if request.client else "unknown")
        limiter = _llm_limiter if path in _LLM_PATHS else _general_limiter
        if limiter.per_min > 0 and not limiter.allow(ip, time.time()):
            return JSONResponse(status_code=429, content={
                "detail": "요청이 너무 잦습니다. 1분 뒤 다시 시도해주세요."})
        if path in _LLM_PATHS and DAILY_COST_LIMIT_USD > 0 \
                and daily_cost_exceeded(DAILY_COST_LIMIT_USD):
            return JSONResponse(status_code=429, content={
                "detail": "오늘의 무료 사용량이 모두 소진되었습니다. 내일 다시 이용해주세요."})
    return await call_next(request)
```

`JSONResponse` import 확인: `from fastapi.responses import JSONResponse` 를 import 블록에 추가 (이미 있으면 생략).

- [ ] **Step 5: 테스트 통과 + 전체 회귀**

Run: `python -m pytest tests/test_api.py -q` 그리고 `python -m pytest tests/ -q`
Expected: 전체 PASS (conftest 가 기본 한도를 꺼서 기존 케이스 무영향)

- [ ] **Step 6: .env.example 갱신**

`RERANKER_ENABLED=0` 블록 아래에 추가:

```
# 배포 방어선 (4단계-A) — 0 이면 해당 방어 끔
RATE_LIMIT_LLM_PER_MIN=5      # /query·/answer IP당 분당 한도
RATE_LIMIT_PER_MIN=60         # 그 외 경로 IP당 분당 한도
DAILY_COST_LIMIT_USD=1.0      # 일별 OpenAI 비용 상한 (query_logs 합산, 60초 캐시)
```

- [ ] **Step 7: 라이브 스모크 (서버 기동)**

백엔드 재시작 후 (기본 한도 활성):

```bash
for i in 1 2 3 4 5 6 7; do curl -s -o /dev/null -w "%{http_code} " -X POST http://127.0.0.1:8000/query -H "Content-Type: application/json" -d '{"question":"zzqq 없는 검색어","mode":"qa"}'; done; echo
```

Expected: `200 200 200 200 200 429 429` (분당 5회 한도). `curl -s http://127.0.0.1:8000/health` → 200.

- [ ] **Step 8: 커밋**

```bash
git add backend/main.py tests/conftest.py tests/test_api.py .env.example
git commit -m "feat(dep-a): guard 미들웨어 배선 — LLM 분당 5회·일별 비용 상한, 테스트는 conftest 로 격리"
```

---

### Task 3: 문서 — progress.md (4단계-A 기록)

**Files:**
- Modify: `docs/progress.md`

**Interfaces:**
- Consumes: Task 1·2 결과
- Produces: 4단계 착수 기록

- [ ] **Step 1: "### POL-7 후속" 섹션 끝(다음 `##` 직전)에 추가**

```markdown
### 4단계-A 구현 기록 — 배포 방어선 (2026-07-11)

> spec: `docs/superpowers/specs/2026-07-11-dep-a-guardrails-design.md`,
> plan: `docs/superpowers/plans/2026-07-11-dep-a-guardrails.md`
> 4단계 분해: A 방어선(이 기록) → B 배포 실행(축소 코퍼스 + Vercel/Render/Supabase, 별도 스펙).

- **이중 방어선**: 1차 IP rate limit (`backend/guard.py` 자체 슬라이딩 윈도우 —
  의존성 0, LLM 경로 분당 5·일반 60, `X-Forwarded-For` 첫 값 기준) + 2차 **일별
  비용 상한**(query_logs est_cost_usd 합산, 기본 $1, 60초 캐시). 1차가 뚫려도
  2차가 지갑을 지킨다 — fix_checklist 5순위 "비용 공격 무방비" 해소.
- **응답**: 429 한국어 detail (프론트가 그대로 표시하는 기존 규약 재사용).
  `/health` 는 무제한(플랫폼 헬스체크).
- **테스트 격리**: `tests/conftest.py` 가 pytest 에서 한도를 끔 — 기존 스위트가
  연속 호출로 오탐 429 를 맞지 않게. 429 경로는 리미터 객체 교체로 검증.
- **알려진 한계(문서화)**: 인메모리 단일 인스턴스 전제(재시작 리셋) / XFF 스푸핑
  완전 방어 아님(최종 방어선 = 비용 상한) / `/answer` 는 query_logs 미기록이라
  비용 집계 밖(배포 프론트는 /query 만 사용).
```

- [ ] **Step 2: 3행 최종 업데이트 줄 교체**

```
최종 업데이트: 2026-07-11 (4단계-A 배포 방어선 — rate limit + 일별 비용 상한. 다음: 4단계-B 배포 실행(축소 코퍼스·인프라, 사용자 계정 필요))
```

- [ ] **Step 3: 커밋**

```bash
git add docs/progress.md
git commit -m "docs(dep-a): 배포 방어선 구현 기록 — 4단계 착수"
```

---

## Self-Review

**1. Spec coverage:**
- RateLimiter 슬라이딩 윈도우·now 주입·per_min 노출 → Task 1 ✅
- client_ip XFF 규칙 → Task 1 ✅ (테스트 5케이스)
- 일별 비용 SQL·60초 캐시·주입 테스트 → Task 1 ✅ (SQL verbatim)
- 경로 분류·한도 env·0=끔·/health 통과·429 문구 verbatim → Task 2 ✅
- guard 가 요청 ID 미들웨어보다 먼저 → Task 2 Step 4(c) 등록 순서 + 이유 주석 ✅
- 테스트 격리 확정안(conftest) → Task 2 Step 1 ✅ (스펙의 미정 항목 해소)
- .env.example → Task 2 Step 6 ✅
- 알려진 한계 문서화 → Task 3 + guard.py docstring ✅
- 스펙 이탈 1건(빈 키 GC 생략) → Global Constraints 에 명시 ✅

**2. Placeholder scan:** TBD 없음, 전 스텝 실코드·실명령.

**3. Type consistency:**
- `RateLimiter(per_min)`·`.allow(key, now)`·`.per_min` — Task 1 정의 = Task 2 미들웨어·테스트 사용 ✅
- `client_ip(xff, fallback)` — Task 1 = Task 2 호출 ✅
- `daily_cost_exceeded(limit, now=None, fetch=None)`·`reset_cost_cache` — Task 1 = Task 2 테스트(guard._cost_cache 직접 주입 포함) ✅
- monkeypatch 대상 `main._llm_limiter`/`main.DAILY_COST_LIMIT_USD` — Task 2 (b) 정의 = Step 2 테스트 ✅
