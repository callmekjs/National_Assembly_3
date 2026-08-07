"""
리포트 구조 검사기(scripts/report_eval.py) 단위 테스트 — LLM·DB 없이 로직만 검증.

왜 채점기를 테스트하는가:
    이 프로젝트는 채점기를 검증 없이 믿다가 두 번 데였다.
      ① 옛 retrieval_eval 의 R@5 0.983 — 정답 판정이 "본문에 키워드가 있는가"였고
         키워드 검색 축이 정확히 같은 연산을 해, 검색기가 채점 기준을 미리 아는
         동어반복이었다 (2026-08-06 폐기).
      ② report_eval 의 첫 C4 — 문장 단위로 인용을 세어 멀쩡한 리포트를 27~60%로
         찍었다. 리포트가 아니라 검사기가 틀린 것이었다 (2026-08-07).
    채점기가 틀리면 그 위의 모든 수치가 거짓이 된다. 합격/불합격 양쪽을 다 고정한다.

실행: python tests/test_report_eval.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from report_eval import check_report, claim_sentences, sections  # noqa: E402


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


GOOD = """## 개요
정무위원회에서 티메프 사태가 논의됐다 [1]. 정산 지연이 핵심 쟁점으로 지적됐다 [2].

## 쟁점별 정리
### 2024-07-30
김남근 위원은 정산 기간이 길어 시장을 교란한다고 지적했다 [2].

### 2024-08-26
김남근 위원은 정산 기한 단축이 필요하다고 강조했다 [7].

## 주요 발언 근거
- "이런 시장 교란행위를 방치하고 있으니까" — 김남근 위원 [2]

## 논의의 한계
구체적인 법안 세부 내용은 다뤄지지 않았다. 재발 방지 계획도 확인되지 않는다.
"""


def result(answer: str, invalid=None) -> dict:
    return {"answer": answer, "invalid_citations": invalid or [], "sources": [], "cited_numbers": []}


def failed_codes(question: str, answer: str, invalid=None) -> list[str]:
    ch = check_report(question, result(answer, invalid))
    return [k for k, (ok, _) in ch.items() if not ok and not k.startswith("_")]


def test_good_report_passes():
    # 합격 쪽을 고정하지 않으면 "전부 불합격" 검사기도 테스트를 통과한다
    bad = failed_codes("티메프 사태 논의", GOOD)
    check("정상 리포트는 전 항목 통과", bad == [], bad)


def test_sections_and_sentences():
    secs = sections(GOOD)
    check("섹션 분리: 4개", len(secs) == 4, list(secs))
    check("섹션 분리: 제목 보존", "## 쟁점별 정리" in secs, list(secs))
    check("섹션 분리: 본문만 담김", "## " not in secs["## 개요"], secs["## 개요"])
    # 짧은 목록 기호·제목은 사실 주장이 아니다
    check("문장 추출: 짧은 조각 제외", claim_sentences("- 짧음\n\n## 제목") == [],
          claim_sentences("- 짧음\n\n## 제목"))


def test_missing_section_detected():
    bad = failed_codes("티메프 사태 논의", GOOD.replace("## 논의의 한계", "## 마무리"))
    check("C1: 필수 섹션 누락 검출", "C1 필수섹션" in bad, bad)


def test_section_order_detected():
    swapped = ("## 쟁점별 정리\n김 위원이 지적했다 [2].\n\n## 개요\n요약이다 [1].\n\n"
               "## 주요 발언 근거\n- \"인용문이다\" — 김 위원 [2]\n\n## 논의의 한계\n한계가 있다.")
    check("C2: 섹션 순서 뒤바뀜 검출", "C2 섹션순서" in failed_codes("질문", swapped),
          failed_codes("질문", swapped))


def test_invalid_citation_detected():
    check("C3: 범위 밖 인용 검출", "C3 인용유효" in failed_codes("질문", GOOD, invalid=[99]))


def test_uncited_paragraph_detected():
    # 실제로 잡았던 결함: '## 개요' 가 근거 번호 없이 사실을 주장 (2026-08-07)
    no_cite = GOOD.replace(
        "정무위원회에서 티메프 사태가 논의됐다 [1]. 정산 지연이 핵심 쟁점으로 지적됐다 [2].",
        "정무위원회에서 티메프 사태가 활발히 논의됐으며 정산 지연이 핵심 쟁점이었다.")
    bad = failed_codes("티메프 사태 논의", no_cite)
    check("C4: 개요 무인용 검출", "C4 문단인용" in bad, bad)
    ch = check_report("티메프 사태 논의", result(no_cite))
    check("C4: 어느 섹션인지 기록", "## 개요" in ch["C4 문단인용"][1], ch["C4 문단인용"][1])


def test_cited_paragraph_not_flagged():
    # 과검출 회귀 방지: 한 근거를 두 문장에 나눠 쓰고 끝에 [n] 을 붙이는 형태는
    # 정상이다. 문장 단위로 세던 첫 버전이 이걸 위반으로 찍었다.
    two_sent = GOOD.replace(
        "김남근 위원은 정산 기간이 길어 시장을 교란한다고 지적했다 [2].",
        "김남근 위원은 정산 기간 문제를 지적했다. 그는 시장 교란이라고 언급했다 [2].")
    bad = failed_codes("티메프 사태 논의", two_sent)
    check("C4: 문단 끝 인용은 통과 (과검출 방지)", "C4 문단인용" not in bad, bad)


def test_narrative_section_exempt():
    # '## 논의의 한계' 는 프롬프트가 무인용 서술을 허용한 예외 — 위반으로 세면 안 된다
    bad = failed_codes("티메프 사태 논의", GOOD)
    check("C4: 논의의 한계는 예외", "C4 문단인용" not in bad, bad)
    check("C5: 논의의 한계로 끝나도 통과", "C5 총평금지" not in bad, bad)


def test_dangling_refusal_detected():
    dangling = GOOD + "\n이 외의 내용은 확인할 수 없습니다.\n"
    check("C7: 대상 없는 꼬리 거절문 검출",
          "C7 꼬리거절" in failed_codes("질문", dangling), failed_codes("질문", dangling))


def test_timeline_order():
    # 경과를 묻는 질문일 때만 시간순을 요구한다
    rev = GOOD.replace("### 2024-07-30", "### 2024년 12월").replace("### 2024-08-26", "### 2024년 8월")
    check("C8: 경과 질문이면 역순 검출", "C8 시간순" in failed_codes("논의 경과는?", rev),
          failed_codes("논의 경과는?", rev))
    check("C8: 경과 질문이 아니면 미적용", "C8 시간순" not in failed_codes("누가 발언했나?", rev),
          failed_codes("누가 발언했나?", rev))


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print("\n전부 통과")
