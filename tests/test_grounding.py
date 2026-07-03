"""
grounding 모듈(RAG-7) 단위 테스트 — LLM·DB 호출 없이 판정 규칙만 검증.

검사 항목:
    1. 사전차단: 검색 0건 → NONE
    2. 사전차단: 벡터 <threshold + 키워드 0건 → REFUSED
    3. 사전차단: 키워드 매치 있으면 유사도 낮아도 통과
    4. 사전차단: threshold 를 env 로 바꾸면 판정이 바뀜 (설정값 동작)
    5. 사후 판정 표: FULL / PARTIAL / REFUSED / PARTIAL+ungrounded
    6. 어순 변형 거절 문구 감지 (부분 문자열)
    7. invalid_citations → FULL 강등

실행: python tests/test_grounding.py
"""

import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from grounding import judge, pre_gate, sim_threshold  # noqa: E402

FAILURES = []


def check(name: str, cond: bool):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILURES.append(name)


def hit(kw_rank=None, vec_score=None):
    return {"chunk_id": "테스트_turn_0001_chunk_001", "kw_rank": kw_rank, "vec_score": vec_score}


# ── 사전차단 ──────────────────────────────────────────────────────────────────

def test_pre_gate():
    check("사전차단: 검색 0건 → NONE", pre_gate([]) == "NONE")

    low_vec_only = [hit(vec_score=0.32), hit(vec_score=0.38)]
    check("사전차단: 벡터<0.4 + 키워드 0건 → REFUSED", pre_gate(low_vec_only) == "REFUSED")

    with_keyword = [hit(kw_rank=1, vec_score=0.32), hit(vec_score=0.35)]
    check("사전차단: 키워드 매치 있으면 통과", pre_gate(with_keyword) is None)

    high_vec = [hit(vec_score=0.55), hit(vec_score=0.38)]
    check("사전차단: 벡터 최고점 ≥0.4 통과", pre_gate(high_vec) is None)

    boundary = [hit(vec_score=0.4)]
    check("사전차단: 정확히 0.4 는 통과 (미만만 차단)", pre_gate(boundary) is None)


def test_threshold_env():
    check("설정값: 기본 0.4", sim_threshold() == 0.4)

    low_vec = [hit(vec_score=0.45)]
    orig = os.environ.get("GROUNDING_SIM_THRESHOLD")
    try:
        os.environ["GROUNDING_SIM_THRESHOLD"] = "0.5"
        check("설정값: 임계 0.5 로 올리면 0.45 도 차단", pre_gate(low_vec) == "REFUSED")
        os.environ["GROUNDING_SIM_THRESHOLD"] = "0.3"
        check("설정값: 임계 0.3 으로 내리면 통과", pre_gate(low_vec) is None)
    finally:
        if orig is None:
            os.environ.pop("GROUNDING_SIM_THRESHOLD", None)
        else:
            os.environ["GROUNDING_SIM_THRESHOLD"] = orig


# ── 사후 판정 ─────────────────────────────────────────────────────────────────

def result(answer, cited=(), invalid=()):
    return {"answer": answer, "cited_numbers": list(cited), "invalid_citations": list(invalid)}


def test_judge():
    check("판정: 인용+거절문구 없음 → FULL",
          judge(result("발언했습니다[1].", cited=[1])) == ("FULL", False))

    check("판정: 인용+거절문구 → PARTIAL",
          judge(result("발언했습니다[1]. 나머지는 제공된 회의록에서 확인할 수 없습니다.", cited=[1]))
          == ("PARTIAL", False))

    check("판정: 무인용+거절문구 → REFUSED",
          judge(result("제공된 회의록에서 확인할 수 없습니다.")) == ("REFUSED", False))

    check("판정: 어순 변형 거절도 REFUSED (부분 문자열)",
          judge(result("제공된 회의록에서 이준석 의원의 발언은 확인할 수 없습니다."))
          == ("REFUSED", False))

    check("판정: '확인되지 않습니다' 활용 변형도 REFUSED (스모크 실측)",
          judge(result("정무위에서 북한 핵 논의가 있었는지에 대한 내용이 확인되지 않습니다."))
          == ("REFUSED", False))

    check("판정: '포함되어 있지 않습니다' 변형도 REFUSED (스모크 실측)",
          judge(result("제공된 회의록에는 이준석 의원의 발언 내용이 포함되어 있지 않습니다."))
          == ("REFUSED", False))

    check("판정: '언급이 없습니다' 변형도 REFUSED (스모크 실측)",
          judge(result("구체적인 금액이나 비율에 대한 언급이 없습니다.")) == ("REFUSED", False))

    check("판정: 인용 있는 답변 속 발언 인용('근거가 없다')은 오탐하지 않음 → FULL",
          judge(result("김 위원은 법적 근거가 없다고 지적하며 언급이 없었다고 비판했습니다[1].",
                       cited=[1])) == ("FULL", False))

    check("판정: 무인용+무거절 → PARTIAL + ungrounded",
          judge(result("근거 없이 주장만 있는 답변.")) == ("PARTIAL", True))

    check("판정: invalid 있으면 FULL → PARTIAL 강등",
          judge(result("발언했습니다[1][7].", cited=[1], invalid=[7])) == ("PARTIAL", False))

    report = "## 쟁점별 정리\n논의했다[1].\n\n## 논의의 한계\n세부 내용은 제공된 회의록에서 확인할 수 없다."
    check("판정: report '논의의 한계' 섹션의 확인 불가는 거절로 안 셈 → FULL",
          judge(result(report, cited=[1])) == ("FULL", False))

    report2 = "## 쟁점별 정리\n이 부분은 확인할 수 없다[1].\n\n## 논의의 한계\n한계 서술."
    check("판정: 한계 섹션 밖의 확인 불가는 여전히 PARTIAL",
          judge(result(report2, cited=[1])) == ("PARTIAL", False))

    check("판정: invalid 있어도 REFUSED 는 유지",
          judge(result("제공된 회의록에서 확인할 수 없습니다.", invalid=[9])) == ("REFUSED", False))


def main():
    test_pre_gate()
    test_threshold_env()
    test_judge()

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}건: {FAILURES}")
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
