"""평가 응답의 검색 trace에서 단계별 gold chunk recall을 보수적으로 계산한다."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


# 2026-08-19 포트폴리오 출시 기준. 소규모 유형 묶음(4~5건)은 한 건 실패가
# 20~25%이므로 전체 기준과 같은 85%를 강제하지 않고 75%를 하한으로 둔다.
RRF_RECALL_MIN = 0.85
FINAL_CONTEXT_RECALL_MIN = 0.85
CATEGORY_RRF_RECALL_MIN = 0.75


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def indexed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in result:
            raise ValueError(f"{label}: missing or duplicate id {row_id!r}")
        result[row_id] = row
    return result


def _stage_ids(trace: dict[str, Any], key: str, limit: int | None = None) -> set[str]:
    rows = trace.get(key)
    if not isinstance(rows, list):
        return set()
    selected = rows[:limit] if limit else rows
    ids: set[str] = set()
    for item in selected:
        if not isinstance(item, dict):
            continue
        if item.get("chunk_id"):
            ids.add(str(item["chunk_id"]))
        # final_results는 답변 모델에 실제로 복원해 보여준 turn 조각을 함께 기록한다.
        if key == "final_results" and isinstance(item.get("support_chunk_ids"), list):
            ids.update(str(value) for value in item["support_chunk_ids"] if value)
    return ids


def analyze(dataset: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    records = indexed(dataset, "dataset")
    responses = indexed(results, "results")
    if set(records) != set(responses):
        raise ValueError("dataset/results ids mismatch")

    answerable = [row_id for row_id, row in records.items() if row["answerable"]]
    stages = {
        "keyword_at_30": ("keyword_candidates", 30),
        "vector_at_30": ("vector_candidates", 30),
        "rrf_at_10": ("rrf_candidates", 10),
        "final_context_at_5": ("final_results", 5),
    }
    hits = {name: [] for name in stages}
    misses = {name: [] for name in stages}
    category_hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    category_totals: dict[str, int] = defaultdict(int)
    missing_trace: list[str] = []
    filter_mismatches: list[str] = []

    for row_id, record in records.items():
        response = responses[row_id].get("response") or {}
        trace = response.get("retrieval_trace")
        if not isinstance(trace, dict):
            missing_trace.append(row_id)
            continue
        filters = trace.get("filters") or {}
        expected_committee = record["filters"]["committee"]
        expected_committees = [expected_committee] if expected_committee else []
        if (
            filters.get("committees") != expected_committees
            or filters.get("date_from") != record["filters"]["date_from"]
            or filters.get("date_to") != record["filters"]["date_to"]
        ):
            filter_mismatches.append(row_id)

        # 답변 불가 문항도 trace·필터는 반드시 정확해야 한다. 잘못된 필터로 검색 0건이
        # 된 것을 정상 거절로 인정하지 않기 위해 recall 계산만 answerable에 제한한다.
        if not record["answerable"]:
            continue
        expected = {item["chunk_id"] for item in record["gold"]["evidence"]}
        category = record["category"]
        category_totals[category] += 1
        for stage_name, (trace_key, limit) in stages.items():
            matched = bool(expected & _stage_ids(trace, trace_key, limit))
            (hits if matched else misses)[stage_name].append(row_id)
            if matched:
                category_hits[category][stage_name] += 1

    denominator = len(answerable)
    recall = {
        name: round(len(hits[name]) / denominator, 6) if denominator else 0.0
        for name in stages
    }
    by_category = {
        category: {
            "records": total,
            **{
                name: round(category_hits[category][name] / total, 6) if total else 0.0
                for name in stages
            },
        }
        for category, total in sorted(category_totals.items())
    }
    category_rrf_ok = bool(by_category) and all(
        row["rrf_at_10"] >= CATEGORY_RRF_RECALL_MIN for row in by_category.values()
    )
    checks = {
        "trace_complete": not missing_trace,
        "filters_exact": not filter_mismatches,
        "rrf_recall_at_10": recall["rrf_at_10"] >= RRF_RECALL_MIN,
        "final_context_recall_at_5": recall["final_context_at_5"] >= FINAL_CONTEXT_RECALL_MIN,
        "category_rrf_recall_at_10": category_rrf_ok,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "answerable_records": denominator,
        "recall": recall,
        "by_category": by_category,
        "miss_ids": misses,
        "missing_trace_ids": missing_trace,
        "filter_mismatch_ids": filter_mismatches,
        "checks": checks,
        "thresholds": {
            "rrf_at_10": RRF_RECALL_MIN,
            "final_context_at_5": FINAL_CONTEXT_RECALL_MIN,
            "category_rrf_at_10": CATEGORY_RRF_RECALL_MIN,
            "filter_errors": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(load_jsonl(args.dataset), load_jsonl(args.results))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
