"""독립 원문 한 건씩으로 블라인드/예비 질문과 정답표를 새로 작성한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


SYSTEM = """당신은 국회 회의록 RAG 평가문항 작성자다.
오직 제공된 원문과 메타데이터에 직접 나온 사실만 사용한다.
질문은 날짜·위원회·발언자를 명확히 적고, 원문 한 건만으로 답할 수 있어야 한다.
required_claims에는 질문이 실제로 요구한 내용만 넣는다. 질문보다 넓은 배경·후속 질문을 필수로 만들지 않는다.
정답에는 원문이 지지하지 않는 이름·숫자·원인·결론을 추가하지 않는다.
의견·우려·제안을 결정·시행·확정 사실로 바꾸지 않는다.
comparison_timeline은 원문 안의 두 수치·시점·대상을 실제로 비교할 수 있을 때만 작성한다.
required_exact_values에는 질문이 직접 요구하여 답변에 반드시 나와야 하는 값만 넣는다.
reference_values에는 원문·메타데이터에는 있지만 질문이 답변에서 반복하도록 요구하지 않은 값을 넣는다.
같은 값은 required_exact_values와 reference_values 양쪽에 넣지 않는다."""

# exact value는 채점기가 임의 해석하지 않도록 원문/메타데이터의 짧은 연속 문자열을
# 그대로 복사한다. 설명, 괄호 해설, 여러 값을 한 항목으로 합친 문구는 금지한다.
SYSTEM += """
required_exact_values와 reference_values의 value는 원문 또는 메타데이터에 실제 존재하는 가장 짧은 연속 문자열을 그대로 복사한다.
`총 258건 중 224건`, `상임위원 3명`처럼 설명을 붙이지 말고 필요한 값은 `258건`, `224건`, `3명`처럼 각각 분리한다.
숫자·날짜의 한글/숫자 표기를 바꾸지 말고 원문 표기를 유지한다.
질문 첫머리에는 메타데이터의 전체 날짜, 위원회, speaker_display 발언자 이름을 명시한다.
speaker_display와 원문 speaker가 다르면 질문·정답에는 speaker_display를 쓰고, reference_values에는 원문 speaker를 그대로 넣는다."""
SYSTEM += """
reference_values에는 질문 범위 확인용 메타데이터인 날짜, 위원회, 발언자만 넣고 최대 3개로 제한한다.
질문이 요구하지 않는 의안번호·부가 숫자·부가 인물은 reference_values에 넣지 않는다.
required_claims와 forbidden_claims는 항목 하나당 한 주장만 220자 이내의 평문으로 작성한다. 채점 지시, 해설, 마크다운, 영어 메모를 절대 넣지 않는다."""


SCHEMA = {
    "name": "source_first_gold",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "question", "answer", "required_claims", "forbidden_claims",
            "required_exact_values", "reference_values",
        ],
        "properties": {
            "question": {"type": "string", "minLength": 20, "maxLength": 500},
            "answer": {"type": "string", "minLength": 20, "maxLength": 1500},
            "required_claims": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": {"type": "string", "minLength": 5, "maxLength": 220},
            },
            "forbidden_claims": {
                "type": "array", "minItems": 1, "maxItems": 3,
                "items": {"type": "string", "minLength": 5, "maxLength": 220},
            },
            "required_exact_values": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "value"],
                    "properties": {
                        "type": {"enum": ["person", "date", "committee", "number", "organization"]},
                        "value": {"type": "string", "minLength": 1, "maxLength": 100},
                    },
                },
            },
            "reference_values": {
                "type": "array", "maxItems": 3,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["type", "value"],
                    "properties": {
                        "type": {"enum": ["person", "date", "committee", "number", "organization"]},
                        "value": {"type": "string", "minLength": 1, "maxLength": 100},
                    },
                },
            },
        },
    },
}


INPUT_RATE = 5.0 / 1_000_000
OUTPUT_RATE = 30.0 / 1_000_000


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_speaker_display_names(repo: Path) -> dict[str, str]:
    path = repo / "data" / "members" / "hanja_aliases.json"
    if not path.exists():
        return {}
    mapping: dict[str, str] = {}
    for item in json.loads(path.read_text(encoding="utf-8")):
        hanja = str(item["hanja"])
        name = str(item["name"])
        mapping[hanja] = name
        mapping[unicodedata.normalize("NFKC", hanja)] = name
    return mapping


def ensure_resume_config(output: Path, current: dict[str, Any]) -> Path:
    path = output.with_suffix(output.suffix + ".config.json")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != current:
            changed = sorted(key for key in set(existing) | set(current) if existing.get(key) != current.get(key))
            raise ValueError(f"refusing mixed authoring resume; config changed: {changed}")
    elif output.exists() and output.stat().st_size:
        raise ValueError("authoring results exist without resume config")
    else:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def cost_total(rows: list[dict[str, Any]]) -> float:
    return sum(float(row.get("estimated_cost_usd", 0)) for row in rows)


def select_records(queue: list[dict[str, Any]], ids: list[str] | None) -> list[dict[str, Any]]:
    """문항 ID 일부만 별도 비용 shard로 실행하되 원래 큐 순서를 보존한다."""
    if not ids:
        return queue
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate --ids are not allowed")
    requested = set(ids)
    selected = [row for row in queue if row["id"] in requested]
    missing = sorted(requested - {row["id"] for row in selected})
    if missing:
        raise ValueError(f"unknown authoring ids: {missing}")
    return selected


def ids_from_validation(path: Path | None) -> list[str]:
    if path is None:
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    return sorted({
        str(item["id"])
        for field in ("hard_errors", "review_flags")
        for item in report.get(field, [])
        if item.get("id")
    })


def ids_from_review(path: Path | None) -> list[str]:
    if path is None:
        return []
    rows = load_jsonl(path)
    return sorted({
        str(row["id"])
        for row in rows
        if row.get("status") != "approved"
        or not row.get("checks")
        or not all(bool(value) for value in row["checks"].values())
    })


def record_from(source: dict[str, Any], authored: dict[str, Any]) -> dict[str, Any]:
    required_exact_values = authored["required_exact_values"]
    reference_values = authored["reference_values"]
    return {
        "schema_version": "2.0",
        "id": source["id"],
        "split": source["split"],
        "category": source["category"],
        "question": authored["question"],
        "mode": "qa",
        "answerable": True,
        "filters": {
            "committee": source["committee_filter"],
            "date_from": source["date"],
            "date_to": source["date"],
        },
        "gold": {
            "answer": authored["answer"],
            "required_claims": authored["required_claims"],
            "forbidden_claims": authored["forbidden_claims"],
            "exact_values": [*required_exact_values, *reference_values],
            "required_exact_values": required_exact_values,
            "reference_values": reference_values,
            "evidence": [{
                "chunk_id": source["chunk_id"],
                "source_id": source["source_id"],
                "quote": source["text"],
                "speaker": source.get("speaker"),
                "role": source.get("role"),
                "committee": source["committee"],
                "date": source["date"],
                "page_start": source.get("page_start"),
            }],
            "refusal_reason": None,
        },
        "provenance": {
            "selection_method": "source_first_v2",
            "created_at": datetime.now().astimezone().isoformat(),
            "review_status": "source_checked",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--max-additional-cost", type=float, default=2.5)
    parser.add_argument("--max-completion-tokens", type=int, default=2400)
    parser.add_argument("--prior-ledger", type=Path, action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="*", help="별도 shard로 작성할 문항 ID")
    parser.add_argument("--ids-from-validation", type=Path, help="FAIL/경고 ID를 자동 수리")
    parser.add_argument("--ids-from-review", type=Path, help="문항별 검토에서 거절된 ID를 자동 수리")
    args = parser.parse_args()

    load_dotenv(args.repo / ".env")
    selected_ids = sorted(
        set(args.ids or [])
        | set(ids_from_validation(args.ids_from_validation))
        | set(ids_from_review(args.ids_from_review))
    )
    queue = select_records(load_jsonl(args.queue), selected_ids or None)
    if args.limit:
        queue = queue[:args.limit]
    completed_rows = load_jsonl(args.output)
    completed = {row["id"] for row in completed_rows}
    ledger_path = args.output.with_suffix(args.output.suffix + ".calls.jsonl")
    previous_calls = load_jsonl(ledger_path)
    prior_calls = [row for path in args.prior_ledger for row in load_jsonl(path)]
    # max-additional-cost는 이번 output/ledger에서 새로 발생한 비용만 제한한다.
    # prior ledger는 전체 승인액 감사용이며 새 회차의 증분 한도를 소진시키지 않는다.
    spent = cost_total(previous_calls or completed_rows)
    prior_spent = cost_total(prior_calls)
    ensure_resume_config(args.output, {
        "queue": str(args.queue.resolve()),
        "queue_sha256": sha256_bytes(args.queue.read_bytes()),
        "records": len(queue),
        "ids": selected_ids or None,
        "validation_source": (
            {
                "path": str(args.ids_from_validation.resolve()),
                "sha256": sha256_bytes(args.ids_from_validation.read_bytes()),
            }
            if args.ids_from_validation else None
        ),
        "review_source": (
            {
                "path": str(args.ids_from_review.resolve()),
                "sha256": sha256_bytes(args.ids_from_review.read_bytes()),
            }
            if args.ids_from_review else None
        ),
        "model": args.model,
        "effort": args.effort,
        "max_completion_tokens": args.max_completion_tokens,
        "prior_ledgers": [
            {"path": str(path.resolve()), "sha256": sha256_bytes(path.read_bytes())}
            for path in args.prior_ledger
        ],
        "system_sha256": sha256_bytes(SYSTEM.encode("utf-8")),
        "schema_sha256": sha256_bytes(json.dumps(SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")),
    })
    client = OpenAI()
    speaker_display_names = load_speaker_display_names(args.repo)

    with args.output.open("a", encoding="utf-8", newline="\n") as handle:
        for index, source in enumerate(queue, start=1):
            if source["id"] in completed:
                continue
            if spent >= args.max_additional_cost:
                raise SystemExit(f"authoring cost cap reached: ${spent:.4f}")
            payload = {
                "target_category": source["category"],
                "metadata": {
                    "date": source["date"],
                    "committee": source["committee"],
                    "speaker": source.get("speaker"),
                    "speaker_display": speaker_display_names.get(
                        unicodedata.normalize("NFKC", source.get("speaker") or ""),
                        source.get("speaker"),
                    ),
                    "role": source.get("role"),
                },
                "source_text": source["text"],
            }
            response = None
            attempt = 1 + sum(row.get("id") == source["id"] for row in previous_calls)
            for retry in range(3):
                try:
                    response = client.chat.completions.create(
                        model=args.model,
                        reasoning_effort=args.effort,
                        messages=[
                            {"role": "system", "content": SYSTEM},
                            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                        ],
                        response_format={"type": "json_schema", "json_schema": SCHEMA},
                        max_completion_tokens=args.max_completion_tokens,
                    )
                    break
                except Exception as exc:
                    append_jsonl(ledger_path, {
                        "id": source["id"], "attempt": attempt + retry, "model": args.model,
                        "status": "API_ERROR", "usage": {"input_tokens": 0, "output_tokens": 0},
                        "estimated_cost_usd": 0.0, "error_type": type(exc).__name__,
                    })
                    previous_calls.append({"id": source["id"]})
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
                authored = json.loads(content)
            except json.JSONDecodeError as exc:
                append_jsonl(ledger_path, {
                    "id": source["id"], "attempt": attempt, "model": args.model,
                    "status": "INVALID_JSON", "usage": usage,
                    "estimated_cost_usd": round(cost, 8), "error_type": type(exc).__name__,
                })
                raise SystemExit(f"invalid authoring JSON for {source['id']}; call cost recorded") from exc
            append_jsonl(ledger_path, {
                "id": source["id"], "attempt": attempt, "model": args.model,
                "status": "SUCCESS", "usage": usage, "estimated_cost_usd": round(cost, 8),
            })
            previous_calls.append({"id": source["id"]})
            row = {
                "id": source["id"],
                "candidate_id": source["candidate_id"],
                "model": args.model,
                "effort": args.effort,
                "authored_at": datetime.now().astimezone().isoformat(),
                "usage": usage,
                "estimated_cost_usd": round(cost, 8),
                "record": record_from(source, authored),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                f"[{index}/{len(queue)}] {source['id']} "
                f"new=${spent:.4f} audited_total=${prior_spent + spent:.4f}",
                flush=True,
            )
            if spent > args.max_additional_cost:
                raise SystemExit(f"authoring cost cap crossed by final recorded call: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
