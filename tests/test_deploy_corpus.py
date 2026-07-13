"""배포 코퍼스 생성기 순수 로직 테스트 — DB 없이 실행.
실행: python tests/test_deploy_corpus.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from make_deploy_corpus import choose_scope, estimate_mb, expand_neighbor_turn_ids  # noqa: E402


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


def test_choose_scope():
    # 인접 포함이 350MB 이내면 그대로 (인덱스 포함)
    s = choose_scope(n_with_neighbors=10_000, n_core_only=6_000)
    check("여유 시 인접+인덱스", s["neighbors"] and s["index"], s)
    # 인접 포함이 초과, core-only 는 이내 → 인접 제외
    s2 = choose_scope(n_with_neighbors=20_000, n_core_only=14_000)
    check("초과 시 인접 제외", not s2["neighbors"] and s2["index"], s2)
    check("n_chunks 는 선택 범위 기준", s2["n_chunks"] == 14_000, s2)
    # core-only 도 인덱스 포함 초과 → 인덱스 생략 레버
    s3 = choose_scope(n_with_neighbors=40_000, n_core_only=30_000)
    check("최후 레버 인덱스 생략", not s3["neighbors"] and not s3["index"], s3)
    check("est_mb 동봉", s3["est_mb"] > 0, s3)


if __name__ == "__main__":
    test_expand_neighbor_turn_ids()
    test_estimate_mb()
    test_choose_scope()
    print("all passed")
