"""2차 골드 자동검사 실패를 원문 그대로 수리한다.

API 재호출이나 새로운 사실 생성 없이 exact_values 표기, 질문 범위 누락,
구조화 출력에 섞인 내부 문구만 명시적으로 고친다.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path


EXACT_REPLACEMENTS = {
    "blind-004": [
        ("date", "2024-11-27"), ("committee", "과학기술정보방송통신위원회"),
        ("person", "강도현"), ("number", "51%"), ("number", "8%"),
        ("number", "60%"), ("number", "50%"),
    ],
    "blind-007": [
        ("date", "2026-04-14"), ("committee", "국토교통위원회"),
        ("person", "곽현준"), ("number", "25조"), ("number", "2항"),
    ],
    "blind-008": [
        ("date", "2025-11-11"), ("committee", "국방위원회"),
        ("person", "송수환"), ("number", "6200만 원"), ("number", "5000만 원"),
    ],
    "blind-010": [
        ("date", "2024-07-16"), ("committee", "행정안전위원회"),
        ("person", "이해식"), ("date", "2022년 이후"),
        ("organization", "홍콩"), ("organization", "싱가포르"),
        ("organization", "대만"), ("organization", "호주"),
    ],
    "blind-013": [
        ("date", "2024-08-28"), ("committee", "과학기술정보방송통신위원회"),
        ("person", "신성범"), ("number", "2800억"), ("number", "1967억"),
        ("number", "2581억"),
    ],
    "blind-015": [
        ("date", "2024-08-13"), ("committee", "외교통일위원회"),
        ("person", "한정애"), ("number", "1만 7000명"), ("number", "10년 이상"),
        ("number", "2300명"), ("number", "20년"),
    ],
    "blind-022": [
        ("date", "2024-11-26"), ("committee", "산업통상자원중소벤처기업위원회"),
        ("person", "김완기"), ("number", "21페이지"), ("number", "24페이지"),
        ("number", "제89조제1항"), ("number", "제90조제7항"), ("number", "93조"),
    ],
    "blind-039": [
        ("date", "2024-07-16"), ("committee", "과학기술정보방송통신위원회"),
        ("person", "강성범"), ("person", "김제동"), ("person", "류승완"),
        ("person", "문소리"), ("person", "박찬욱"), ("person", "봉준호"),
    ],
    "blind-043": [
        ("date", "2024-11-18"), ("committee", "정무위원회"),
        ("person", "박종민"), ("number", "26명"), ("number", "3억 원"),
    ],
    "blind-051": [
        ("date", "2025-08-18"), ("committee", "보건복지위원회"),
        ("person", "서미화"), ("organization", "서비스 종합조사 TF"),
        ("organization", "국립의대"),
    ],
    "blind-057": [
        ("date", "2024-07-24"), ("committee", "정무위원회"),
        ("person", "신장식"), ("person", "류희림"), ("number", "어제저녁 6시"),
        ("number", "방심위원 5명"), ("organization", "권익위원회"),
    ],
    "blind-082": [
        ("date", "2024-11-12"), ("committee", "정무위원회"),
        ("person", "방기선"), ("date", "15년도"), ("organization", "복지부"),
    ],
    "reserve-003": [
        ("date", "2025-11-24"), ("committee", "정무위원회"),
        ("person", "김동아"), ("person", "이병진"),
        ("number", "2211919"), ("number", "2211964"),
        ("number", "2212348"), ("number", "2212384"),
    ],
    "reserve-006": [
        ("date", "2026-04-28"), ("committee", "국토교통위원회"),
        ("person", "김이탁"), ("date", "11년 10월"), ("date", "11년 11월"),
        ("number", "800%"),
    ],
}


def exact(items: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"type": kind, "value": value} for kind, value in items]


def repair(rows: list[dict]) -> list[dict]:
    repaired = deepcopy(rows)
    by_id = {row["id"]: row for row in repaired}
    missing = sorted(set(EXACT_REPLACEMENTS) - set(by_id))
    if missing:
        raise ValueError(f"missing repair IDs: {missing}")
    for record_id, replacements in EXACT_REPLACEMENTS.items():
        record = by_id[record_id]["record"]
        record["gold"]["exact_values"] = exact(replacements)
        record["provenance"]["source_review_repairs"] = ["exact_values"]

    # 1200만 원은 6200-5000의 올바른 계산이지만 원문 직접값이 아니다. 블라인드
    # 골드는 직접 근거만 허용하므로 차액 숫자를 묻지 않고 어느 쪽이 큰지만 남긴다.
    record = by_id["blind-008"]["record"]
    record["question"] = (
        "2025년 11월 11일 국방위원회에서 송수환 수석전문위원이 보고한 "
        "한미 핵·재래식 통합(CNI) 도상연습 참가 대비 부족분과 CNI 워게임 참가 "
        "부족분의 증액 필요액은 각각 얼마이며, 어느 쪽이 더 많습니까?"
    )
    record["gold"]["answer"] = (
        "CNI 도상연습 참가 대비 부족분은 6200만 원, CNI 워게임 참가 부족분은 "
        "5000만 원이며 도상연습 참가 대비 부족분이 더 많습니다."
    )
    record["gold"]["required_claims"] = [
        "CNI 도상연습 참가 대비 부족분의 증액 필요액은 6200만 원이다.",
        "CNI 워게임 참가 부족분의 증액 필요액은 5000만 원이다.",
        "도상연습 참가 대비 부족분이 더 많다.",
    ]
    record["provenance"]["source_review_repairs"] = ["exact_values", "derived_number_removed"]

    record = by_id["blind-026"]["record"]
    record["question"] = (
        "2025년 11월 27일 행정안전위원회에서 신정훈 위원장이 제시한 안건 중 "
        "공직선거법 일부개정법률안(대안)과 경찰공무원법 일부개정법률안(대안)의 "
        "안건 번호는 각각 무엇입니까?"
    )
    record["gold"]["answer"] = "공직선거법 일부개정법률안(대안)은 104번, 경찰공무원법 일부개정법률안(대안)은 110번 안건입니다."
    record["gold"]["required_claims"] = [
        "공직선거법 일부개정법률안(대안)은 104번 안건이다.",
        "경찰공무원법 일부개정법률안(대안)은 110번 안건이다.",
    ]
    record["gold"]["forbidden_claims"] = [
        "두 대안이 최종 의결·시행됐다고 단정하는 것",
        "원문에 없는 법률안의 세부 내용을 추가하는 것",
    ]
    record["gold"]["exact_values"] = exact([
        ("date", "2025-11-27"), ("committee", "행정안전위원회"),
        ("person", "신정훈"), ("number", "104"), ("number", "110"),
    ])
    record["provenance"]["source_review_repairs"] = ["question_scope", "gold"]

    record = by_id["blind-044"]["record"]
    record["question"] = (
        "2025년 4월 23일 산업통상자원중소벤처기업위원회에서 김원이 위원은 "
        "정권 교체기의 책임 있는 결정 문제를 장관에게 이야기할 때 자신과 함께 "
        "보고받았다고 언급한 두 사람은 누구입니까?"
    )
    record["gold"]["answer"] = "김원이 위원은 이철규 산자위원장과 박성민 간사를 함께 보고받은 사람으로 언급했습니다."
    record["gold"]["required_claims"] = ["함께 보고받았다고 언급한 사람은 이철규 산자위원장과 박성민 간사이다."]
    record["gold"]["forbidden_claims"] = [
        "두 사람이 협상 결정을 승인했다고 단정하는 것",
        "원문에 없는 보고 내용이나 결론을 추가하는 것",
    ]
    record["provenance"]["source_review_repairs"] = ["question_scope", "gold"]

    # 구조화 출력에 섞인 아랍어 구두점을 정상 문장 두 개로 분리한다.
    record = by_id["blind-082"]["record"]
    record["gold"]["required_claims"] = [
        "분양형 노인주택 제도는 2015년에 폐지됐다.",
        "불법행위와 부실 운영 사례가 많았던 것이 폐지 이유였다.",
        "현재 보건복지부가 관련 연구용역을 추진하고 있으며 연말 정도까지 진행될 것으로 보인다고 설명했다.",
        "필요성이 인정되면 노인복지법 개정안 발의와 사회적 의견 수렴을 거칠 수 있다고 설명했다.",
    ]
    record["provenance"]["source_review_repairs"] = ["exact_values", "required_claims"]

    # 목록 구분자로 섞인 U+00BA 문자를 제거하고 독립 주장으로 복원한다.
    record = by_id["blind-080"]["record"]
    record["gold"]["required_claims"] = [
        "버커킹 행사 소개를 사례로 제시했다.",
        "한화 재벌가에서 들여온 햄버거집 소개를 사례로 제시했다.",
        "틱톡 초대 시 3만 원 이벤트 소개를 사례로 제시했다.",
        "편의점에서 판매하는 화장품 소개를 사례로 제시했다.",
        "올가을 제니가 즐겨 입는 옷 브랜드의 국내 출시를 사례로 제시했다.",
        "스타벅스 진동벨 도입을 사례로 제시했다.",
    ]
    record["provenance"]["source_review_repairs"] = ["required_claims"]

    # 단독으로 읽어도 지시 대상이 드러나도록 모호한 대명사를 제거한다.
    record = by_id["blind-090"]["record"]
    record["question"] = (
        "2025년 7월 21일 정무위원회에서 권대영 금융위원회부위원장은 대한민국의 "
        "향후 20년 이상을 바라보는 거대한 프로젝트의 운영·집행과 관련해 정부가 "
        "어떻게 하겠다고 답변했습니까?"
    )
    record["provenance"]["source_review_repairs"] = ["question_clarity"]

    record = by_id["reserve-030"]["record"]
    record["question"] = (
        "2024년 9월 11일 외교통일위원회에서 조태열 외교부장관은 관동대지진 사건과 "
        "같은 문제에 관해 어떤 순서로 입장을 정리하고 조치를 취해야 한다고 말했습니까?"
    )
    record["provenance"]["source_review_repairs"] = ["question_clarity"]

    rendered = "\n".join(json.dumps(row, ensure_ascii=False) for row in repaired)
    banned = re.compile(r"wait invalid|Need redo|자료 그대로 써야|[\u00ba\u0600-\u06ff]", re.IGNORECASE)
    if banned.search(rendered):
        raise ValueError("model-internal or foreign-script contamination remains")
    return repaired


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    repaired = repair(rows)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in repaired),
        encoding="utf-8", newline="\n",
    )
    print(json.dumps({"status": "PASS", "records": len(repaired), "repaired_ids": sorted(set(EXACT_REPLACEMENTS) | {"blind-026", "blind-044"})}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
