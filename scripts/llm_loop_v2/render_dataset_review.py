"""v2 JSONL을 사람이 검토하기 쉬운 Markdown 문서로 렌더링한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="LLM Loop v2 데이터 검토")
    args = parser.parse_args()
    records = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines = [f"# {args.title}", "", f"총 {len(records)}개", ""]
    for record in records:
        gold = record["gold"]
        required_values = gold.get("required_exact_values", gold["exact_values"])
        reference_values = gold.get("reference_values", [])
        lines.extend([
            f"## {record['id']} — {record['category']}",
            "",
            f"- 질문: {record['question']}",
            f"- 모드: {record['mode']}",
            f"- 답변 가능: {record['answerable']}",
            f"- 필터: 위원회 `{record['filters']['committee']}`, "
            f"날짜 `{record['filters']['date_from']} ~ {record['filters']['date_to']}`",
            "",
            "### 정답",
            "",
            gold["answer"] or "없음 — 답변 거절 대상",
            "",
            "### 반드시 포함할 주장",
            "",
            *[f"- {claim}" for claim in gold["required_claims"]],
            "",
            "### 포함하면 안 되는 주장",
            "",
            *[f"- {claim}" for claim in gold["forbidden_claims"]],
            "",
            "### 답변에 반드시 포함할 값",
            "",
            *[f"- `{item['type']}`: {item['value']}" for item in required_values],
            "",
            "### 원문 지지만 확인할 참고값",
            "",
            *[f"- `{item['type']}`: {item['value']}" for item in reference_values],
            "",
            "### 원문 근거",
            "",
        ])
        for evidence in gold["evidence"]:
            lines.extend([
                f"- `{evidence['chunk_id']}` / {evidence['speaker']} {evidence['role'] or ''} / "
                f"{evidence['committee']} / {evidence['date']} / {evidence['page_start']}쪽",
                "",
                "> " + evidence["quote"].replace("\n", "\n> "),
                "",
            ])
    args.output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(json.dumps({"status": "PASS", "records": len(records), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
