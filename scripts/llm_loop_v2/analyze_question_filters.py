"""질문 파서 결과가 평가 정답의 날짜·위원회 필터와 일치하는지 검사한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from query_parser import extract_filters  # noqa: E402


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        row_id = record.get("id")
        if not isinstance(row_id, str) or row_id in seen:
            raise ValueError(f"missing or duplicate id: {row_id!r}")
        seen.add(row_id)
        _, committees, date_from, date_to = extract_filters(record["question"])
        expected = record.get("filters") or {}
        expected_committees = expected.get("committees")
        if expected_committees is None:
            committee = expected.get("committee")
            expected_committees = [committee] if committee else None
        actual = {
            "committees": committees,
            "date_from": date_from,
            "date_to": date_to,
        }
        wanted = {
            "committees": expected_committees,
            "date_from": expected.get("date_from"),
            "date_to": expected.get("date_to"),
        }
        if actual != wanted:
            mismatches.append({
                "id": row_id,
                "question": record["question"],
                "expected": wanted,
                "actual": actual,
            })
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "records": len(records),
        "matched": len(records) - len(mismatches),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(load_jsonl(args.dataset))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
