"""실제 회의에 참석하지 않은 발언자를 조합해 답변 불가 대조군을 만든다.

각 조합은 DB에서 해당 위원회·날짜·발언자 청크 수가 0인지 확인하고 증명 파일에 남긴다.
외부 LLM이나 기존 평가 데이터는 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


def excluded_source_ids(paths: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for path in paths:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if isinstance(row.get("source_id"), str):
                excluded.add(row["source_id"])
            for source_id in row.get("target_source_ids", []):
                if isinstance(source_id, str):
                    excluded.add(source_id)
            for evidence in (row.get("gold") or {}).get("evidence", []):
                if isinstance(evidence.get("source_id"), str):
                    excluded.add(evidence["source_id"])
    return excluded


def item_id(split: str, index: int) -> str:
    if split not in {"dev", "blind"}:
        raise ValueError(f"unsupported split: {split}")
    return f"{split}-{index:03d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--split", choices=("dev", "blind"), default="blind")
    parser.add_argument("--id-start", type=int, default=91)
    parser.add_argument("--exclude-dataset", type=Path, action="append", default=[])
    args = parser.parse_args()
    load_dotenv(args.repo / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL missing")

    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(cm.full_name, cm.name) AS committee,
                   cm.name AS committee_filter,
                   ch.meeting_date::text,
                   array_agg(DISTINCT ch.source_id),
                   array_agg(DISTINCT ch.speaker) FILTER (WHERE ch.speaker IS NOT NULL),
                   count(*)
            FROM chunks ch
            JOIN committees cm ON cm.committee_id = ch.committee_id
            WHERE ch.meeting_date IS NOT NULL
            GROUP BY COALESCE(cm.full_name, cm.name), cm.name, ch.meeting_date
            HAVING count(*) >= 50
            ORDER BY committee, ch.meeting_date
        """)
        meetings = [
            {"committee": row[0], "committee_filter": row[1], "date": row[2], "source_ids": row[3],
             "speakers": set(row[4] or []), "chunk_count": row[5]}
            for row in cur.fetchall()
        ]
        cur.execute("""
            SELECT speaker, max(role), count(*)
            FROM chunks
            WHERE speaker IS NOT NULL AND role = '위원'
            GROUP BY speaker
            HAVING count(*) >= 30
            ORDER BY speaker
        """)
        speakers = [{"speaker": row[0], "role": row[1], "reference_count": row[2]} for row in cur.fetchall()]

    excluded = excluded_source_ids(args.exclude_dataset)
    meetings = [meeting for meeting in meetings if not (set(meeting["source_ids"]) & excluded)]
    if not meetings:
        raise SystemExit("all negative-control meetings were excluded")

    rng = random.Random(args.seed)
    rng.shuffle(meetings)
    rng.shuffle(speakers)
    by_committee: dict[str, list[dict]] = defaultdict(list)
    for meeting in meetings:
        by_committee[meeting["committee"]].append(meeting)
    committees = sorted(by_committee)
    rng.shuffle(committees)

    selected: list[tuple[dict, dict]] = []
    used_people: set[str] = set()
    round_index = 0
    while len(selected) < args.count:
        committee = committees[round_index % len(committees)]
        meeting = by_committee[committee][round_index % len(by_committee[committee])]
        person = next(
            (item for item in speakers
             if item["speaker"] not in meeting["speakers"] and item["speaker"] not in used_people),
            None,
        )
        if person:
            selected.append((meeting, person))
            used_people.add(person["speaker"])
        round_index += 1
        if round_index > len(meetings) * 2:
            raise SystemExit("negative control selection failed")

    verified_counts: dict[tuple[str, str, str], int] = {}
    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        for meeting, person in selected:
            key = (meeting["committee"], meeting["date"], person["speaker"])
            cur.execute("""
                SELECT count(*)
                FROM chunks ch
                JOIN committees cm ON cm.committee_id = ch.committee_id
                WHERE COALESCE(cm.full_name, cm.name) = %s
                  AND ch.meeting_date = %s::date
                  AND ch.speaker = %s
            """, key)
            verified_counts[key] = cur.fetchone()[0]
            if verified_counts[key] != 0:
                raise SystemExit(f"negative control is answerable: {key}")

    created_at = datetime.now().astimezone().isoformat()
    records = []
    proofs = []
    for offset, (meeting, person) in enumerate(selected, start=args.id_start):
        record_id = item_id(args.split, offset)
        speaker = person["speaker"]
        committee = meeting["committee"]
        committee_filter = meeting["committee_filter"]
        meeting_date = meeting["date"]
        records.append({
            "schema_version": "2.0",
            "id": record_id,
            "split": args.split,
            "category": "unanswerable",
            "question": f"{meeting_date} {committee} 회의에서 {speaker} 위원은 어떤 발언을 했는가?",
            "mode": "qa",
            "answerable": False,
            "filters": {"committee": committee_filter, "date_from": meeting_date, "date_to": meeting_date},
            "gold": {
                "answer": None,
                "required_claims": [],
                "forbidden_claims": [f"{speaker} 위원이 해당 회의에서 발언했다고 주장한다."],
                "exact_values": [
                    {"type": "person", "value": speaker},
                    {"type": "date", "value": meeting_date},
                    {"type": "committee", "value": committee},
                ],
                "evidence": [],
                "refusal_reason": f"해당 날짜의 {committee} 회의 원문에 {speaker} 위원의 발언 청크가 없다.",
            },
            "provenance": {
                "selection_method": "source_first_v2",
                "created_at": created_at,
                "review_status": "source_checked",
            },
        })
        proofs.append({
            "id": record_id,
            "committee": committee,
            "committee_filter": committee_filter,
            "date": meeting_date,
            "speaker": speaker,
            "target_source_ids": meeting["source_ids"],
            "target_chunk_count": meeting["chunk_count"],
            "speaker_chunk_count_in_target": verified_counts[(committee, meeting_date, speaker)],
            "speaker_reference_count_elsewhere": person["reference_count"],
            "proof_rule": "independent SQL COUNT(*) for committee/date/speaker equals zero",
        })

    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8", newline="\n",
    )
    args.proof.write_text(
        "".join(json.dumps(proof, ensure_ascii=False) + "\n" for proof in proofs),
        encoding="utf-8", newline="\n",
    )
    print(json.dumps({"status": "PASS", "records": len(records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
