"""최초 문항별 검토를 보존하면서 재검토 결정만 ID 기준으로 교체한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge(base: list[dict[str, Any]], replacements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_ids = [str(row.get("id") or "") for row in base]
    if any(not row_id for row_id in base_ids) or len(base_ids) != len(set(base_ids)):
        raise ValueError("base review ids must be non-empty and unique")
    replacement_ids = [str(row.get("id") or "") for row in replacements]
    if any(row_id not in set(base_ids) for row_id in replacement_ids):
        raise ValueError("replacement review id not in base")
    if len(replacement_ids) != len(set(replacement_ids)):
        raise ValueError("replacement review ids must be unique")
    by_id = {row["id"]: row for row in base}
    by_id.update({row["id"]: row for row in replacements})
    return [by_id[row_id] for row_id in base_ids]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--replacement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    args = parser.parse_args()
    rows = merge(load_jsonl(args.base), load_jsonl(args.replacement))
    if len(rows) != args.expected_records:
        raise ValueError(f"expected {args.expected_records} review rows, got {len(rows)}")
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8", newline="\n",
    )
    print(json.dumps({"status": "MERGED", "records": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
