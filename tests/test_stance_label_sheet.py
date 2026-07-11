"""POL-7 블라인드 시트 생성 순수 로직 테스트 — DB 없이 실행."""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stance_label_sheet import sample_turns, render_sheet  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_sample_turns():
    rows = [(f"t{i:03d}", "sp", "role", "2024-06-13", "text") for i in range(100)]
    a = sample_turns(rows, n=40, seed=42)
    b = sample_turns(rows, n=40, seed=42)
    check("40건", len(a) == 40, len(a))
    check("재현성(같은 seed 같은 결과)", a == b)
    check("turn_id 정렬", [r[0] for r in a] == sorted(r[0] for r in a))
    small = [(f"t{i}", "sp", "r", "d", "x") for i in range(5)]
    check("표본<n 이면 전체", len(sample_turns(small, n=40)) == 5)


def test_render_sheet_is_blind():
    picked = [("복지위_A_turn_0001", "이주영 위원", "위원", "2024-06-13",
               "지지 발언 원문")]
    md = render_sheet("medical-reform", picked, total=212)
    check("turn_id 출력", "복지위_A_turn_0001" in md)
    check("입장 빈칸 존재", "입장:" in md)
    check("rubric support 정의 포함", "support:" in md)
    check("표본/전체 표기", "212" in md)
    # 블라인드: 시트 텍스트에 판정 stance 토큰이 라벨 문맥으로 새지 않아야 함
    check("판정 라벨 문자열 미노출", "판정:" not in md)


if __name__ == "__main__":
    test_sample_turns()
    test_render_sheet_is_blind()
    print("all passed")
