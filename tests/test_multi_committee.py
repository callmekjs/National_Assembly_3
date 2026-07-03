"""
복수 위원회 질문 처리 단위 테스트 (2026-07-03) — LLM·DB 없이 로직만 검증.

배경 (실측): "외교위와 국방위에서 오물풍선을 어떻게 다뤘나" →
  ① "외교위"가 미등록 표기라 인식 실패
  ② "국방위"만 필터로 잡혀 외통위 근거가 검색에서 원천 배제
  → 데이터에 있는 걸 "확인할 수 없다"고 답하는 거짓 부정 발생

검사 항목:
    1. 위원회 통용 별칭 (외교위→외통위 등)
    2. 복수 위원회 전부 감지 (findall, 순서 유지·중복 제거)
    3. 위원회별 근거 균형 배분 (_balance_by_committee)

실행: python tests/test_multi_committee.py
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from query_parser import extract_filters  # noqa: E402
from search_hybrid import _balance_by_committee  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    if not cond:
        FAILURES.append(name)


def committees_of(q: str):
    return extract_filters(q)[1]


def test_aliases():
    check("별칭: 외교위 → 외통위", committees_of("외교위에서 북핵 논의") == ["외통위"])
    check("별칭: 외교위원회 → 외통위", committees_of("외교위원회의 논의") == ["외통위"])
    check("별칭: 산자위 → 산자중기위", committees_of("산자위 티메프 논의") == ["산자중기위"])
    check("별칭: 국토교통위 → 국토위", committees_of("국토교통위 전세사기") == ["국토위"])
    check("별칭: 기존 정식명 유지", committees_of("외교통일위원회의 논의") == ["외통위"])
    check("별칭: 위원회 없으면 None", committees_of("티메프 사태 피해자 구제") is None)


def test_multi_detection():
    got = committees_of("외통위와 국방위의 북핵 논의 비교")
    check("복수: 2개 모두 감지 (순서 유지)", got == ["외통위", "국방위"], got)

    got = committees_of("외교위와 국방위에서 오물풍선을 어떻게 다뤘는지")
    check("복수: 별칭+정식 혼합 감지 (실측 사례)", got == ["외통위", "국방위"], got)

    got = committees_of("외통위, 정무위, 국토위에서 부동산 관련 논의")
    check("복수: 3개 감지", got == ["외통위", "정무위", "국토위"], got)

    got = committees_of("외통위와 외교통일위원회 논의")  # 같은 위원회 두 표기
    check("복수: 같은 위원회 중복 제거", got == ["외통위"], got)


def hit(cid, committee, rrf):
    return {"chunk_id": cid, "committee": committee, "rrf": rrf}


def test_balance():
    # 외통위가 상위 독식하는 상황: 상위 7개 외통위, 하위 3개 국방위
    ranked = [hit(f"a{i}", "외통위", 1.0 - i * 0.01) for i in range(7)] + \
             [hit(f"b{i}", "국방위", 0.5 - i * 0.01) for i in range(3)]

    out = _balance_by_committee(ranked, ["외통위", "국방위"], 10)
    check("균형: 전체 10개면 그대로 다 포함", len(out) == 10)

    out = _balance_by_committee(ranked, ["외통위", "국방위"], 6)
    from collections import Counter
    dist = Counter(e["committee"] for e in out)
    check("균형: limit 6 → 각 3개씩", dist == {"외통위": 3, "국방위": 3}, dict(dist))

    # 한쪽 위원회 근거 부족 시 다른 쪽이 자리를 넘겨받음
    ranked2 = [hit(f"a{i}", "외통위", 1.0 - i * 0.01) for i in range(8)] + [hit("b0", "국방위", 0.5)]
    out = _balance_by_committee(ranked2, ["외통위", "국방위"], 6)
    dist = Counter(e["committee"] for e in out)
    check("균형: 부족분은 다른 위원회가 채움 (5+1)", dist == {"외통위": 5, "국방위": 1}, dict(dist))

    # 3개 위원회, limit 10 → quota 3
    ranked3 = ([hit(f"a{i}", "외통위", 0.9 - i * 0.01) for i in range(5)]
               + [hit(f"b{i}", "정무위", 0.8 - i * 0.01) for i in range(5)]
               + [hit(f"c{i}", "국토위", 0.7 - i * 0.01) for i in range(5)])
    out = _balance_by_committee(ranked3, ["외통위", "정무위", "국토위"], 10)
    dist = Counter(e["committee"] for e in out)
    check("균형: 3개 위원회 최소 quota 3 보장",
          all(dist[c] >= 3 for c in ("외통위", "정무위", "국토위")), dict(dist))

    out = _balance_by_committee(ranked, ["외통위", "국방위"], 6)
    check("균형: 출력은 rrf 내림차순 유지",
          all(out[i]["rrf"] >= out[i + 1]["rrf"] for i in range(len(out) - 1)))


def main():
    test_aliases()
    test_multi_detection()
    test_balance()

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
