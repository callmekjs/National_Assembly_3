"""공개된 블라인드 실패를 다음 개발 회귀셋으로 복사한다.

최종 블라인드 점수를 다시 주장하지 않고, 실패 사례만 개발 데이터로 승격한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_IDS = ("blind-024", "blind-074", "blind-093", "blind-098")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def promote(source: Path, output: Path, ids: tuple[str, ...] = DEFAULT_IDS) -> list[dict]:
    records = {row["id"]: row for row in load_jsonl(source)}
    missing = [record_id for record_id in ids if record_id not in records]
    if missing:
        raise ValueError(f"source에 없는 ID: {', '.join(missing)}")
    promoted = []
    for index, record_id in enumerate(ids, start=1):
        row = dict(records[record_id])
        # schema v2 ID는 dev-NNN 고정이다. 기존 개발셋 dev-001~020과 충돌하지
        # 않도록 2차 승격 구간을 dev-101부터 사용한다.
        row["id"] = f"dev-{100 + index:03d}"
        row["split"] = "dev"
        row["provenance"] = {
            **row.get("provenance", {}),
            "promoted_from": record_id,
            "promotion_reason": "blind_v2_failure_round2",
        }
        promoted.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in promoted),
        encoding="utf-8", newline="\n",
    )
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = promote(args.source, args.output)
    print(json.dumps({"records": len(rows), "ids": [row["id"] for row in rows]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
