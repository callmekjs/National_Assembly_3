"""130개 골드 문항의 사람이 읽을 검토 묶음과 미승인 체크리스트를 만든다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def render(
    answerable_results: list[dict[str, Any]],
    negatives: list[dict[str, Any]],
    proofs: list[dict[str, Any]],
    *,
    title: str = "골드 문항 명시 검토",
):
    records = [row["record"] for row in answerable_results] + negatives
    proof_by_id = {row["id"]: row for row in proofs}
    records.sort(key=lambda row: row["id"])
    sections = [
        f"# {title}\n",
        "각 문항에서 질문 범위, 정답의 원문 지지, 필수 주장, 금지 주장, 답변 필수값과 원문 참고값을 확인한다. "
        "검토 전에는 어떤 문항도 동결되지 않는다.\n",
    ]
    checklist: list[dict[str, Any]] = []
    for record in records:
        gold = record["gold"]
        required_values = gold.get("required_exact_values", gold["exact_values"])
        reference_values = gold.get("reference_values", [])
        sections.extend([
            f"## {record['id']} — {record['category']}\n",
            f"- 질문: {record['question']}",
            f"- 정답: {gold['answer'] if record['answerable'] else '답변 불가'}",
            f"- 필수 주장: {json.dumps(gold['required_claims'], ensure_ascii=False)}",
            f"- 금지 주장: {json.dumps(gold['forbidden_claims'], ensure_ascii=False)}",
            f"- 답변 필수값: {json.dumps(required_values, ensure_ascii=False)}",
            f"- 원문 참고값: {json.dumps(reference_values, ensure_ascii=False)}",
        ])
        if record["answerable"]:
            evidence = gold["evidence"][0]
            sections.extend([
                f"- 원문: {evidence['committee']} / {evidence['date']} / {evidence.get('speaker')} / {evidence.get('role')}",
                "",
                "> " + evidence["quote"].replace("\n", "\n> "),
                "",
            ])
        else:
            sections.extend([
                f"- 거절 이유: {gold['refusal_reason']}",
                f"- DB 증명: `{json.dumps(proof_by_id.get(record['id']), ensure_ascii=False)}`",
                "",
            ])
        checklist.append({
            "id": record["id"],
            "status": "pending",
            "reviewer": None,
            "reviewed_at": None,
            "checks": {
                "question_scope": False,
                "answer_supported": False,
                "claims_match_question": False,
                "required_values_match_question": False,
                "reference_values_supported": False,
            },
            "note": "",
        })
    return "\n".join(sections).rstrip() + "\n", checklist


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--unanswerable", type=Path, required=True)
    parser.add_argument("--negative-proofs", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--expected-answerable", type=int, default=120)
    parser.add_argument("--expected-unanswerable", type=int, default=10)
    parser.add_argument("--title", default="블라인드·예비 골드 130문항 명시 검토")
    args = parser.parse_args()
    markdown, checklist = render(
        load_jsonl(args.results),
        load_jsonl(args.unanswerable),
        load_jsonl(args.negative_proofs),
        title=args.title,
    )
    expected = args.expected_answerable + args.expected_unanswerable
    if len(checklist) != expected:
        raise SystemExit(f"expected {expected} review items, got {len(checklist)}")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown, encoding="utf-8")
    args.approvals.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in checklist),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "PENDING_REVIEW", "records": expected}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
