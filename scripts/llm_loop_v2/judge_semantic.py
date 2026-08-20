"""별도 강한 모델로 답변의 정확성·완전성·인용 지지를 보수적으로 채점한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


SYSTEM = """당신은 국회 회의록 RAG의 엄격한 독립 채점자다.
주어진 인용 전문만 사실 근거로 인정한다. 그럴듯함이나 상식으로 보충하지 않는다.
필수 주장 하나라도 빠지면 completeness를 2로 주지 않는다.
답변의 주장 하나라도 인용 전문으로 지지되지 않거나 발언자·날짜·위원회·숫자가 틀리면 FAIL이다.
의견·제안·우려를 결정·시행·확정으로 과장하면 critical_errors에 기록하고 FAIL이다.
근거가 애매하면 PASS가 아니라 REVIEW를 선택한다.
answerable=false이면 negative_control_proof가 해당 회의에 발언 청크 0건임을 증명한다.
이때 후보는 자료 부재를 명확히 거절하고 실질 답변이나 인용을 만들지 않아야 PASS다.
거절문은 질문의 인명·날짜·위원회를 반복할 필요가 없으며, 이를 반복하지 않았다는 이유로 감점하지 않는다.
required_exact_values는 answerable=true 질문이 답변에서 요구한 값이므로 누락 여부를 확인한다.
reference_values는 원문 확인용 참고값일 뿐 답변에 없다는 이유로 감점하지 않는다.
구형 입력에만 있는 exact_values는 질문의 핵심값과 범위 확인에 사용하되, 질문에 이미 있는 날짜·위원회를 답변이 반복하지 않았다는 이유만으로 감점하지 않는다.
시스템의 기존 grounding 등급은 참고하지 말고 독립적으로 판정한다."""


SCHEMA = {
    "name": "conservative_rag_judgment",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "correctness", "completeness", "citation_support",
                     "missing_required_claims", "unsupported_claims", "critical_errors", "rationale"],
        "properties": {
            "verdict": {"enum": ["PASS", "FAIL", "REVIEW"]},
            "correctness": {"type": "integer", "minimum": 0, "maximum": 2},
            "completeness": {"type": "integer", "minimum": 0, "maximum": 2},
            "citation_support": {"type": "integer", "minimum": 0, "maximum": 2},
            "missing_required_claims": {"type": "array", "items": {"type": "string"}},
            "unsupported_claims": {"type": "array", "items": {"type": "string"}},
            "critical_errors": {"type": "array", "items": {"type": "string"}},
            "rationale": {"type": "string"}
        }
    }
}


INPUT_RATE = 5.0 / 1_000_000
OUTPUT_RATE = 30.0 / 1_000_000


def load_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def ensure_resume_config(output: Path, current: dict) -> Path:
    path = output.with_suffix(output.suffix + ".config.json")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != current:
            changed = sorted(key for key in set(existing) | set(current) if existing.get(key) != current.get(key))
            raise ValueError(f"refusing mixed judgment resume; config changed: {changed}")
    elif output.exists() and output.stat().st_size:
        raise ValueError("judgment results exist without resume config")
    else:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def append_jsonl(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-additional-cost", type=float, default=2.0)
    args = parser.parse_args()
    load_dotenv(args.repo / ".env")
    items = load_jsonl(args.inputs)
    if args.limit:
        items = items[:args.limit]
    previous = load_jsonl(args.output) if args.output.exists() else []
    completed = {item["id"] for item in previous}
    ledger_path = args.output.with_suffix(args.output.suffix + ".calls.jsonl")
    previous_calls = load_jsonl(ledger_path)
    spent = sum(float(item.get("estimated_cost_usd", 0)) for item in previous_calls or previous)
    ensure_resume_config(args.output, {
        "inputs": str(args.inputs.resolve()),
        "inputs_sha256": sha256_bytes(args.inputs.read_bytes()),
        "records": len(items),
        "model": args.model,
        "effort": args.effort,
        "system_sha256": sha256_bytes(SYSTEM.encode("utf-8")),
        "schema_sha256": sha256_bytes(json.dumps(SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")),
    })
    client = OpenAI()
    with args.output.open("a", encoding="utf-8", newline="\n") as handle:
        for index, item in enumerate(items, start=1):
            if item["id"] in completed:
                continue
            if spent >= args.max_additional_cost:
                raise SystemExit(f"judge cost cap reached: ${spent:.4f}")
            response = None
            attempt = 1 + sum(row.get("id") == item["id"] for row in previous_calls)
            for retry in range(3):
                try:
                    response = client.chat.completions.create(
                        model=args.model,
                        reasoning_effort=args.effort,
                        messages=[
                            {"role": "system", "content": SYSTEM},
                            {"role": "user", "content": json.dumps(item, ensure_ascii=False)},
                        ],
                        response_format={"type": "json_schema", "json_schema": SCHEMA},
                        max_completion_tokens=1200,
                    )
                    break
                except Exception as exc:
                    append_jsonl(ledger_path, {
                        "id": item["id"], "attempt": attempt + retry, "model": args.model,
                        "status": "API_ERROR", "usage": {"input_tokens": 0, "output_tokens": 0},
                        "estimated_cost_usd": 0.0, "error_type": type(exc).__name__,
                    })
                    previous_calls.append({"id": item["id"]})
                    if retry == 2:
                        raise
                    time.sleep(2 ** (retry + 1))
            assert response is not None
            attempt += retry
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
            cost = usage["input_tokens"] * INPUT_RATE + usage["output_tokens"] * OUTPUT_RATE
            spent += cost
            content = response.choices[0].message.content or ""
            try:
                judgment = json.loads(content)
            except json.JSONDecodeError as exc:
                append_jsonl(ledger_path, {
                    "id": item["id"], "attempt": attempt, "model": args.model,
                    "status": "INVALID_JSON", "usage": usage,
                    "estimated_cost_usd": round(cost, 8), "error_type": type(exc).__name__,
                })
                raise SystemExit(f"invalid judgment JSON for {item['id']}; call cost recorded") from exc
            append_jsonl(ledger_path, {
                "id": item["id"], "attempt": attempt, "model": args.model,
                "status": "SUCCESS", "usage": usage, "estimated_cost_usd": round(cost, 8),
            })
            previous_calls.append({"id": item["id"]})
            row = {
                "id": item["id"],
                "model": args.model,
                "effort": args.effort,
                "judged_at": datetime.now().astimezone().isoformat(),
                "judgment": judgment,
                "usage": usage,
                "estimated_cost_usd": round(cost, 8),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"[{index}/{len(items)}] {item['id']} {judgment['verdict']} ${spent:.4f}", flush=True)
            if spent > args.max_additional_cost:
                raise SystemExit(f"judge cost cap crossed by final recorded call: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
