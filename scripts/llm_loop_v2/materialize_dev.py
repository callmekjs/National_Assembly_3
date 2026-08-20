"""검토 승인된 답변 가능 18건과 답변 불가 2건을 신규 dev 20건으로 조립한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from materialize_blind import (  # noqa: E402
    approved_ids,
    frozen,
    load_jsonl,
    load_module,
    validate_negative_controls,
    validate_negative_source_disjoint,
    write_jsonl,
)


def materialize_dev(
    queue: list[dict[str, Any]],
    results: list[dict[str, Any]],
    unanswerable: list[dict[str, Any]],
    approvals: set[str],
) -> list[dict[str, Any]]:
    result_by_id = {row["id"]: row["record"] for row in results}
    expected_answerable = {f"dev-{index:03d}" for index in range(1, 19)}
    expected_unanswerable = {f"dev-{index:03d}" for index in range(19, 21)}
    if len(result_by_id) != 18 or set(result_by_id) != expected_answerable:
        raise ValueError("dev authored results must contain exact dev-001..dev-018")
    if len(unanswerable) != 2 or {row["id"] for row in unanswerable} != expected_unanswerable:
        raise ValueError("dev unanswerable controls must contain exact dev-019..dev-020")
    if {row["id"] for row in queue} != expected_answerable:
        raise ValueError("dev queue must contain exact dev-001..dev-018")
    expected_all = expected_answerable | expected_unanswerable
    if approvals != expected_all:
        raise ValueError("all 20 dev records need explicit approval")
    records = [frozen(result_by_id[f"dev-{index:03d}"]) for index in range(1, 19)]
    records.extend(frozen(row) for row in sorted(unanswerable, key=lambda item: item["id"]))
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    authored_validator = load_module("authored_validator_dev", script_dir / "validate_authored_gold.py")
    dataset_validator = load_module("dataset_validator_dev", script_dir / "validate_dataset.py")
    queue = load_jsonl(args.queue)
    results = load_jsonl(args.results)
    report = authored_validator.validate_rows(queue, results, require_complete=True)
    if report["status"] != "PASS" or report["review_flags"]:
        raise SystemExit(
            f"dev authoring validation not clean: errors={len(report['hard_errors'])} "
            f"flags={len(report['review_flags'])}"
        )
    unanswerable = load_jsonl(args.unanswerable)
    proofs = load_jsonl(args.negative_proofs)
    validate_negative_controls(unanswerable, proofs, expected_count=2)
    validate_negative_source_disjoint([], queue, proofs)
    records = materialize_dev(queue, results, unanswerable, approved_ids(args.approvals))
    for line_no, row in enumerate(records, 1):
        dataset_validator.validate_record(row, line_no, "dev")
    write_jsonl(args.output, records)
    print(json.dumps({"status": "READY_TO_FREEZE", "dev": 20}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
