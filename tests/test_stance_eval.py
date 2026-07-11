"""POL-7 입장 eval 순수 로직 테스트 — DB·LLM 없이 실행.
실행: python tests/test_stance_eval.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from stance_eval import parse_label_sheet, agreement  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


SHEET = """# 입장 블라인드 라벨 — medical-reform

> 안내문...
> - support: 지지

- `복지위_A_turn_0001` (2024-06-13) 이주영 위원
      입장: support
      안녕하십니까 발언 전문 ...

- `복지위_A_turn_0002` (2024-06-19) 김윤 위원
      입장: oppose
      의대 증원에 반대하는 ...

- `복지위_A_turn_0003` (2024-06-26) 서영석 위원
      입장:
      발언 전문 (미기입) ...

- `복지위_A_turn_0004` (2024-06-26) 안상훈 위원
      입장: supprt
      오타 토큰 발언 ...
"""


def test_parse_label_sheet():
    r = parse_label_sheet(SHEET)
    check("정상 2건 추출", r.get("복지위_A_turn_0001") == "support" and r.get("복지위_A_turn_0002") == "oppose", r)
    check("빈칸 제외", "복지위_A_turn_0003" not in r, r)
    check("허용밖 토큰 제외", "복지위_A_turn_0004" not in r, r)
    check("총 2건만", len(r) == 2, r)


def test_agreement():
    # 완전 일치
    h = {"t1": "support", "t2": "oppose"}
    r = agreement(h, dict(h))
    check("완전 일치 1.0", r["agreement"] == 1.0 and r["n"] == 2, r)
    check("혼동행렬 대각 카운트", r["matrix"]["support"]["support"] == 1 and r["matrix"]["oppose"]["oppose"] == 1, r)
    check("불일치 없음", r["disagreements"] == [], r)

    # 부분 일치 — 공통 t1(일치)·t2(불일치), t3 는 사람만 → 제외
    human = {"t1": "support", "t2": "concern", "t3": "none"}
    llm = {"t1": "support", "t2": "oppose"}
    r2 = agreement(human, llm)
    check("공통 2건", r2["n"] == 2, r2)
    check("일치율 0.5", r2["agreement"] == 0.5, r2)
    check("혼동 concern→oppose", r2["matrix"]["concern"]["oppose"] == 1, r2)
    check("불일치 1건 t2", [d["turn_id"] for d in r2["disagreements"]] == ["t2"], r2)

    # 공통 0건 방어
    r3 = agreement({"a": "support"}, {"b": "oppose"})
    check("공통 0건 agreement None", r3["n"] == 0 and r3["agreement"] is None, r3)


if __name__ == "__main__":
    test_parse_label_sheet()
    test_agreement()
    print("all passed")
