"""이슈↔청크 매핑 파이프라인 (POL-3 단계 2).

흐름 (docs/issue_module_spec.md — 사용자 결정 3):
  issues_seed.json → 이슈별 [후보 수집(하이브리드 축 직접 호출) → 저점수 컷
  → gpt-4o-mini 배치 관련도 판정] → issues·issue_chunks 적재 (통과분만 — 누락 > 오염)

실행:
  python scripts/build_issue_map.py --dry-run     # 후보 수·예상 비용만
  python scripts/build_issue_map.py               # 전체 이슈 매핑
  python scripts/build_issue_map.py --issue martial-law   # 단일 이슈 재실행 (시드 수정 시)
  python scripts/build_issue_map.py --issue X --judge-model gpt-4o --batch-size 10 --map-version v1.1
      # 판정 모델·배치·버전 재지정 (예: mini 오판정 이슈 재판정)
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"

MAP_VERSION = "v1.0"     # 매핑 방법 버전 — 수집·컷·판정 방식이 바뀌면 올린다
BATCH_SIZE = 20          # LLM 판정 배치 크기
DOC_CHARS = 600          # 판정에 보여줄 청크 발췌 길이 (reranker 와 동일)
PER_QUERY_VEC = 100      # seed_query 당 벡터 후보 수 (hnsw.ef_search=100 이 상한)
PER_KEYWORD_KW = 300     # seed_keyword 당 키워드 후보 수
MAX_TRANSIENT_RETRIES = 5   # 일시 오류 재시도 상한 — 소진 시 예외 전파 (이슈 실패로 기록, embeddings_v1 패턴)
_MODEL = "gpt-4o-mini"

_REQUIRED = ("issue_id", "title", "type", "description",
             "seed_keywords", "seed_queries", "anchor_meetings")


def load_seed(path: Path) -> list[dict]:
    """issues_seed.json 로드 + 검증. 시드 오류는 매핑 전체를 오염시키므로 즉시 실패."""
    issues = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for i, issue in enumerate(issues):
        for f in _REQUIRED:
            if f not in issue:
                raise ValueError(f"이슈 #{i}: 필수 필드 '{f}' 누락")
        if issue["type"] not in ("event", "policy"):
            raise ValueError(f"{issue['issue_id']}: type 은 event|policy (got {issue['type']!r})")
        if not issue["seed_keywords"] or not issue["seed_queries"]:
            raise ValueError(f"{issue['issue_id']}: seed_keywords·seed_queries 는 비울 수 없음")
        if issue["issue_id"] in seen:
            raise ValueError(f"issue_id 중복: {issue['issue_id']}")
        seen.add(issue["issue_id"])
    return issues


def cut_candidates(cands: dict[str, dict], threshold: float) -> dict[str, dict]:
    """저점수 컷 (1차 필터) — grounding 사전차단과 같은 기준:
    키워드 매치도 없고 벡터 유사도도 임계값 미만이면 LLM 판정에 보낼 가치가 없다."""
    return {
        cid: c for cid, c in cands.items()
        if c["kw_hit"] or (c["vec_score"] is not None and c["vec_score"] >= threshold)
    }


def make_batches(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def parse_judge_response(content: str, batch_size: int) -> list[int] | None:
    """판정 응답 → 관련 번호 목록. 구조 자체가 틀리면 None(재시도 신호),
    개별 항목 오류(범위 밖·비정수)는 그 항목만 버린다 (누락 우선)."""
    try:
        nums = json.loads(content).get("relevant")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(nums, list):
        return None
    return [n for n in nums if isinstance(n, int) and 0 <= n < batch_size]


def collect_candidates(issue: dict) -> dict[str, dict]:
    """시드 → 후보 합집합 {chunk_id: {"vec_score": 최대 유사도|None, "kw_hit": bool}}.

    hybrid_search 를 쓰지 않는 이유: limit 컷·turn dedup·reranker 가 걸려 있어
    '넓은 후보 수집'에 부적합 — 두 축을 직접 호출한다 (재현율 담당).
    """
    from search_keyword import keyword_search
    from search_vector import vector_search
    cands: dict[str, dict] = {}
    for q in issue["seed_queries"]:
        for hit in vector_search(q, limit=PER_QUERY_VEC):
            c = cands.setdefault(hit["chunk_id"], {"vec_score": None, "kw_hit": False})
            s = hit.get("score")
            if s is not None and (c["vec_score"] is None or s > c["vec_score"]):
                c["vec_score"] = round(float(s), 4)
    for kw in issue["seed_keywords"]:
        for hit in keyword_search(kw, limit=PER_KEYWORD_KW):
            c = cands.setdefault(hit["chunk_id"], {"vec_score": None, "kw_hit": False})
            c["kw_hit"] = True
    return cands


def fetch_texts(chunk_ids: list[str]) -> dict[str, dict]:
    """판정용 청크 본문·메타 일괄 조회 — 검색 응답의 snippet(200자)은 판정엔 부족."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.chunk_id, c.turn_id, c.speaker, c.role, co.name, c.meeting_date,
                   left(c.text, %s)
            FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
            WHERE c.chunk_id = ANY(%s)
        """, (DOC_CHARS, chunk_ids))
        return {r[0]: {"turn_id": r[1], "speaker": r[2], "role": r[3],
                       "committee": r[4], "date": str(r[5]), "text": r[6]}
                for r in cur.fetchall()}


_JUDGE_SYSTEM = """당신은 국회 회의록 발언이 특정 쟁점과 관련 있는지 판정하는 도우미다.
쟁점 정의와 번호 매긴 발언 목록이 주어진다. 각 발언에 대해:
- 쟁점의 사건·정책·대상을 실질적으로 다루면(질의·답변·주장·보고) 관련이다.
- 단어만 스치듯 지나가는 발언, 안건 목록 낭독, 의사진행 발언(개의·산회·표결 처리)은 무관이다.
- 확신이 없으면 무관으로 판정한다 — 누락이 오염보다 낫다.
반드시 아래 JSON 만 출력: {"relevant": [관련 있는 발언 번호 목록]}"""

_TRANSIENT = None  # 지연 로드 (openai import 비용)


def _transient_errors():
    global _TRANSIENT
    if _TRANSIENT is None:
        from openai import (APIConnectionError, APITimeoutError,
                            InternalServerError, RateLimitError)
        _TRANSIENT = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
    return _TRANSIENT


def _judge_batch(client, issue: dict, batch: list[tuple[str, dict]],
                  model: str = _MODEL) -> list[int] | None:
    """배치 1개 판정. 형식 위반 1회 재시도, 일시 오류는 지수 백오프 (embeddings_v1 패턴)."""
    docs = "\n".join(
        f"[{i}] ({m['committee']} {m['date']}) {m['speaker'] or ''} {m['role'] or ''}: {m['text']}"
        for i, (_, m) in enumerate(batch)
    )
    user = (f"쟁점: {issue['title']}\n정의: {issue['description']}\n\n발언 목록:\n{docs}")
    for attempt in range(2):          # 형식 위반 재시도 1회
        delay = 2
        for retry in range(MAX_TRANSIENT_RETRIES):  # 일시 오류 재시도
            try:
                resp = client.chat.completions.create(
                    model=model, temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _JUDGE_SYSTEM},
                              {"role": "user", "content": user}],
                )
                break
            except _transient_errors() as e:
                if retry == MAX_TRANSIENT_RETRIES - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 60)
        result = parse_judge_response(resp.choices[0].message.content, len(batch))
        if result is not None:
            return result
    return None  # 2회 모두 형식 위반 → 배치 제외 (누락 우선)


def store_mapping(issue: dict, rows: list[tuple], map_version: str = MAP_VERSION) -> int:
    """이슈 단위 DELETE+재삽입 + 행수 검증 (jsonl_to_postgres 패턴). rows:
    (chunk_id, turn_id, vec_score, kw_hit)."""
    from db import get_conn
    from psycopg2.extras import execute_values
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO issues (issue_id, title, type, description, seed)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (issue_id) DO UPDATE SET
              title = EXCLUDED.title, type = EXCLUDED.type,
              description = EXCLUDED.description, seed = EXCLUDED.seed
        """, (issue["issue_id"], issue["title"], issue["type"], issue["description"],
              json.dumps(issue, ensure_ascii=False)))
        cur.execute("DELETE FROM issue_chunks WHERE issue_id = %s", (issue["issue_id"],))
        execute_values(cur, """
            INSERT INTO issue_chunks
              (issue_id, chunk_id, turn_id, vec_score, kw_hit, judge, map_version)
            VALUES %s
        """, [(issue["issue_id"], cid, tid, vs, kh, "llm_relevant", map_version)
              for cid, tid, vs, kh in rows])
        cur.execute("SELECT count(*) FROM issue_chunks WHERE issue_id = %s",
                    (issue["issue_id"],))
        n = cur.fetchone()[0]
    if n != len(rows):
        raise RuntimeError(f"{issue['issue_id']}: 행수 불일치 (기대 {len(rows)}, DB {n})")
    return n


def _est_cost_usd(n_candidates: int) -> float:
    """판정 입력 비용 추정 — 후보당 발췌 600자 ≈ 540토큰(한국어 ~0.9tok/자) + 오버헤드.
    gpt-4o-mini 단가 기준 (--judge-model 이 다르면 실제 비용과 다를 수 있음, main()에서 경고 출력)."""
    input_tokens = n_candidates * (DOC_CHARS * 0.9 + 60)
    return input_tokens / 1e6 * 0.15


def process_issue(client, issue: dict, threshold: float, dry_run: bool,
                   judge_model: str = _MODEL, batch_size: int = BATCH_SIZE,
                   map_version: str = MAP_VERSION) -> dict:
    t0 = time.time()
    cands = collect_candidates(issue)
    kept = cut_candidates(cands, threshold)
    if dry_run:
        return {"issue_id": issue["issue_id"], "candidates": len(cands),
                "after_cut": len(kept), "est_cost": round(_est_cost_usd(len(kept)), 3)}
    meta = fetch_texts(list(kept))
    items = [(cid, meta[cid]) for cid in kept if cid in meta]
    relevant_ids, dropped = [], 0
    for batch in make_batches(items, size=batch_size):
        result = _judge_batch(client, issue, batch, model=judge_model)
        if result is None:
            dropped += 1
            continue
        relevant_ids += [batch[i][0] for i in result]
    rows = [(cid, meta[cid]["turn_id"], kept[cid]["vec_score"], kept[cid]["kw_hit"])
            for cid in relevant_ids]
    n = store_mapping(issue, rows, map_version=map_version)
    return {"issue_id": issue["issue_id"], "candidates": len(cands),
            "after_cut": len(kept), "mapped": n, "dropped_batches": dropped,
            "secs": round(time.time() - t0, 1)}


def main():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="후보 수·예상 비용만")
    ap.add_argument("--issue", help="단일 이슈만 재실행 (issue_id)")
    ap.add_argument("--judge-model", default=_MODEL, help="판정 LLM (기본: %(default)s)")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="LLM 판정 배치 크기")
    ap.add_argument("--map-version", default=MAP_VERSION, help="적재 시 찍을 map_version")
    args = ap.parse_args()

    if args.judge_model != _MODEL:  # 비용 추정은 mini 단가 기준 — 실제와 다를 수 있음
        print(f"[WARN] --judge-model={args.judge_model} — dry-run 비용 추정은 "
              f"{_MODEL} 단가 기준이라 실제와 다를 수 있음")

    issues = load_seed(SEED_PATH)
    if args.issue:
        issues = [i for i in issues if i["issue_id"] == args.issue]
        if not issues:
            print(f"[FAIL] issue_id 없음: {args.issue}")
            sys.exit(1)

    threshold = float(os.environ.get("GROUNDING_SIM_THRESHOLD", "0.4"))
    init_pool()
    client = None if args.dry_run else _get_client()

    failures = []
    total_cost = 0.0
    for issue in issues:
        try:
            r = process_issue(client, issue, threshold, args.dry_run,
                               judge_model=args.judge_model, batch_size=args.batch_size,
                               map_version=args.map_version)
        except Exception as e:
            failures.append((issue["issue_id"], f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {issue['issue_id']}: {type(e).__name__}: {e}")
            continue
        total_cost += r.get("est_cost", 0)
        print(f"[{'DRY' if args.dry_run else 'OK'}] {json.dumps(r, ensure_ascii=False)}")
    close_pool()

    if args.dry_run:
        print(f"예상 판정 입력 비용 합계: ~${total_cost:.2f}")
    if failures:  # 조용한 유실 금지 — 실패 이슈를 남기고 비정상 종료
        print(f"[FAIL] {len(failures)}개 이슈 실패: {[f[0] for f in failures]}")
        sys.exit(1)
    print("전체 완료")


if __name__ == "__main__":
    main()
