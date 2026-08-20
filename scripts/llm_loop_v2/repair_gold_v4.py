"""v4 작성 결과의 원문 불일치 exact_values만 명시적으로 교정한다.

질문·정답·필수 주장·금지 주장·원문은 바꾸지 않는다. 원본 v4는 그대로 보존한다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


REPAIRS = {
    "blind-001": [
        ("date", "2024-11-27"), ("committee", "외교통일위원회"), ("person", "김준형"),
        ("date", "22년도"), ("number", "1조 2000억 원"), ("number", "1조 7000억"),
    ],
    "blind-002": [
        ("date", "2024-11-27"), ("committee", "국토교통위원회"), ("person", "진현환"),
        ("number", "8000가구"), ("organization", "국토교통부"),
    ],
    "blind-004": [
        ("date", "2025-11-25"), ("committee", "국방위원회"), ("person", "부승찬"),
        ("person", "성일종"), ("number", "55"), ("number", "2206090"),
        ("person", "전현희"), ("number", "56"), ("number", "2210378"),
    ],
    "blind-009": [
        ("date", "2026-04-30"), ("committee", "재정경제기획위원회"), ("person", "임이자"),
        ("number", "57"), ("person", "안태준"), ("number", "2216730"),
        ("number", "64"), ("person", "조승환"), ("number", "2217069"),
    ],
    "blind-011": [
        ("date", "2024-09-04"), ("committee", "국토교통위원회"), ("person", "박재유"),
        ("date", "2022년도"), ("number", "14.2%"), ("date", "23년도"), ("number", "65.9%"),
    ],
    "blind-012": [
        ("date", "2025-02-18"), ("committee", "행정안전위원회"), ("person", "조승환"),
        ("date", "2023년 10월"), ("date", "2025년 6월"), ("number", "1년"),
        ("number", "최소 1년 4개월"), ("person", "윤희근"), ("number", "7개월"),
        ("person", "우철문"), ("date", "2021년 7월"), ("date", "2022년 6월"), ("number", "11개월"),
    ],
    "blind-014": [
        ("date", "2025-11-13"), ("committee", "정무위원회"), ("person", "성재민"),
        ("organization", "한국노동연구원"), ("number", "1억 400만 원"),
        ("number", "6000만 원"), ("number", "300만 원"), ("date", "26년예산안"),
    ],
    "blind-016": [
        ("date", "2025-08-19"), ("committee", "산업통상자원중소벤처기업위원회"), ("person", "이종배"),
        ("date", "7월 31일"), ("date", "18일"), ("number", "407개 품목"), ("number", "50%"),
    ],
    "blind-018": [
        ("date", "2025-08-26"), ("committee", "과학기술정보방송통신위원회"), ("person", "이훈기"),
        ("date", "2017년부터"), ("number", "1조 원"), ("number", "1380억"),
        ("number", "1600억"), ("number", "1000억"),
    ],
    "blind-021": [
        ("date", "2024-09-02"), ("committee", "행정안전위원회"), ("person", "윤건영"),
        ("number", "2년 전"), ("number", "정보공개법 제9조 1항 5호"),
    ],
    "blind-028": [
        ("date", "2026-04-29"), ("committee", "보건복지위원회"), ("person", "이지민"),
        ("date", "23년 5월"),
    ],
    "blind-054": [
        ("date", "2024-08-22"), ("committee", "보건복지위원회"), ("person", "이개호"),
        ("organization", "보건복지부"), ("organization", "기재부"),
    ],
    "blind-060": [
        ("date", "2025-09-24"), ("committee", "국방위원회"), ("person", "박선원"),
        ("number", "열 번"), ("number", "열 번 이상"),
    ],
    "blind-062": [
        ("date", "2025-08-19"), ("committee", "보건복지위원회"), ("person", "이지민"),
        ("organization", "복지부"), ("organization", "환자정책위원회"),
        ("organization", "환자통합지원센터"),
    ],
    "blind-074": [
        ("date", "2025-02-17"), ("committee", "산업통상자원중소벤처기업위원회"), ("person", "최남호"),
        ("number", "24조"), ("number", "45조"),
    ],
    "blind-082": [
        ("date", "2025-09-10"), ("committee", "산업통상자원중소벤처기업위원회"), ("person", "유법민"),
        ("number", "1000만 원"),
    ],
    "reserve-005": [
        ("date", "2025-07-18"), ("committee", "행정안전위원회"), ("person", "윤건영"),
        ("date", "22년 6월 6일"), ("date", "7월 7일"),
    ],
    "reserve-012": [
        ("date", "2024-09-04"), ("committee", "행정안전위원회"), ("person", "이성권"),
        ("date", "작년 12월 31일"), ("organization", "부산시"),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    found = {row["id"] for row in rows if row["id"] in REPAIRS}
    if found != set(REPAIRS):
        raise SystemExit(f"repair ids mismatch; missing={sorted(set(REPAIRS) - found)}")
    repaired_at = datetime.now().astimezone().isoformat()
    for row in rows:
        values = REPAIRS.get(row["id"])
        if values is None:
            continue
        row["record"]["gold"]["exact_values"] = [
            {"type": value_type, "value": value} for value_type, value in values
        ]
        row["repair"] = {
            "repaired_at": repaired_at,
            "scope": "exact_values_only",
            "reason": "source-literal normalization or malformed exact value",
        }
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "REPAIRED", "records": len(rows), "changed": len(REPAIRS)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
