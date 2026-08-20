"""결정 규칙과 독립 의미 채점을 합쳐 블라인드 통과 여부를 계산한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def gold_evidence_cited(rule: dict[str, Any]) -> bool:
    """최종 점수는 같은 문서가 아니라 정확한 정답 조각 인용만 인정한다."""
    return rule.get("gold_evidence_chunk_cited") is True


PROFILES = {
    "strict": {
        "overall_accuracy_min": 0.90,
        "citation_accuracy_min": 0.98,
        "refusal_accuracy_min": 1.0,
        "critical_hallucinations_max": 0,
    },
    "portfolio": {
        "overall_accuracy_min": 0.90,
        "citation_accuracy_min": 0.95,
        "refusal_accuracy_min": 1.0,
        "critical_hallucinations_max": 0,
    },
    "generation": {
        "overall_accuracy_min": 0.95,
        "citation_accuracy_min": 0.95,
        "refusal_accuracy_min": 1.0,
        "critical_hallucinations_max": 0,
    },
}


def summarize(
    dataset: list[dict[str, Any]],
    deterministic: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
    *,
    expected_records: int,
    profile: str = "strict",
    expected_unanswerable: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    thresholds = PROFILES[profile]
    records = indexed(dataset, "dataset")
    stage1 = indexed(deterministic, "deterministic")
    judges = indexed(judgments, "judgments")
    expected_ids = set(records)
    if len(records) != expected_records:
        raise ValueError(f"expected {expected_records} dataset records, got {len(records)}")
    for label, values in (("deterministic", stage1), ("judgments", judges)):
        if set(values) != expected_ids:
            missing = sorted(expected_ids - set(values))
            extra = sorted(set(values) - expected_ids)
            raise ValueError(f"{label} ids mismatch; missing={missing[:5]} extra={extra[:5]}")

    final_rows: list[dict[str, Any]] = []
    for row_id in sorted(expected_ids):
        record = records[row_id]
        rule = stage1[row_id]
        judgment = judges[row_id].get("judgment") or {}
        auto_fail = rule.get("stage1_verdict") == "AUTO_FAIL"
        semantic_pass = judgment.get("verdict") == "PASS"
        unsupported = list(judgment.get("unsupported_claims") or [])
        critical = list(judgment.get("critical_errors") or [])
        critical_hallucination = bool(unsupported or critical)
        citation_ok = (
            not record["answerable"]
            or (
                gold_evidence_cited(rule)
                and judgment.get("citation_support") == 2
                and not any(
                    reason in set(rule.get("auto_fail_reasons") or [])
                    for reason in ("wrong_committee_citation", "citation_before_date_filter", "citation_after_date_filter")
                )
            )
        )
        refusal_ok = (
            record["answerable"]
            or (
                "missing_required_refusal" not in set(rule.get("auto_fail_reasons") or [])
                and "unanswerable_has_citations" not in set(rule.get("auto_fail_reasons") or [])
                and semantic_pass
            )
        )
        passed = not auto_fail and semantic_pass and not critical_hallucination and citation_ok and refusal_ok
        final_rows.append({
            "id": row_id,
            "answerable": record["answerable"],
            "passed": passed,
            "auto_fail_reasons": rule.get("auto_fail_reasons") or [],
            "semantic_verdict": judgment.get("verdict"),
            "citation_ok": citation_ok,
            "refusal_ok": refusal_ok,
            "unsupported_claims": unsupported,
            "critical_errors": critical,
            "critical_hallucination": critical_hallucination,
        })

    answerable = [row for row in final_rows if row["answerable"]]
    unanswerable = [row for row in final_rows if not row["answerable"]]
    if expected_unanswerable is not None and len(unanswerable) != expected_unanswerable:
        raise ValueError(
            f"expected {expected_unanswerable} unanswerable records, got {len(unanswerable)}"
        )
    passes = sum(row["passed"] for row in final_rows)
    citation_passes = sum(row["citation_ok"] for row in answerable)
    refusal_passes = sum(row["refusal_ok"] for row in unanswerable)
    critical_count = sum(row["critical_hallucination"] for row in final_rows)
    accuracy = passes / len(final_rows) if final_rows else 0.0
    citation_accuracy = citation_passes / len(answerable) if answerable else 0.0
    refusal_accuracy = (
        refusal_passes / len(unanswerable)
        if unanswerable else (1.0 if expected_unanswerable == 0 else 0.0)
    )
    checks = {
        "overall_accuracy": accuracy >= thresholds["overall_accuracy_min"],
        "citation_accuracy": citation_accuracy >= thresholds["citation_accuracy_min"],
        "critical_hallucinations": critical_count <= thresholds["critical_hallucinations_max"],
        "unanswerable_refusal": (
            (bool(unanswerable) or expected_unanswerable == 0)
            and refusal_accuracy >= thresholds["refusal_accuracy_min"]
        ),
    }
    summary = {
        "profile": profile,
        "thresholds": thresholds,
        "records": len(final_rows),
        "passed": passes,
        "failed": len(final_rows) - passes,
        "overall_accuracy": round(accuracy, 6),
        "answerable_records": len(answerable),
        "citation_passed": citation_passes,
        "citation_accuracy": round(citation_accuracy, 6),
        "unanswerable_records": len(unanswerable),
        "refusal_passed": refusal_passes,
        "refusal_accuracy": round(refusal_accuracy, 6),
        "critical_hallucinations": critical_count,
        "checks": checks,
        "gate": "PASS" if all(checks.values()) else "FAIL",
        "failed_ids": [row["id"] for row in final_rows if not row["passed"]],
    }
    return summary, final_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--deterministic", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, default=100)
    parser.add_argument("--expected-unanswerable", type=int)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="strict")
    args = parser.parse_args()
    try:
        summary, rows = summarize(
            load_jsonl(args.dataset),
            load_jsonl(args.deterministic),
            load_jsonl(args.judgments),
            expected_records=args.expected_records,
            profile=args.profile,
            expected_unanswerable=(
                args.expected_unanswerable
                if args.expected_unanswerable is not None
                else (10 if args.expected_records == 100 else None)
            ),
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "final_scores.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    (args.output_dir / "gate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
