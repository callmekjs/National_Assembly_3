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
