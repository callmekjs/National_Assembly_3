"""사람/에이전트 검수에서 발견한 원문 내부 모순을 보수적으로 표시한다.

원본 작성 결과는 보존하고, 명시된 ID의 질문·정답·채점 주장만 바꾼 새 JSONL을
만든다. 숫자를 임의로 교정하지 않고 회의록 기록과 검증 필요성을 함께 남긴다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPAIRS = {
    "blind-008": {
        "question": (
            "2024년 11월 11일 과학기술정보방송통신위원회에서 신성범 위원이 "
            "중고거래 관련 분쟁 조정 예산에 관해 언급한 2024년도 예산액과 내년도 "
            "감소액은 회의록에 각각 어떻게 기록되어 있으며, 이 수치를 답할 때 어떤 "
            "주의가 필요합니까?"
        ),
        "answer": (
            "회의록에는 2024년도 예산이 7억 원이고 내년에는 4조 9702억 원이 "
            "줄었다고 기록되어 있습니다. 다만 7억 원 규모의 예산과 4조 원대 감소액은 "
            "서로 규모가 맞지 않으므로, OCR 또는 원문 오류 가능성을 표시하고 원자료 "
            "확인 없이 실제 예산 수치로 단정하면 안 됩니다."
        ),
        "required_claims": [
            "회의록에는 2024년도 예산이 7억 원이라고 기록되어 있다.",
            "회의록에는 내년도 감소액이 4조 9702억 원이라고 기록되어 있다.",
            "두 수치는 규모상 모순 가능성이 있어 원자료 확인 전에는 사실값으로 단정할 수 없다.",
        ],
        "forbidden_claims": [
            "4조 9702억 원을 검증된 실제 감소액이라고 단정하는 것",
            "근거 없이 4억 9702만 원 등 다른 수치로 교정하는 것",
            "회의록에 오류가 확정되었다고 단정하는 것",
        ],
    },
    "blind-012": {
        "question": (
            "2025년 2월 18일 행정안전위원회에서 조승환 위원은 치안감에서 "
            "치안정감으로 승진하는 기간을 2023년 10월~2025년 6월 사례, 윤희근 청장 "
            "사례, 우철문 청장 사례로 각각 어떻게 표현했으며, 첫 기간을 인용할 때 "
            "어떤 주의가 필요합니까?"
        ),
        "answer": (
            "조승환 위원은 2023년 10월부터 2025년 6월까지의 사례를 ‘1년, 최소 "
            "1년 4개월’이라고 표현했고, 윤희근 청장은 7개월, 우철문 청장은 11개월이 "
            "걸렸다고 비교했습니다. 다만 첫 번째 날짜 구간과 발언 속 기간 표현은 "
            "단순 달력 계산상 서로 맞지 않으므로, 발언을 그대로 인용하되 검증된 "
            "기간으로 단정하면 안 됩니다."
        ),
        "required_claims": [
            "2023년 10월부터 2025년 6월까지를 발언에서는 ‘1년, 최소 1년 4개월’이라고 표현했다.",
            "윤희근 청장 사례는 7개월이라고 말했다.",
            "우철문 청장 사례는 11개월이라고 말했다.",
            "첫 날짜 구간과 발언 속 기간 표현이 맞지 않아 그대로 인용하되 검증값으로 단정할 수 없다.",
        ],
        "forbidden_claims": [
            "‘1년, 최소 1년 4개월’을 날짜 계산으로 검증된 기간이라고 단정하는 것",
            "회의록 근거 없이 첫 기간을 임의의 다른 수치로 교정하는 것",
            "윤희근 청장과 우철문 청장의 기간을 서로 바꾸는 것",
        ],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    seen: set[str] = set()
    repaired_at = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    rendered: list[str] = []
    for row in rows:
        record_id = row["id"]
        if record_id in REPAIRS:
            seen.add(record_id)
            change = REPAIRS[record_id]
            row["record"]["question"] = change["question"]
            gold = row["record"]["gold"]
            gold["answer"] = change["answer"]
            gold["required_claims"] = change["required_claims"]
            gold["forbidden_claims"] = change["forbidden_claims"]
            row["semantic_review_repair"] = {
                "repaired_at": repaired_at,
                "scope": "question_answer_claims",
                "reason": "source-internal numerical inconsistency; preserve quote and require verification",
            }
        rendered.append(json.dumps(row, ensure_ascii=False))

    missing = sorted(set(REPAIRS) - seen)
    if missing:
        raise ValueError(f"repair ids missing from input: {missing}")
    args.output.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "records": len(rows), "repaired_ids": sorted(seen)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
