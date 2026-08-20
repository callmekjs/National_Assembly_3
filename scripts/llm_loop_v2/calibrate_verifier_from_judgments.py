"""폐기된 독립 의미판정 답변으로 현재 검증기의 오차단·미차단률을 측정한다.

최종 점수에는 사용하지 않는다. 정답 가능 질문의 잘못된 거절은 답변만 보는 검증기가
판별할 수 없으므로 별도 집계하고 G3/G5 의미 게이트에서 0건을 요구한다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import answer  # noqa: E402
from query_parser import classify_question  # noqa: E402
from verification import verify  # noqa: E402


REFUSAL_MARKERS = ("확인할 수 없습니다", "확인되지 않습니다", "찾을 수 없습니다")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sources_from(
    items: list[dict[str, Any]],
    fallback_numbers: list[int] | None = None,
) -> list[dict[str, Any]]:
    return [{
        "n": item.get("n") or (
            fallback_numbers[index - 1]
            if fallback_numbers and len(fallback_numbers) == len(items) else index
        ),
        "chunk_id": item["chunk_id"],
        "speaker": item.get("speaker"),
        "role": item.get("role"),
        "party": item.get("party"),
        "committee": item.get("committee"),
        "date": item.get("date"),
        "page_start": item.get("page_start"),
        "text": item.get("text") or "",
    } for index, item in enumerate(items, 1)]


def calibrate(inputs: list[dict[str, Any]], judgments: list[dict[str, Any]]) -> dict[str, Any]:
    by_judgment = {row["id"]: row["judgment"] for row in judgments}
    if len(by_judgment) != len(judgments):
        raise ValueError("duplicate judgment ids")
    rows: list[dict[str, Any]] = []
    wrong_refusals: list[str] = []
    for item in inputs:
        judgment = by_judgment.get(item["id"])
        if judgment is None:
            raise ValueError(f"missing judgment: {item['id']}")
        candidate = item["candidate_answer"]
        refusal = any(marker in candidate for marker in REFUSAL_MARKERS)
        unsupported = list(judgment.get("unsupported_claims") or [])
        critical = list(judgment.get("critical_errors") or [])
        if item.get("answerable") and refusal and (unsupported or critical):
            wrong_refusals.append(item["id"])
            label = "wrong_refusal_separate_gate"
        elif unsupported and not refusal:
            label = "blockable_critical"
        elif critical and not refusal:
            label = "scope_or_completeness_separate_g3_g5"
        elif (
            judgment.get("verdict") == "PASS"
            and not unsupported
            and not critical
        ):
            label = "normal"
        else:
            label = "excluded_semantic_failure"

        cited_numbers_in_answer = sorted({int(value) for value in re.findall(r"\[(\d+)\]", candidate)})
        sources = sources_from(item.get("cited_evidence") or [], cited_numbers_in_answer)
        # 의미채점 입력에는 실제로 인용된 source만 남고 인용되지 않은 source 수는 없다.
        # 따라서 [4]를 "근거 3개 중 4번"으로 재해석하면 정상 인용을 오탐한다.
        # invalid citation은 원래 deterministic 결과가 담당하며, 여기서는 보존된 n만 사용한다.
        cited = [source["n"] for source in sources]
        invalid: list[int] = []
        verification = verify(
            item["question"], candidate, sources, cited,
            classify_question(item["question"]),
        )
        failures = answer.critical_verification_failures(verification, invalid)
        rows.append({
            "id": item["id"], "label": label, "blocked": bool(failures),
            "critical_failures": failures,
            "verification_flags": verification.get("flags") or [],
        })

    normal = [row for row in rows if row["label"] == "normal"]
    critical = [row for row in rows if row["label"] == "blockable_critical"]
    false_blocks = [row["id"] for row in normal if row["blocked"]]
    missed = [row["id"] for row in critical if not row["blocked"]]
    false_block_rate = len(false_blocks) / len(normal) if normal else 1.0
    critical_block_rate = (len(critical) - len(missed)) / len(critical) if critical else 0.0
    checks = {
        "normal_sample_gte_50": len(normal) >= 50,
        "critical_sample_gte_3": len(critical) >= 3,
        "normal_false_block_lte_2pct": false_block_rate <= 0.02,
        "critical_block_100pct": critical_block_rate == 1.0,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "normal_records": len(normal),
        "normal_false_blocks": len(false_blocks),
        "normal_false_block_rate": round(false_block_rate, 6),
        "critical_records": len(critical),
        "critical_blocked": len(critical) - len(missed),
        "critical_block_rate": round(critical_block_rate, 6),
        "false_block_ids": false_blocks,
        "missed_critical_ids": missed,
        "wrong_refusal_ids_separate_g3_g5": wrong_refusals,
        "scope_or_completeness_ids_separate_g3_g5": [
            row["id"] for row in rows
            if row["label"] == "scope_or_completeness_separate_g3_g5"
        ],
        "excluded_records": sum(row["label"] == "excluded_semantic_failure" for row in rows),
        "rows": rows,
        "note": "폐기된 round2 답변의 독립 의미판정을 검증기 보정에만 사용하며 최종 정확도에는 포함하지 않는다.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, action="append", required=True)
    parser.add_argument("--judgments", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if len(args.inputs) != len(args.judgments):
            raise ValueError("--inputs and --judgments counts must match")
        report = calibrate(
            [row for path in args.inputs for row in load_jsonl(path)],
            [row for path in args.judgments for row in load_jsonl(path)],
        )
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
