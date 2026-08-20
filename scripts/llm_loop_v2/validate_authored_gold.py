"""외부 작성 결과가 선택된 원문과 정확히 일치하는지 보수적으로 검사한다.

의미가 맞는지를 이 스크립트 하나로 주장하지 않는다. 대신 동결 전에 자동으로
증명할 수 있는 ID, 원문, 메타데이터, 숫자, 분할 누수 규칙을 강제한다.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


NUMBER_RE = re.compile(
    r"(?<![\w])(?:\d{2,4}년(?:도)?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?(?:조|억|만)?\s*(?:원|명|개|건|가구|회|배|곳|대|일|개월|년))"
)
SPACE_RE = re.compile(r"\s+")
CLAIM_CONTAMINATION_RE = re.compile(
    r"(?:\*\*|```|if\s+malformed|actually\s+json|이어야\s*한다\s*:|\*\*이유|답변할\s*것\s*\()",
    re.IGNORECASE,
)


def _load_speaker_alias_index() -> dict[str, set[str]]:
    path = Path(__file__).parents[2] / "data" / "members" / "hanja_aliases.json"
    if not path.exists():
        return {}
    index: dict[str, set[str]] = {}
    for item in json.loads(path.read_text(encoding="utf-8")):
        forms = {str(item["name"]), str(item["hanja"]), unicodedata.normalize("NFKC", str(item["hanja"]))}
        for form in forms:
            index.setdefault(unicodedata.normalize("NFKC", form).casefold(), set()).update(forms)
    return index


SPEAKER_ALIAS_INDEX = _load_speaker_alias_index()


class AuthoringError(ValueError):
    """동결을 막아야 하는 작성 결과 오류."""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuthoringError(f"{path.name}:{line_no}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise AuthoringError(f"{path.name}:{line_no}: row must be an object")
            rows.append(row)
    return rows


def _load_validator(script_dir: Path):
    path = script_dir / "validate_dataset.py"
    spec = importlib.util.spec_from_file_location("llm_loop_v2_validator_authored", path)
    if not spec or not spec.loader:
        raise RuntimeError("dataset validator load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compact(value: str) -> str:
    return SPACE_RE.sub("", value).replace(",", "").casefold()


def _claim_contamination(record: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for field in ("required_claims", "forbidden_claims"):
        for index, claim in enumerate(record["gold"][field], 1):
            if len(claim) > 220:
                problems.append(f"{field}[{index}]_too_long:{len(claim)}")
            if CLAIM_CONTAMINATION_RE.search(claim):
                problems.append(f"{field}[{index}]_instruction_contamination")
            if any(unicodedata.category(char).startswith("M") for char in claim):
                problems.append(f"{field}[{index}]_combining_mark_contamination")
    return problems


def _numeric_compact(value: str) -> str:
    compact = _compact(value)
    compact = re.sub(r"20(\d{2})(?=년)", r"\1", compact)
    return re.sub(r"(조|억|만)원", r"\1", compact)


def _date_forms(value: str) -> set[str]:
    year, month, day = value.split("-")
    return {
        value,
        f"{year}.{int(month)}.{int(day)}",
        f"{year}년{int(month)}월{int(day)}일",
        f"{int(month)}월{int(day)}일",
    }


def _value_supported(value: dict[str, str], source: dict[str, Any]) -> bool:
    raw = value["value"]
    haystacks = [source["text"]]
    value_type = value["type"]
    if value_type == "date":
        return (
            _compact(raw) in _compact(source["text"])
            or _compact(raw) in {_compact(form) for form in _date_forms(source["date"])}
        )
    if value_type == "person":
        haystacks.append(source.get("speaker") or "")
    elif value_type == "committee":
        haystacks.extend([source["committee"], source["committee_filter"]])
    elif value_type == "organization":
        haystacks.extend([source["committee"], source["committee_filter"], source.get("role") or ""])
    if value_type == "number":
        return any(_numeric_compact(raw) in _numeric_compact(text) for text in haystacks)
    return any(_compact(raw) in _compact(text) for text in haystacks)


def _question_has_scope(question: str, source: dict[str, Any]) -> list[str]:
    compact = _compact(question)
    missing: list[str] = []
    if not any(_compact(form) in compact for form in _date_forms(source["date"])):
        missing.append("date")
    if not any(_compact(value) in compact for value in {source["committee"], source["committee_filter"]}):
        missing.append("committee")
    speaker = source.get("speaker")
    speaker_forms = {speaker} if speaker else set()
    if speaker:
        speaker_forms |= SPEAKER_ALIAS_INDEX.get(unicodedata.normalize("NFKC", speaker).casefold(), set())
    if speaker and not any(_compact(form) in compact for form in speaker_forms) and "누구" not in question:
        missing.append("speaker")
    return missing


def _unsupported_answer_numbers(answer: str, source: dict[str, Any]) -> list[str]:
    allowed = _numeric_compact(source["text"] + " " + " ".join(_date_forms(source["date"])))
    unsupported: list[str] = []
    for number in NUMBER_RE.findall(answer):
        year = re.fullmatch(r"(20\d{2})년(?:도)?", number)
        if year and "작년" in source["text"] and int(year.group(1)) == int(source["date"][:4]) - 1:
            continue
        if _numeric_compact(number) not in allowed and number not in unsupported:
            unsupported.append(number)
    return unsupported


def validate_rows(
    queue: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    validator = _load_validator(Path(__file__).parent)
    queue_by_id = {row["id"]: row for row in queue}
    if len(queue_by_id) != len(queue):
        raise AuthoringError("queue contains duplicate ids")

    seen: set[str] = set()
    hard_errors: list[dict[str, str]] = []
    review_flags: list[dict[str, Any]] = []
    categories: Counter[str] = Counter()

    for line_no, wrapper in enumerate(results, 1):
        record = wrapper.get("record")
        record_id = wrapper.get("id")
        if not isinstance(record, dict) or not isinstance(record_id, str):
            hard_errors.append({"id": str(record_id), "error": "missing wrapper id/record"})
            continue
        if record_id in seen:
            hard_errors.append({"id": record_id, "error": "duplicate result id"})
            continue
        seen.add(record_id)
        source = queue_by_id.get(record_id)
        if source is None:
            hard_errors.append({"id": record_id, "error": "id not present in authoring queue"})
            continue

        try:
            validator.validate_record(record, line_no, source["split"])
        except Exception as exc:  # validator owns the detailed contract
            hard_errors.append({"id": record_id, "error": f"schema: {exc}"})
            continue

        mismatches: list[str] = []
        if record["id"] != record_id:
            mismatches.append("record.id")
        if wrapper.get("candidate_id") != source["candidate_id"]:
            mismatches.append("candidate_id")
        if record["category"] != source["category"]:
            mismatches.append("category")
        expected_filters = {
            "committee": source["committee_filter"],
            "date_from": source["date"],
            "date_to": source["date"],
        }
        if record["filters"] != expected_filters:
            mismatches.append("filters")
        evidence = record["gold"]["evidence"]
        if len(evidence) != 1:
            mismatches.append("evidence_count")
        else:
            item = evidence[0]
            expected_evidence = {
                "chunk_id": source["chunk_id"],
                "source_id": source["source_id"],
                "quote": source["text"],
                "speaker": source.get("speaker"),
                "role": source.get("role"),
                "committee": source["committee"],
                "date": source["date"],
                "page_start": source.get("page_start"),
            }
            if item != expected_evidence:
                mismatches.append("evidence")
        if mismatches:
            hard_errors.append({"id": record_id, "error": "source mismatch: " + ", ".join(mismatches)})
            continue

        claim_contamination = _claim_contamination(record)
        if claim_contamination:
            hard_errors.append({
                "id": record_id,
                "error": "claim contamination: " + ", ".join(claim_contamination),
            })
            continue

        unsupported_values = [
            value for value in record["gold"]["exact_values"]
            if not _value_supported(value, source)
        ]
        if unsupported_values:
            hard_errors.append({
                "id": record_id,
                "error": "unsupported exact_values: " + json.dumps(unsupported_values, ensure_ascii=False),
            })
            continue

        flags: list[str] = []
        scope = _question_has_scope(record["question"], source)
        if scope:
            flags.append("question_missing_" + "_".join(scope))
        unsupported_numbers = _unsupported_answer_numbers(record["gold"]["answer"], source)
        if unsupported_numbers:
            flags.append("answer_numbers_need_review:" + ",".join(unsupported_numbers))
        if flags:
            review_flags.append({"id": record_id, "flags": flags})
        categories[record["category"]] += 1

    missing = sorted(set(queue_by_id) - seen)
    if require_complete and missing:
        hard_errors.append({"id": "*", "error": f"missing {len(missing)} results: {missing[:5]}"})

    return {
        "status": "PASS" if not hard_errors else "FAIL",
        "queue_records": len(queue),
        "result_records": len(results),
        "validated_records": sum(categories.values()),
        "categories": dict(sorted(categories.items())),
        "missing_ids": missing,
        "hard_errors": hard_errors,
        "review_flags": review_flags,
        "semantic_review_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        report = validate_rows(
            load_jsonl(args.queue),
            load_jsonl(args.results),
            require_complete=args.require_complete,
        )
    except (OSError, AuthoringError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
