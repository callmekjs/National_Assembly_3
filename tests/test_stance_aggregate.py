"""POL-5 행위자 집계 규칙 순수 테스트.
실행: python tests/test_stance_aggregate.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import aggregate_stances  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def rows(*stances):
    return [{"stance": s} for s in stances]


def test_aggregate_stances():
    check("입장 발언 0 → no_stance", aggregate_stances(rows("neutral", "none")) == "no_stance")
    check("빈 목록 → no_stance", aggregate_stances([]) == "no_stance")
    check("단일 support", aggregate_stances(rows("support", "support", "neutral")) == "support")
    check("concern 대표 가능", aggregate_stances(rows("concern", "concern", "support")) == "concern")
    check("혼재(각 ⅓ 이상)", aggregate_stances(rows("support", "support", "oppose", "oppose")) == "mixed")
    check("혼재 아님(oppose 1/5 미만)", aggregate_stances(rows("support", "support", "support", "support", "oppose")) == "support")


if __name__ == "__main__":
    test_aggregate_stances()
    print("all passed")
