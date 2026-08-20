"""골드와 실제 인용 전문을 결합해 의미 채점 입력을 만든다."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def index_unique(rows, label: str):
    indexed = {}
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in indexed:
            raise ValueError(f"{label}: missing or duplicate id {row_id!r}")
        indexed[row_id] = row
    return indexed


def build_inputs(records, results, full, proofs=None):
    record_by_id = index_unique(records, "dataset")
    result_by_id = index_unique(results, "results")
    if set(record_by_id) != set(result_by_id):
        missing = sorted(set(record_by_id) - set(result_by_id))
        extra = sorted(set(result_by_id) - set(record_by_id))
        raise ValueError(f"result ids mismatch; missing={missing[:5]} extra={extra[:5]}")
    proof_by_id = index_unique(proofs or [], "negative proofs")
    expected_negative = {row_id for row_id, row in record_by_id.items() if not row["answerable"]}
    if proofs is not None and not expected_negative.issubset(proof_by_id):
        missing = sorted(expected_negative - set(proof_by_id))
        raise ValueError(f"negative proof ids mismatch; missing={missing[:5]}")

    output = []
    for row_id in record_by_id:
        record = record_by_id[row_id]
        result = result_by_id[row_id]
        response = result.get("response")
        if not isinstance(response, dict):
            raise ValueError(f"{row_id}: response missing")
        citations = response.get("citations", [])
        support_ids = [
            chunk_id
            for citation in citations
            for chunk_id in (
                citation.get("support_chunk_ids")
                if isinstance(citation.get("support_chunk_ids"), list)
                else [citation.get("chunk_id")]
            )
            if chunk_id
        ]
        missing_chunks = [chunk_id for chunk_id in support_ids if chunk_id not in full]
        if missing_chunks:
            raise ValueError(f"{row_id}: cited chunks missing from DB: {missing_chunks[:3]}")
        cited_evidence = []
        for citation in citations:
            ids = citation.get("support_chunk_ids")
            if not isinstance(ids, list) or not ids:
                ids = [citation.get("chunk_id")]
            evidence_rows = [full[chunk_id] for chunk_id in ids if chunk_id]
            if not evidence_rows:
                continue
            primary = dict(full.get(citation.get("chunk_id")) or evidence_rows[0])
            primary["n"] = citation.get("n")
            primary["support_chunk_ids"] = ids
            primary["text"] = " ".join(row["text"] for row in evidence_rows)
            cited_evidence.append(primary)
        output.append({
            "id": record["id"],
            "answerable": record["answerable"],
            "question": record["question"],
            "gold_answer": record["gold"]["answer"],
            "required_claims": record["gold"]["required_claims"],
            "forbidden_claims": record["gold"]["forbidden_claims"],
            "exact_values": record["gold"]["exact_values"],
            # 답변 불가 문항의 exact_values는 부재 범위를 검증하는 질문 메타데이터다.
            # 표준 거절문이 질문의 인명·날짜·위원회를 반복하도록 강제하지 않는다.
            "required_exact_values": (
                record["gold"].get("required_exact_values", record["gold"]["exact_values"])
                if record["answerable"]
                else []
            ),
            "reference_values": record["gold"].get("reference_values", []),
            "refusal_reason": record["gold"]["refusal_reason"],
            "negative_control_proof": proof_by_id.get(row_id),
            "candidate_answer": response.get("answer"),
            "candidate_grounding": response.get("grounding"),
            "cited_evidence": cited_evidence,
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path)
    args = parser.parse_args()
    load_dotenv(args.repo / ".env")
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL missing")
    records = load_jsonl(args.dataset)
    results = load_jsonl(args.results)
    chunk_ids = sorted({
        chunk_id
        for result in results
        for citation in (result.get("response") or {}).get("citations", [])
        for chunk_id in (
            citation.get("support_chunk_ids")
            if isinstance(citation.get("support_chunk_ids"), list)
            else [citation.get("chunk_id")]
        )
        if chunk_id
    })
    with psycopg2.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT ch.chunk_id, ch.speaker, ch.role, co.name,
                   ch.meeting_date::text, ch.page_start, ch.text
            FROM chunks ch
            JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.chunk_id = ANY(%s)
        """, (chunk_ids,))
        full = {
            row[0]: {"chunk_id": row[0], "speaker": row[1], "role": row[2],
                     "committee": row[3], "date": row[4], "page_start": row[5], "text": row[6]}
            for row in cur.fetchall()
        }
    output = build_inputs(
        records,
        results,
        full,
        load_jsonl(args.negative_proofs) if args.negative_proofs else None,
    )
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in output),
        encoding="utf-8", newline="\n",
    )
    print(json.dumps({"status": "PASS", "records": len(output), "cited_chunks": len(full)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
