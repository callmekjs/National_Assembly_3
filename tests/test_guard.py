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
