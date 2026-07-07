"""답변 평가셋 5단계 — 확정된 평가셋으로 점수표(scorecard) 산출.

answer_eval_set.json 의 grades 를 집계해 4기준별 통과율 + 유형별 + overall 을
낸다. 사람 검수(human_reviewed=true)가 있으면 그 값을, 없으면 자동 채점값을 쓴다.

이것이 재사용 도구다: 청킹·reranker 등을 바꾼 뒤
    1. answer_eval_build.py 로 답변 재수집
    2. answer_eval_judge.py 로 재채점 (또는 사람 재검수)
    3. 이 스크립트로 before/after 점수 비교
검색 eval(retrieval_eval.py)의 답변 버전.

실행: python scripts/answer_eval_score.py
"""

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EVAL_SET = Path(__file__).parent.parent / "data" / "eval" / "answer_eval_set.json"
CRITERIA = ["faithfulness", "citation_accuracy", "classification", "refusal"]


def rate(grades: list[dict], key: str) -> str:
    """pass/(pass+fail) 비율. na 는 분모에서 제외 (분류 안 한 문항 등)."""
    p = sum(1 for g in grades if g.get(key) == "pass")
    f = sum(1 for g in grades if g.get(key) == "fail")
    denom = p + f
    return f"{p}/{denom} = {p/denom:.1%}" if denom else "해당 없음 (전부 na)"


def main() -> None:
    items = json.load(open(EVAL_SET, encoding="utf-8"))
    grades = [x["grades"] for x in items]

    reviewed = sum(1 for g in grades if g.get("human_reviewed"))
    print(f"=== 답변 평가 점수표 ({len(items)}문항, 사람 검수 {reviewed}건) ===\n")

    print("[4기준별 통과율]")
    for c in CRITERIA:
        print(f"  {c:20} {rate(grades, c)}")

    op = sum(1 for g in grades if g.get("overall") == "pass")
    print(f"\n[종합] overall pass: {op}/{len(items)} = {op/len(items):.1%}")

    print("\n[유형별 overall 통과율]")
    by_type = defaultdict(list)
    for x in items:
        by_type[x["type"]].append(x["grades"])
    for t in sorted(by_type):
        gs = by_type[t]
        p = sum(1 for g in gs if g.get("overall") == "pass")
        print(f"  {t:20} {p}/{len(gs)}")

    fails = [(x["id"], x["type"], x["grades"]) for x in items
             if x["grades"].get("overall") != "pass"]
    print(f"\n[미통과 {len(fails)}건]")
    for qid, typ, g in fails:
        flags = [c for c in CRITERIA if g.get(c) == "fail"]
        tag = "✓검수" if g.get("human_reviewed") else "자동"
        print(f"  {qid} ({typ}) [{tag}]: {'/'.join(flags) or g.get('overall')}")

    if not reviewed:
        print("\n주의: 아직 사람 검수 0건 — 현재 점수는 LLM-judge 초안이다.")
        print("      answer_eval_review.py 로 검수 후 확정할 것.")


if __name__ == "__main__":
    main()
