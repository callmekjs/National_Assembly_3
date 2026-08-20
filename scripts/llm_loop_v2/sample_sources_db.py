"""PostgreSQL corpus에서 기존 split과 겹치지 않는 신규 평가 원문 후보를 뽑는다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from sample_sources import candidate, excluded_source_ids, sample_balanced  # noqa: E402
from select_authoring_queue import RISK  # noqa: E402


def authoring_safe(records: list[dict]) -> list[dict]:
    """질문 작성에 부적합한 절차·지시어 중심 청크를 샘플링 전에 제거한다."""
    return [row for row in records if not RISK.search(str(row.get("text") or ""))]


def fetch_candidates(db_url: str, excluded: set[str], seed: int, limit: int) -> list[dict]:
    with psycopg2.connect(db_url) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT ch.chunk_id, ch.source_id, ch.chunk_type, ch.speaker, ch.role,
                   co.full_name AS committee, co.name AS folder,
                   ch.meeting_date::text AS meeting_date, ch.page_start, ch.text
            FROM chunks ch
            JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.chunk_type = 'utterance'
              AND length(ch.text) BETWEEN 220 AND 1800
              AND ch.speaker IS NOT NULL AND btrim(ch.speaker) <> ''
              AND ch.meeting_date IS NOT NULL
              AND NOT (ch.source_id = ANY(%s))
              AND ch.text !~ '(개의하겠습니다|산회를 선포|의사일정|상정합니다|정회를 선포|속개하겠습니다|감사합니다[.]?$|수고하셨습니다[.]?$)'
            ORDER BY md5(ch.chunk_id || %s)
            LIMIT %s
            """,
            (sorted(excluded), str(seed), limit),
        )
        return [dict(row) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20260818)
    parser.add_argument("--exclude-dataset", type=Path, action="append", default=[])
    args = parser.parse_args()
    load_dotenv(args.repo / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL missing")
    excluded = excluded_source_ids(args.exclude_dataset)
    pool = fetch_candidates(db_url, excluded, args.seed, max(5000, args.count * 100))
    safe_pool = authoring_safe(pool)
    selected = sample_balanced(safe_pool, args.count, args.seed)
    if len(selected) != args.count:
        raise SystemExit(f"후보 부족: 요청 {args.count}, 추출 {len(selected)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for index, chunk in enumerate(selected, start=1):
            handle.write(json.dumps(candidate(chunk, index), ensure_ascii=False) + "\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(json.dumps({
        "status": "PASS",
        "queried_candidates": len(pool),
        "authoring_safe_candidates": len(safe_pool),
        "selected": len(selected),
        "seed": args.seed,
        "excluded_sources": len(excluded),
        "sha256": digest,
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
