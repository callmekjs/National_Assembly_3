"""배포 코퍼스 생성기 순수 로직 테스트 — DB 없이 실행.
실행: python tests/test_deploy_corpus.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from make_deploy_corpus import (  # noqa: E402
    SIZE_CALIBRATION, estimate_mb, expand_neighbor_turn_ids)


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_expand_neighbor_turn_ids():
    r = expand_neighbor_turn_ids({"복지위_20240613_52087_52087_turn_0047"})
    check("원본 포함", "복지위_20240613_52087_52087_turn_0047" in r)
    check("이전 turn (자릿수 보존)", "복지위_20240613_52087_52087_turn_0046" in r, r)
    check("다음 turn", "복지위_20240613_52087_52087_turn_0048" in r, r)
    check("3개 정확히", len(r) == 3, r)
    r0 = expand_neighbor_turn_ids({"A_turn_0001"})
    check("첫 turn(0001)은 이전 없음 — answer.py 동일", r0 == {"A_turn_0001", "A_turn_0002"}, r0)
    check("패턴 밖 id 는 그대로", expand_neighbor_turn_ids({"weird"}) == {"weird"})
    # 인접끼리 겹치면 합집합
    r2 = expand_neighbor_turn_ids({"A_turn_0001", "A_turn_0002"})
    check("겹침 합집합 (0000 미생성)", r2 == {"A_turn_0001", "A_turn_0002", "A_turn_0003"}, r2)


def test_estimate_mb():
    # 실측 상수: 청크 2.3KB + 임베딩(인덱스 포함) 21.0KB → 행당 23.3KB
    check("인덱스 포함 추정", abs(estimate_mb(10_000, with_index=True) - 10_000 * 23.3 / 1024) < 0.01)
    check("인덱스 생략 추정", abs(estimate_mb(10_000, with_index=False) - 10_000 * 8.8 / 1024) < 0.01)

    # 보정계수 (2026-08-07): 추정식은 실측보다 20% 크게 나온다. 보정을 켜지 않으면
    # 쓸 수 있는 용량을 남기고 버린다 — 실제로 그 때문에 인접 turn 이 통째로 빠졌다.
    raw = estimate_mb(10_000, with_index=True)
    cal = estimate_mb(10_000, with_index=True, calibrated=True)
    check("보정계수는 추정을 줄인다", cal < raw, (cal, raw))
    check("보정계수 값 일치", abs(cal - raw * SIZE_CALIBRATION) < 0.01, (cal, raw))
    check("기본값은 보정 안 함 (기존 계약 유지)", estimate_mb(10_000, True) == raw)


def test_expand_neighbor_window():
    """±N 창 — 근거 회수율을 올리려면 창을 넓혀야 한다(2026-08-07 실측)."""
    seed = {"A_turn_0010"}
    r1 = expand_neighbor_turn_ids(seed, 1)
    check("k=1 은 기존과 동일 (3개)", r1 == {"A_turn_0009", "A_turn_0010", "A_turn_0011"}, r1)

    r3 = expand_neighbor_turn_ids(seed, 3)
    check("k=3 은 7개 (±3)", len(r3) == 7, sorted(r3))
    check("k=3 경계 포함", {"A_turn_0007", "A_turn_0013"} <= r3, sorted(r3))

    # 경계: 창이 1번 turn 을 넘어가도 0 이하를 만들지 않는다
    edge = expand_neighbor_turn_ids({"A_turn_0002"}, 5)
    check("0 이하 turn 미생성", all(not t.endswith("_0000") for t in edge), sorted(edge))
    check("1번 turn 은 포함", "A_turn_0001" in edge, sorted(edge))
    check("경계에서 개수는 1..7", len(edge) == 7, sorted(edge))

    check("k 기본값은 1 (호출부 계약 유지)",
          expand_neighbor_turn_ids(seed) == r1)
    check("패턴 밖 id 는 k 와 무관하게 그대로",
          expand_neighbor_turn_ids({"weird"}, 5) == {"weird"})



if __name__ == "__main__":
    test_expand_neighbor_turn_ids()
    test_estimate_mb()
    test_expand_neighbor_window()
    print("all passed")
