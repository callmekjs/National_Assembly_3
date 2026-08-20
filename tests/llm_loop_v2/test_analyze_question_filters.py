import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "llm_loop_v2"))

from analyze_question_filters import analyze  # noqa: E402


def row(question: str, *, committee="외통위", date_from="2024-10-24", date_to="2024-10-24"):
    return {
        "id": "blind-001",
        "question": question,
        "filters": {"committee": committee, "date_from": date_from, "date_to": date_to},
    }


def test_analyze_passes_exact_question_filters():
    report = analyze([row(
        "2024년 10월 24일 외교통일위원회에서 10월 18일 발표 이후 조치는 무엇입니까?"
    )])
    assert report["status"] == "PASS"
    assert report["mismatch_count"] == 0


def test_analyze_reports_mismatch_details():
    report = analyze([row(
        "2024년 10월 24일 외교통일위원회에서 무엇을 논의했습니까?",
        date_from="2024-10-25",
        date_to="2024-10-25",
    )])
    assert report["status"] == "FAIL"
    assert report["mismatches"][0]["actual"]["date_from"] == "2024-10-24"


def test_speaker_context_keeps_leading_meeting_date_not_comparison_years():
    report = analyze([row(
        "2024-11-11 국방위원회 강선영 위원 발언에 따르면, 사고는 2022년과 2023년에 각각 몇 건입니까?",
        committee="국방위",
        date_from="2024-11-11",
        date_to="2024-11-11",
    )])
    assert report["status"] == "PASS"


def test_possessive_speaker_context_keeps_leading_meeting_date():
    report = analyze([row(
        "2024-08-14 과학기술정보방송통신위원회 한민수 위원의 발언에 따르면, 2017년 6월과 17년 7월은 어떻게 다릅니까?",
        committee="과방위",
        date_from="2024-08-14",
        date_to="2024-08-14",
    )])
    assert report["status"] == "PASS"
