"""승인된 작성 결과만 blind 100개와 reserve 30개로 동결 전 조립한다."""

from __future__ import annotations

import argparse
import importlib.util
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"module load failed: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def approved_ids(path: Path) -> set[str]:
    rows = load_jsonl(path)
    approved: set[str] = set()
    seen: set[str] = set()
    for line_no, row in enumerate(rows, 1):
        required = {"id", "status", "reviewer", "reviewed_at", "checks"}
        if not required.issubset(row):
            raise ValueError(f"approval line {line_no}: missing {sorted(required - row.keys())}")
        if row["id"] in seen:
            raise ValueError(f"approval line {line_no}: duplicate id {row['id']}")
        seen.add(row["id"])
        if row["status"] == "approved":
            checks = row["checks"]
            legacy_checks = {"question_scope", "answer_supported", "claims_match_question", "exact_values_supported"}
            partitioned_checks = {
                "question_scope", "answer_supported", "claims_match_question",
                "required_values_match_question", "reference_values_supported",
            }
            if (
                not isinstance(checks, dict)
                or set(checks) not in (legacy_checks, partitioned_checks)
                or not all(value is True for value in checks.values())
            ):
                raise ValueError(f"approval line {line_no}: all review checks must be true")
            if not isinstance(row["reviewer"], str) or not row["reviewer"].strip():
                raise ValueError(f"approval line {line_no}: reviewer is required")
            try:
                datetime.fromisoformat(str(row["reviewed_at"]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"approval line {line_no}: reviewed_at is invalid") from exc
            approved.add(row["id"])
        elif row["status"] != "rejected":
            raise ValueError(f"approval line {line_no}: invalid status")
    return approved


def frozen(record: dict[str, Any]) -> dict[str, Any]:
    copy = deepcopy(record)
    copy["provenance"]["review_status"] = "frozen"
    return copy


def validate_negative_controls(
    records: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
    *,
    expected_count: int = 10,
) -> None:
    record_by_id = {row["id"]: row for row in records}
    proof_by_id = {row["id"]: row for row in proofs}
    if (
        len(record_by_id) != expected_count
        or len(proof_by_id) != expected_count
        or set(record_by_id) != set(proof_by_id)
    ):
        raise ValueError(
            f"negative controls and proofs must contain the same {expected_count} unique ids"
        )
    for row_id, record in record_by_id.items():
        proof = proof_by_id[row_id]
        expected = {
            "committee": proof["committee_filter"],
            "date_from": proof["date"],
            "date_to": proof["date"],
        }
        if record["filters"] != expected:
            raise ValueError(f"{row_id}: negative-control filter does not match proof")
        if proof.get("speaker_chunk_count_in_target") != 0:
            raise ValueError(f"{row_id}: target speaker is present")
        if int(proof.get("target_chunk_count") or 0) <= 0 or not proof.get("target_source_ids"):
            raise ValueError(f"{row_id}: target meeting existence is not proven")
        question = record["question"]
        for value in (proof["committee"], proof["date"], proof["speaker"]):
            if str(value) not in question:
                raise ValueError(f"{row_id}: question does not name proof value {value}")


def validate_negative_source_disjoint(
    dev: list[dict[str, Any]],
    queue: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
) -> None:
    dev_sources = {
        evidence["source_id"]
        for record in dev
        for evidence in record["gold"]["evidence"]
    }
    queue_sources = {row["source_id"] for row in queue}
    negative_sources = {
        source_id for proof in proofs for source_id in proof["target_source_ids"]
    }
    if dev_sources & negative_sources:
        raise ValueError("negative-control source overlaps development split")
    if queue_sources & negative_sources:
        raise ValueError("negative-control source overlaps answerable blind/reserve queue")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def materialize(
    queue: list[dict[str, Any]],
    results: list[dict[str, Any]],
    unanswerable: list[dict[str, Any]],
    approvals: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result_by_id = {row["id"]: row["record"] for row in results}
    expected_answerable = {row["id"] for row in queue}
    expected_unanswerable = {row["id"] for row in unanswerable}
    expected_all = expected_answerable | expected_unanswerable
    if len(result_by_id) != 120 or set(result_by_id) != expected_answerable:
        missing = sorted(expected_answerable - set(result_by_id))
        extra = sorted(set(result_by_id) - expected_answerable)
        raise ValueError(f"authored results must be exact 120; missing={missing[:5]} extra={extra[:5]}")
    if len(unanswerable) != 10 or len(expected_unanswerable) != 10:
        raise ValueError("unanswerable controls must be exact 10 unique records")
    if approvals != expected_all:
        missing = sorted(expected_all - approvals)
        extra = sorted(approvals - expected_all)
        raise ValueError(f"all 130 records need explicit approval; missing={missing[:5]} extra={extra[:5]}")

    blind = [frozen(result_by_id[f"blind-{index:03d}"]) for index in range(1, 91)]
    blind.extend(frozen(row) for row in sorted(unanswerable, key=lambda item: item["id"]))
    reserve = [frozen(result_by_id[f"reserve-{index:03d}"]) for index in range(1, 31)]
    return blind, reserve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--blind-output", type=Path, required=True)
    parser.add_argument("--reserve-output", type=Path, required=True)
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    authored_validator = load_module("authored_validator_materialize", script_dir / "validate_authored_gold.py")
    dataset_validator = load_module("dataset_validator_materialize", script_dir / "validate_dataset.py")
    freezer = load_module("freezer_materialize", script_dir / "freeze_dataset.py")

    queue = load_jsonl(args.queue)
    results = load_jsonl(args.results)
    report = authored_validator.validate_rows(queue, results, require_complete=True)
    if report["status"] != "PASS" or report["review_flags"]:
        raise SystemExit(
            f"authoring validation not clean: errors={len(report['hard_errors'])} "
            f"flags={len(report['review_flags'])}"
        )
    unanswerable = load_jsonl(args.unanswerable)
    proofs = load_jsonl(args.negative_proofs)
    validate_negative_controls(unanswerable, proofs)
    dev = dataset_validator.load_and_validate(args.dev, "dev")
    validate_negative_source_disjoint(dev, queue, proofs)
    blind, reserve = materialize(
        queue,
        results,
        unanswerable,
        approved_ids(args.approvals),
    )
    for line_no, row in enumerate(blind, 1):
        dataset_validator.validate_record(row, line_no, "blind")
    for line_no, row in enumerate(reserve, 1):
        dataset_validator.validate_record(row, line_no, "reserve")
    freezer.assert_disjoint({"dev": dev, "blind": blind, "reserve": reserve})
    write_jsonl(args.blind_output, blind)
    write_jsonl(args.reserve_output, reserve)
    print(json.dumps({
        "status": "READY_TO_FREEZE",
        "blind": len(blind),
        "reserve": len(reserve),
        "source_overlap": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
