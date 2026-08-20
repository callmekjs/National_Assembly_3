"""예비 30문항 3회 실행의 실패·근거·등급 변동을 보수적으로 집계한다."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


RUN_CONFIG_KEYS = {
    "git_head", "dataset_sha256", "base_url", "requested_records",
    "safe_settings", "runtime_sha256", "max_additional_cost",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"module load failed: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_run_configs(configs: list[dict[str, Any]]) -> str:
    if len(configs) != 3:
        raise ValueError("stability requires exactly 3 run configs")
    normalized: list[dict[str, Any]] = []
    for index, config in enumerate(configs, 1):
        missing = RUN_CONFIG_KEYS - set(config)
        if missing:
            raise ValueError(f"run {index}: config missing {sorted(missing)}")
        if config["requested_records"] != 30:
            raise ValueError(f"run {index}: requested_records must be 30")
        normalized.append({key: config[key] for key in sorted(RUN_CONFIG_KEYS)})
    if any(item != normalized[0] for item in normalized[1:]):
        raise ValueError("stability run configs differ")
    return hashlib.sha256(
        json.dumps(normalized[0], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def summarize(dataset, runs, scorer) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = {row["id"]: row for row in dataset}
    if len(records) != 30 or len(runs) != 3:
        raise ValueError("stability requires 30 unique records and exactly 3 runs")
    indexed_runs = []
    for index, run in enumerate(runs, 1):
        indexed = {row["id"]: row for row in run}
        if len(indexed) != 30 or set(indexed) != set(records) or len(run) != 30:
            raise ValueError(f"run {index}: ids/count mismatch")
        indexed_runs.append(indexed)

    rows = []
    for row_id in sorted(records):
        scores = [scorer.score(records[row_id], run[row_id]) for run in indexed_runs]
        responses = [run[row_id].get("response") or {} for run in indexed_runs]
        stage1_passes = [score["stage1_verdict"] != "AUTO_FAIL" for score in scores]
        grounding = [response.get("grounding") for response in responses]
        flags = [sorted((response.get("verification") or {}).get("flags") or []) for response in responses]
        citation_sets = [
            sorted(item.get("chunk_id") for item in response.get("citations", []))
            for response in responses
        ]
        rows.append({
            "id": row_id,
            "stage1_passes": stage1_passes,
            "all_stage1_pass": all(stage1_passes),
            "auto_fail_reasons": [score["auto_fail_reasons"] for score in scores],
            "grounding": grounding,
            "grounding_stable": len(set(grounding)) == 1,
            "verification_flags": flags,
            "verification_stable": flags[0] == flags[1] == flags[2],
            "citation_sets": citation_sets,
            "citations_stable": citation_sets[0] == citation_sets[1] == citation_sets[2],
        })
    exposures = 90
    passed_exposures = sum(sum(row["stage1_passes"]) for row in rows)
    summary = {
        "records": 30,
        "runs": 3,
        "exposures": exposures,
        "stage1_passed_exposures": passed_exposures,
        "stage1_failure_exposures": exposures - passed_exposures,
        "all_three_passed_records": sum(row["all_stage1_pass"] for row in rows),
        "grounding_changed_records": [row["id"] for row in rows if not row["grounding_stable"]],
        "verification_changed_records": [row["id"] for row in rows if not row["verification_stable"]],
        "citations_changed_records": [row["id"] for row in rows if not row["citations_stable"]],
        "stage1_gate": "PASS" if passed_exposures == exposures else "FAIL",
        "note": "이 결과는 반복 안정성의 결정 규칙 검사이며 독립 의미 정확도 점수를 대체하지 않는다.",
    }
    return summary, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    scorer = load_module("score_stability", Path(__file__).parent / "score_deterministic.py")
    try:
        config_hash = validate_run_configs([
            json.loads((path.parent / "config.json").read_text(encoding="utf-8"))
            for path in args.results
        ])
        summary, rows = summarize(
            load_jsonl(args.dataset),
            [load_jsonl(path) for path in args.results],
            scorer,
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    summary["run_config_match"] = True
    summary["run_config_sha256"] = config_hash
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "stability_rows.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n"
    )
    (args.output_dir / "stability_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["stage1_gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
