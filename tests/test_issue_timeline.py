"""POL-4 타임라인 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_timeline.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import build_keyword_patterns, merge_months  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_build_keyword_patterns():
    check("기본 패턴", build_keyword_patterns(["계엄"]) == ["%계엄%"])
    check("% 이스케이프", build_keyword_patterns(["50%"]) == ["%50\\%%"])
    check("_ 이스케이프", build_keyword_patterns(["a_b"]) == ["%a\\_b%"])
    check("빈 입력", build_keyword_patterns([]) == [])


def test_merge_months_basic():
    corpus = {"2024-12": 1478, "2025-01": 241}
    mapped = {"2024-12": (166, 75), "2025-01": (24, 15)}
    out = merge_months(corpus, mapped)
    check("2개월 정렬", [m["month"] for m in out] == ["2024-12", "2025-01"], out)
    check("첫 달 값", out[0] == {"month": "2024-12", "corpus_turns": 1478,
                                 "mapped_turns": 166, "mapped_core_turns": 75}, out[0])


def test_merge_months_gap_fill():
    # 2024-12 와 2025-03 사이 1·2월은 빈 달 → 0 으로 채움
    out = merge_months({"2024-12": 10, "2025-03": 5}, {})
    months = [m["month"] for m in out]
    check("갭 채움", months == ["2024-12", "2025-01", "2025-02", "2025-03"], months)
    check("빈 달 0", out[1] == {"month": "2025-01", "corpus_turns": 0,
                                "mapped_turns": 0, "mapped_core_turns": 0}, out[1])


def test_merge_months_one_sided_and_empty():
    # 매핑만 있는 달(코퍼스 0), 코퍼스만 있는 달(매핑 0) 이 합집합 범위에 포함
    out = merge_months({"2025-02": 3}, {"2024-12": (2, 1)})
    check("합집합 범위", [m["month"] for m in out] == ["2024-12", "2025-01", "2025-02"], out)
    check("매핑만 달", out[0] == {"month": "2024-12", "corpus_turns": 0,
                                  "mapped_turns": 2, "mapped_core_turns": 1}, out[0])
    check("코퍼스만 달", out[2] == {"month": "2025-02", "corpus_turns": 3,
                                    "mapped_turns": 0, "mapped_core_turns": 0}, out[2])
    check("양쪽 빈 입력", merge_months({}, {}) == [])


if __name__ == "__main__":
    test_build_keyword_patterns()
    test_merge_months_basic()
    test_merge_months_gap_fill()
    test_merge_months_one_sided_and_empty()
    print("all passed")
