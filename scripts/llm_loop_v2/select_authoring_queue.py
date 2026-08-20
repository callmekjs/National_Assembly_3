"""독립 원문 풀에서 블라인드 90개와 예비 30개의 작성 대상을 고른다."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


RISK = re.compile(
    r"개의하겠습니다|산회를 선포|상정합니다|정회를 선포|마이크 중단|발언시간 초과|"
    r"선서,|위증의 벌|맹서합니다|그거|이것|그 부분|이 부분|아까|조금 전"
)
COMPARE = re.compile(r"반면|비교|증가|감소|늘었|줄었|이전|이후|각각|차이|과거|현재|전년|작년|올해|에서\s*[^ ]+로")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?:%|년|월|일|명|건|개|억|조|만|배|시간|개월)?")

TARGETS = {
    "blind": {"fact": 25, "entity_date": 20, "synthesis": 20, "comparison_timeline": 25},
    "reserve": {"fact": 8, "entity_date": 7, "synthesis": 7, "comparison_timeline": 8},
}

COMMITTEE_FILTERS = {
    "과학기술정보방송통신위원회": "과방위",
    "국방위원회": "국방위",
    "국토교통위원회": "국토위",
    "보건복지위원회": "복지위",
    "산업통상자원중소벤처기업위원회": "산자중기위",
    "외교통일위원회": "외통위",
    "재정경제기획위원회": "기재위",
    "정무위원회": "정무위",
    "행정안전위원회": "행안위",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scores(record: dict[str, Any]) -> dict[str, int]:
    text = record["text"]
    numbers = len(NUMBER.findall(text))
    sentences = text.count(".") + text.count("?")
    return {
        "comparison_timeline": len(COMPARE.findall(text)) * 4 + min(numbers, 12),
        "entity_date": min(numbers, 15) * 3 + (2 if record.get("speaker") else 0),
        "synthesis": min(len(text) // 250, 12) + min(sentences, 8),
        "fact": 20 - min(abs(len(text) - 700) // 100, 15),
    }


def balanced_pick(
    pool: list[dict[str, Any]],
    category: str,
    count: int,
    reserve_per_committee: int = 0,
) -> list[dict[str, Any]]:
    committee_count: Counter[str] = Counter()
    remaining_capacity = Counter(row["committee"] for row in pool)
    for committee in remaining_capacity:
        remaining_capacity[committee] = max(0, remaining_capacity[committee] - reserve_per_committee)
    selected: list[dict[str, Any]] = []
    remaining = list(pool)
    while remaining and len(selected) < count:
        eligible = [row for row in remaining if remaining_capacity[row["committee"]] > 0]
        if not eligible:
            break
        eligible.sort(
            key=lambda row: (
                committee_count[row["committee"]],
                -scores(row)[category],
                row["candidate_id"],
            )
        )
        chosen = eligible[0]
        remaining.remove(chosen)
        selected.append(chosen)
        committee_count[chosen["committee"]] += 1
        remaining_capacity[chosen["committee"]] -= 1
    if len(selected) != count:
        raise ValueError(f"not enough sources for {category}: {len(selected)}/{count}")
    return selected


def build_queue(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe = [row for row in records if not RISK.search(row["text"])]
    required = sum(sum(categories.values()) for categories in TARGETS.values())
    if len(safe) < required:
        raise ValueError(f"safe sources {len(safe)} < required {required}")

    available = {row["candidate_id"]: row for row in safe}
    assignments: list[tuple[str, str, dict[str, Any]]] = []
    # 먼저 구분 난도가 높은 유형을 확보한다.
    for category in ("comparison_timeline", "entity_date", "synthesis", "fact"):
        count = sum(TARGETS[split][category] for split in TARGETS)
        # 신규 개발셋도 모든 위원회를 다룰 수 있도록 각 위원회 원문을 2건 이상 남긴다.
        chosen = balanced_pick(list(available.values()), category, count, reserve_per_committee=2)
        for row in chosen:
            available.pop(row["candidate_id"])
        offset = 0
        for split in ("blind", "reserve"):
            split_count = TARGETS[split][category]
            for row in chosen[offset:offset + split_count]:
                assignments.append((split, category, row))
            offset += split_count

    blind_items = [(category, row) for split, category, row in assignments if split == "blind"]
    reserve_items = [(category, row) for split, category, row in assignments if split == "reserve"]
    queue: list[dict[str, Any]] = []
    for index, (category, row) in enumerate(blind_items, start=1):
        queue.append({
            "id": f"blind-{index:03d}", "split": "blind", "category": category,
            "committee_filter": COMMITTEE_FILTERS[row["committee"]], **row,
        })
    for index, (category, row) in enumerate(reserve_items, start=1):
        queue.append({
            "id": f"reserve-{index:03d}", "split": "reserve", "category": category,
            "committee_filter": COMMITTEE_FILTERS[row["committee"]], **row,
        })
    return queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    queue = build_queue(load_jsonl(args.pool))
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue), encoding="utf-8", newline="\n"
    )
    summary = {
        "records": len(queue),
        "splits": dict(Counter(row["split"] for row in queue)),
        "categories": dict(Counter(f"{row['split']}:{row['category']}" for row in queue)),
        "committees": dict(Counter(row["committee_filter"] for row in queue)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
