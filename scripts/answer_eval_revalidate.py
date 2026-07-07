"""답변 평가셋 1단계 — prototype 75문항을 현재 코퍼스로 재검증.

목적: 예전 프로토타입(National_Assembly_2)에서 만든 질문이 우리 767회의
코퍼스에 맞는지 검색 신호로 진단한다. LLM 답변 생성은 하지 않는다 (비용 0).

판정:
    - answerable 문항: 사전차단 통과(근거 확보) → KEEP, 아니면 REVIEW(코퍼스 불일치 의심)
    - unanswerable 문항: 사전차단(NONE/REFUSED) 이 정답 → 통과 시 KEEP, 근거 잡히면 REVIEW
      (우리 코퍼스엔 답이 있을 수도 — prototype 은 다른 코퍼스 기준이었으므로)

출력: data/eval/revalidation_report.json + 콘솔 요약

실행: python scripts/answer_eval_revalidate.py
"""

import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROTO = Path(__file__).parent.parent / "data" / "eval" / "prototype_75q_full.json"
OUT = Path(__file__).parent.parent / "data" / "eval" / "revalidation_report.json"

# prototype grounding_level 이 이 값이면 '답이 없어야 정상'인 문항
UNANSWERABLE_LEVELS = {"NONE", "REFUSED"}


def main() -> None:
    import db

    db.init_pool()
    from grounding import pre_gate
    from search_hybrid import hybrid_search

    items = json.load(open(PROTO, encoding="utf-8"))
    results = []

    for x in items:
        q = x["query"]
        hits = hybrid_search(q, limit=5)
        gate = pre_gate(hits)  # None=통과 / "NONE"·"REFUSED"=차단
        blocked = gate is not None

        proto_unanswerable = x.get("grounding_level") in UNANSWERABLE_LEVELS

        # 판정
        if proto_unanswerable:
            # 답 없어야 정상 — 차단되면 KEEP(거절 능력 검증), 근거 잡히면 REVIEW
            verdict = "KEEP" if blocked else "REVIEW"
            reason = "거절 검증 문항 (차단 정상)" if blocked else "우리 코퍼스엔 근거 있음 — 유형 재분류 검토"
        else:
            # 답 있어야 정상 — 통과면 KEEP, 차단되면 REVIEW(코퍼스 불일치)
            verdict = "KEEP" if not blocked else "REVIEW"
            reason = "근거 확보" if not blocked else "근거 없음 — 우리 코퍼스에 없는 질문일 수 있음"

        top = hits[0] if hits else None
        results.append({
            "id": x["id"],
            "type": x.get("type"),
            "proto_grounding": x.get("grounding_level"),
            "query": q,
            "gate": gate or "PASS",
            "n_hits": len(hits),
            "top_speaker": top.get("speaker") if top else None,
            "top_committee": top.get("committee") if top else None,
            "top_date": str(top.get("meeting_date")) if top else None,
            "verdict": verdict,
            "reason": reason,
            "grading_notes": x.get("grading_notes", ""),
            "must_not": x.get("must_not_checklist", []),
        })

    keep = [r for r in results if r["verdict"] == "KEEP"]
    review = [r for r in results if r["verdict"] == "REVIEW"]

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== 재검증 결과: {len(items)}문항 ===")
    print(f"KEEP {len(keep)} / REVIEW {len(review)}\n")
    print("유형별 KEEP 비율:")
    by_type = Counter(r["type"] for r in results)
    keep_type = Counter(r["type"] for r in keep)
    for t, n in by_type.most_common():
        print(f"  {t:20} {keep_type[t]:>2}/{n}")
    print(f"\n=== REVIEW {len(review)}건 (수동 확인 필요) ===")
    for r in review:
        print(f"  [{r['id']} {r['type']}/{r['proto_grounding']}] gate={r['gate']} — {r['reason']}")
        print(f"     Q: {r['query'][:64]}")
        if r["top_speaker"]:
            print(f"     top: {r['top_speaker']} ({r['top_committee']}, {r['top_date']})")
    print(f"\n리포트 저장: {OUT}")


if __name__ == "__main__":
    main()
