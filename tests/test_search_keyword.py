"""search_keyword 순수 로직 테스트 — DB 없이 패턴 생성만 검증.

실행: python tests/test_search_keyword.py  (pytest 도 지원)
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from search_keyword import _like_escape, _pat, _terms_from_query  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_like_escape():
    check("이스케이프: % 는 리터럴로", _like_escape("50%") == "50\\%", _like_escape("50%"))
    check("이스케이프: _ 는 리터럴로", _like_escape("a_b") == "a\\_b", _like_escape("a_b"))
    check("이스케이프: 백슬래시 자체", _like_escape("a\\b") == "a\\\\b", _like_escape("a\\b"))
    check("이스케이프: 일반 한국어는 그대로", _like_escape("인공지능") == "인공지능")


def test_pat():
    got = _pat("지분 50%")
    check("패턴: 양끝 % 만 와일드카드", got == "%지분 50\\%%", got)
    check("패턴: 특수문자 없는 토큰", _pat("티메프") == "%티메프%")


def test_terms_from_query():
    phrases, tokens = _terms_from_query("AI 기본법 논의")
    check("토큰화: 구문 후보 존재", len(phrases) >= 1, phrases)
    check("토큰화: 토큰 상한 8", len(tokens) <= 8, tokens)


def main():
    test_like_escape()
    test_pat()
    test_terms_from_query()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
