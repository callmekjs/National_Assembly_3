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
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from dotenv import load_dotenv

logger = logging.getLogger("uvicorn.error")

# main.py 의 import 순서상 (import auth 가 from db import ... 보다 먼저 실행되어)
# db.py 의 load_dotenv 가 아직 평가되지 않은 시점에 이 모듈이 로드될 수 있다.
# load_dotenv 는 멱등이므로 여기서도 동일하게 호출해 JWT_SECRET 이 .env 에서
# 조용히 무시되는 것을 막는다.
load_dotenv(Path(__file__).parent.parent / ".env")

# HS256 의 해시 블록 크기 — RFC 7518 §3.2 가 이보다 짧은 키를 쓰지 말라고 못박는다
_MIN_SECRET_BYTES = 32

JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    # 고정 문자열 기본값("dev-secret-not-for-production")을 쓰던 자리 (감사 2026-08-05).
    # 이 저장소는 공개라 그 값을 누구나 읽을 수 있었다 — 배포에서 JWT_SECRET 을 한 번만
    # 빠뜨리면 임의 user_id 의 토큰을 위조해 /me/queries 로 남의 질의 이력을 열 수 있다.
    # 경고 로그는 사람이 놓칠 수 있으므로 방어를 코드로 옮긴다: 프로세스마다 무작위 키를
    # 생성하면 위조가 구조적으로 불가능하다. 대가는 재기동 시 기존 토큰 전부 무효(재로그인)
    # — 보안 구멍을 UX 불편으로 바꾸는 교환이고, 배포에서 JWT_SECRET 을 설정하면
    # 재기동 내구성까지 얻는다. fail-safe: 설정을 잊어도 안전한 쪽으로 실패한다.
    JWT_SECRET = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET 미설정 — 이번 프로세스 한정 무작위 키 생성. "
        "재기동 시 발급된 토큰이 모두 무효가 된다 (배포에서는 반드시 설정)"
    )
elif len(JWT_SECRET.encode()) < _MIN_SECRET_BYTES:
    # 짧은 키를 조용히 받아들이면, 미설정보다 **나쁜** 상태가 된다 — 자동 생성 키는
    # 64자라 안전한데 사람이 "mysecret" 같은 것을 넣으면 무차별 대입에 노출되면서도
    # 아무 신호가 없다. HS256 은 키가 해시 블록(32바이트)보다 짧으면 강도가 그만큼
    # 떨어진다 (RFC 7518 §3.2). PyJWT 2.13 이 이 경우 경고를 내기 시작해 드러났다.
    # 거절하지 않고 생성 키로 대체한다 — 배포가 뜨긴 뜨되(가용성) 약한 키로는 돌지
    # 않게 하는 fail-safe. 대가는 재기동 시 토큰 무효이며, 로그가 그 이유를 밝힌다.
    logger.error(
        "JWT_SECRET 이 %d바이트로 너무 짧습니다 (최소 %d). 무작위 키로 대체합니다 — "
        "재기동 시 토큰이 무효가 됩니다. 생성: "
        'python -c "import secrets; print(secrets.token_hex(32))"',
        len(JWT_SECRET.encode()), _MIN_SECRET_BYTES,
    )
    JWT_SECRET = secrets.token_hex(32)

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


# 타이밍 부채널 방지 — 없는 아이디도 bcrypt 1회 수행해 응답 시간을 균일화
# (없으면 없는 아이디는 즉시 401 → 응답 시간으로 계정 존재 여부가 샌다).
# 모듈 로드 시 1회 생성 — 앱 시작에 bcrypt 1회(~100ms) 비용이 추가된다.
DUMMY_HASH = hash_password("timing-equalizer-dummy")


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
        # 검증층 스프린트(2026-07-25)가 db/schema.sql 에만 verification 컬럼을 추가하고
        # 런타임 자가 마이그레이션을 빠뜨렸다 — user_id 전례를 그대로 따라 여기 추가
        # (최종 리뷰 F7, 2026-07-26). schema.sql 만 실행되는 jsonl_to_postgres.py·
        # make_deploy_corpus.py 경로 밖(백엔드만 재배포된 기존 DB)에서도 컬럼이
        # 보장되어야 main.py 의 무조건 INSERT(verification 포함)가 죽지 않는다.
        cur.execute(
            "ALTER TABLE query_logs ADD COLUMN IF NOT EXISTS verification JSONB"
        )
        conn.commit()
