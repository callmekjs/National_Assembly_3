"""G3: 검색을 건너뛰고 검토된 gold turn만 제품 생성기에 제공한다."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import answer  # noqa: E402
import db  # noqa: E402
import grounding  # noqa: E402
from run_baseline import (  # noqa: E402
    accumulated_cost,
    git_head,
    load_jsonl,
    runtime_hashes,
    safe_settings,
    sha256,
    successful_result_ids,
    write_or_verify_config,
)


def gold_hit(evidence: dict) -> dict:
    return {
        "chunk_id": evidence["chunk_id"],
        "speaker": evidence.get("speaker"),
        "role": evidence.get("role"),
        "committee": evidence["committee"],
        "meeting_date": evidence["date"],
        "page_start": evidence.get("page_start"),
        "snippet": evidence["quote"],
    }


def apply_product_grounding(response: dict) -> dict:
    """제품 ``/query``와 동일한 생성 후 grounding 정책을 적용한다."""
    level, ungrounded = grounding.judge(response)
    flags = (response.get("verification") or {}).get("flags") or []
    if flags and level == "FULL":
        level = "PARTIAL"
    response["grounding"] = level
    if ungrounded:
        response["ungrounded"] = True
    return response


def initialize_runtime(repo: Path = REPO) -> None:
    """직접 호출도 API lifespan과 같은 DB 풀 생명주기를 갖게 한다."""
    load_dotenv(repo / ".env")
    db.init_pool()
    atexit.register(db.close_pool)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, default=18)
    parser.add_argument("--max-additional-cost", type=float, required=True)
    args = parser.parse_args()
    records = [row for row in load_jsonl(args.dataset) if row.get("answerable") is True]
    if len(records) != args.expected_records or len({row["id"] for row in records}) != len(records):
        raise SystemExit(f"G3 requires {args.expected_records} unique answerable records")
    initialize_runtime()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    g3_dataset = args.output_dir / "answerable_dataset.jsonl"
    rendered_dataset = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)
    if g3_dataset.exists() and g3_dataset.read_text(encoding="utf-8") != rendered_dataset:
        raise SystemExit("refusing changed G3 answerable dataset")
    g3_dataset.write_text(rendered_dataset, encoding="utf-8", newline="\n")
    result_path = args.output_dir / "results.jsonl"
    error_path = args.output_dir / "errors.jsonl"
    existing = load_jsonl(result_path) if result_path.exists() else []
    existing_errors = load_jsonl(error_path) if error_path.exists() else []
    completed = successful_result_ids(existing)
    spent = accumulated_cost(existing) + accumulated_cost(existing_errors)
    config = {
        "run_id": args.output_dir.name,
        "started_at": datetime.now().astimezone().isoformat(),
        "git_head": git_head(REPO),
        "dataset": str(args.dataset),
        "dataset_sha256": sha256(args.dataset),
        "base_url": "direct:answer.generate_answer",
        "requested_records": len(records),
        "max_additional_cost": args.max_additional_cost,
        "evaluation_mode": "gold_context_only",
        "safe_settings": safe_settings(REPO / ".env"),
        "runtime_sha256": runtime_hashes(REPO),
    }
    write_or_verify_config(args.output_dir / "config.json", config)

    with (
        result_path.open("a", encoding="utf-8", newline="\n") as output,
        error_path.open("a", encoding="utf-8", newline="\n") as errors,
    ):
        for index, record in enumerate(records, 1):
            if record["id"] in completed:
                continue
            if spent >= args.max_additional_cost:
                raise SystemExit(f"G3 cost cap reached: ${spent:.4f}")
            started = time.perf_counter()
            try:
                evidence = record["gold"]["evidence"]
                response = answer.generate_answer(
                    record["question"],
                    mode=record["mode"],
                    hits=[gold_hit(item) for item in evidence],
                    gold_context_only=True,
                )
                response = apply_product_grounding(response)
                source_block = response.pop("source_block", None)
                row = {
                    "id": record["id"],
                    "question": record["question"],
                    "request": {"mode": record["mode"], "gold_context_only": True},
                    "http_status": 200,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "response": response,
                    "source_block_sha256": (
                        hashlib.sha256(source_block.encode("utf-8")).hexdigest()
                        if source_block else None
                    ),
                    "error": None,
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
            except Exception as exc:
                row = {
                    "id": record["id"], "question": record["question"],
                    "request": {"mode": record["mode"], "gold_context_only": True},
                    "http_status": 0,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "response": {}, "source_block_sha256": None,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "finished_at": datetime.now().astimezone().isoformat(),
                }
            success = row["http_status"] == 200 and not row["error"]
            ledger = output if success else errors
            ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            ledger.flush()
            spent += accumulated_cost([row])
            print(f"[{index}/{len(records)}] {record['id']} cost=${spent:.4f}", flush=True)
            if not success:
                raise SystemExit(f"G3 call failed: {record['id']}")
            if spent > args.max_additional_cost:
                raise SystemExit(f"G3 cost cap crossed by final recorded call: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
