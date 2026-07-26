"""
검증층 1단계 재리뷰 — 최종 리뷰 확정 8건(F1~F8) 수정 후 오프라인 replay 측정.

목표: 신규 LLM 호출 0회($0) — answer_eval_set_router.json 에 이미 기록된 pass
확정 답변(58건, grades.overall=="pass" and citations 비어있지 않음)에
verify() 를 그대로 재생해 flags 를 산출한다. 실행 시점의 코드(verification.py)가
그대로 쓰이므로, 수정 전(HEAD~) 대비 수정 후 잔존 flag 를 비교할 수 있다.

주의(구현 시 실측, scripts/verification_regress.py 와 동일 계열):
- answer_eval_set_router.json 최상위 구조는 리스트 자체(딕셔너리 아님).
- "pass 확정 답변" 58건 = grades.overall == "pass" and citations 비어있지 않음
  (인용 0건 9건은 verify() 가 자체적으로 빈 flags 를 반환하므로 replay 대상에서
  제외해도 무방 — 애초 flag 가 뜰 수 없다).
- **한계(정직 기록)**: citations 필드는 200자 snippet 만 담고 있고 인용 근거의
  전문(text)은 이 eval 셋에 저장돼 있지 않다(생성 시점 sources 전문은
  answer.py 의 _source_summary() 가 200자로 잘라 citations 에 넣고 전문은
  버림). keyword_containment 처럼 "인용 근거 본문에 문자열이 있는가"를 검사하는
  규칙은 snippet 재생 시 200자 밖 문자열을 오탐 흡사(false positive)할 수
  있다 — 문항별 소견에서 이 한계를 반영해 판정한다(LLM 재호출 없이는 전문
  복원이 불가능하므로 실서비스 verify() 호출과 완전히 동일하지 않음을 명시).

실행: python scripts/verification_replay.py
비용: $0 (신규 LLM 호출 없음 — 기록된 답변 재사용)
출력: data/eval/verification_replay_report.md
"""

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

EVAL_SET = Path(__file__).parent.parent / "data" / "eval" / "answer_eval_set_router.json"
REPORT = Path(__file__).parent.parent / "data" / "eval" / "verification_replay_report.md"


def _to_source(c: dict) -> dict:
    """citations 항목(200자 snippet) → verify() 가 기대하는 source dict.

    전문(text) 이 없으므로 snippet 을 text 자리에 대신 채운다 — keyword_containment
    등 본문 대조 규칙은 이 대체로 인해 실서비스보다 보수적으로(근거 있음을
    놓칠 방향으로) 판정될 수 있다(한계, 위 docstring 참고).
    """
    return {
        "n": c["n"], "speaker": c.get("speaker"), "role": c.get("role"),
        "party": c.get("party"), "committee": c.get("committee"),
        "date": c.get("date"), "text": c.get("snippet") or "",
    }


def main():
    from query_parser import classify_question
    from verification import verify

    items = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    pass_with_citations = [
        it for it in items
        if it.get("grades", {}).get("overall") == "pass" and it.get("citations")
    ]

    lines = [
        "# 검증층 1단계 재리뷰 replay (최종 리뷰 F1~F8 수정 후)",
        "",
        f"재생 대상: pass 확정 답변 {len(pass_with_citations)}건 "
        f"(grades.overall==pass and citations 비어있지 않음, 전체 {len(items)}문항 중)",
        "신규 LLM 호출 없음 — 기록된 답변에 verify() 만 재실행($0).",
        "",
    ]
    flagged_items = []
    for it in pass_with_citations:
        sources = [_to_source(c) for c in it["citations"]]
        cited_numbers = [c["n"] for c in it["citations"]]
        q_types = classify_question(it["query"])
        v = verify(it["query"], it["answer"], sources, cited_numbers, q_types)
        if v["flags"]:
            flagged_items.append((it, v))
        print(f"{it['id']}: flags={v['flags']}")

    lines.insert(
        4,
        f"**수정 후 flag 잔존: {len(flagged_items)}/{len(pass_with_citations)}** "
        "(수정 전 기준선 30/58 — 렌즈 측정치, F1~F8 적용 후 재측정)",
    )
    lines.append("")

    if flagged_items:
        lines.append("## 잔존 flag 문항 (사람 소견 필요 — 정탐/오탐/불명)")
        lines.append("")
        for it, v in flagged_items:
            lines += [
                f"### {it['id']} ({it.get('type', '?')})",
                f"- query: {it['query']}",
                f"- flags: `{v['flags']}`",
                f"- detail: `{json.dumps(v['detail'], ensure_ascii=False)}`",
                "- answer:", "```", it["answer"], "```", "",
            ]
    else:
        lines.append("잔존 flag 없음.")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n수정 후 flag {len(flagged_items)}/{len(pass_with_citations)} — 리포트: {REPORT}")


if __name__ == "__main__":
    main()
