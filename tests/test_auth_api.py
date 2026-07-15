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

    import auth
    import main
    # TestClient(app) 는 "with" 없이 쓰면 lifespan(startup) 을 실행하지 않는다
    # (test_api.py 가 db.init_pool() 을 직접 호출하는 것과 같은 이유) —
    # auth.ensure_schema() 를 여기서 직접 호출해 users 테이블을 준비한다.
    auth.ensure_schema()
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


if __name__ == "__main__":
    test_signup_success_and_hash()
    test_signup_duplicate_and_format()
    test_login_and_uniform_failure()
    test_me()
    test_query_records_user_id()
    test_me_queries()
    print("전체 통과")
