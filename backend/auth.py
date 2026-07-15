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
