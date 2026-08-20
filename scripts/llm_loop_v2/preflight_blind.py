"""외부 호출 전에 블라인드 작성 큐·대조군·비용 여유를 한 번에 검사한다."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED = {
    "blind": {"fact": 25, "entity_date": 20, "synthesis": 20, "comparison_timeline": 25},
    "reserve": {"fact": 8, "entity_date": 7, "synthesis": 7, "comparison_timeline": 8},
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


def preflight(dev, queue, negatives, proofs, spend, *, stage: str = "authoring", required_budget: float | None = None) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["dev_exact_20"] = len(dev) == 20 and len({row["id"] for row in dev}) == 20
    checks["queue_exact_120"] = len(queue) == 120 and len({row["id"] for row in queue}) == 120
    expected_queue_ids = (
        {f"blind-{index:03d}" for index in range(1, 91)}
        | {f"reserve-{index:03d}" for index in range(1, 31)}
    )
    checks["queue_exact_ids"] = {row["id"] for row in queue} == expected_queue_ids
    checks["queue_unique_candidates"] = len({row["candidate_id"] for row in queue}) == len(queue)
    checks["queue_unique_sources"] = len({row["source_id"] for row in queue}) == len(queue)
    actual_distribution = {
        split: dict(Counter(row["category"] for row in queue if row["split"] == split))
        for split in EXPECTED
    }
    checks["queue_distribution"] = actual_distribution == EXPECTED
    checks["negative_exact_10"] = len(negatives) == 10 and len({row["id"] for row in negatives}) == 10
    checks["negative_speakers_absent"] = all(
        row.get("speaker_chunk_count_in_target") == 0 for row in proofs
    )
    checks["negative_meetings_exist"] = all(
        int(row.get("target_chunk_count") or 0) > 0 and row.get("target_source_ids")
        for row in proofs
    )
    checks["negative_speakers_exist_elsewhere"] = all(
        int(row.get("speaker_reference_count_elsewhere") or 0) > 0 for row in proofs
    )
    # 추가 승인 루프에서는 이전 총액 상한의 미사용분을 새 승인액처럼 쓰면 안 된다.
    # 증분 원장이 있으면 반드시 그 잔액과 gate를 우선한다.
    remaining = float(spend.get("additional_remaining_usd", spend.get("remaining_to_cap_usd", -1)))
    budget_within_cap = bool(spend.get("within_cap") is True)
    if "within_additional_cap" in spend:
        budget_within_cap = budget_within_cap and spend.get("within_additional_cap") is True
    if stage == "authoring":
        # round3 전체: gold 138건, G3 gold-context 18건, dev 20건,
        # reserve 30건×3회, blind 100건, G3+dev+blind 의미채점과 20% 여유.
        required = 11.0 if required_budget is None else required_budget
        budget_key = "budget_reserve_gte_11_00"
        ready_stage = "READY_FOR_AUTHORING_APPROVAL"
    elif stage == "final":
        # 작성·예비평가가 끝난 뒤에는 이미 지출된 단계를 다시 예약하지 않는다.
        # 최근 예비 실행 실측($0.0184/문항)에 약 3% 여유를 둔 블라인드 100건 예산.
        required = 1.90 if required_budget is None else required_budget
        budget_key = "final_blind_budget_available"
        ready_stage = "READY_FOR_FINAL_BLIND"
    else:
        raise ValueError(f"unknown stage: {stage}")
    checks[budget_key] = remaining >= required and budget_within_cap
    passed = all(checks.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "stage": ready_stage if passed else "NOT_READY",
        "checks": checks,
        "queue_distribution": actual_distribution,
        "records": {"dev": len(dev), "queue": len(queue), "negative": len(negatives)},
        "remaining_budget_usd": remaining,
        "required_budget_usd": required,
        "budget_stage": stage,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path, required=True)
    parser.add_argument("--spend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("authoring", "final"), default="authoring")
    parser.add_argument("--required-budget", type=float)
    args = parser.parse_args()
    dev = load_jsonl(args.dev)
    queue = load_jsonl(args.queue)
    negatives = load_jsonl(args.unanswerable)
    proofs = load_jsonl(args.negative_proofs)
    materializer = load_module("materialize_preflight", Path(__file__).parent / "materialize_blind.py")
    materializer.validate_negative_controls(negatives, proofs)
    materializer.validate_negative_source_disjoint(dev, queue, proofs)
    report = preflight(
        dev, queue, negatives, proofs,
        json.loads(args.spend.read_text(encoding="utf-8")),
        stage=args.stage,
        required_budget=args.required_budget,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
