"""LLM Loop v2 데이터셋의 구조와 누수 방지 규칙을 검사한다.

기존 평가 코드와 독립적으로 동작하도록 Python 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "2.0"
SPLITS = {"dev", "blind", "reserve"}
CATEGORIES = {
    "fact",
    "entity_date",
    "synthesis",
    "comparison_timeline",
    "unanswerable",
}
MODES = {"qa", "report"}
EXACT_TYPES = {"person", "date", "committee", "number", "organization"}
REVIEW_STATES = {"draft", "source_checked", "frozen"}
ID_RE = re.compile(r"^(dev|blind|reserve)-[0-9]{3}$")


class DatasetError(ValueError):
    """검증 실패를 레코드 위치와 함께 보고한다."""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _date(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _require_keys(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - obj.keys())
    if missing:
        raise DatasetError(f"{label}: missing keys {missing}")


def validate_record(record: Any, line_no: int, expected_split: str | None = None) -> None:
    label = f"line {line_no}"
    if not isinstance(record, dict):
        raise DatasetError(f"{label}: record must be an object")

    _require_keys(
        record,
        {"schema_version", "id", "split", "category", "question", "mode",
         "answerable", "filters", "gold", "provenance"},
        label,
    )
    if record["schema_version"] != SCHEMA_VERSION:
        raise DatasetError(f"{label}: schema_version must be {SCHEMA_VERSION}")
    if not isinstance(record.get("id"), str) or not ID_RE.fullmatch(record["id"]):
        raise DatasetError(f"{label}: invalid id")
    split = record.get("split")
    if split not in SPLITS or record["id"].split("-", 1)[0] != split:
        raise DatasetError(f"{label}: id and split do not match")
    if expected_split and split != expected_split:
        raise DatasetError(f"{label}: expected split {expected_split}, got {split}")
    if record.get("category") not in CATEGORIES:
        raise DatasetError(f"{label}: invalid category")
    if not _text(record.get("question")) or len(record["question"].strip()) < 5:
        raise DatasetError(f"{label}: question is too short")
    if record.get("mode") not in MODES:
        raise DatasetError(f"{label}: invalid mode")
    if not isinstance(record.get("answerable"), bool):
        raise DatasetError(f"{label}: answerable must be boolean")

    filters = record.get("filters")
    if not isinstance(filters, dict):
        raise DatasetError(f"{label}: filters must be an object")
    _require_keys(filters, {"committee", "date_from", "date_to"}, f"{label}.filters")
    if filters["committee"] is not None and not _text(filters["committee"]):
        raise DatasetError(f"{label}: invalid committee filter")
    if not _date(filters["date_from"]) or not _date(filters["date_to"]):
        raise DatasetError(f"{label}: invalid filter date")
    if filters["date_from"] and filters["date_to"]:
        if filters["date_from"] > filters["date_to"]:
            raise DatasetError(f"{label}: date_from is after date_to")

    gold = record.get("gold")
    if not isinstance(gold, dict):
        raise DatasetError(f"{label}: gold must be an object")
    _require_keys(
        gold,
        {"answer", "required_claims", "forbidden_claims", "exact_values",
         "evidence", "refusal_reason"},
        f"{label}.gold",
    )
    partition_keys = ("required_exact_values", "reference_values")
    present_partition_keys = [key for key in partition_keys if key in gold]
    if len(present_partition_keys) == 1:
        raise DatasetError(f"{label}: required_exact_values and reference_values must be provided together")

    array_keys = ["required_claims", "forbidden_claims", "exact_values", "evidence"]
    array_keys.extend(present_partition_keys)
    for key in array_keys:
        if not isinstance(gold[key], list):
            raise DatasetError(f"{label}: gold.{key} must be an array")
    if any(not _text(x) for x in gold["required_claims"] + gold["forbidden_claims"]):
        raise DatasetError(f"{label}: claims must be non-empty strings")
    required = {x.strip() for x in gold["required_claims"]}
    forbidden = {x.strip() for x in gold["forbidden_claims"]}
    if required & forbidden:
        raise DatasetError(f"{label}: required and forbidden claims overlap")

    value_fields = ["exact_values", *present_partition_keys]
    for field in value_fields:
        for item in gold[field]:
            if not isinstance(item, dict) or item.get("type") not in EXACT_TYPES or not _text(item.get("value")):
                raise DatasetError(f"{label}: invalid {field} value")

    if present_partition_keys:
        def value_key(item: dict[str, str]) -> tuple[str, str]:
            return item["type"], item["value"].strip()

        required_values = [value_key(item) for item in gold["required_exact_values"]]
        reference_values = [value_key(item) for item in gold["reference_values"]]
        exact_values = [value_key(item) for item in gold["exact_values"]]
        if len(required_values) != len(set(required_values)) or len(reference_values) != len(set(reference_values)):
            raise DatasetError(f"{label}: partitioned exact values contain duplicates")
        if set(required_values) & set(reference_values):
            raise DatasetError(f"{label}: required_exact_values and reference_values overlap")
        if len(exact_values) != len(set(exact_values)):
            raise DatasetError(f"{label}: exact_values contain duplicates")
        if set(exact_values) != set(required_values) | set(reference_values):
            raise DatasetError(f"{label}: exact_values must equal required_exact_values plus reference_values")

    for item in gold["exact_values"]:
        if not isinstance(item, dict) or item.get("type") not in EXACT_TYPES or not _text(item.get("value")):
            raise DatasetError(f"{label}: invalid exact value")
    for evidence in gold["evidence"]:
        if not isinstance(evidence, dict):
            raise DatasetError(f"{label}: evidence must be an object")
        _require_keys(
            evidence,
            {"chunk_id", "source_id", "quote", "speaker", "role", "committee",
             "date", "page_start"},
            f"{label}.evidence",
        )
        if not _text(evidence["chunk_id"]) or not _text(evidence["source_id"]):
            raise DatasetError(f"{label}: evidence ids are required")
        if not _text(evidence["quote"]) or len(evidence["quote"].strip()) < 10:
            raise DatasetError(f"{label}: evidence quote is too short")
        if not _text(evidence["committee"]) or not _date(evidence["date"]):
            raise DatasetError(f"{label}: invalid evidence committee/date")
        page = evidence["page_start"]
        if page is not None and (not isinstance(page, int) or page < 1):
            raise DatasetError(f"{label}: invalid evidence page")

    if record["answerable"]:
        if not _text(gold["answer"]) or not gold["required_claims"] or not gold["evidence"]:
            raise DatasetError(f"{label}: answerable item needs answer, claims, and evidence")
        if gold["refusal_reason"] is not None:
            raise DatasetError(f"{label}: answerable item cannot have refusal_reason")
    else:
        if record["category"] != "unanswerable":
            raise DatasetError(f"{label}: unanswerable item needs unanswerable category")
        if gold["answer"] is not None or gold["required_claims"] or gold["evidence"]:
            raise DatasetError(f"{label}: unanswerable item cannot contain a gold answer/evidence")
        if not _text(gold["refusal_reason"]):
            raise DatasetError(f"{label}: unanswerable item needs refusal_reason")

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        raise DatasetError(f"{label}: provenance must be an object")
    _require_keys(provenance, {"selection_method", "created_at", "review_status"}, f"{label}.provenance")
    if provenance["selection_method"] != "source_first_v2":
        raise DatasetError(f"{label}: selection_method must be source_first_v2")
    if not _datetime(provenance["created_at"]) or provenance["review_status"] not in REVIEW_STATES:
        raise DatasetError(f"{label}: invalid provenance")


def load_and_validate(path: Path, expected_split: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    ids: set[str] = set()
    questions: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
            validate_record(record, line_no, expected_split)
            normalized_question = re.sub(r"\s+", " ", record["question"].strip()).casefold()
            if record["id"] in ids:
                raise DatasetError(f"line {line_no}: duplicate id {record['id']}")
            if normalized_question in questions:
                raise DatasetError(f"line {line_no}: duplicate question")
            ids.add(record["id"])
            questions.add(normalized_question)
            records.append(record)
    if not records:
        raise DatasetError("dataset is empty")
    return records


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--expected-split", choices=sorted(SPLITS))
    args = parser.parse_args()
    try:
        records = load_and_validate(args.dataset, args.expected_split)
    except (OSError, DatasetError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    counts = Counter(record["category"] for record in records)
    print(json.dumps({
        "status": "PASS",
        "path": str(args.dataset),
        "sha256": sha256(args.dataset),
        "records": len(records),
        "categories": dict(sorted(counts.items())),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
