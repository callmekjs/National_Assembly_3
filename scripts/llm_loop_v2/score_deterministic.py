"""현재 답변을 보수적인 결정 규칙으로 1차 채점한다.

의미상 정답 여부는 이 단계에서 PASS로 판정하지 않는다. 명백한 실패만 AUTO_FAIL로
떨어뜨리고 나머지는 SEMANTIC_REVIEW_REQUIRED로 보낸다.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REFUSAL = re.compile(r"(확인할 수 없|근거가 없|자료가 없|답변할 수 없)")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compact(value: str) -> str:
    return re.sub(r"[\s,_*]", "", value).casefold()


NATIVE_NUMBERS = {
    "1": ("한", "하나"), "2": ("두", "둘"), "3": ("세", "셋"),
    "4": ("네", "넷"), "5": ("다섯",), "6": ("여섯",),
    "7": ("일곱",), "8": ("여덟",), "9": ("아홉",),
}

NATIVE_TENS = {
    "열": "10", "스무": "20", "서른": "30", "마흔": "40", "쉰": "50",
    "예순": "60", "일흔": "70", "여든": "80", "아흔": "90",
}

NATIVE_COUNTERS = ("명", "분", "대", "개", "건", "곳", "회", "번", "차례", "차", "배")

SINO_DIGITS = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
SINO_UNITS = {"십": 10, "백": 100, "천": 1000}


def parse_sino_korean_integer(value: str) -> int | None:
    """단위 앞의 한자어 수사(백이십, 이십 등)만 보수적으로 정수화한다."""
    if not value or any(char not in SINO_DIGITS and char not in SINO_UNITS for char in value):
        return None
    total = 0
    pending = 0
    for char in value:
        if char in SINO_DIGITS:
            pending = SINO_DIGITS[char]
            continue
        unit = SINO_UNITS[char]
        total += (pending or 1) * unit
        pending = 0
    return total + pending


def normalize_number_expression(value: str) -> str:
    """의미가 같은 흔한 한국어 수량·횟수 표기를 하나의 문자열로 맞춘다."""
    # 공백을 지우기 전에 `세 대`처럼 고유어 수사+단위인 표현만 숫자로 바꾼다.
    # 단순히 답변 어딘가에 `세`가 있다는 이유로 숫자 3을 인정하면 `세부`·`세계`도
    # 정답으로 오인하므로 단위가 붙은 수량만 변환한다.
    normalized = value.casefold()
    for digit, words in NATIVE_NUMBERS.items():
        for word in words:
            for counter in NATIVE_COUNTERS:
                normalized = re.sub(
                    rf"{re.escape(word)}\s+{re.escape(counter)}",
                    f"{digit}{counter}",
                    normalized,
                )
    counters = "|".join(map(re.escape, NATIVE_COUNTERS))

    def replace_sino(match: re.Match[str]) -> str:
        parsed = parse_sino_korean_integer(match.group("number"))
        return match.group(0) if parsed is None else f"{parsed}{match.group('counter')}"

    normalized = re.sub(
        rf"(?P<number>[일이삼사오육칠팔구십백천]+)\s*(?P<counter>{counters})",
        replace_sino,
        normalized,
    )
    normalized = compact(normalized)
    # 회의록의 ``25년도``와 답변의 ``2025년도``는 같은 연도 표기다.
    normalized = re.sub(r"(?<!\d)20(\d{2})(?=년도?)", r"\1", normalized)
    normalized = normalized.replace("몇백", "수백")
    for native, digit in NATIVE_TENS.items():
        normalized = re.sub(rf"{native}(?=번|회|차례)", digit, normalized)
    normalized = re.sub(r"(?:번|차례)", "회", normalized)
    return normalized


def number_present(value: str, answer: str) -> bool:
    """표기 차이(4번/4회/네 번)를 허용하되 숫자 자체가 없으면 실패한다."""
    expected = normalize_number_expression(value)
    expected_groups = re.findall(r"\d+", expected)
    answer_flat = normalize_number_expression(answer)
    for group in expected_groups:
        digit_found = bool(re.search(rf"(?<!\d){re.escape(group)}(?!\d)", answer_flat))
        if not digit_found:
            # `총 120명`을 `국민 100명과 전문가 20명`으로 풀어 쓴 경우처럼
            # 동일한 사람 수 구성요소의 명시적 합계도 인정한다. 다른 단위나
            # 문맥의 숫자를 임의로 더하지 않도록 사람 계수사에만 한정한다.
            expected_people = re.fullmatch(r"(\d+)(?:명|분)", expected)
            answer_people = [
                int(number)
                for number in re.findall(r"(?<!\d)(\d+)(?:명|분)", answer_flat)
            ]
            if not (
                expected_people
                and len(answer_people) >= 2
                and sum(answer_people) == int(expected_people.group(1))
            ):
                return False
    if expected_groups:
        return True

    # 숫자가 한글로만 쓰인 경우도 실제 핵심값이다. 몇백/수백, 번/회/차례처럼
    # 의미가 같은 표기를 정규화한 뒤 구문 포함 여부를 검사한다.
    return bool(expected) and expected in answer_flat


def exact_value_present(item: dict[str, str], answer: str) -> bool:
    """정답표가 핵심값으로 선언한 사람·날짜·기관·숫자를 모두 확인한다."""
    value = item["value"]
    if item["type"] == "number":
        return number_present(value, answer)
    answer_flat = compact(answer)
    if item["type"] == "date":
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
        if match:
            year, month, day = match.groups()
            forms = {
                value,
                f"{year}.{int(month)}.{int(day)}",
                f"{year}년{int(month)}월{int(day)}일",
                f"{int(month)}월{int(day)}일",
            }
            return any(compact(form) in answer_flat for form in forms)
    return compact(value) in answer_flat


def speaker_matches(expected: str, actual: object) -> bool:
    """한자명과 `한자명(한글명)`처럼 명시적인 표기 별칭만 동치로 인정한다."""
    expected_flat = compact(expected)
    actual_flat = compact(str(actual or ""))
    if expected_flat == actual_flat:
        return True
    return (
        actual_flat.startswith(expected_flat + "(") and actual_flat.endswith(")")
    ) or (
        expected_flat.startswith(actual_flat + "(") and expected_flat.endswith(")")
    )


def score(record: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    response = result.get("response") or {}
    answer = str(response.get("answer") or "")
    citations = response.get("citations") or []
    gold = record["gold"]
    expected_sources = {item["source_id"] for item in gold["evidence"]}
    expected_chunks = {item["chunk_id"] for item in gold["evidence"]}
    expected_speakers = {item["speaker"] for item in gold["evidence"] if item.get("speaker")}
    expected_committee = record["filters"]["committee"]
    expected_committee_values = {expected_committee} if expected_committee else set()
    expected_committee_values.update(
        item["committee"] for item in gold["evidence"] if item.get("committee")
    )
    date_from = record["filters"]["date_from"]
    date_to = record["filters"]["date_to"]
    reasons: list[str] = []

    if result.get("http_status") != 200 or result.get("error"):
        reasons.append("http_or_runner_error")
    if record["answerable"] and REFUSAL.search(answer):
        reasons.append("unexpected_refusal")
    if not record["answerable"] and not REFUSAL.search(answer):
        reasons.append("missing_required_refusal")
    if not record["answerable"] and citations:
        reasons.append("unanswerable_has_citations")
    if record["answerable"] and not citations:
        reasons.append("no_citation")
    if response.get("invalid_citations"):
        reasons.append("invalid_citation_number")

    cited_source_ids = {
        source_id
        for source_id in expected_sources
        if any(str(citation.get("chunk_id") or "").startswith(source_id + "_") for citation in citations)
    }
    cited_chunk_ids = {
        str(chunk_id)
        for citation in citations
        for chunk_id in (
            citation.get("support_chunk_ids")
            if isinstance(citation.get("support_chunk_ids"), list)
            else [citation.get("chunk_id")]
        )
        if chunk_id
    }
    cited_evidence_chunks = expected_chunks & cited_chunk_ids
    if record["answerable"] and not cited_evidence_chunks:
        reasons.append("gold_evidence_not_cited")
    speaker_cited = not expected_speakers or any(
        speaker_matches(expected, citation.get("speaker"))
        for expected in expected_speakers
        for citation in citations
    )
    if not speaker_cited:
        reasons.append("gold_speaker_not_cited")
    if expected_committee_values and any(
        citation.get("committee") not in expected_committee_values for citation in citations
    ):
        reasons.append("wrong_committee_citation")
    if date_from and any(str(citation.get("date")) < date_from for citation in citations):
        reasons.append("citation_before_date_filter")
    if date_to and any(str(citation.get("date")) > date_to for citation in citations):
        reasons.append("citation_after_date_filter")

    # 날짜·위원회·발언자는 질문의 범위 지정값일 수 있어 답변 본문 반복을 강제하지
    # 않는다. 답변에 실제로 요구된 핵심 숫자만 결정 규칙으로 검사하고 나머지는
    # 독립 의미 채점에서 문맥상 정확성을 확인한다.
    # v2.1부터 질문이 실제로 요구한 값과 원문에만 존재하는 참고값을 분리한다.
    # 새 필드가 없는 동결된 구자료는 기존 exact_values를 사용해 재현성을 보존한다.
    required_exact_values = gold.get("required_exact_values", gold["exact_values"])
    missing_values = [
        item for item in required_exact_values
        if item["type"] == "number" and not exact_value_present(item, answer)
    ]
    missing_numbers = [item["value"] for item in missing_values if item["type"] == "number"]
    if missing_values:
        reasons.append("missing_exact_number")

    return {
        "id": record["id"],
        "grounding": response.get("grounding"),
        "system_verification_flags": (response.get("verification") or {}).get("flags") or [],
        "citation_count": len(citations),
        "gold_source_cited": bool(cited_source_ids),
        "gold_evidence_chunk_cited": bool(cited_evidence_chunks),
        "gold_speaker_cited": speaker_cited,
        "missing_numbers": missing_numbers,
        "missing_exact_values": missing_values,
        "auto_fail_reasons": reasons,
        "stage1_verdict": "AUTO_FAIL" if reasons else "SEMANTIC_REVIEW_REQUIRED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    records = {item["id"]: item for item in load_jsonl(args.dataset)}
    results = load_jsonl(args.results)
    scored = [score(records[item["id"]], item) for item in results]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    score_path = args.output_dir / "deterministic_scores.jsonl"
    score_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in scored),
        encoding="utf-8", newline="\n",
    )
    verdicts = Counter(item["stage1_verdict"] for item in scored)
    grounding = Counter(str(item["grounding"]) for item in scored)
    full_auto_fail = [item["id"] for item in scored if item["grounding"] == "FULL" and item["stage1_verdict"] == "AUTO_FAIL"]
    summary = {
        "records": len(scored),
        "verdicts": dict(verdicts),
        "grounding": dict(grounding),
        "full_but_auto_fail": full_auto_fail,
        "semantic_review_required": [item["id"] for item in scored if item["stage1_verdict"] == "SEMANTIC_REVIEW_REQUIRED"],
        "note": "1차 규칙 통과는 정답 판정이 아니며 의미 검토 전에는 PASS로 승격하지 않는다.",
    }
    (args.output_dir / "deterministic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
