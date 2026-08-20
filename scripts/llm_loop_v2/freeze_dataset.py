"""검토 완료된 v2 데이터셋을 동결하고 공개 질문 파일과 해시 명세를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def load_validator(script_dir: Path):
    path = script_dir / "validate_dataset.py"
    spec = importlib.util.spec_from_file_location("llm_loop_v2_validator", path)
    if not spec or not spec.loader:
        raise RuntimeError("validator module load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_filter_analyzer(script_dir: Path):
    path = script_dir / "analyze_question_filters.py"
    spec = importlib.util.spec_from_file_location("llm_loop_v2_filter_analyzer", path)
    if not spec or not spec.loader:
        raise RuntimeError("filter analyzer module load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_sources(records: list[dict[str, Any]]) -> set[str]:
    return {
        evidence["source_id"]
        for record in records
        for evidence in record["gold"]["evidence"]
    }


def assert_disjoint(named_records: dict[str, list[dict[str, Any]]]) -> None:
    names = list(named_records)
    for index, left in enumerate(names):
        left_sources = evidence_sources(named_records[left])
        for right in names[index + 1:]:
            overlap = left_sources & evidence_sources(named_records[right])
            if overlap:
                sample = sorted(overlap)[:3]
                raise ValueError(f"source leakage: {left}/{right}: {sample}")


def assert_question_filters(named_records: dict[str, list[dict[str, Any]]], analyzer: Any) -> None:
    """실제 UI 입력인 질문만으로 정답의 날짜·위원회 필터를 재현할 수 있어야 한다."""
    for split, records in named_records.items():
        report = analyzer.analyze(records)
        if report["status"] != "PASS":
            ids = [row["id"] for row in report["mismatches"][:5]]
            raise ValueError(
                f"{split}: question/filter mismatch {report['mismatch_count']}: {ids}"
            )


def git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--blind", type=Path, required=True)
    parser.add_argument("--reserve", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    validator = load_validator(script_dir)
    filter_analyzer = load_filter_analyzer(script_dir)
    datasets = {
        "dev": validator.load_and_validate(args.dev, "dev"),
        "blind": validator.load_and_validate(args.blind, "blind"),
        "reserve": validator.load_and_validate(args.reserve, "reserve"),
    }
    expected = {"dev": 20, "blind": 100, "reserve": 30}
    for split, count in expected.items():
        if len(datasets[split]) != count:
            raise SystemExit(f"{split}: expected {count}, got {len(datasets[split])}")
    for split in ("blind", "reserve"):
        not_frozen = [r["id"] for r in datasets[split] if r["provenance"]["review_status"] != "frozen"]
        if not_frozen:
            raise SystemExit(f"{split}: records not frozen: {not_frozen[:5]}")
    assert_disjoint(datasets)
    assert_question_filters(datasets, filter_analyzer)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    questions_path = args.output_dir / "blind_questions.jsonl"
    with questions_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in datasets["blind"]:
            public = {
                "schema_version": record["schema_version"],
                "id": record["id"],
                "question": record["question"],
                "mode": record["mode"],
                "filters": record["filters"],
            }
            handle.write(json.dumps(public, ensure_ascii=False) + "\n")

    repo = Path(__file__).resolve().parents[2]
    paths = {
        "dev": args.dev,
        "blind_gold": args.blind,
        "reserve": args.reserve,
        "blind_questions": questions_path,
    }
    manifest = {
        "schema_version": "2.0",
        "frozen_at": datetime.now().astimezone().isoformat(),
        "git_head": git_head(repo),
        "files": {
            name: {"path": str(path), "sha256": sha256(path), "records": len(datasets[name]) if name in datasets else 100}
            for name, path in paths.items()
        },
        "categories": {
            split: dict(sorted(Counter(r["category"] for r in records).items()))
            for split, records in datasets.items()
        },
        "source_overlap": 0,
    }
    manifest_path = args.output_dir / "dataset_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "FROZEN", "manifest": str(manifest_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
