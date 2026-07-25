"""
verification 모듈(답변-근거 자동 검증 1단계) 단위 테스트 — LLM·DB 없이 순수 규칙만.

spec: docs/superpowers/specs/2026-07-15-answer-verification-design.md
실행: python -m pytest tests/test_verification.py -q  또는  python tests/test_verification.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from verification import (  # noqa: E402
    comparison_coverage,
    core_party,
)


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    assert cond, f"{name}" + (f" — {detail}" if detail else "")


# ── core_party (spec §2-1) ───────────────────────────────────────────────────

def test_core_party():
    check("core: 여당 접미 제거", core_party("더불어민주당(당시 여당)") == "더불어민주당")
    check("core: 야당 접미 제거", core_party("국민의힘(당시 야당)") == "국민의힘")
    check("core: 위성정당 표기 유지", core_party("더불어민주연합(당시 여당)") == "더불어민주연합")
    check("core: None → None", core_party(None) is None)
    check("core: 정부측 → None", core_party("정부측") is None)
    check("core: 무소속 → None (진영 대표 아님)", core_party("무소속") is None)


# ── comparison_coverage (spec §2-1) ──────────────────────────────────────────

def _src(n, speaker, party, date="2025-09-01", **kw):
    base = {"n": n, "speaker": speaker, "role": "위원", "party": party,
            "committee": "정무위", "date": date, "text": ""}
    base.update(kw)
    return base


def test_comparison_coverage():
    # eval_019 재현: 인용 3건 전부 더불어민주당 (시점만 달라 여야 표기가 갈림)
    one_sided = [
        _src(1, "김우영", "더불어민주당(당시 야당)", "2024-11-01"),
        _src(2, "박민규", "더불어민주당(당시 여당)", "2025-09-01"),
        _src(3, "이연희", "더불어민주당(당시 여당)"),
    ]
    cov = comparison_coverage(one_sided)
    check("커버리지: 같은 정당 시점차는 1개 진영", cov["covered"] is False, str(cov))
    check("커버리지: core_parties 정당명", cov["core_parties"] == ["더불어민주당"])

    covered = one_sided + [_src(4, "강민국", "국민의힘(당시 야당)")]
    check("커버리지: 정당 2개면 covered", comparison_coverage(covered)["covered"] is True)

    # 정부측·무소속·라벨없음은 집계 제외 (거짓 covered 방지)
    mixed = [
        _src(1, "김우영", "더불어민주당(당시 여당)"),
        _src(2, "김병환", "정부측", role="금융위원장"),
        _src(3, "우원식", "무소속"),
        _src(4, "곽현준", None, role="수석전문위원"),
    ]
    cov = comparison_coverage(mixed)
    check("커버리지: 정부측·무소속·무표기 제외", cov["covered"] is False and cov["core_parties"] == ["더불어민주당"])

    check("커버리지: 빈 목록", comparison_coverage([]) == {"core_parties": [], "covered": False})


if __name__ == "__main__":
    test_core_party()
    test_comparison_coverage()
    print("\n전체 통과")
