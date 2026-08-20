"""신규 루프 실행 파일의 실제 토큰을 현재 공식 단가로 보수적으로 재집계한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PRICES = {
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    input_rate, output_rate = PRICES[model]
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def summarize(
    runs_dir: Path,
    approved_cap_usd: float = 10.0,
    baseline_spend_usd: float | None = None,
    additional_cap_usd: float | None = None,
) -> dict[str, Any]:
    answer_input = answer_output = rerank_input = rerank_output = 0
    judge_input = judge_output = 0
    answer_calls = judge_calls = 0
    author_input = author_output = author_calls = 0
    files: list[str] = []
    for path in sorted(runs_dir.glob("*/results.jsonl")):
        files.append(str(path))
        for row in load_jsonl(path):
            usage = (row.get("response") or {}).get("usage") or {}
            if not usage:
                continue
            answer_calls += 1
            answer_input += int(usage.get("input_tokens", 0))
            answer_output += int(usage.get("output_tokens", 0))
            rerank_input += int(usage.get("reranker_input_tokens", 0))
            rerank_output += int(usage.get("reranker_output_tokens", 0))
    judgment_paths = [
        path for path in sorted(runs_dir.glob("*/semantic_judgments*.jsonl"))
        if ".calls." not in path.name
    ]
    for path in judgment_paths:
        files.append(str(path))
        ledger = path.with_suffix(path.suffix + ".calls.jsonl")
        usage_path = ledger if ledger.exists() else path
        if ledger.exists():
            files.append(str(ledger))
        for row in load_jsonl(usage_path):
            usage = row.get("usage") or {}
            judge_calls += 1
            judge_input += int(usage.get("input_tokens", 0))
            judge_output += int(usage.get("output_tokens", 0))
    # Call ledgers are the source of truth.  Generated/repaired result files may
    # contain copied usage fields, so summing both would charge the same API call
    # twice.  Fall back to result files only for legacy runs with no call ledger.
    authoring_ledgers = sorted(runs_dir.parent.glob("authoring_results*.jsonl.calls.jsonl"))
    if authoring_ledgers:
        authoring_usage_paths = authoring_ledgers
    else:
        authoring_usage_paths = [
            path for path in sorted(runs_dir.parent.glob("authoring_results*.jsonl"))
            if ".calls." not in path.name and "repaired" not in path.stem
        ]
    for usage_path in authoring_usage_paths:
        files.append(str(usage_path))
        for row in load_jsonl(usage_path):
            usage = row.get("usage") or {}
            author_calls += 1
            author_input += int(usage.get("input_tokens", 0))
            author_output += int(usage.get("output_tokens", 0))

    components = {
        "answer_terra": {
            "calls": answer_calls,
            "input_tokens": answer_input,
            "output_tokens": answer_output,
            "estimated_cost_usd": round(cost("gpt-5.6-terra", answer_input, answer_output), 6),
        },
        "reranker_luna": {
            "calls": answer_calls,
            "input_tokens": rerank_input,
            "output_tokens": rerank_output,
            "estimated_cost_usd": round(cost("gpt-5.6-luna", rerank_input, rerank_output), 6),
        },
        "semantic_judge_sol": {
            "calls": judge_calls,
            "input_tokens": judge_input,
            "output_tokens": judge_output,
            "estimated_cost_usd": round(cost("gpt-5.6-sol", judge_input, judge_output), 6),
        },
        "gold_author_sol": {
            "calls": author_calls,
            "input_tokens": author_input,
            "output_tokens": author_output,
            "estimated_cost_usd": round(cost("gpt-5.6-sol", author_input, author_output), 6),
        },
    }
    total = sum(item["estimated_cost_usd"] for item in components.values())
    within_cap = total <= approved_cap_usd
    additional: dict[str, Any] = {}
    if baseline_spend_usd is not None and additional_cap_usd is not None:
        additional_spent = max(0.0, total - baseline_spend_usd)
        additional = {
            "baseline_spend_usd": baseline_spend_usd,
            "additional_cap_usd": additional_cap_usd,
            "additional_spent_usd": round(additional_spent, 6),
            "additional_remaining_usd": round(additional_cap_usd - additional_spent, 6),
            "within_additional_cap": additional_spent <= additional_cap_usd,
        }
        within_cap = within_cap and additional["within_additional_cap"]
    return {
        "pricing_checked_at": "2026-08-18",
        "price_source": "https://developers.openai.com/api/docs/models",
        "components": components,
        "estimated_total_usd": round(total, 6),
        "approved_cap_usd": approved_cap_usd,
        "remaining_to_cap_usd": round(approved_cap_usd - total, 6),
        "within_cap": within_cap,
        **additional,
        "files_scanned": files,
        "note": "응답 생성 terra, 재순위 luna, 독립 의미 채점·골드 작성 sol의 저장 토큰을 공식 단가로 재계산했다.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approved-cap", type=float, default=10.0)
    parser.add_argument("--baseline-spend", type=float)
    parser.add_argument("--additional-cap", type=float)
    args = parser.parse_args()
    if (args.baseline_spend is None) != (args.additional_cap is None):
        parser.error("--baseline-spend and --additional-cap must be used together")
    summary = summarize(
        args.runs_dir, args.approved_cap, args.baseline_spend, args.additional_cap
    )
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["within_cap"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
