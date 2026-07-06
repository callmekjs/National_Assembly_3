"""
actors 모듈(POL-2) 단위 테스트 — DB 없이 순수 로직만 검증.

검사 항목:
    1. 기관 별칭 정규화 (canonical_org — 금융위/금융위원회 → 같은 정식명)
    2. 여야 이력 생성 (정권 구간별 라벨, 미등록 인물은 빈 이력)

실행: python tests/test_actors.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import party  # noqa: E402
from party import _build_map  # noqa: E402
from actors import build_party_history, canonical_org  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"  # pytest 에서도 실패가 실패로 잡히게


def test_canonical_org():
    check("정규화: 금융위와 금융위원회가 같은 정식명으로",
          canonical_org("금융위") == canonical_org("금융위원회") == "금융위원회")
    check("정규화: 과기부 → 과학기술정보통신부",
          canonical_org("과기부") == "과학기술정보통신부", canonical_org("과기부"))
    check("정규화: 별칭 없는 기관은 그대로", canonical_org("경찰청") == "경찰청")


def test_party_history():
    party._party_map = _build_map([
        ("김병주", "金炳周", "더불어민주당"),
        ("윤종오", None, "무소속"),
    ])

    p, hist = build_party_history("김병주")
    check("이력: 정당 반환", p == "더불어민주당")
    check("이력: 정권 구간 2개", len(hist) == 2, hist)
    check("이력: 교체 전 야당", hist[0]["label"] == "더불어민주당(당시 야당)", hist[0])
    check("이력: 교체 후 여당", hist[1]["label"] == "더불어민주당(당시 여당)", hist[1])
    check("이력: 구간 표기", hist[0]["period"].startswith("2024-05-30 ~ 2025-06-03"), hist[0])

    p, hist = build_party_history("조태열")
    check("이력: 미등록 인물은 (None, [])", p is None and hist == [])

    p, hist = build_party_history("윤종오")
    check("이력: 무소속은 여야 없이 표기", p == "무소속" and all(h["label"] == "무소속" for h in hist), hist)


def main():
    test_canonical_org()
    test_party_history()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
