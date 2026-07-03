"""
[RAG-5] retrieval_eval
검색 품질 평가 — data/eval/retrieval_eval_set.json 의 문항으로
keyword/vector/hybrid 3개 모드를 채점한다.

평가셋: 63문항 13유형 (자체 18 + prototype 75문항에서 이식 45)
    유형: proper_noun / person / topic / mixed / comparison / date_based /
          multi_chunk / numerical_fact / cause_effect / quote_exact /
          aggregation / cross_committee / unanswerable

정답 판정: 기준(criteria) 방식 — chunk_id 재배열에도 유효
    - text_any:      본문에 하나라도 포함
    - text_all:      그룹별로 각각 any-of 만족 (text_any 와 AND)
    - speaker_any:   발언자 일치
    - committee_any: 위원회 일치 (정의 시 항상 AND 제약)
    - date_any:      회의 날짜 접두 일치, 예: "2025-07-14", "2024-06" (항상 AND 제약)
    - mode:          "or"(기본) = speaker 또는 text 만족 / "and" = 둘 다 만족

unanswerable 문항: 반전 채점 — 상위 k 에 기준 일치가 0건이면 통과
    (답이 없는 질문에 관련 문서를 만들어내지 않는지 검사 — REFUSED 판정의 기초)

지표:
    Recall@k — 상위 k 안에 정답 1개 이상인 문항 비율 (unanswerable 제외)
    MRR@10   — 첫 정답 등수의 역수 평균
    unanswerable_pass — 반전 채점 통과 수

실행:
    python scripts/retrieval_eval.py            # 3개 모드 전부
    python scripts/retrieval_eval.py hybrid     # 특정 모드만
"""

import io
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from db import init_pool, close_pool, get_conn          # noqa: E402
from search_keyword import keyword_search                # noqa: E402
from search_vector import vector_search                  # noqa: E402
from search_hybrid import hybrid_search                  # noqa: E402

K = 10
EVAL_SET_PATH = PROJECT_ROOT / "data" / "eval" / "retrieval_eval_set.json"

MODES = {
    "keyword": lambda q: keyword_search(q, limit=K),
    "vector":  lambda q: vector_search(q, limit=K),
    "hybrid":  lambda q: hybrid_search(q, limit=K),
}


def load_eval_set() -> list[dict]:
    data = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))
    return data["questions"]


def fetch_docs(chunk_ids: list[str]) -> dict[str, dict]:
    """판정에 필요한 전문·발언자·위원회·날짜를 DB 에서 가져온다 (snippet 은 200자뿐)."""
    if not chunk_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ch.chunk_id, ch.speaker, ch.text, co.name, ch.meeting_date::text
            FROM chunks ch JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.chunk_id = ANY(%s)
            """,
            (chunk_ids,),
        )
        return {r[0]: {"speaker": r[1], "text": r[2], "committee": r[3], "date": r[4]}
                for r in cur.fetchall()}


def is_relevant(doc: dict, crit: dict) -> bool:
    # 제약 조건 (정의되어 있으면 반드시 만족)
    if crit.get("committee_any") and doc["committee"] not in crit["committee_any"]:
        return False
    if crit.get("date_any"):
        if not any((doc["date"] or "").startswith(d) for d in crit["date_any"]):
            return False

    text = doc["text"] or ""
    has_spk = bool(crit.get("speaker_any"))
    has_txt = bool(crit.get("text_any"))

    spk_ok = doc["speaker"] in crit.get("speaker_any", []) if has_spk else None
    if has_txt:
        txt_ok = any(t in text for t in crit["text_any"])
        if txt_ok:
            for group in crit.get("text_all", []):
                if not any(t in text for t in group):
                    txt_ok = False
                    break
    else:
        txt_ok = None

    if has_spk and has_txt:
        if crit.get("mode") == "and":
            return spk_ok and txt_ok
        return spk_ok or txt_ok
    if has_spk:
        return spk_ok
    if has_txt:
        return txt_ok
    return True  # 순수 위원회/날짜 문항 (date_based)


def eval_mode(mode: str, eval_set: list[dict]) -> dict:
    fn = MODES[mode]
    details = []

    for item in eval_set:
        hits = fn(item["q"])
        docs = fetch_docs([h["chunk_id"] for h in hits])
        first_rank = None
        n_matches = 0
        for rank, h in enumerate(hits, start=1):
            doc = docs.get(h["chunk_id"])
            if doc and is_relevant(doc, item["criteria"]):
                n_matches += 1
                if first_rank is None:
                    first_rank = rank
        details.append({
            "id": item["id"], "q": item["q"], "type": item["type"],
            "unanswerable": bool(item.get("unanswerable")),
            "first_relevant_rank": first_rank, "n_matches_topk": n_matches,
        })

    ans = [d for d in details if not d["unanswerable"]]
    una = [d for d in details if d["unanswerable"]]
    n = len(ans)
    ranks = [d["first_relevant_rank"] for d in ans]
    recall5 = sum(1 for r in ranks if r and r <= 5) / n
    recall10 = sum(1 for r in ranks if r and r <= 10) / n
    mrr = sum(1.0 / r for r in ranks if r) / n
    una_pass = sum(1 for d in una if d["n_matches_topk"] == 0)

    # 유형별 분석 (unanswerable 제외)
    by_type: dict[str, dict] = defaultdict(lambda: {"n": 0, "hit5": 0, "mrr_sum": 0.0})
    for d in ans:
        t = by_type[d["type"]]
        t["n"] += 1
        r = d["first_relevant_rank"]
        if r and r <= 5:
            t["hit5"] += 1
        if r:
            t["mrr_sum"] += 1.0 / r
    type_table = {k: {"n": v["n"], "recall@5": round(v["hit5"] / v["n"], 3),
                      "mrr": round(v["mrr_sum"] / v["n"], 3)}
                  for k, v in sorted(by_type.items())}

    return {
        "recall@5": round(recall5, 3), "recall@10": round(recall10, 3),
        "mrr@10": round(mrr, 3),
        "unanswerable_pass": f"{una_pass}/{len(una)}",
        "by_type": type_table, "details": details,
    }


def main() -> None:
    targets = [m for m in sys.argv[1:] if m in MODES] or list(MODES)
    eval_set = load_eval_set()
    init_pool()
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "eval_set": str(EVAL_SET_PATH.name), "n_questions": len(eval_set),
              "k": K, "modes": {}}

    try:
        for mode in targets:
            t0 = time.time()
            result = eval_mode(mode, eval_set)
            result["elapsed_sec"] = round(time.time() - t0, 1)
            report["modes"][mode] = result
            print(f"[{mode:8s}] Recall@5={result['recall@5']:.3f}  "
                  f"Recall@10={result['recall@10']:.3f}  MRR@10={result['mrr@10']:.3f}  "
                  f"unanswerable={result['unanswerable_pass']}  ({result['elapsed_sec']}s)")
    finally:
        close_pool()

    # 유형별 표 + 실패 문항 (hybrid 기준, 없으면 마지막 모드)
    show = "hybrid" if "hybrid" in report["modes"] else targets[-1]
    print(f"\n=== 유형별 ({show}) ===")
    for t, v in report["modes"][show]["by_type"].items():
        print(f"  {t:14s} n={v['n']:2d}  Recall@5={v['recall@5']:.2f}  MRR={v['mrr']:.2f}")

    misses = [d for d in report["modes"][show]["details"]
              if not d["unanswerable"] and d["first_relevant_rank"] is None]
    if misses:
        print(f"\n[{show}] 상위 {K} 안에 정답 없음 ({len(misses)}건):")
        for d in misses:
            print(f"  - [{d['type']}] {d['q'][:50]}")
    una_fail = [d for d in report["modes"][show]["details"]
                if d["unanswerable"] and d["n_matches_topk"] > 0]
    if una_fail:
        print(f"\n[{show}] unanswerable 실패 (관련 문서를 반환함):")
        for d in una_fail:
            print(f"  - {d['q'][:50]} (일치 {d['n_matches_topk']}건)")

    out_dir = PROJECT_ROOT / "data" / "v1" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"retrieval_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n리포트 저장: {out}")


if __name__ == "__main__":
    main()
