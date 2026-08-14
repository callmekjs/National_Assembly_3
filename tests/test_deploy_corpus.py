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
    CHUNK_ROW_KB, EMB_ROW_KB_INDEXED, EMB_ROW_KB_RAW,
    SIZE_CALIBRATION_INDEXED, build_target_turn_ids, estimate_mb,
    expand_neighbor_turn_ids)


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
    # 행단가 상수는 재측정될 수 있다. 그래서 숫자를 박아 두지 않고 **조합 규칙**을
    # 고정한다 — 상수를 갱신했을 때 깨져야 하는 것은 이 테스트가 아니라 한도 판단이다.
    for n in (1, 10_000):
        want_i = n * (CHUNK_ROW_KB + EMB_ROW_KB_INDEXED) / 1024
        want_r = n * (CHUNK_ROW_KB + EMB_ROW_KB_RAW) / 1024
        check(f"인덱스 포함 추정 (n={n})", abs(estimate_mb(n, True) - want_i) < 1e-9)
        check(f"인덱스 생략 추정 (n={n})", abs(estimate_mb(n, False) - want_r) < 1e-9)
    check("인덱스가 있으면 더 크다", estimate_mb(10_000, True) > estimate_mb(10_000, False))

    # 보정은 인덱스 있는 경우에만 적용한다. 2026-08-07 에 이 계수를 인덱스 없는
    # 경우에 그대로 써서 362MB 로 예상하고 실제 504MB 를 만들어 한도를 넘겼다.
    raw_i = estimate_mb(10_000, with_index=True)
    cal_i = estimate_mb(10_000, with_index=True, calibrated=True)
    check("인덱스 O: 보정이 추정을 줄인다", cal_i < raw_i, (cal_i, raw_i))
    check("인덱스 O: 보정 값 일치",
          abs(cal_i - raw_i * SIZE_CALIBRATION_INDEXED) < 0.01, (cal_i, raw_i))

    raw_n = estimate_mb(10_000, with_index=False)
    cal_n = estimate_mb(10_000, with_index=False, calibrated=True)
    check("인덱스 X: 보정해도 줄지 않는다 (이미 실측 단가)", cal_n == raw_n, (cal_n, raw_n))
    check("기본값은 보정 안 함 (기존 계약 유지)", estimate_mb(10_000, True) == raw_i)


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


def test_priority_turns_are_included_without_extra_neighbors():
    selected = build_target_turn_ids(
        {"A_turn_0010"}, {"B_turn_0020"}, neighbors=1
    )
    check(
        "이슈 turn은 ±1 포함",
        {"A_turn_0009", "A_turn_0010", "A_turn_0011"} <= selected,
        selected,
    )
    check("우선 직책 turn 직접 포함", "B_turn_0020" in selected, selected)
    check("우선 직책 주변은 불필요하게 포함하지 않음",
          "B_turn_0019" not in selected and "B_turn_0021" not in selected, selected)



if __name__ == "__main__":
    test_expand_neighbor_turn_ids()
    test_estimate_mb()
    test_expand_neighbor_window()
    test_priority_turns_are_included_without_extra_neighbors()
    print("all passed")
