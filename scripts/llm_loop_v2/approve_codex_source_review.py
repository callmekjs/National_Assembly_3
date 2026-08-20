"""자동 검증과 별도 문항별 원문 대조를 모두 통과한 승인표를 확정한다.

이 스크립트가 검토를 했다고 가정해 130개를 자동 승인하지 않는다. 별도 검토 파일의
문항별 reviewer/note/check를 검증하고, 동결 자료에 자동검증 보고서 해시를 결합한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


REVIEW_CHECKS = {
    "question_scope",
    "answer_supported",
    "claims_match_question",
    "required_values_match_question",
    "reference_values_supported",
}


def validate_review_rows(expected_ids: set[str], rows: list[dict]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for line_no, row in enumerate(rows, 1):
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in by_id:
            raise ValueError(f"review line {line_no}: missing or duplicate id")
        by_id[row_id] = row
    if set(by_id) != expected_ids:
        missing = sorted(expected_ids - set(by_id))
        extra = sorted(set(by_id) - expected_ids)
        raise ValueError(f"review ids mismatch; missing={missing[:5]} extra={extra[:5]}")
    for row_id, row in by_id.items():
        if row.get("status") != "approved":
            raise ValueError(f"{row_id}: review is not approved")
        checks = row.get("checks")
        if not isinstance(checks, dict) or set(checks) != REVIEW_CHECKS or not all(value is True for value in checks.values()):
            raise ValueError(f"{row_id}: all partitioned review checks must be true")
        if not isinstance(row.get("reviewer"), str) or not row["reviewer"].strip():
            raise ValueError(f"{row_id}: reviewer is required")
        if not isinstance(row.get("note"), str) or not row["note"].strip():
            raise ValueError(f"{row_id}: per-item review note is required")
        try:
            datetime.fromisoformat(str(row.get("reviewed_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{row_id}: reviewed_at is invalid") from exc
    return by_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path, required=True)
    parser.add_argument("--reviewed-checklist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-answerable", type=int, default=120)
    parser.add_argument("--expected-unanswerable", type=int, default=10)
    args = parser.parse_args()

    report = json.loads(args.validation.read_text(encoding="utf-8"))
    if report.get("status") != "PASS" or report.get("hard_errors") or report.get("review_flags"):
        raise ValueError("authoring validation is not a clean PASS")

    answerable = load_jsonl(args.results)
    unanswerable = load_jsonl(args.unanswerable)
    proofs = load_jsonl(args.negative_proofs)
    if (
        len(answerable) != args.expected_answerable
        or len(unanswerable) != args.expected_unanswerable
        or len(proofs) != args.expected_unanswerable
    ):
        raise ValueError(
            f"expected {args.expected_answerable} answerable rows and "
            f"{args.expected_unanswerable} negative controls/proofs"
        )
    expected_ids = {row["id"] for row in answerable} | {row["id"] for row in unanswerable}
    review_by_id = validate_review_rows(expected_ids, load_jsonl(args.reviewed_checklist))
    rendered_answerable = "\n".join(json.dumps(row, ensure_ascii=False) for row in answerable)
    if any(token in rendered_answerable.lower() for token in ("wait invalid", "need redo", "자료 그대로 써야")):
        raise ValueError("model-internal contamination remains in reviewed gold")
    if any("\u0600" <= char <= "\u06ff" for char in rendered_answerable):
        raise ValueError("unexpected Arabic-script contamination remains in reviewed gold")
    proof_by_id = {row["id"]: row for row in proofs}
    if len(proof_by_id) != args.expected_unanswerable:
        raise ValueError("duplicate negative proof ids")
    for row in unanswerable:
        proof = proof_by_id.get(row["id"])
        if not proof or proof.get("speaker_chunk_count_in_target") != 0:
            raise ValueError(f"negative proof failed: {row['id']}")

    validation_hash = sha256(args.validation)
    approvals: list[dict] = []
    for wrapper in answerable:
        approvals.append({**review_by_id[wrapper["id"]], "validation_sha256": validation_hash})
    for row in unanswerable:
        approvals.append({
            **review_by_id[row["id"]],
            "validation_sha256": validation_hash,
            "negative_proof": {
                "target_chunk_count": proof_by_id[row["id"]]["target_chunk_count"],
                "speaker_chunk_count_in_target": 0,
                "proof_rule": proof_by_id[row["id"]]["proof_rule"],
            },
        })
    expected_total = args.expected_answerable + args.expected_unanswerable
    if len({row["id"] for row in approvals}) != expected_total:
        raise ValueError("approval ids are not unique")
    args.output.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in approvals) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "approved": expected_total, "reviewers": len({row['reviewer'] for row in approvals})}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
