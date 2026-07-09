"""이슈↔행위자 입장 판정 (POL-5). core turn 을 5택 LLM 판정해 issue_stances 에 적재.

흐름 (docs/superpowers/specs/2026-07-09-pol5-stance-analysis-design.md):
  issue_chunks.judge='llm_core' 의 DISTINCT turn_id → turn 텍스트(core chunk 이어붙임)
  → gpt-4o-mini 배치 5택 판정 → issue_stances upsert (턴 단위, 멱등).

실행:
  python scripts/build_issue_stance.py --issue medical-reform --dry-run
  python scripts/build_issue_stance.py --issue medical-reform
"""
import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from build_issue_map import (  # noqa: E402
    MAX_TRANSIENT_RETRIES, _transient_errors, load_seed, make_batches,
)

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"
MAP_VERSION = "s1.0"
BATCH_SIZE = 20
_MODEL = "gpt-4o-mini"
_STANCES = ("support", "oppose", "concern", "neutral", "none")

_SYSTEM = """당신은 국회 회의록 발언에서 발언자가 특정 쟁점에 취하는 입장을 판정하는 도우미다.
쟁점 정의와 번호 매긴 발언 목록이 주어진다. 각 발언을 다섯 중 하나로 분류한다:
- support: 쟁점의 정책·조치를 지지·찬성한다.
- oppose: 반대·철회를 주장한다.
- concern: 방향엔 동의하나 부작용·속도·방식에 우려를 표한다 (조건부).
- neutral: 입장 없이 다룬다 (사실 확인·질의·중계).
- none: 입장 판정 불가 (순수 절차·인사·다른 주제 경유).
확신이 없으면 neutral 또는 none 으로. 반드시 아래 JSON 만 출력, 발언 수와 같은 길이:
{"stances": ["support"|"oppose"|"concern"|"neutral"|"none", ...]}"""


def parse_stance_response(content: str, batch_size: int) -> list[str] | None:
    """응답 → 입장 목록. 길이 불일치·허용 밖 토큰·구조 오류면 None(재시도 신호)."""
    try:
        arr = json.loads(content).get("stances")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(arr, list) or len(arr) != batch_size:
        return None
    if any(s not in _STANCES for s in arr):
        return None
    return arr


def fetch_core_turns(issue_id: str) -> list[dict]:
    """이슈의 core turn 목록 — turn_id, speaker, role, date, 이어붙인 텍스트.
    core chunk 를 turn 별로 chunk_index 순 이어붙인다."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT c.turn_id, c.speaker, c.role, c.meeting_date::text AS date,
                   string_agg(c.text, ' ' ORDER BY c.chunk_index) AS text
            FROM issue_chunks ic JOIN chunks c ON c.chunk_id = ic.chunk_id
            WHERE ic.issue_id = %s AND ic.judge = 'llm_core'
            GROUP BY c.turn_id, c.speaker, c.role, c.meeting_date
            ORDER BY c.turn_id
        """, (issue_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _classify_batch(client, issue, batch, model=_MODEL):
    docs = "\n".join(f"[{i}] {t['speaker'] or ''} {t['role'] or ''}: {t['text'][:600]}"
                     for i, t in enumerate(batch))
    user = f"쟁점: {issue['title']}\n정의: {issue['description']}\n\n발언 목록:\n{docs}"
    for _ in range(2):
        delay = 2
        for retry in range(MAX_TRANSIENT_RETRIES):
            try:
                resp = client.chat.completions.create(
                    model=model, temperature=0, response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _SYSTEM},
                              {"role": "user", "content": user}])
                break
            except _transient_errors() as e:
                if retry == MAX_TRANSIENT_RETRIES - 1:
                    raise
                print(f"[retry] {type(e).__name__} — {delay}s")
                time.sleep(delay); delay = min(delay * 2, 60)
        result = parse_stance_response(resp.choices[0].message.content, len(batch))
        if result is not None:
            return result
    return None


def store_stances(issue_id, rows, model):
    """turn 단위 upsert (멱등). rows: [(turn_id, speaker, role, stance), ...]"""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        for turn_id, speaker, role, stance in rows:
            cur.execute("""
                INSERT INTO issue_stances (issue_id, turn_id, speaker, role, stance,
                                           judge_model, map_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (issue_id, turn_id) DO UPDATE SET
                    stance=EXCLUDED.stance, speaker=EXCLUDED.speaker, role=EXCLUDED.role,
                    judge_model=EXCLUDED.judge_model, map_version=EXCLUDED.map_version,
                    mapped_at=now()
            """, (issue_id, turn_id, speaker, role, stance, model, MAP_VERSION))
    return len(rows)


def classify_issue(client, issue, judge_model=_MODEL, dry_run=False):
    turns = fetch_core_turns(issue["issue_id"])
    if dry_run:
        return {"issue_id": issue["issue_id"], "core_turns": len(turns)}
    stored, dropped = [], 0
    for batch in make_batches(turns, BATCH_SIZE):
        result = _classify_batch(client, issue, batch, judge_model)
        if result is None:
            dropped += len(batch)
            print(f"[WARN] 배치 보류 {len(batch)}건")
            continue
        for t, s in zip(batch, result):
            stored.append((t["turn_id"], t["speaker"], t["role"], s))
    n = store_stances(issue["issue_id"], stored, judge_model)
    if n + dropped != len(turns):
        raise RuntimeError(f"행수 불일치 {n}+{dropped} != {len(turns)}")
    return {"issue_id": issue["issue_id"], "core_turns": len(turns), "stored": n, "dropped": dropped}


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--judge-model", default=_MODEL)
    args = ap.parse_args()
    issues = [i for i in load_seed(SEED_PATH) if i["issue_id"] == args.issue]
    if not issues:
        print(f"[FAIL] issue_id 없음: {args.issue}"); sys.exit(1)
    init_pool()
    client = None if args.dry_run else _get_client()
    print(f"MAP_VERSION={MAP_VERSION}")
    r = classify_issue(client, issues[0], args.judge_model, args.dry_run)
    print(f"[{'DRY' if args.dry_run else 'OK'}] {json.dumps(r, ensure_ascii=False)}")
    close_pool()


if __name__ == "__main__":
    main()
