"""규칙 채점, 독립 의미 채점, 수동 재검토를 보수적으로 합친다.

PASS는 모든 자동·의미 조건을 통과한 경우에만 허용한다. REVIEW도 통과로
간주하지 않으며, 질문보다 넓게 작성된 잘못된 루브릭은 시스템 점수에서 제외한다.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def semantic_consistent(judgment: dict[str, Any]) -> bool:
    """PASS가 실제로 모든 엄격 조건을 만족하는지 확인한다."""
    if judgment["verdict"] != "PASS":
        return True
    scores_ok = all(judgment[name] == 2 for name in ("correctness", "completeness", "citation_support"))
    lists_empty = all(
        not judgment[name]
        for name in ("missing_required_claims", "unsupported_claims", "critical_errors")
    )
    return scores_ok and lists_empty


def combine(
    dataset: list[dict[str, Any]],
    results: list[dict[str, Any]],
    deterministic: list[dict[str, Any]],
    semantic: list[dict[str, Any]],
    adjudications: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ids = [item["id"] for item in dataset]
    maps = {
        "result": {item["id"]: item for item in results},
        "deterministic": {item["id"]: item for item in deterministic},
        "semantic": {item["id"]: item for item in semantic},
    }
    for name, mapping in maps.items():
        missing = sorted(set(ids) - set(mapping))
        extra = sorted(set(mapping) - set(ids))
        if missing or extra:
            raise ValueError(f"{name} ID mismatch: missing={missing}, extra={extra}")

    manual = {item["id"]: item for item in adjudications.get("items", [])}
    rows: list[dict[str, Any]] = []
    for item_id in ids:
        result = maps["result"][item_id]
        stage1 = maps["deterministic"][item_id]
        judge_row = maps["semantic"][item_id]
        judgment = judge_row["judgment"]
        review = manual.get(item_id)
        review_class = review["classification"] if review else None
        consistent = semantic_consistent(judgment)

        if review_class == "RUBRIC_INVALID":
            final_verdict = "EXCLUDED_RUBRIC_INVALID"
        elif review_class == "SYSTEM_FAIL":
            final_verdict = "FAIL"
        elif stage1["stage1_verdict"] == "AUTO_FAIL":
            final_verdict = "FAIL"
        elif not consistent or judgment["verdict"] == "REVIEW":
            final_verdict = "REVIEW"
        elif judgment["verdict"] == "FAIL":
            final_verdict = "FAIL"
        else:
            final_verdict = "PASS"

        rows.append(
            {
                "id": item_id,
                "grounding": (result.get("response") or {}).get("grounding"),
                "stage1_verdict": stage1["stage1_verdict"],
                "auto_fail_reasons": stage1["auto_fail_reasons"],
                "semantic_verdict": judgment["verdict"],
                "semantic_scores": {
                    "correctness": judgment["correctness"],
                    "completeness": judgment["completeness"],
                    "citation_support": judgment["citation_support"],
                },
                "semantic_consistent": consistent,
                "missing_required_claims": judgment["missing_required_claims"],
                "unsupported_claims": judgment["unsupported_claims"],
                "critical_errors": judgment["critical_errors"],
                "manual_classification": review_class,
                "manual_reason": review["reason"] if review else None,
                "final_verdict": final_verdict,
            }
        )

    included = [row for row in rows if row["final_verdict"] != "EXCLUDED_RUBRIC_INVALID"]
    passed = [row for row in included if row["final_verdict"] == "PASS"]
    failed = [row for row in included if row["final_verdict"] in {"FAIL", "REVIEW"}]
    unsupported_failures = [row for row in failed if row["unsupported_claims"]]
    full_failures = [row for row in failed if row["grounding"] == "FULL"]
    usage = {
        "input_tokens": sum(int(item.get("usage", {}).get("input_tokens", 0)) for item in semantic),
        "output_tokens": sum(int(item.get("usage", {}).get("output_tokens", 0)) for item in semantic),
    }
    pass_rate = len(passed) / len(included) if included else 0.0
    gate = {
        "minimum_pass_rate": 0.90,
        "maximum_unsupported_failures": 0,
        "actual_pass_rate": round(pass_rate, 4),
        "actual_unsupported_failures": len(unsupported_failures),
        "passed": pass_rate >= 0.90 and not unsupported_failures,
    }
    summary = {
        "raw_records": len(rows),
        "raw_semantic_verdicts": dict(Counter(row["semantic_verdict"] for row in rows)),
        "excluded_rubric_invalid": [row["id"] for row in rows if row["final_verdict"] == "EXCLUDED_RUBRIC_INVALID"],
        "included_records": len(included),
        "final_verdicts": dict(Counter(row["final_verdict"] for row in included)),
        "confirmed_failure_ids": [row["id"] for row in failed],
        "unsupported_failure_ids": [row["id"] for row in unsupported_failures],
        "full_but_failed_ids": [row["id"] for row in full_failures],
        "semantic_judge_usage": usage,
        "gate": gate,
        "note": "REVIEW는 PASS로 세지 않으며 질문보다 넓은 잘못된 루브릭은 시스템 점수에서 제외한다.",
    }
    return rows, summary


def render_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    failures = [row for row in rows if row["final_verdict"] in {"FAIL", "REVIEW"}]
    excluded = [row for row in rows if row["final_verdict"] == "EXCLUDED_RUBRIC_INVALID"]
    lines = [
        "# 신규 20문항 기준선 보수적 평가",
        "",
        "## 결론",
        "",
        f"- 원시 의미 채점: PASS {summary['raw_semantic_verdicts'].get('PASS', 0)} / "
        f"FAIL {summary['raw_semantic_verdicts'].get('FAIL', 0)} / REVIEW {summary['raw_semantic_verdicts'].get('REVIEW', 0)}",
        f"- 잘못된 루브릭 제외 후: {summary['final_verdicts'].get('PASS', 0)} / {summary['included_records']} 통과 "
        f"({summary['gate']['actual_pass_rate'] * 100:.1f}%)",
        f"- 통과 기준: 90% 이상이면서 근거 없는 추가 주장 0건 — **{'PASS' if summary['gate']['passed'] else 'FAIL'}**",
        f"- 기존 grounding이 FULL인데 최종 실패: {', '.join(summary['full_but_failed_ids']) or '없음'}",
        "",
        "## 확인된 시스템 실패",
        "",
    ]
    for row in failures:
        reason = row["manual_reason"] or "; ".join(
            row["unsupported_claims"] + row["missing_required_claims"] + row["critical_errors"]
        )
        lines.append(f"- **{row['id']}** ({row['grounding']}): {reason}")
    lines.extend(["", "## 점수에서 제외한 루브릭 결함", ""])
    for row in excluded:
        lines.append(f"- **{row['id']}**: {row['manual_reason']}")
    usage = summary["semantic_judge_usage"]
    lines.extend(
        [
            "",
            "## 독립 의미 채점 사용량",
            "",
            f"- 입력 토큰: {usage['input_tokens']:,}",
            f"- 출력 토큰: {usage['output_tokens']:,}",
            "- 모델: gpt-5.6-sol / reasoning high",
            "",
            "이 보고서의 PASS는 답변 품질을 최종 보증하지 않는다. 신규 블라인드 100문항은 개발 수정에 사용하지 않고 마지막에 한 번만 실행한다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--deterministic", type=Path, required=True)
    parser.add_argument("--semantic", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows, summary = combine(
        load_jsonl(args.dataset),
        load_jsonl(args.results),
        load_jsonl(args.deterministic),
        load_jsonl(args.semantic),
        load_json(args.adjudications),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "final_scores.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n"
    )
    (args.output_dir / "baseline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (args.output_dir / "baseline_report.md").write_text(render_report(rows, summary), encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
