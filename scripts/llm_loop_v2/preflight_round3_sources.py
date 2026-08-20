"""round3 외부 작성 호출 전 source split·부정 증명·예산을 통합 점검한다."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from materialize_blind import validate_negative_controls  # noqa: E402
from preflight_blind import preflight  # noqa: E402
from select_dev_queue import TARGETS as DEV_TARGETS  # noqa: E402


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_sets(
    dev_queue: list[dict[str, Any]],
    final_queue: list[dict[str, Any]],
    dev_proofs: list[dict[str, Any]],
    blind_proofs: list[dict[str, Any]],
) -> dict[str, set[str]]:
    dev = {row["source_id"] for row in dev_queue}
    dev.update(source_id for proof in dev_proofs for source_id in proof["target_source_ids"])
    blind = {row["source_id"] for row in final_queue if row["split"] == "blind"}
    blind.update(source_id for proof in blind_proofs for source_id in proof["target_source_ids"])
    reserve = {row["source_id"] for row in final_queue if row["split"] == "reserve"}
    return {"dev": dev, "blind": blind, "reserve": reserve}


def assert_disjoint(sets: dict[str, set[str]]) -> None:
    names = list(sets)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            overlap = sets[left] & sets[right]
            if overlap:
                raise ValueError(f"source leakage {left}/{right}: {sorted(overlap)[:3]}")


def build_report(
    dev_queue: list[dict[str, Any]],
    final_queue: list[dict[str, Any]],
    dev_negatives: list[dict[str, Any]],
    dev_proofs: list[dict[str, Any]],
    blind_negatives: list[dict[str, Any]],
    blind_proofs: list[dict[str, Any]],
    spend: dict[str, Any],
) -> dict[str, Any]:
    validate_negative_controls(dev_negatives, dev_proofs, expected_count=2)
    validate_negative_controls(blind_negatives, blind_proofs, expected_count=10)
    dev_plan = [*dev_queue, *dev_negatives]
    base = preflight(dev_plan, final_queue, blind_negatives, blind_proofs, spend, stage="authoring")
    sets = source_sets(dev_queue, final_queue, dev_proofs, blind_proofs)
    assert_disjoint(sets)

    dev_categories = Counter(row["category"] for row in dev_queue)
    dev_committees = Counter(row["committee_filter"] for row in dev_queue)
    blind_committees = Counter(
        row["committee_filter"] for row in final_queue if row["split"] == "blind"
    )
    reserve_committees = Counter(
        row["committee_filter"] for row in final_queue if row["split"] == "reserve"
    )
    extra_checks = {
        "dev_answerable_exact_18": (
            len(dev_queue) == 18
            and {row["id"] for row in dev_queue} == {f"dev-{i:03d}" for i in range(1, 19)}
        ),
        "dev_distribution": dict(dev_categories) == DEV_TARGETS,
        "dev_nine_committees_two_each": len(dev_committees) == 9 and set(dev_committees.values()) == {2},
        "blind_all_nine_committees": len(blind_committees) == 9,
        "reserve_all_nine_committees": len(reserve_committees) == 9,
        "split_source_overlap_zero": True,
        "dev_source_records_exact_20": len(sets["dev"]) == 20,
        "blind_source_records_exact_100": len(sets["blind"]) == 100,
        "reserve_source_records_exact_30": len(sets["reserve"]) == 30,
    }
    base["checks"].update(extra_checks)
    budget_key = "budget_reserve_gte_11_00"
    source_ready = all(value for key, value in base["checks"].items() if key != budget_key)
    budget_ready = base["checks"].get(budget_key) is True
    base["source_ready"] = source_ready
    base["budget_ready"] = budget_ready
    base["status"] = (
        "READY_FOR_AUTHORING" if source_ready and budget_ready
        else "WAITING_FOR_BUDGET" if source_ready
        else "FAIL"
    )
    base["stage"] = base["status"]
    base["source_counts"] = {name: len(values) for name, values in sets.items()}
    base["dev_categories"] = dict(dev_categories)
    base["committee_counts"] = {
        "dev": dict(dev_committees),
        "blind": dict(blind_committees),
        "reserve": dict(reserve_committees),
    }
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev-queue", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--dev-unanswerable", type=Path, required=True)
    parser.add_argument("--dev-negative-proofs", type=Path, required=True)
    parser.add_argument("--blind-unanswerable", type=Path, required=True)
    parser.add_argument("--blind-negative-proofs", type=Path, required=True)
    parser.add_argument("--spend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(
            load_jsonl(args.dev_queue),
            load_jsonl(args.queue),
            load_jsonl(args.dev_unanswerable),
            load_jsonl(args.dev_negative_proofs),
            load_jsonl(args.blind_unanswerable),
            load_jsonl(args.blind_negative_proofs),
            json.loads(args.spend.read_text(encoding="utf-8")),
        )
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "READY_FOR_AUTHORING" else 2 if report["source_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
