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
    qa_pair_question,
    qa_pairing_dates,
    speaker_both_sides,
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


# ── speaker_both_sides (spec §2-2, eval_068) ─────────────────────────────────

def test_speaker_both_sides():
    srcs = [_src(1, "김우영", "더불어민주당(당시 여당)")]
    both = ("여당 측에서는 김우영 위원이 특별법 보완을 주장했습니다[1]. "
            "반면 야당 측 김우영 위원은 정부 대응을 비판했습니다[1].")
    check("양진영: 같은 화자 여당·야당 배치 감지", speaker_both_sides(both, srcs) is True)

    ok = ("김우영 위원은 특별법 보완을 주장했습니다[1]. "
          "김우영 위원은 이어서 피해자 구제도 언급했습니다[1].")
    check("양진영: 진영 프레이밍 없으면 통과", speaker_both_sides(ok, srcs) is False)

    once = "여당의 김우영 위원이 발언했습니다[1]."
    check("양진영: 1회 등장은 통과", speaker_both_sides(once, srcs) is False)

    # '여야'라는 단어 자체는 여당/야당 어느 쪽도 아님
    yeoya = "김우영 위원[1]의 발언에 여야 모두 주목했고, 김우영 위원이 재차 강조했습니다[1]."
    check("양진영: '여야' 단어는 비매칭", speaker_both_sides(yeoya, srcs) is False)

    # 리뷰 fix: 상대 진영 반응 서술을 오탐하지 않음 (화자는 일관되게 여당)
    opposite_reaction = ("여당 측에서는 김우영 위원이 특별법 보완을 주장했습니다[1]. "
                         "이에 대해 야당 반대에도 불구하고 김우영 위원은 원안을 재차 강조했습니다[1].")
    check("양진영: 상대 진영 반응 서술은 오탐하지 않음", speaker_both_sides(opposite_reaction, srcs) is False)


# ── Q-A 짝짓기 (spec §3, eval_029) ───────────────────────────────────────────

def test_qa_pair_question():
    q29 = ("홍기원 의원이 2024년 11월 우크라이나 무기 지원에 대해 조태열 장관에게 "
           "질의했는데, 조태열 장관은 어떻게 답변했나요?")
    check("QA패턴: eval_029 질의 감지", qa_pair_question(q29) is True)
    check("QA패턴: 일반 질문 비매칭", qa_pair_question("의대 정원 확대 논의를 알려줘") is False)
    check("QA패턴: 답변만 있으면 비매칭", qa_pair_question("조태열 장관의 답변 내용은?") is False)


def test_qa_pairing_dates():
    q29 = ("홍기원 의원이 2024년 11월 우크라이나 무기 지원에 대해 조태열 장관에게 "
           "질의했는데, 조태열 장관은 어떻게 답변했나요?")
    # eval_029 재현: 질문 인용은 2024-11 외통위, 답변 인용은 2025-02 회의
    srcs = [
        _src(1, "홍기원", "더불어민주당(당시 야당)", "2024-11-11", committee="외통위"),
        _src(5, "조태열", "정부측", "2025-02-19", committee="외통위", role="장관"),
    ]
    fabricated = ("홍기원 의원이 무기 지원 가능성을 질의했고[1], "
                  "조태열 장관은 신중히 검토하겠다고 답변했습니다[5].")
    check("QA짝: 다른 회의 인용 + 미공시 → flag", qa_pairing_dates(fabricated, srcs, q29) is True)

    honest = ("홍기원 의원은 2024년 11월 무기 지원 가능성을 질의했습니다[1]. "
              "다만 조태열 장관의 해당 발언은 2025년 2월 업무보고의 것으로, "
              "같은 회의의 답변은 아닙니다[5].")
    check("QA짝: 두 날짜 공시하면 통과", qa_pairing_dates(honest, srcs, q29) is False)

    same_meeting = [
        _src(1, "홍기원", "더불어민주당(당시 야당)", "2024-11-11", committee="외통위"),
        _src(2, "조태열", "정부측", "2024-11-11", committee="외통위", role="장관"),
    ]
    check("QA짝: 같은 회의면 통과", qa_pairing_dates(fabricated, same_meeting, q29) is False)
    check("QA짝: QA 질문 아니면 통과", qa_pairing_dates(fabricated, srcs, "무기 지원 논의 알려줘") is False)

    # 리뷰 fix: 날짜 결측 source는 판정 제외 (크래시 없음, 예외 격리)
    with_none_date = [
        _src(1, "홍기원", "더불어민주당(당시 야당)", date=None, committee="외통위"),
        _src(5, "조태열", "정부측", "2025-02-19", committee="외통위", role="장관"),
    ]
    check("QA짝: 날짜 결측 source는 판정 제외(크래시 없음)",
          qa_pairing_dates(fabricated, with_none_date, q29) is False)


if __name__ == "__main__":
    test_core_party()
    test_comparison_coverage()
    test_speaker_both_sides()
    test_qa_pair_question()
    test_qa_pairing_dates()
    print("\n전체 통과")
