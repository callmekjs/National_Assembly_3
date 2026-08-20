"""블라인드·예비 큐와 겹치지 않는 개발용 답변 가능 원문 18건을 고른다."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from select_authoring_queue import COMMITTEE_FILTERS, scores  # noqa: E402


TARGETS = {
    "comparison_timeline": 5,
    "entity_date": 4,
    "synthesis": 4,
    "fact": 5,
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_dev_queue(
    records: list[dict[str, Any]], excluded_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    excluded_sources = {row["source_id"] for row in excluded_records}
    available = {row["candidate_id"]: row for row in records if row["source_id"] not in excluded_sources}
    required = sum(TARGETS.values())
    if len(available) < required:
        raise ValueError(f"unused safe sources {len(available)} < required {required}")

    committee_count: Counter[str] = Counter()
    max_per_committee = math.floor(required * 0.20)
    selected: list[tuple[str, dict[str, Any]]] = []
    for category in ("comparison_timeline", "entity_date", "synthesis", "fact"):
        for _ in range(TARGETS[category]):
            eligible = [
                row for row in available.values()
                if committee_count[row["committee"]] < max_per_committee
            ]
            if not eligible:
                raise ValueError(f"committee cap prevents selecting {required} dev sources")
            eligible.sort(key=lambda row: (
                committee_count[row["committee"]],
                -scores(row)[category],
                row["candidate_id"],
            ))
            chosen = eligible[0]
            selected.append((category, chosen))
            committee_count[chosen["committee"]] += 1
            available.pop(chosen["candidate_id"])

    queue: list[dict[str, Any]] = []
    for index, (category, row) in enumerate(selected, start=1):
        queue.append({
            "id": f"dev-{index:03d}",
            "split": "dev",
            "category": category,
            "committee_filter": COMMITTEE_FILTERS[row["committee"]],
            **row,
        })
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--exclude-queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue = build_dev_queue(load_jsonl(args.pool), load_jsonl(args.exclude_queue))
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "status": "PASS",
        "records": len(queue),
        "categories": dict(Counter(row["category"] for row in queue)),
        "committees": dict(Counter(row["committee_filter"] for row in queue)),
        "unique_sources": len({row["source_id"] for row in queue}),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
