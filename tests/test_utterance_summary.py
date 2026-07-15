"""
utterance_summary(최근 발언 한 줄 요약) 단위 테스트 — DB·LLM 없이 파서만 검증.

검사 항목: index-keyed 출력 파싱 (POL-5 교훈 — 배치 다항목은 번호 매칭이 생명선)

실행: python tests/test_utterance_summary.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from utterance_summary import parse_indexed  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_parse_indexed_basic():
    text = "1. 재외국민 보호 예산 자료 제출 거부 지적\n2) 외교부 사고방식 전환 촉구"
    got = parse_indexed(text, 2)
    check("번호점·번호괄호 둘 다 파싱", got == {
        1: "재외국민 보호 예산 자료 제출 거부 지적",
        2: "외교부 사고방식 전환 촉구",
    }, got)


def test_parse_indexed_defenses():
    # 범위 밖 번호·서두 잡담·빈 요약은 버리고, 확보된 항목만 반환
    text = "다음은 요약입니다.\n0. 범위 밖\n1. 유효한 요약\n5. 범위 밖\n2. "
    got = parse_indexed(text, 3)
    check("범위 밖·빈 요약 방어", got == {1: "유효한 요약"}, got)


def test_parse_indexed_empty():
    check("빈 응답은 빈 dict", parse_indexed("", 3) == {})


if __name__ == "__main__":
    test_parse_indexed_basic()
    test_parse_indexed_defenses()
    test_parse_indexed_empty()
    print("전체 통과")
