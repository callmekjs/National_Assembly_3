"""의미 채점 전에 이미 확정된 하드 실패로 최종 게이트를 보수적으로 종료한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


PROFILES = {
    "strict": {"citation_min": 0.98, "refusal_min": 1.0, "max_auto_failures": 0},
    "portfolio": {"citation_min": 0.95, "refusal_min": 1.0, "max_auto_failures": 10},
}


def gold_evidence_cited(score: dict) -> bool:
    """최종 점수는 같은 문서가 아니라 정확한 정답 조각 인용만 인정한다."""
    return score.get("gold_evidence_chunk_cited") is True


def indexed(rows: list[dict], label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or row_id in result:
            raise ValueError(f"{label}: missing or duplicate id {row_id!r}")
        result[row_id] = row
    return result


def summarize(
    dataset: list[dict], deterministic: list[dict], expected: int = 100, profile: str = "strict",
    expected_unanswerable: int | None = None,
) -> dict:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    thresholds = PROFILES[profile]
    records = indexed(dataset, "dataset")
    scores = indexed(deterministic, "deterministic")
    if len(records) != expected or set(records) != set(scores):
        raise ValueError("dataset/deterministic ids or counts do not match")

    auto_failed = [row_id for row_id, score in scores.items() if score.get("stage1_verdict") == "AUTO_FAIL"]
    answerable = [row_id for row_id, row in records.items() if row["answerable"]]
    unanswerable = [row_id for row_id, row in records.items() if not row["answerable"]]
    if expected_unanswerable is not None and len(unanswerable) != expected_unanswerable:
        raise ValueError(
            f"expected {expected_unanswerable} unanswerable records, got {len(unanswerable)}"
        )
    if not answerable or not unanswerable:
        raise ValueError("dataset must contain answerable and unanswerable records")
    citation_passed = sum(gold_evidence_cited(scores[row_id]) for row_id in answerable)
    refusal_failed = []
    for row_id in unanswerable:
        reasons = set(scores[row_id].get("auto_fail_reasons") or [])
        if reasons & {"missing_required_refusal", "unanswerable_has_citations", "unexpected_refusal"}:
            refusal_failed.append(row_id)
    refusal_passed = len(unanswerable) - len(refusal_failed)
    citation_accuracy = citation_passed / len(answerable)
    refusal_accuracy = refusal_passed / len(unanswerable)
    hard_checks = {
        "citation_accuracy": citation_accuracy >= thresholds["citation_min"],
        "unanswerable_refusal": refusal_accuracy >= thresholds["refusal_min"],
        "stage1_auto_failures": len(auto_failed) <= thresholds["max_auto_failures"],
    }
    hard_veto = not all(hard_checks.values())
    return {
        "records": expected,
        "profile": profile,
        "thresholds": thresholds,
        "gate": "FAIL_EARLY" if hard_veto else "SEMANTIC_REVIEW_REQUIRED",
        "hard_veto": hard_veto,
        "auto_failed": len(auto_failed),
        "auto_failed_ids": sorted(auto_failed),
        "answerable_records": len(answerable),
        "citation_passed": citation_passed,
        "citation_accuracy": round(citation_accuracy, 6),
        "unanswerable_records": len(unanswerable),
        "refusal_passed": refusal_passed,
        "refusal_accuracy": round(refusal_accuracy, 6),
        "refusal_failed_ids": sorted(refusal_failed),
        "hard_checks": hard_checks,
        "semantic_review": "SKIPPED_AFTER_HARD_VETO" if hard_veto else "REQUIRED",
        "overall_accuracy": None,
        "critical_hallucinations": None,
        "note": (
            "하드 기준 하나라도 실패하면 의미 채점 결과와 무관하게 최종 FAIL이다. "
            "하드 기준을 통과한 경우에만 독립 의미 채점을 진행한다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--deterministic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, default=100)
    parser.add_argument("--expected-unanswerable", type=int)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="strict")
    args = parser.parse_args()
    report = summarize(
        load_jsonl(args.dataset), load_jsonl(args.deterministic), args.expected_records, args.profile,
        (args.expected_unanswerable
         if args.expected_unanswerable is not None
         else (10 if args.expected_records == 100 else None)),
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["hard_veto"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
