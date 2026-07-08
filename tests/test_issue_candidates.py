"""이슈 후보 탐사 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_candidates.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from issue_candidates import detect_spikes, parse_topics, top_agenda_lines  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_detect_spikes_basic():
    # 국방위 2024-12 가 중앙값(100)의 3배 — 스파이크로 잡혀야 한다 (계엄 자기 검증과 동일 구조)
    rows = [("국방위", "2024-10", 100), ("국방위", "2024-11", 100),
            ("국방위", "2024-12", 300), ("국방위", "2025-01", 110)]
    spikes = detect_spikes(rows, ratio=1.8)
    check("스파이크 1건", len(spikes) == 1, spikes)
    check("월 식별", spikes[0]["month"] == "2024-12", spikes[0])
    check("배율 계산", spikes[0]["ratio"] == 3.0, spikes[0])


def test_detect_spikes_below_ratio():
    rows = [("과방위", "2024-10", 100), ("과방위", "2024-11", 100),
            ("과방위", "2024-12", 150)]
    check("1.8배 미만은 비스파이크", detect_spikes(rows, ratio=1.8) == [])


def test_detect_spikes_needs_three_months():
    # 월 2개뿐이면 중앙값이 무의미 — 판단 불가로 제외
    rows = [("기재위", "2026-05", 10), ("기재위", "2026-06", 100)]
    check("월 3 미만 위원회 제외", detect_spikes(rows) == [])


def test_top_agenda_lines():
    lines = ["  방송법 일부개정법률안  ", "방송법 일부개정법률안", "인사청문요청안",
             "짧다", "방송법 일부개정법률안"]
    top = top_agenda_lines(lines, top_n=2)
    check("빈도 1위", top[0] == ("방송법 일부개정법률안", 3), top)
    check("8자 미만 제외", all("짧다" != t[0] for t in top), top)


def test_parse_topics():
    check("정상", parse_topics('{"topics": ["AI 기본법", "계엄"]}') == ["AI 기본법", "계엄"])
    check("topics 아님", parse_topics('{"other": 1}') == [])
    check("JSON 아님", parse_topics("주제: 계엄") == [])
    check("문자열 아닌 항목 걸러냄", parse_topics('{"topics": ["a", 3]}') == ["a"])


if __name__ == "__main__":
    for fn in [test_detect_spikes_basic, test_detect_spikes_below_ratio,
               test_detect_spikes_needs_three_months, test_top_agenda_lines, test_parse_topics]:
        fn()
    print("ALL PASS")
