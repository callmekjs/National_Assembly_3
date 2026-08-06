"""
[EVAL-4] retrieval_eval_v2 — 등급 라벨(qrels) 기반 검색 평가

옛 채점기(retrieval_eval.py)를 대체한다. 폐기 사유:
    정답 판정이 `any(t in text for t in crit["text_any"])` — **본문 문자열 포함 여부**인데
    키워드 검색 축이 정확히 같은 연산을 한다. 검색기가 채점 기준을 미리 아는 동어반복이라
    R@5 0.983 은 "근거 도달률"이 아니라 "키워드 도달률"이었다. 게다가 내용 판정 기준이
    아예 없는 6문항은 필터만 맞으면 전원 정답 처리됐다.

새 방식
    사람이 검토한 평가셋 20문항(질문 + 합격 기준 3개)에 대해 LLM 심판이 매긴
    등급 라벨(0/1/2)을 미리 만들어 두고, 채점기는 **조회만** 한다.
    → 채점이 결정적이고 빠르며, 검색 로직과 판정 로직이 분리된다.

지표 (모두 pooled — 아래 한계 참조)
    strict  : rel≥2 (합격 기준 3개를 모두 충족한 근거)만 정답
    loose   : rel≥1 (주제 관련 이상)도 정답 — 옛 수치와 비교 가능한 쪽
    R@5     : qa 모드가 실제로 보는 범위 (MODE_CONFIG["qa"]["limit"] = 5)
    R@10    : report 모드가 보는 범위 (MODE_CONFIG["report"]["limit"] = 10)
    MRR@10  : 첫 정답 등수의 역수 평균 (옛 지표와 연속성 유지)
    nDCG@10 : report 모드가 보는 깊이 — @5 만 재면 그 모드를 측정하지 못한다
    nDCG@5  : **신규.** 등급을 쓰는 유일한 지표. 리랭커는 "찾은 것을 위로 올리는" 일을
              하므로 등급 지표가 없으면 그 효과를 측정할 수 없다.
    unanswerable : 반전 채점 — 상위 k 에 rel≥1 이 0건이면 통과

**한계 (리포트에 반드시 병기)**
    라벨은 5개 검색 축이 찾아온 후보에만 붙는다. 어느 축에도 안 걸린 근거는 영원히
    라벨이 없으므로 이 값은 recall 이 아니라 **pooled recall** 이다.
    또한 라벨은 LLM 심판이 매긴 것이다 — 사람이 전수 검증하지 않았다.

실행
    python scripts/retrieval_eval_v2.py            # 3개 모드 전부
    python scripts/retrieval_eval_v2.py hybrid     # 특정 모드만
"""

import io
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from db import init_pool, close_pool                     # noqa: E402
from search_keyword import keyword_search                 # noqa: E402
from search_vector import vector_search                   # noqa: E402
from search_hybrid import hybrid_search                   # noqa: E402

EVAL_SET = PROJECT_ROOT / "data" / "eval" / "retrieval_eval_set_v2.json"
QRELS = PROJECT_ROOT / "data" / "eval" / "qrels_judged.jsonl"
REPORT_DIR = PROJECT_ROOT / "data" / "v1" / "reports"

K_MAX = 10          # 가져올 깊이 — R@10·MRR@10 이 필요로 하는 최댓값
K_QA = 5            # MODE_CONFIG["qa"]["limit"]
K_REPORT = 10       # MODE_CONFIG["report"]["limit"]

MODES = {
    "keyword": lambda q: keyword_search(q, limit=K_MAX),
    "vector":  lambda q: vector_search(q, limit=K_MAX),
    "hybrid":  lambda q: hybrid_search(q, limit=K_MAX),
}


def load_qrels() -> dict[str, dict[str, int]]:
    """qid → {chunk_id: grade}. 미판정(None)은 0 으로 두지 않고 제외한다 —
    '판정 못 함'을 '무관'으로 바꾸면 조용히 점수를 부풀린다."""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for line in QRELS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("grade") in (0, 1, 2):
            out[r["qid"]][r["chunk_id"]] = r["grade"]
    return out


def dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(grades: list[int], ideal_pool: list[int], k: int) -> float:
    """nDCG@k. **이상적 순위는 코퍼스 전체 라벨에서 만든다** — 검색 결과를 정렬해
    IDCG 를 만들면 '가져온 것 중 최선'이 되어 항상 1.0 에 가까워진다(흔한 구현 오류)."""
    got = dcg([2 ** g - 1 for g in grades[:k]])
    best = dcg([2 ** g - 1 for g in sorted(ideal_pool, reverse=True)[:k]])
    return got / best if best > 0 else 0.0


def eval_mode(mode: str, questions: list[dict], qrels: dict) -> dict:
    fn = MODES[mode]
    rows, t0 = [], time.time()

    for q in questions:
        labels = qrels.get(q["id"], {})
        hits = fn(q["q"])
        grades = [labels.get(h["chunk_id"], 0) for h in hits]

        if q["type"] == "unanswerable":
            # 반전 채점 — 상위 K_QA 안에 관련(rel≥1) 근거가 없어야 통과
            passed = not any(g >= 1 for g in grades[:K_QA])
            rows.append({"qid": q["id"], "type": q["type"], "unanswerable_pass": passed})
            continue

        n_labeled = len(labels)
        rows.append({
            "qid": q["id"], "type": q["type"],
            "strict@5":  any(g >= 2 for g in grades[:K_QA]),
            "strict@10": any(g >= 2 for g in grades[:K_REPORT]),
            "loose@5":   any(g >= 1 for g in grades[:K_QA]),
            "loose@10":  any(g >= 1 for g in grades[:K_REPORT]),
            "mrr": next((1.0 / i for i, g in enumerate(grades[:K_MAX], 1) if g >= 2), 0.0),
            "ndcg@5": ndcg_at_k(grades, list(labels.values()), K_QA),
            # report 모드는 상위 10개를 근거로 쓴다 — @5 만 재면 그 모드를 못 본다
            "ndcg@10": ndcg_at_k(grades, list(labels.values()), K_REPORT),
            "n_labeled": n_labeled,
            "top_grades": grades[:K_QA],
        })

    ans = [r for r in rows if r["type"] != "unanswerable"]
    una = [r for r in rows if r["type"] == "unanswerable"]
    n = len(ans) or 1

    def avg(key):
        return sum(float(r[key]) for r in ans) / n

    return {
        "mode": mode,
        "n_answerable": len(ans),
        "strict@5": avg("strict@5"), "strict@10": avg("strict@10"),
        "loose@5": avg("loose@5"), "loose@10": avg("loose@10"),
        "mrr@10": avg("mrr"), "ndcg@5": avg("ndcg@5"), "ndcg@10": avg("ndcg@10"),
        "unanswerable_pass": sum(1 for r in una if r["unanswerable_pass"]),
        "unanswerable_total": len(una),
        "elapsed_sec": round(time.time() - t0, 1),
        "rows": rows,
    }


def by_type(res: dict) -> dict:
    agg = defaultdict(lambda: {"n": 0, "s5": 0.0, "l5": 0.0})
    for r in res["rows"]:
        if r["type"] == "unanswerable":
            continue
        a = agg[r["type"]]
        a["n"] += 1
        a["s5"] += float(r["strict@5"])
        a["l5"] += float(r["loose@5"])
    return {k: {"n": v["n"], "strict@5": v["s5"] / v["n"], "loose@5": v["l5"] / v["n"]}
            for k, v in agg.items()}


def main():
    if not QRELS.exists():
        print(f"[FAIL] qrels 없음: {QRELS} — qrels_pool.py → qrels_judge.py 를 먼저 실행")
        sys.exit(1)

    want = sys.argv[1:] or list(MODES)
    questions = json.loads(EVAL_SET.read_text(encoding="utf-8"))["questions"]
    qrels = load_qrels()
    labeled = sum(len(v) for v in qrels.values())
    print(f"평가셋 v2 — {len(questions)}문항 / 라벨 {labeled:,}쌍 "
          f"(등급2 {sum(1 for v in qrels.values() for g in v.values() if g == 2)})")
    print("**pooled recall** — 5축이 못 찾은 근거는 라벨이 없다. recall 이 아니다.\n")

    init_pool()
    results = []
    try:
        for m in want:
            r = eval_mode(m, questions, qrels)
            results.append(r)
            print(f"[{m}]  {r['elapsed_sec']}초")
            print(f"  strict  R@5 {r['strict@5']:.3f}   R@10 {r['strict@10']:.3f}"
                  f"   (합격 기준 3개 모두 충족한 근거만 정답)")
            print(f"  loose   R@5 {r['loose@5']:.3f}   R@10 {r['loose@10']:.3f}"
                  f"   (주제 관련 이상)")
            print(f"  MRR@10  {r['mrr@10']:.3f}      nDCG@5 {r['ndcg@5']:.3f}"
                  f"   nDCG@10 {r['ndcg@10']:.3f}  (@5=qa · @10=report)")
            print(f"  unanswerable {r['unanswerable_pass']}/{r['unanswerable_total']}")
            t = by_type(r)
            print("  유형별 strict@5:", "  ".join(
                f"{k} {v['strict@5']:.2f}({v['n']})" for k, v in sorted(t.items())))
            print()
    finally:
        close_pool()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"retrieval_eval_v2_{stamp}.json"
    out.write_text(json.dumps({
        "generated_at": stamp,
        "eval_set": "retrieval_eval_set_v2.json (20문항, 사람 검토 20/20)",
        "qrels": {"pairs": labeled, "source": "LLM 심판 — 합격 기준 3개 대조"},
        "limitation": "pooled recall — 5개 검색 축이 찾은 후보에만 라벨이 있다. "
                      "라벨은 LLM 심판이 매긴 것이며 사람이 전수 검증하지 않았다.",
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
