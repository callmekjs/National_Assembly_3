# 회원가입 + 질의 히스토리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 아이디+비밀번호 직접 인증(bcrypt+JWT)과 로그인 사용자의 질의 히스토리를 추가한다 — 비회원 기능 제약 없음.

**Architecture:** 신규 `backend/auth.py`(순수 해시·토큰 로직 + 스키마 자가 생성) + main.py 에 엔드포인트 4개(/auth/signup·login·me, /me/queries). 히스토리는 기존 query_logs 에 user_id 컬럼 하나 추가로 해결. 프론트는 localStorage 토큰 + 헤더 모달 + 접이식 히스토리 패널.

**Tech Stack:** FastAPI, psycopg2, bcrypt(신규), PyJWT(신규), React(Vite).

**Spec:** `docs/superpowers/specs/2026-07-15-auth-history-design.md`

## Global Constraints

- username 규칙: 영문·숫자·한글 2~20자 (`^[A-Za-z0-9가-힣]{2,20}$`)
- password 규칙: 8자 이상, 72바이트 이하 (bcrypt 입력 한계)
- 로그인 실패 메시지는 아이디 존재 여부와 무관하게 **"아이디 또는 비밀번호가 올바르지 않습니다"** 단일
- 무효/만료 토큰이 와도 `/query` 는 익명으로 통과 (401 은 /auth/me·/me/queries 만)
- JWT: HS256, exp 7일, `JWT_SECRET` env (없으면 dev 기본값 + 경고 로그 1회)
- 개인정보 금지: 이메일·이름 등 어떤 추가 필드도 받지 않는다
- 가입 폼 문구: "포트폴리오 데모 — 다른 곳에서 쓰는 비밀번호를 입력하지 마세요. 계정과 기록은 예고 없이 초기화될 수 있습니다."
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 테스트 파일은 repo 관례를 따른다: `check()` 헬퍼 + `if __name__ == "__main__"` 직접 실행 지원 + DB 필요 테스트는 HAS_DB 가드 (tests/test_actors.py·test_api.py 참조)

---

### Task 1: auth 순수 로직 (해시·토큰·검증) + 의존성

**Files:**
- Create: `backend/auth.py`
- Modify: `backend/requirements.txt` (끝에 2줄 추가)
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces (Task 2·4 가 사용):
  - `hash_password(pw: str) -> str` / `verify_password(pw: str, hashed: str) -> bool`
  - `valid_username(name: str) -> bool` / `valid_password(pw: str) -> bool`
  - `create_token(user_id: int, username: str) -> str`
  - `decode_token(token: str) -> dict | None` — 유효하면 `{"user_id": int, "username": str}`, 무효·만료면 None
  - 상수 `JWT_SECRET`, `TOKEN_TTL_DAYS = 7`

- [ ] **Step 1: 의존성 설치 + requirements 추가**

`backend/requirements.txt` 끝에 추가:

```
bcrypt==4.2.1             # 비밀번호 해시 (인증 — 해시·서명은 직접 구현 금지 영역)
PyJWT==2.10.1             # JWT 서명/검증 (인증)
```

Run: `pip install bcrypt==4.2.1 PyJWT==2.10.1`
Expected: Successfully installed

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_auth.py` 생성:

```python
"""
auth 모듈 단위 테스트 — DB 없이 순수 로직만 검증.

검사 항목:
    1. bcrypt 해시 왕복 (원문 미저장·솔트 무작위)
    2. username/password 형식 규칙
    3. JWT 왕복 · 만료 · 위조 거부

실행: python tests/test_auth.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import jwt as pyjwt  # noqa: E402

import auth  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_password_hash_roundtrip():
    h = auth.hash_password("correct-horse-1")
    check("해시는 원문이 아님", h != "correct-horse-1" and h.startswith("$2"), h[:6])
    check("검증 성공", auth.verify_password("correct-horse-1", h))
    check("오답 거부", not auth.verify_password("wrong-password", h))
    check("솔트 무작위 — 같은 입력도 다른 해시", auth.hash_password("correct-horse-1") != h)


def test_username_rules():
    check("한글 2자 허용", auth.valid_username("김윤"))
    check("영문+숫자 허용", auth.valid_username("kim123"))
    check("1자 거부", not auth.valid_username("k"))
    check("21자 거부", not auth.valid_username("a" * 21))
    check("공백 거부", not auth.valid_username("kim lee"))
    check("특수문자 거부", not auth.valid_username("kim@lee"))


def test_password_rules():
    check("8자 허용", auth.valid_password("12345678"))
    check("7자 거부", not auth.valid_password("1234567"))
    check("72바이트 초과 거부", not auth.valid_password("가" * 30))  # 한글 30자 = 90바이트


def test_token_roundtrip():
    tok = auth.create_token(42, "kim")
    got = auth.decode_token(tok)
    check("토큰 왕복", got == {"user_id": 42, "username": "kim"}, got)


def test_token_rejects():
    check("빈 토큰 거부", auth.decode_token("") is None)
    check("쓰레기 거부", auth.decode_token("abc.def.ghi") is None)
    tampered = pyjwt.encode({"sub": "42", "username": "kim"}, "wrong-secret", algorithm="HS256")
    check("위조 서명 거부", auth.decode_token(tampered) is None)
    expired = pyjwt.encode({"sub": "42", "username": "kim", "exp": 0}, auth.JWT_SECRET, algorithm="HS256")
    check("만료 거부", auth.decode_token(expired) is None)


if __name__ == "__main__":
    test_password_hash_roundtrip()
    test_username_rules()
    test_password_rules()
    test_token_roundtrip()
    test_token_rejects()
    print("전체 통과")
```

- [ ] **Step 3: 실패 확인**

Run: `python -m pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 4: 최소 구현**

`backend/auth.py` 생성:

```python
"""인증 코어 (직접 구현) — 아이디+비밀번호(bcrypt) + JWT.

설계 결정 (spec 2026-07-15):
  - 이메일을 받지 않는다 — 개인정보 부담 0 (유출돼도 외부 신원과 연결 불가)
  - 의존성 0 기조의 예외 지점: 비밀번호 해시(bcrypt)·토큰 서명(PyJWT)은
    직접 구현하면 안 되는 영역이라는 판단 자체가 설계다
  - 비밀번호 원문은 어디에도 저장하지 않는다 — bcrypt 일방향 해시만
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

logger = logging.getLogger("uvicorn.error")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    JWT_SECRET = "dev-secret-not-for-production"
    logger.warning("JWT_SECRET 미설정 — dev 기본값 사용 (배포에서는 반드시 설정)")

TOKEN_TTL_DAYS = 7
_USERNAME_RE = re.compile(r"^[A-Za-z0-9가-힣]{2,20}$")


def valid_username(name: str) -> bool:
    return bool(_USERNAME_RE.match(name or ""))


def valid_password(pw: str) -> bool:
    # 72바이트 상한 = bcrypt 입력 한계 (초과분 무시 절단을 검증 단계에서 차단)
    return bool(pw) and len(pw) >= 8 and len(pw.encode("utf-8")) <= 72


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),  # JWT 표준상 sub 는 문자열
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    """유효하면 {user_id, username}, 무효·만료·위조면 None — 예외를 밖으로 내지 않는다."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return {"user_id": int(payload["sub"]), "username": payload["username"]}
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
```

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_auth.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/auth.py backend/requirements.txt tests/test_auth.py
git commit -m "feat(auth): 인증 코어 — bcrypt 해시 + JWT 발급/검증 (순수 로직)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 스키마 자가 생성 + /auth/signup·login·me 엔드포인트

**Files:**
- Modify: `backend/auth.py` (ensure_schema 추가)
- Modify: `backend/main.py` (lifespan + 엔드포인트 3개 + Pydantic 모델)
- Test: `tests/test_auth_api.py`

**Interfaces:**
- Consumes: Task 1 의 `hash_password`·`verify_password`·`valid_username`·`valid_password`·`create_token`·`decode_token`
- Produces (Task 4·5 가 사용):
  - `POST /auth/signup` `{username, password}` → 200 `{token, username}` / 409 중복 / 422 형식
  - `POST /auth/login` `{username, password}` → 200 `{token, username}` / 401 단일 메시지
  - `GET /auth/me` (Bearer) → 200 `{username}` / 401
  - `auth.ensure_schema()` — users 테이블 + query_logs.user_id 컬럼 자가 생성 (멱등)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_auth_api.py` 생성:

```python
"""
인증 API 테스트 — TestClient + 로컬 DB. DB 없으면 건너뜀 (test_api 패턴).

검사 항목:
    1. signup 성공 → 토큰 발급 + DB 에 bcrypt 해시 저장 (원문 아님)
    2. signup 중복 409 · 형식 위반 422
    3. login 성공 / 실패 메시지 단일 (없는 아이디 = 틀린 비번)
    4. /auth/me 토큰 검증 (유효 200 / 무효 401)

실행: python tests/test_auth_api.py
"""

import io
import sys
import uuid
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import os  # noqa: E402

os.environ.setdefault("RATE_LIMIT_LLM_PER_MIN", "0")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("DAILY_COST_LIMIT_USD", "0")

import db  # noqa: E402

try:
    db.init_pool()
    with db.get_conn() as _conn:
        pass
    HAS_DB = True
except Exception:
    HAS_DB = False

if HAS_DB:
    from fastapi.testclient import TestClient

    import main
    client = TestClient(main.app)

_SKIP_MSG = "  - DB 없음 — 건너뜀 (로컬 Docker 필요)"


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def _fresh_name() -> str:
    return "테스트" + uuid.uuid4().hex[:8]


def _cleanup(username: str):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM query_logs WHERE user_id IN (SELECT user_id FROM users WHERE username = %s)",
            (username,),
        )
        cur.execute("DELETE FROM users WHERE username = %s", (username,))
        conn.commit()


def test_signup_success_and_hash():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        r = client.post("/auth/signup", json={"username": name, "password": "test-pw-123"})
        check("signup 200", r.status_code == 200, (r.status_code, r.text))
        body = r.json()
        check("토큰 발급", bool(body.get("token")) and body.get("username") == name, body)
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE username = %s", (name,))
            h = cur.fetchone()[0]
        check("DB 에 bcrypt 해시 (원문 아님)", h != "test-pw-123" and h.startswith("$2"), h[:6])
    finally:
        _cleanup(name)


def test_signup_duplicate_and_format():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        client.post("/auth/signup", json={"username": name, "password": "test-pw-123"})
        r = client.post("/auth/signup", json={"username": name, "password": "other-pw-456"})
        check("중복 409", r.status_code == 409, r.status_code)
        r = client.post("/auth/signup", json={"username": "김@해커", "password": "test-pw-123"})
        check("형식 위반 422", r.status_code == 422, r.status_code)
        r = client.post("/auth/signup", json={"username": _fresh_name(), "password": "short"})
        check("짧은 비번 422", r.status_code == 422, r.status_code)
    finally:
        _cleanup(name)


def test_login_and_uniform_failure():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        client.post("/auth/signup", json={"username": name, "password": "test-pw-123"})
        r = client.post("/auth/login", json={"username": name, "password": "test-pw-123"})
        check("login 200 + 토큰", r.status_code == 200 and r.json().get("token"), r.status_code)

        wrong_pw = client.post("/auth/login", json={"username": name, "password": "wrong-pw-000"})
        no_user = client.post("/auth/login", json={"username": "없는계정xyz", "password": "wrong-pw-000"})
        check("실패는 둘 다 401", wrong_pw.status_code == 401 and no_user.status_code == 401,
              (wrong_pw.status_code, no_user.status_code))
        check("실패 메시지 단일 (계정 열거 방지)",
              wrong_pw.json()["detail"] == no_user.json()["detail"]
              == "아이디 또는 비밀번호가 올바르지 않습니다",
              (wrong_pw.json(), no_user.json()))
    finally:
        _cleanup(name)


def test_me():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        tok = client.post("/auth/signup", json={"username": name, "password": "test-pw-123"}).json()["token"]
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {tok}"})
        check("me 200", r.status_code == 200 and r.json() == {"username": name}, (r.status_code, r.text))
        r = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
        check("무효 토큰 401", r.status_code == 401, r.status_code)
        r = client.get("/auth/me")
        check("토큰 없음 401", r.status_code == 401, r.status_code)
    finally:
        _cleanup(name)


if __name__ == "__main__":
    test_signup_success_and_hash()
    test_signup_duplicate_and_format()
    test_login_and_uniform_failure()
    test_me()
    print("전체 통과")
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_auth_api.py -q`
Expected: FAIL — /auth/signup 404 (엔드포인트 없음)

- [ ] **Step 3: ensure_schema 를 auth.py 끝에 추가**

```python
def ensure_schema() -> None:
    """users 테이블 + query_logs.user_id 자가 생성 (멱등) — 배포 DB 마이그레이션 단계 제거.

    utterance_summaries 와 같은 패턴. 실패해도 앱은 뜬다(인증은 부가 기능) —
    호출측(lifespan)이 try/except 로 감싼다."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       SERIAL PRIMARY KEY,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute(
            "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(user_id)"
        )
        conn.commit()
```

- [ ] **Step 4: main.py — lifespan 에 스키마 준비 + 엔드포인트 3개**

main.py 상단 import 에 추가 (`from fastapi import ...` 줄에 `Header` 가 없으면 추가):

```python
from fastapi import FastAPI, Header, HTTPException, Query, Request
import psycopg2

import auth
```

lifespan(main.py 40-45행) 수정 — `init_pool()` 다음에:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작: connection pool 준비 / 종료: 반납
    init_pool()
    try:
        auth.ensure_schema()
    except Exception:
        logger.warning("auth 스키마 준비 실패 — 인증 기능만 비활성 (서비스는 계속)", exc_info=True)
    yield
    close_pool()
```

Pydantic 모델 (기존 QueryRequest 근처에 추가):

```python
class AuthRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def _username_rule(cls, v: str) -> str:
        if not auth.valid_username(v):
            raise ValueError("아이디는 영문·숫자·한글 2~20자입니다")
        return v

    @field_validator("password")
    @classmethod
    def _password_rule(cls, v: str) -> str:
        if not auth.valid_password(v):
            raise ValueError("비밀번호는 8자 이상 72바이트 이하입니다")
        return v
```

(main.py 의 기존 pydantic import 에 `field_validator` 가 없으면 추가)

엔드포인트 (기존 /feedback 근처, 파일 뒤쪽에 추가):

```python
def _bearer_user(authorization: str | None) -> dict | None:
    """Authorization 헤더 → {user_id, username} | None. 무효 토큰도 None (익명 통과용)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return auth.decode_token(authorization[len("Bearer "):])


@app.post("/auth/signup")
def auth_signup(req: AuthRequest):
    """가입 즉시 로그인 — 토큰 반환. 이메일 등 개인정보는 받지 않는다 (spec)."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING user_id",
                (req.username, auth.hash_password(req.password)),
            )
            user_id = cur.fetchone()[0]
            conn.commit()
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다")
    return {"token": auth.create_token(user_id, req.username), "username": req.username}


@app.post("/auth/login")
def auth_login(req: AuthRequest):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, password_hash FROM users WHERE username = %s", (req.username,))
        row = cur.fetchone()
    # 아이디 존재 여부와 무관하게 단일 메시지 — 계정 열거 방지 (spec)
    if row is None or not auth.verify_password(req.password, row[1]):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다")
    return {"token": auth.create_token(row[0], req.username), "username": req.username}


@app.get("/auth/me")
def auth_me(authorization: str | None = Header(default=None)):
    user = _bearer_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    return {"username": user["username"]}
```

주의: `get_conn` 컨텍스트가 자동 커밋하지 않으면 signup 의 `conn.commit()` 유지.
UniqueViolation 이 `psycopg2.errors` 에서 직접 안 잡히면(풀 래퍼에 따라)
`except psycopg2.IntegrityError` 로 잡는다 — 테스트가 판정한다.

- [ ] **Step 5: 통과 확인**

Run: `python -m pytest tests/test_auth_api.py tests/test_auth.py -q`
Expected: PASS (9 passed)

- [ ] **Step 6: 기존 스위트 회귀 확인**

Run: `python -m pytest -q`
Expected: 전부 PASS (기존 95 + 신규)

- [ ] **Step 7: Commit**

```bash
git add backend/auth.py backend/main.py tests/test_auth_api.py
git commit -m "feat(auth): signup/login/me 엔드포인트 + users 스키마 자가 생성

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: guard 편입 — /auth/* 무차별 대입 방어

**Files:**
- Modify: `backend/main.py:64` (_LLM_PATHS 근처) 및 guard_middleware 의 limiter 선택(90행 부근)
- Test: `tests/test_guard.py` (테스트 1개 추가)

**Interfaces:**
- Consumes: 기존 `_llm_limiter`(분당 5)·`_general_limiter`(분당 60), `_LLM_PATHS`
- Produces: `_STRICT_PATHS` — 강한 한도 적용 경로 튜플 (비용 상한은 여전히 `_LLM_PATHS` 만)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_guard.py` 끝에 추가 (파일의 기존 import·check 패턴 재사용):

```python
def test_auth_paths_in_strict_group():
    """/auth/login·signup 이 강한 한도(분당 5) 그룹에 있어야 무차별 대입이 막힌다.
    비용 상한 경로(_LLM_PATHS)에는 없어야 한다 — auth 는 LLM 을 안 쓴다."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    import main
    check("auth 가 강한 한도 그룹에", "/auth/login" in main._STRICT_PATHS
          and "/auth/signup" in main._STRICT_PATHS, main._STRICT_PATHS)
    check("auth 는 비용 상한 밖", "/auth/login" not in main._LLM_PATHS, main._LLM_PATHS)
```

(test_guard.py 가 main 을 import 하려면 DB 가 필요할 수 있다 — 파일 상단에 이미
HAS_DB 스킵 패턴이 있으면 따르고, 없으면 이 테스트 안에서 try/except ImportError
로 감싸 skip 처리한다.)

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_guard.py -q`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_STRICT_PATHS'`

- [ ] **Step 3: main.py 수정**

64행 부근:

```python
_LLM_PATHS = ("/query", "/answer")   # LLM 호출 경로 — 비용 상한 대상
# 강한 rate limit 대상 = LLM 경로 + 인증 경로 (무차별 대입 방어 — spec 2026-07-15)
_STRICT_PATHS = _LLM_PATHS + ("/auth/login", "/auth/signup")
```

guard_middleware 내부(90행 부근) 한 줄 교체:

```python
        limiter = _llm_limiter if path in _STRICT_PATHS else _general_limiter
```

(비용 상한 검사 `if path in _LLM_PATHS and DAILY_COST_LIMIT_USD > 0:` 는 그대로 둔다)

- [ ] **Step 4: 통과 확인**

Run: `python -m pytest tests/test_guard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_guard.py
git commit -m "feat(guard): /auth/login·signup 을 강한 rate limit 그룹에 편입 (무차별 대입 방어)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 질의 히스토리 백엔드 — user_id 기록 + GET /me/queries

**Files:**
- Modify: `backend/main.py` — `_log_query`(166행), `/query`(201행), 신규 `GET /me/queries`
- Test: `tests/test_auth_api.py` (테스트 2개 추가)

**Interfaces:**
- Consumes: Task 2 의 `_bearer_user`, `/auth/signup`
- Produces (Task 6 프론트가 사용):
  - `GET /me/queries` (Bearer) → `{"queries": [{query_id, question, mode, grounding, created_at}]}` 최근 20건 / 401

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_auth_api.py` 끝에 추가:

```python
def test_query_records_user_id():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        tok = client.post("/auth/signup", json={"username": name, "password": "test-pw-123"}).json()["token"]
        # 검색 0건 → 사전차단 경로: LLM 미호출로 로그까지 도달 (test_api 의 기존 기법)
        orig = main.hybrid_search
        main.hybrid_search = lambda *a, **k: []
        try:
            r = client.post("/query", json={"question": "히스토리 기록 테스트 질문입니다"},
                            headers={"Authorization": f"Bearer {tok}"})
            check("토큰 질의 200", r.status_code == 200, r.status_code)
            r2 = client.post("/query", json={"question": "무효 토큰도 익명으로 통과해야 한다"},
                             headers={"Authorization": "Bearer garbage"})
            check("무효 토큰도 질의 통과 (익명)", r2.status_code == 200, r2.status_code)
        finally:
            main.hybrid_search = orig
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM query_logs ql JOIN users u USING (user_id) WHERE u.username = %s",
                (name,),
            )
            check("user_id 기록됨", cur.fetchone()[0] == 1)
    finally:
        _cleanup(name)


def test_me_queries():
    if not HAS_DB:
        print(_SKIP_MSG); return
    name = _fresh_name()
    try:
        tok = client.post("/auth/signup", json={"username": name, "password": "test-pw-123"}).json()["token"]
        orig = main.hybrid_search
        main.hybrid_search = lambda *a, **k: []
        try:
            client.post("/query", json={"question": "내 기록 조회 테스트 질문"},
                        headers={"Authorization": f"Bearer {tok}"})
        finally:
            main.hybrid_search = orig
        r = client.get("/me/queries", headers={"Authorization": f"Bearer {tok}"})
        check("me/queries 200", r.status_code == 200, r.status_code)
        qs = r.json()["queries"]
        check("본인 기록 1건 + 필드", len(qs) == 1 and qs[0]["question"] == "내 기록 조회 테스트 질문"
              and set(qs[0]) >= {"query_id", "question", "mode", "grounding", "created_at"}, qs)
        check("무인증 401", client.get("/me/queries").status_code == 401)
    finally:
        _cleanup(name)
```

- [ ] **Step 2: 실패 확인**

Run: `python -m pytest tests/test_auth_api.py -q`
Expected: FAIL — user_id 미기록(count 0) / /me/queries 404

- [ ] **Step 3: 구현**

`_log_query` 시그니처·INSERT 수정 (166-198행):

```python
def _log_query(req: QueryRequest, result: dict, grounding: str, latency_ms: int,
               source_block: str | None = None, user_id: int | None = None) -> str | None:
```

INSERT 컬럼에 `user_id` 추가:

```python
                INSERT INTO query_logs
                  (question, mode, committee, date_from, date_to,
                   answer, grounding, citations, invalid_citations, usage, latency_ms,
                   source_block, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
```

VALUES 튜플 끝에 `user_id,` 추가.

`/query` 엔드포인트 (201행):

```python
@app.post("/query")
def query(req: QueryRequest, authorization: str | None = Header(default=None)):
```

로그 호출부(239행)를:

```python
    user = _bearer_user(authorization)  # 무효 토큰이어도 None — 질의는 익명으로 계속 (spec)
    query_id = _log_query(req, result, grounding, latency_ms, source_block,
                          user_id=user["user_id"] if user else None)
```

신규 엔드포인트 (auth 엔드포인트들 아래):

```python
@app.get("/me/queries")
def my_queries(authorization: str | None = Header(default=None)):
    """내 질의 히스토리 최근 20건 — 답변 재표시는 범위 밖 (질문 재실행 유도, spec)."""
    user = _bearer_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT query_id::text, question, mode, grounding, created_at::text
            FROM query_logs WHERE user_id = %s
            ORDER BY created_at DESC LIMIT 20
            """,
            (user["user_id"],),
        )
        return {"queries": [dict(r) for r in cur.fetchall()]}
```

(main.py 에 `RealDictCursor` import 가 없으면 `from psycopg2.extras import RealDictCursor` 추가)

- [ ] **Step 4: 통과 확인 + 전체 회귀**

Run: `python -m pytest tests/test_auth_api.py -q && python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/main.py tests/test_auth_api.py
git commit -m "feat(history): /query user_id 기록 + GET /me/queries (최근 20건)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 프론트 — 토큰 관리 + 로그인/가입 모달 + 헤더 상태

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/AuthModal.jsx`
- Modify: `frontend/src/App.jsx` (헤더에 인증 영역 + 모달 마운트)
- Modify: `frontend/src/App.css` (모달 스타일)

**Interfaces:**
- Consumes: Task 2 API (`/auth/signup`·`/auth/login`·`/auth/me`)
- Produces (Task 6 이 사용):
  - api.js: `getToken()`, `clearToken()`, `signup(u,p)`, `login(u,p)`, `fetchMe()`, `fetchMyQueries()` — 모든 `request()` 가 토큰 존재 시 Authorization 자동 첨부
  - App.jsx: `user` 상태 (`{username} | null`)

- [ ] **Step 1: api.js 에 토큰 계층 추가**

api.js 의 `request` 함수 위에:

```js
// 인증 토큰 (localStorage) — XSS 시 탈취 가능하나 걸린 자산이 질의 히스토리뿐인
// 데모라 수용. HttpOnly 쿠키는 Vercel↔Render 교차 출처(제3자 쿠키 차단)에서 더 취약.
const TOKEN_KEY = 'auth_token'
export function getToken() { return localStorage.getItem(TOKEN_KEY) }
export function setToken(t) { localStorage.setItem(TOKEN_KEY, t) }
export function clearToken() { localStorage.removeItem(TOKEN_KEY) }
```

`request` 내부의 fetch 호출을 수정 — headers 에 토큰 자동 첨부:

```js
async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const token = getToken()
  const headers = { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  let res
  try {
    res = await fetch(`${API_BASE}${path}`, { ...options, headers, signal: AbortSignal.timeout(timeoutMs) })
```

파일 끝에 API 함수 4개:

```js
export function signup(username, password) {
  return request('/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function login(username, password) {
  return request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function fetchMe() {
  return request('/auth/me')
}

export function fetchMyQueries() {
  return request('/me/queries')
}
```

- [ ] **Step 2: AuthModal 컴포넌트 생성**

`frontend/src/components/AuthModal.jsx`:

```jsx
import { useState } from 'react'
import { login, signup, setToken } from '../api'

// 로그인·가입 모달 — 성공 시 토큰 저장 후 onSuccess(username) 로 부모에 알린다
export default function AuthModal({ onClose, onSuccess }) {
  const [tab, setTab] = useState('login') // login | signup
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setErr(null); setBusy(true)
    try {
      const fn = tab === 'login' ? login : signup
      const res = await fn(username.trim(), password)
      setToken(res.token)
      onSuccess(res.username)
      onClose()
    } catch (e2) { setErr(e2.message) } finally { setBusy(false) }
  }

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={e => e.stopPropagation()} role="dialog" aria-label="로그인 또는 가입">
        <div className="auth-tabs">
          <button className={tab === 'login' ? 'active' : ''} onClick={() => { setTab('login'); setErr(null) }}>로그인</button>
          <button className={tab === 'signup' ? 'active' : ''} onClick={() => { setTab('signup'); setErr(null) }}>가입</button>
        </div>
        <form onSubmit={submit}>
          <input value={username} onChange={e => setUsername(e.target.value)}
                 placeholder="아이디 (영문·숫자·한글 2~20자)" autoFocus />
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                 placeholder="비밀번호 (8자 이상)" />
          {tab === 'signup' && (
            <p className="auth-notice">
              포트폴리오 데모 — 다른 곳에서 쓰는 비밀번호를 입력하지 마세요.
              계정과 기록은 예고 없이 초기화될 수 있습니다. (이메일 등 개인정보는 받지 않습니다)
            </p>
          )}
          {err && <p className="auth-error">{err}</p>}
          <button type="submit" disabled={busy || !username.trim() || !password}>
            {busy ? '처리 중…' : tab === 'login' ? '로그인' : '가입하기'}
          </button>
        </form>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: App.css 에 모달·헤더 스타일 추가** (파일 끝)

```css
/* 인증 — 헤더 우측 상태 + 모달 */
.auth-corner {
  position: absolute;
  top: 1rem;
  right: 0;
  font-size: 0.85rem;
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.auth-corner button {
  font-size: 0.85rem;
  padding: 0.3rem 0.7rem;
  cursor: pointer;
}

.auth-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.auth-modal {
  background: #fff;
  border-radius: 10px;
  padding: 1.4rem;
  width: min(360px, 90vw);
}

.auth-tabs {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 1rem;
}

.auth-tabs button {
  flex: 1;
  padding: 0.45rem;
  cursor: pointer;
}

.auth-tabs button.active {
  background: #212529;
  color: #fff;
}

.auth-modal form {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}

.auth-modal input {
  padding: 0.55rem 0.7rem;
  font-size: 0.95rem;
}

.auth-notice {
  font-size: 0.75rem;
  color: #868e96;
  margin: 0;
  line-height: 1.5;
}

.auth-error {
  font-size: 0.8rem;
  color: #c92a2a;
  margin: 0;
}
```

(App.css 의 기존 header 가 `position: relative` 가 아니면 `header { position: relative; }` 도 추가 — .auth-corner 기준점)

- [ ] **Step 4: App.jsx 통합**

import 에 추가:

```jsx
import AuthModal from './components/AuthModal'
import { fetchMe, getToken, clearToken } from './api'
```

App() 상태 추가 (기존 useState 들 옆):

```jsx
  const [user, setUser] = useState(null)         // {username} | null
  const [authOpen, setAuthOpen] = useState(false)
```

마운트 시 토큰 검증 (pingHealth useEffect 옆에 추가):

```jsx
  useEffect(() => {
    if (!getToken()) return
    fetchMe().then(me => setUser(me)).catch(() => clearToken())
  }, [])

  function logout() { clearToken(); setUser(null) }
```

header JSX(98-103행)를:

```jsx
      <header>
        <h1>국회 회의록 RAG</h1>
        <p className="subtitle">
          국회 회의록을 근거로 정책 의제, 행위자, 쟁점, 입장 차이, 시계열 흐름을 분석하는 GovTech RAG 서비스
        </p>
        <div className="auth-corner">
          {user ? (
            <>
              <span>{user.username}님</span>
              <button type="button" onClick={logout}>로그아웃</button>
            </>
          ) : (
            <button type="button" onClick={() => setAuthOpen(true)}>로그인 / 가입</button>
          )}
        </div>
      </header>
```

컴포넌트 끝(footer 위)에 모달 마운트:

```jsx
      {authOpen && (
        <AuthModal onClose={() => setAuthOpen(false)} onSuccess={name => setUser({ username: name })} />
      )}
```

- [ ] **Step 5: 검증 — vitest + 브라우저**

Run: `cd frontend && npx vitest run`
Expected: 기존 5 passed (회귀 없음)

브라우저(HMR): 헤더 우측 "로그인 / 가입" → 모달 → 가입(데모 문구 확인) → 헤더에
"아이디님 · 로그아웃" → 새로고침 후에도 로그인 유지(fetchMe) → 로그아웃 동작.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.js frontend/src/components/AuthModal.jsx frontend/src/App.jsx frontend/src/App.css
git commit -m "feat(ui): 로그인/가입 모달 + 헤더 인증 상태 + 토큰 자동 첨부

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 프론트 — 내 질문 기록 패널

**Files:**
- Create: `frontend/src/components/MyQueries.jsx`
- Modify: `frontend/src/App.jsx` (질의 탭에 패널 삽입)

**Interfaces:**
- Consumes: api.js `fetchMyQueries()`, App 의 `user` 상태·`setQuestion`
- Produces: `<MyQueries user={user} onPick={q => setQuestion(q)} />`

- [ ] **Step 1: MyQueries 컴포넌트 생성**

`frontend/src/components/MyQueries.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { fetchMyQueries } from '../api'

const GROUNDING_KO = { FULL: '근거 충분', PARTIAL: '부분 근거', REFUSED: '답변 보류', NONE: '근거 없음' }

// 내 질문 기록 (로그인 시 질의 탭) — 클릭하면 입력창에 채워 재실행을 유도한다.
// 저장된 답변 재표시는 범위 밖 (spec).
export default function MyQueries({ user, onPick }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    if (!open || items !== null) return
    fetchMyQueries().then(d => setItems(d.queries)).catch(e => setErr(e.message))
  }, [open, items])

  // 다른 계정으로 바뀌면 캐시 무효화
  useEffect(() => { setItems(null); setErr(null) }, [user?.username])

  if (!user) return null
  return (
    <div style={{ margin: '10px 0' }}>
      <button type="button" onClick={() => setOpen(!open)}
              style={{ fontSize: 13, padding: '4px 10px', cursor: 'pointer' }}>
        {open ? '▾' : '▸'} 내 질문 기록
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          {err && <p style={{ fontSize: 13, color: '#c92a2a' }}>{err}</p>}
          {items && items.length === 0 && (
            <p style={{ fontSize: 13, color: '#868e96' }}>아직 기록이 없습니다 — 질문하면 자동으로 저장됩니다.</p>
          )}
          {items && items.length > 0 && (
            <ul style={{ margin: 0, padding: '0 0 0 18px', maxWidth: 720 }}>
              {items.map(q => (
                <li key={q.query_id} style={{ fontSize: 14, margin: '5px 0' }}>
                  <button type="button" onClick={() => onPick(q.question)}
                          title="클릭하면 입력창에 채워집니다"
                          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer',
                                   fontSize: 14, fontFamily: 'inherit', color: '#1c7ed6', textAlign: 'left' }}>
                    {q.question}
                  </button>
                  <span style={{ color: '#868e96', fontSize: 12, marginLeft: 8 }}>
                    {q.created_at.slice(0, 10)} · {GROUNDING_KO[q.grounding] || q.grounding}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: App.jsx 질의 탭에 삽입**

import 추가:

```jsx
import MyQueries from './components/MyQueries'
```

QueryForm 바로 아래(145행 부근, example-chips 위)에:

```jsx
            <MyQueries user={user} onPick={q => setQuestion(q)} />
```

- [ ] **Step 3: 검증 — 브라우저 수동 확인**

로그인 상태에서: 질의 1회 실행 → "내 질문 기록" 펼침 → 방금 질문이 날짜·판정과 함께
보임 → 클릭 → 입력창에 채워짐. 로그아웃하면 패널 자체가 사라짐.
콘솔 에러 0건 확인 (`read_console_messages` 또는 브라우저 개발자 도구).

- [ ] **Step 4: vitest 회귀**

Run: `cd frontend && npx vitest run`
Expected: 기존 5 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/MyQueries.jsx frontend/src/App.jsx
git commit -m "feat(ui): 내 질문 기록 패널 — 클릭 시 입력창 채움

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 문서화 + 최종 검증

**Files:**
- Modify: `db/schema.sql` (끝에 users DDL 문서화)
- Modify: `README.md` (배포 런북 env 목록에 JWT_SECRET 1줄)
- Modify: `docs/progress.md` (구현 기록)

**Interfaces:**
- Consumes: 전 태스크 완료 상태

- [ ] **Step 1: schema.sql 문서화** (파일 끝에 추가)

```sql

-- 12. 회원 (2026-07-15 spec). 이메일 등 개인정보 없음 — 아이디+bcrypt 해시만.
--     backend/auth.py ensure_schema 가 시작 시 자가 생성 (query_logs.user_id 포함) —
--     이 정의는 문서화 목적.
CREATE TABLE IF NOT EXISTS users (
  user_id       SERIAL PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,   -- 영문·숫자·한글 2~20자
  password_hash TEXT NOT NULL,          -- bcrypt (원문은 어디에도 저장하지 않음)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS user_id INT REFERENCES users(user_id);
```

- [ ] **Step 2: README 런북 갱신**

README 의 "공개 배포 (4단계-B 런북)" 섹션에서 Render 환경변수 목록을 찾아
(`grep -n "JWT_SECRET\|환경변수\|DATABASE_URL" README.md` 로 위치 확인) 한 줄 추가:

```
- `JWT_SECRET`: 회원 토큰 서명 키 — 임의의 긴 문자열 (예: `python -c "import secrets; print(secrets.token_hex(32))"`)
```

- [ ] **Step 3: progress.md 기록** (최신 섹션에 추가 — 기존 기록 형식을 따라 5~8줄)

내용에 반드시 포함: 스펙 링크, 아이디+bcrypt+JWT 직접 구현(이메일 없음 = 개인정보 0),
query_logs.user_id 재사용, /auth/* 강한 한도 편입, 무효 토큰 익명 통과 원칙,
테스트 수, 배포 영향(JWT_SECRET env 1줄).

- [ ] **Step 4: 최종 검증**

```bash
python -m pytest -q          # 전체 백엔드 (기존 95 + 신규 ~11)
cd frontend && npx vitest run  # 프론트 5
```

Expected: 전부 PASS.

백엔드 재시작(포트 8000 프로세스 kill 후 재기동) 후 브라우저 스모크:
가입 → 질의 → 내 기록 확인 → 로그아웃 → 로그인 → 기록 유지.

- [ ] **Step 5: Commit + push**

```bash
git add db/schema.sql README.md docs/progress.md
git commit -m "docs: 회원가입+히스토리 스키마·런북(JWT_SECRET)·진행 기록

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push
```
