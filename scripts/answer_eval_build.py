"""답변 평가셋 2단계 — 정리된 75문항을 현재 시스템에 돌려 답변+근거 수집.

1단계(revalidate) 결과를 반영:
    - REVIEW 중 '거절이 정답'인 4건(이준석 부재 등)은 expect_refusal=True 로 표시
    - 나머지는 현재 시스템이 낸 실제 grounding 을 기록 (prototype 라벨은 버림)
    - grading_notes·must_not_checklist 는 prototype 에서 그대로 승계 (채점 자산)

각 문항에 대해 /query 를 실제 호출(LLM 답변 생성)하므로 비용 발생 (~수백 원).
결과: data/eval/answer_eval_set.json — 3단계(자동 채점)의 입력.

실행: python scripts/answer_eval_build.py
"""

import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EVAL_DIR = Path(__file__).parent.parent / "data" / "eval"
PROTO = EVAL_DIR / "prototype_75q_full.json"
REVAL = EVAL_DIR / "revalidation_report.json"
OUT = EVAL_DIR / "answer_eval_set.json"

# 1단계에서 '지목 대상 부재 — 거절이 정답'으로 확정된 문항 (killer feature 시험)
EXPECT_REFUSAL_IDS = {"eval_044", "eval_054", "eval_061", "eval_067"}


def main() -> None:
    import db

    db.init_pool()
    from answer import generate_answer
    from grounding import judge, pre_gate

    proto = {x["id"]: x for x in json.load(open(PROTO, encoding="utf-8"))}
    reval = {r["id"]: r for r in json.load(open(REVAL, encoding="utf-8"))}

    out = []
    t_start = time.time()
    for i, (qid, x) in enumerate(proto.items(), 1):
        q = x["query"]
        # 현재 시스템 실제 응답 (RAG-7 /query 와 동일 흐름: 검색→사전차단→답변→판정)
        from search_hybrid import hybrid_search
        hits = hybrid_search(q, limit=5)
        gate = pre_gate(hits)
        if gate is not None:
            answer_text, grounding, citations, source_block = (
                "제공된 회의록에서 확인할 수 없습니다.", gate, [], None
            )
            ungrounded = False
        else:
            result = generate_answer(q, mode="qa", hits=hits)
            grounding, ungrounded = judge(result)
            answer_text = result["answer"]
            citations = result["citations"]
            source_block = result.get("source_block")

        out.append({
            "id": qid,
            "type": x.get("type"),
            "query": q,
            "expect_refusal": qid in EXPECT_REFUSAL_IDS,
            "system_grounding": grounding,
            "ungrounded": ungrounded,
            "answer": answer_text,
            "citations": [
                {"n": c["n"], "speaker": c.get("speaker"), "role": c.get("role"),
                 "party": c.get("party"), "committee": c.get("committee"),
                 "date": c.get("date"), "snippet": c.get("snippet")}
                for c in citations
            ],
            "source_block": source_block,          # LLM 이 실제로 본 근거 (채점 재료)
            # prototype 채점 자산 승계
            "grading_notes": x.get("grading_notes", ""),
            "must_not_checklist": x.get("must_not_checklist", []),
            # 4기준 채점칸 (3단계에서 자동, 4단계에서 사람 검수로 확정)
            "grades": {
                "faithfulness": None,      # ① 근거 충실성 (환각 없나)
                "citation_accuracy": None, # ② 인용 정확성
                "classification": None,    # ③ 여야·정당·정부측 분류
                "refusal": None,           # ④ 거절 적절성
                "overall": None,
                "judge_notes": "",
                "human_reviewed": False,
            },
        })
        if i % 10 == 0:
            print(f"  {i}/{len(proto)} 수집 (누적 {time.time()-t_start:.0f}s)")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    gc = Counter(r["system_grounding"] for r in out)
    print(f"\n=== 답변 수집 완료: {len(out)}문항 ({time.time()-t_start:.0f}s) ===")
    print("현재 시스템 grounding 분포:", dict(gc))
    print(f"거절 시험 문항(expect_refusal): {sum(r['expect_refusal'] for r in out)}건")
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
