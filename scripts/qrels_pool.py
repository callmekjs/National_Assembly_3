"""
[EVAL-1] qrels_pool — 등급 라벨(qrels) 제작 1단계: 후보 풀링

배경 (감사 2026-08-05)
    기존 retrieval_eval.py 의 정답 판정은 "결과 본문에 질문 키워드가 포함되는가"인데
    키워드 검색 축이 정확히 같은 연산을 한다 — 검색기가 채점 기준을 미리 아는
    동어반복이라 R@5 0.983 은 검색 품질이 아니라 키워드 도달률에 가깝다.
    이 스크립트는 그 자를 등급 라벨(0/1/2) 기반으로 바꾸기 위한 첫 단계다.

방식 (TREC 풀링)
    질문마다 서로 다른 검색 축 5개를 각각 depth 20 으로 돌려 후보를 합집합으로 모은다.
    축이 서로 다른 실패 모드를 가지므로 합치면 단일 축이 놓치는 근거가 줄어든다.
      1. keyword        — 키워드축 (자동 필터 적용)
      2. vector         — 벡터축 (원문 질문, 필터 없음)
      3. hybrid         — RRF 융합, 리랭커 OFF
      4. hybrid_rerank  — RRF 융합, 리랭커 ON
      5. keyword_nofilter — 키워드축, **위원회·날짜 필터 없이**
    5번이 있는 이유: extract_filters 가 질문에서 날짜·위원회를 자동 추출해 거는데,
    그 필터가 과하게 좁히면 정답이 애초에 후보에 못 든다. 필터를 뺀 축을 하나 둬야
    "필터 때문에 놓친 근거"가 풀에 들어온다.

**한계 (반드시 리포트에 병기할 것)**
    풀링은 5축 어디에도 안 걸린 근거를 영원히 라벨하지 못한다. 따라서 이 qrels 로
    잰 값은 "recall" 이 아니라 **"pooled recall"** 이다. 진짜 recall 을 재려면
    코퍼스 전수 라벨이 필요하고 그건 별도 규모의 작업이다.

결정성
    2026-08-05 수정으로 검색이 결정적이 됐다 (aliases.expand_aliases 순서 고정 +
    search_keyword ORDER BY 타이브레이커). 따라서 PYTHONHASHSEED 재실행 같은 봉합이
    필요 없다 — 같은 입력이면 같은 풀이 나온다. 메타에 시드를 기록해 사후 확인 가능.

출력
    data/eval/qrels_pool.jsonl — 질문 1개당 1줄
    {"qid", "question", "type", "candidates": [{chunk_id, found_by:{축:순위}, ...}]}
    각 후보에는 심판이 읽을 본문 + 앞뒤 턴 맥락 + 내용 해시를 함께 담는다.
    (해시: chunk_id 가 재배열돼도 내용으로 다시 찾아가기 위한 보험 — 과거 v1.2
     재처리에서 chunk_id 가 통째로 밀린 전례가 있다)

실행
    python scripts/qrels_pool.py              # 전체 63문항
    python scripts/qrels_pool.py --limit 5    # 앞 5문항만 (연결·형식 확인용)
"""

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from db import init_pool, close_pool, get_conn          # noqa: E402
from query_parser import extract_filters                 # noqa: E402
from search_keyword import keyword_search                # noqa: E402
from search_vector import vector_search                  # noqa: E402
import search_hybrid                                     # noqa: E402

EVAL_SET_PATH = PROJECT_ROOT / "data" / "eval" / "retrieval_eval_set_v2.json"
OUT_PATH = PROJECT_ROOT / "data" / "eval" / "qrels_pool.jsonl"

DEPTH = 20          # 축당 후보 수. 평가 깊이(R@5·R@10)보다 깊어야 "놓친 것"을 볼 수 있다
NEIGHBOR_TRUNC = 300  # 심판에게 줄 앞뒤 맥락 절단 길이


def _hybrid(q: str, rerank: bool):
    """리랭커 ON/OFF 를 env 로 토글해 하이브리드 실행 (is_enabled 가 호출 시점에 읽음)."""
    saved = os.environ.get("RERANKER_ENABLED")
    os.environ["RERANKER_ENABLED"] = "1" if rerank else "0"
    try:
        return search_hybrid.hybrid_search(q, limit=DEPTH)
    finally:
        if saved is None:
            os.environ.pop("RERANKER_ENABLED", None)
        else:
            os.environ["RERANKER_ENABLED"] = saved


def _keyword(q: str):
    """키워드축 — hybrid_search 와 같은 전처리(날짜·위원회 자동 필터)를 적용."""
    cleaned, committees, date_from, date_to = extract_filters(q)
    return keyword_search(cleaned, committees, date_from, date_to, limit=DEPTH)


def _keyword_nofilter(q: str):
    """필터 없이 키워드축만 — extract_filters 가 과하게 좁힌 경우를 풀에 살린다."""
    cleaned, _committees, _from, _to = extract_filters(q)
    return keyword_search(cleaned, None, None, None, limit=DEPTH)


AXES = {
    "keyword":          _keyword,
    "vector":           lambda q: vector_search(q, limit=DEPTH),
    "hybrid":           lambda q: _hybrid(q, rerank=False),
    "hybrid_rerank":    lambda q: _hybrid(q, rerank=True),
    "keyword_nofilter": _keyword_nofilter,
}


def fetch_context(chunk_ids: list[str]) -> dict[str, dict]:
    """후보의 본문·메타 + 앞뒤 턴 맥락을 한 번에 조회.

    맥락을 붙이는 이유: 회의록 청크는 중앙값 36자·81%가 150자 미만이라 한 줄만
    떼면("예, 그렇습니다") 심판이 무엇에 대한 답인지 알 수 없다. 실제 답변 생성도
    앞뒤 턴을 함께 넣으므로 심판에게도 같은 조건을 준다.
    """
    if not chunk_ids:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ch.chunk_id, ch.turn_id, ch.speaker, ch.role, ch.text,
                   co.name, ch.meeting_date::text, ch.page_start
            FROM chunks ch JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.chunk_id = ANY(%s)
            """,
            (chunk_ids,),
        )
        rows = cur.fetchall()

        # 앞뒤 턴 — turn_id 는 "{source}_turn_{번호}" 형식, 순번 ±1 (answer.py 와 같은 규칙)
        want = set()
        neighbor_of = {}
        for cid, tid, *_ in rows:
            prefix, _, no_str = tid.rpartition("_turn_")
            if not no_str.isdigit():
                continue
            no, width = int(no_str), len(no_str)
            prev_id = f"{prefix}_turn_{no - 1:0{width}d}" if no > 1 else None
            next_id = f"{prefix}_turn_{no + 1:0{width}d}"
            neighbor_of[cid] = (prev_id, next_id)
            want.update(t for t in (prev_id, next_id) if t)

        ctx_text: dict[str, str] = {}
        if want:
            cur.execute(
                """
                SELECT turn_id, speaker, string_agg(text, ' ' ORDER BY chunk_index)
                FROM chunks WHERE turn_id = ANY(%s) GROUP BY turn_id, speaker
                """,
                (sorted(want),),
            )
            for tid, spk, txt in cur.fetchall():
                ctx_text[tid] = f"{spk or ''}: {(txt or '')[:NEIGHBOR_TRUNC]}"

    out = {}
    for cid, tid, spk, role, text, com, date, page in rows:
        prev_id, next_id = neighbor_of.get(cid, (None, None))
        out[cid] = {
            "turn_id": tid, "speaker": spk, "role": role, "text": text,
            "committee": com, "date": date, "page_start": page,
            # chunk_id 재배열 보험 — 내용 해시로 다시 찾아갈 수 있게 (v1.2 전례)
            "text_sha1": hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16],
            "prev": ctx_text.get(prev_id or ""),
            "next": ctx_text.get(next_id or ""),
        }
    return out


def pool_one(item: dict) -> dict:
    """질문 1개 → 5축 합집합 후보 (축별 순위 기록 포함)."""
    q = item["q"]
    found_by: dict[str, dict[str, int]] = {}
    axis_err: dict[str, str] = {}

    for axis, fn in AXES.items():
        try:
            hits = fn(q)
        except Exception as e:
            # 한 축이 죽어도 나머지 축으로 계속 — 다만 조용히 넘기지 않고 기록한다
            # (죽은 축을 모른 채 "풀링했다"고 하면 감사에서 고친 그 실수의 반복)
            axis_err[axis] = f"{type(e).__name__}: {e}"
            continue
        for rank, h in enumerate(hits, start=1):
            found_by.setdefault(h["chunk_id"], {})[axis] = rank

    ctx = fetch_context(list(found_by))
    candidates = []
    for cid, axes in found_by.items():
        c = ctx.get(cid)
        if c is None:
            continue  # DB 에 없는 chunk_id (이론상 없음) — 조용히 버리지 않도록 아래서 센다
        candidates.append({"chunk_id": cid, "found_by": axes, **c})
    # 축 다양성(여러 축이 찾은 것) → 최선 순위 순. 심판 배치의 가독성용 정렬일 뿐
    candidates.sort(key=lambda c: (-len(c["found_by"]), min(c["found_by"].values())))

    return {
        "qid": item["id"],
        "question": q,
        "type": item.get("type"),
        "n_candidates": len(candidates),
        "n_missing_in_db": len(found_by) - len(candidates),
        "axis_errors": axis_err,
        "candidates": candidates,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N문항만 (연결·형식 확인용)")
    args = ap.parse_args()

    questions = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))["questions"]
    if args.limit:
        questions = questions[: args.limit]

    init_pool()
    t0 = time.time()
    rows, total_cand, axis_err_total = [], 0, 0
    try:
        for i, item in enumerate(questions, start=1):
            r = pool_one(item)
            rows.append(r)
            total_cand += r["n_candidates"]
            axis_err_total += len(r["axis_errors"])
            print(f"[{i:2d}/{len(questions)}] {r['qid']} {r['type']:<14} "
                  f"후보 {r['n_candidates']:3d}"
                  + (f"  축오류 {list(r['axis_errors'])}" if r["axis_errors"] else ""))
    finally:
        close_pool()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        meta = {
            "_meta": True,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "depth_per_axis": DEPTH,
            "axes": list(AXES),
            "pythonhashseed": os.environ.get("PYTHONHASHSEED", "(unset)"),
            "n_questions": len(rows),
            "n_candidates_total": total_cand,
            "note": "pooled — 5축 밖 근거는 라벨 없음. 결과는 recall 이 아니라 pooled recall",
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n{len(rows)}문항 / 후보 {total_cand:,}쌍 "
          f"(문항당 평균 {total_cand / max(1, len(rows)):.1f}) / {time.time() - t0:.0f}초")
    if axis_err_total:
        print(f"⚠ 축 실행 오류 {axis_err_total}건 — 위 로그와 파일의 axis_errors 확인")
    print(f"→ {OUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
