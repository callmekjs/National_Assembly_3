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
    keyword_containment,
    party_label_consistency,
    qa_pair_question,
    qa_pairing_dates,
    ruling_period_consistency,
    speaker_both_sides,
    speaker_role_consistency,
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

    # 재리뷰: 찬성/반대 키워드 복원 (spec §2-2 "여당/야당/찬성/반대 대조 키워드")
    stance_both = ("찬성 측에서는 김우영 위원이 특별법 보완을 주장했습니다[1]. "
                   "반면 반대 측 김우영 위원은 정부 대응을 비판했습니다[1].")
    check("양진영: 찬성·반대 측 프레이밍 감지 (스펙 §2-2 복원)", speaker_both_sides(stance_both, srcs) is True)


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


# ── speaker_role_consistency (spec §4-2, eval_013·019) ───────────────────────

def test_speaker_role_consistency():
    # eval_013 재현: 외교부 문단에서 주어 생략 문장이 수석전문위원 발언[5]을 인용
    srcs = [_src(5, "곽현준", None, role="수석전문위원")]
    ans = ("외교부는 재외국민 보호 방안을 검토하고 있다고 밝혔습니다. "
           "관련 법안도 제안되었습니다[5].")
    flags = speaker_role_consistency(ans, srcs)
    check("귀속: 승계 주어 기관 vs 비정부 화자 감지", len(flags) == 1, str(flags))
    check("귀속: inherited 표시", "inherited" in flags[0], str(flags))

    # 명시 주어가 정부기관 + 정부측 인용이면 통과
    gov = [_src(2, "김병환", "정부측", role="금융위원장")]
    ok = "금융위원회는 가계부채 관리 방안을 설명했습니다[2]."
    check("귀속: 정부기관+정부측 인용 통과", speaker_role_consistency(ok, gov) == [])

    # eval_019 재현: 인용 근거에 없는 화자에게 발언 귀속 (환각 화자)
    srcs19 = [_src(1, "김우영", "더불어민주당(당시 야당)")]
    hallucinated = "김수경 차관은 오물풍선 대응을 설명했습니다[1]."
    flags = speaker_role_consistency(hallucinated, srcs19)
    check("귀속: 미등장 화자 감지 (환각)", any("김수경" in f for f in flags), str(flags))

    # 인용 화자와 일치하는 명시 주어는 통과
    ok2 = "김우영 위원은 특별법 보완을 주장했습니다[1]."
    check("귀속: 일치 화자 통과", speaker_role_consistency(ok2, srcs19) == [])

    # 거절 문장은 검사 제외 ("김수경 차관의 발언은 확인할 수 없습니다"는 정직한 처리)
    refusal = "김수경 차관의 발언은 제공된 회의록에서 확인할 수 없습니다."
    check("귀속: 거절 문장 제외", speaker_role_consistency(refusal, srcs19) == [])

    # 일반어 오탐 방지: '해당 위원' '여당 의원'은 화자명이 아니다
    generic = "해당 위원의 지적에 여당 의원들도 동의했습니다[1]. 김우영 위원의 발언입니다[1]."
    check("귀속: 일반어+직함 비매칭", speaker_role_consistency(generic, srcs19) == [])


# ── party_label_consistency (spec §0-1·§4-2, eval_057) ───────────────────────────────

def test_party_label_consistency():
    # eval_057 재현: 주입 라벨은 더불어민주연합(위성정당 표기 유지)인데 답변은 더불어민주당
    srcs = [_src(3, "한창민", "더불어민주연합(당시 여당)")]
    wrong = "한창민 위원(더불어민주당)은 은행 규제를 언급했습니다[3]."
    flags = party_label_consistency(wrong, srcs)
    check("정당: 위성정당 오표기 감지", len(flags) == 1 and "한창민" in flags[0], str(flags))

    right = "한창민 위원(더불어민주연합)은 은행 규제를 언급했습니다[3]."
    check("정당: 일치 표기 통과", party_label_consistency(right, srcs) == [])

    # 근거에 없는 화자의 정당 병기는 이 규칙 대상 아님 (speaker_role 이 잡는다)
    other = "박형수 위원(국민의힘)은 반대했습니다[3]."
    check("정당: 미등장 화자는 이 규칙 통과", party_label_consistency(other, srcs) == [])


# ── keyword_containment (spec §4-2, eval_055) ────────────────────────────────

def test_keyword_containment():
    q = "기업은행의 임금 체계 논의를 알려줘"
    # eval_055 재현: 인용 근거 본문에 '기업은행'이 없는데 문장 주어로 사용
    srcs = [_src(4, "유영하", "국민의힘(당시 야당)", text="우리은행 부당대출 관련 질의입니다.")]
    padded = "기업은행 임금 체계에 대한 논의가 있었습니다[4]."
    flags = keyword_containment(padded, srcs, q)
    check("키워드: 근거에 없는 기관 주어 감지", len(flags) == 1 and "기업은행" in flags[0], str(flags))

    grounded = [_src(4, "강민국", "국민의힘(당시 야당)", text="기업은행의 임금 문제를 지적합니다.")]
    check("키워드: 근거에 있으면 통과", keyword_containment(padded, grounded, q) == [])

    check("키워드: 질문에 기관명 없으면 검사 안 함",
          keyword_containment(padded, srcs, "임금 체계 논의 알려줘") == [])

    no_cite = "기업은행 임금 체계에 대한 논의가 있었습니다."
    check("키워드: 인용 없는 문장 제외", keyword_containment(no_cite, srcs, q) == [])


# ── ruling_period_consistency (spec §4-2, eval_035) ──────────────────────────

def test_ruling_period_consistency():
    # eval_035 재현: 2024-08(전 정권기) 발언을 '현 정부' 비판으로 인용
    srcs = [_src(2, "위성락", None, "2024-08-27", role="증인")]
    wrong = "현 정부의 외교 기조에 대한 비판이 제기됐습니다[2]."
    flags = ruling_period_consistency(wrong, srcs)
    check("정권기: 전 정권 발언을 현 정부 서술에 인용 감지", len(flags) == 1, str(flags))

    current = [_src(2, "김영배", "더불어민주당(당시 여당)", "2025-09-01")]
    check("정권기: 현 정권기 발언은 통과", ruling_period_consistency(wrong, current) == [])

    neutral = "정부의 외교 기조에 대한 비판이 제기됐습니다[2]."
    check("정권기: '현/새 정부' 표현 없으면 통과", ruling_period_consistency(neutral, srcs) == [])

    # 회귀: date=None source는 크래시 없이 건너뜀 (예외격리)
    with_none = [_src(2, "김영배", "더불어민주당(당시 여당)", date=None)]
    check("정권기: date=None source는 판정 제외 (크래시 없음)",
          ruling_period_consistency("현 정부의 외교 기조에 대한 비판이 제기됐습니다[2].", with_none) == [])


# ── 회귀 테스트: 4자 기관명 구조적 차단 ──────────────────────────────────────

def test_speaker_role_consistency_org_names():
    """4자 기관명(방송통신위원회·공정거래위원회)이 화자명으로 오인되지 않음을 확인."""
    # 방송통신위원회 + 정부측 위원장 인용
    srcs = [_src(2, "김병환", "정부측", role="방송통신위원장")]
    sent = "방송통신위원회는 방송법 개정안을 발표했습니다[2]."
    check("귀속: 4자 기관명 위원회는 화자 아님 (구조적 lookahead)",
          speaker_role_consistency(sent, srcs) == [])

    # 공정거래위원회 + 정부측 위원장 인용
    srcs2 = [_src(3, "박경미", "정부측", role="공정거래위원장")]
    sent2 = "공정거래위원회는 독점금지법 위반 사항을 적발했습니다[3]."
    check("귀속: 공정거래위원회 + 정부측 인용 통과",
          speaker_role_consistency(sent2, srcs2) == [])

    # 기존 테스트 유지: 2자 기관명도 여전히 차단
    gov3 = [_src(2, "김병환", "정부측", role="금융위원장")]
    ok3 = "금융위원회는 가계부채 관리 방안을 설명했습니다[2]."
    check("귀속: 금융위원회도 여전히 통과",
          speaker_role_consistency(ok3, gov3) == [])


if __name__ == "__main__":
    test_core_party()
    test_comparison_coverage()
    test_speaker_both_sides()
    test_qa_pair_question()
    test_qa_pairing_dates()
    test_speaker_role_consistency()
    test_party_label_consistency()
    test_keyword_containment()
    test_ruling_period_consistency()
    test_speaker_role_consistency_org_names()
    print("\n전체 통과")
