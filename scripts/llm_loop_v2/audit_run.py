"""평가 실행이 정확히 한 번씩, 올바른 질문·필터로 수집됐는지 감사한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(
    dataset: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    expected_records: int,
) -> dict[str, Any]:
    errors: list[str] = []
    if len(dataset) != expected_records:
        errors.append(f"dataset_count:{len(dataset)}")
    dataset_ids = [row.get("id") for row in dataset]
    result_ids = [row.get("id") for row in results]
    if len(set(dataset_ids)) != len(dataset_ids):
        errors.append("duplicate_dataset_id")
    if len(set(result_ids)) != len(result_ids):
        errors.append("duplicate_result_id")
    expected = {row["id"]: row for row in dataset}
    actual = {row.get("id"): row for row in results}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected), key=str)
    if missing:
        errors.append(f"missing_results:{missing[:5]}")
    if extra:
        errors.append(f"extra_results:{extra[:5]}")

    http_failures: list[str] = []
    mismatched_requests: list[str] = []
    malformed_responses: list[str] = []
    missing_trace: list[str] = []
    for row_id in sorted(set(expected) & set(actual)):
        record = expected[row_id]
        result = actual[row_id]
        # 평가도 실제 프론트처럼 question+mode만 의미 입력으로 보내며 include_trace는
        # 관측용일 뿐 검색 필터 힌트가 아니다.
        expected_request = {"mode": record["mode"], "include_trace": True}
        if result.get("question") != record["question"] or result.get("request") != expected_request:
            mismatched_requests.append(row_id)
        if result.get("http_status") != 200 or result.get("error"):
            http_failures.append(row_id)
            continue
        response = result.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("answer"), str):
            malformed_responses.append(row_id)
            continue
        citations = response.get("citations")
        if not isinstance(citations, list) or any(not isinstance(item.get("chunk_id"), str) for item in citations):
            malformed_responses.append(row_id)
        trace = response.get("retrieval_trace")
        trace_keys = {"keyword_candidates", "vector_candidates", "rrf_candidates", "final_results"}
        if (
            not isinstance(trace, dict)
            or not trace_keys.issubset(trace)
            or any(not isinstance(trace.get(key), list) for key in trace_keys)
        ):
            missing_trace.append(row_id)

    if mismatched_requests:
        errors.append(f"request_mismatch:{mismatched_requests[:5]}")
    if http_failures:
        errors.append(f"http_failure:{http_failures[:5]}")
    if malformed_responses:
        errors.append(f"malformed_response:{malformed_responses[:5]}")
    if missing_trace:
        errors.append(f"missing_retrieval_trace:{missing_trace[:5]}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "expected_records": expected_records,
        "dataset_records": len(dataset),
        "result_records": len(results),
        "unique_result_ids": len(set(result_ids)),
        "http_success": len(results) - len(http_failures),
        "missing_ids": missing,
        "extra_ids": extra,
        "request_mismatch_ids": mismatched_requests,
        "http_failure_ids": http_failures,
        "malformed_response_ids": malformed_responses,
        "missing_trace_ids": missing_trace,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = audit(
            load_jsonl(args.dataset),
            load_jsonl(args.results),
            expected_records=args.expected_records,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    report["dataset_sha256"] = sha256(args.dataset)
    report["results_sha256"] = sha256(args.results)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
