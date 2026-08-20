"""비용 shard로 나뉜 골드 작성 결과를 원본 큐 순서로 감사 가능하게 병합한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge_rows(queue: list[dict[str, Any]], shards: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    queue_ids = [str(row["id"]) for row in queue]
    if len(queue_ids) != len(set(queue_ids)):
        raise ValueError("queue contains duplicate ids")
    by_id: dict[str, dict[str, Any]] = {}
    for shard in shards:
        for row in shard:
            row_id = str(row.get("id") or "")
            if row_id not in set(queue_ids):
                raise ValueError(f"result id not in queue: {row_id}")
            if row_id in by_id:
                raise ValueError(f"duplicate result id across shards: {row_id}")
            source = next(item for item in queue if item["id"] == row_id)
            if row.get("candidate_id") != source.get("candidate_id"):
                raise ValueError(f"candidate mismatch: {row_id}")
            by_id[row_id] = row
    missing = [row_id for row_id in queue_ids if row_id not in by_id]
    if missing:
        raise ValueError(f"missing result ids: {missing}")
    return [by_id[row_id] for row_id in queue_ids]


def overlay_rows(
    queue: list[dict[str, Any]], base: list[dict[str, Any]], replacements: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """완전한 base에서 검증 실패 ID만 교체하고 나머지 바이트 의미를 보존한다."""
    merged = merge_rows(queue, [base])
    by_id = {row["id"]: row for row in merged}
    replacement_ids: set[str] = set()
    queue_by_id = {row["id"]: row for row in queue}
    for shard in replacements:
        for row in shard:
            row_id = str(row.get("id") or "")
            if row_id not in queue_by_id:
                raise ValueError(f"replacement id not in queue: {row_id}")
            if row_id in replacement_ids:
                raise ValueError(f"duplicate replacement id: {row_id}")
            if row.get("candidate_id") != queue_by_id[row_id].get("candidate_id"):
                raise ValueError(f"replacement candidate mismatch: {row_id}")
            replacement_ids.add(row_id)
            by_id[row_id] = row
    return [by_id[row["id"]] for row in queue]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--replacement", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    queue = load_jsonl(args.queue)
    inputs = [load_jsonl(path) for path in args.input]
    replacements = [load_jsonl(path) for path in args.replacement]
    rows = (
        overlay_rows(queue, merge_rows(queue, inputs), replacements)
        if replacements else merge_rows(queue, inputs)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8", newline="\n",
    )
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "queue": {"path": str(args.queue.resolve()), "sha256": sha256(args.queue)},
        "inputs": [
            {"path": str(path.resolve()), "sha256": sha256(path), "records": len(load_jsonl(path))}
            for path in args.input
        ],
        "replacements": [
            {"path": str(path.resolve()), "sha256": sha256(path), "records": len(load_jsonl(path))}
            for path in args.replacement
        ],
        "output": {"path": str(args.output.resolve()), "sha256": sha256(args.output)},
        "records": len(rows),
        "unique_ids": len({row["id"] for row in rows}),
        "estimated_success_cost_usd": round(
            sum(float(row.get("estimated_cost_usd") or 0) for row in rows), 8
        ),
    }
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
