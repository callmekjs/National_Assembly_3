"""
parser_v1 발언자 추출 단위 테스트.

실제 회의록 데이터에서 관측된 헤더 패턴으로 검증한다.
실행: python tests/test_parser_speaker.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from parser_v1 import _extract_speaker, _NON_SPEAKER_HDR, _NON_SPEAKER_NAMES  # noqa: E402

# (header_line, expected_name, expected_role) — v1.0에서 잘 되던 기존 패턴 (회귀 방지)
REGRESSION_CASES = [
    ("위원장 최민희 의석을 정돈해 주시기 바랍니다.", "최민희", "위원장"),
    ("소위원장 김영배 회의를 시작하겠습니다.", "김영배", "소위원장"),
    ("김현 위원 질의하겠습니다.", "김현", "위원"),
    ("외교부장관 조태열 답변드리겠습니다.", "조태열", "외교부장관"),
    ("행정안전부차관 고기동 예.", "고기동", "행정안전부차관"),
]

# v1.1에서 새로 잡아야 하는 패턴 (실측 24종 대표)
NEW_CASES = [
    ("증인 유영상 예.", "유영상", "증인"),
    ("증인 조성은 예, 그렇습니다.", "조성은", "증인"),
    ("참고인 주병기 예.", "주병기", "참고인"),
    ("진술인 이인호 예.", "이인호", "진술인"),
    ("전문위원 정경윤 보고드리겠습니다.", "정경윤", "전문위원"),
    ("전문위원 최기도 전문위원입니다.", "최기도", "전문위원"),
    ("수석전문위원 김일수 검토보고드리겠습니다.", "김일수", "수석전문위원"),
    ("위원장대리 윤건영 수고하셨습니다.", "윤건영", "위원장대리"),
    ("경찰청장 조지호 예.", "조지호", "경찰청장"),
    ("경찰청차장 유재성 예, 그렇습니다.", "유재성", "경찰청차장"),
    ("금융위원장 김병환 예.", "김병환", "금융위원장"),
    ("소방청장 허석곤 예, 그렇습니다.", "허석곤", "소방청장"),
    ("병무청장 김종철 답변드리겠습니다.", "김종철", "병무청장"),
    ("국세청차장 김창기 예.", "김창기", "국세청차장"),
    ("관세청장 고광효 예.", "고광효", "관세청장"),
    ("조달청차장 임기근 예.", "임기근", "조달청차장"),
    ("특허청장 김완기 특허청장입니다.", "김완기", "특허청장"),
    ("조정위원장 박정수 조정안을 말씀드리겠습니다.", "박정수", "조정위원장"),
    ("행정실장 이광재 보고드리겠습니다.", "이광재", "행정실장"),
    ("입법조사관 박철수 예.", "박철수", "입법조사관"),
    # 외국인 음차명 + 익명처리 (v1.1 확장)
    ("증인 해럴드로저스 예.", "해럴드로저스", "증인"),
    ("증인 브랫매티스 맞습니다.", "브랫매티스", "증인"),
    ("참고인 박00 예, 맞습니다.", "박00", "참고인"),
    # 직무대리/직무대행/권한대행 (v1.2 확장)
    ("위원장직무대리 박성민 의석을 정돈해 주시기 바랍니다.", "박성민", "위원장직무대리"),
    ("경찰청장직무대행 이호영 예.", "이호영", "경찰청장직무대행"),
    ("방송통신위원장직무대행 김태규 답변드리겠습니다.", "김태규", "방송통신위원장직무대행"),
    ("서울특별시장권한대행 김성보 예, 그렇습니다.", "김성보", "서울특별시장권한대행"),
    ("방송통신위원회사무처장전담직무대리 김영관 보고드리겠습니다.", "김영관", "방송통신위원회사무처장전담직무대리"),
    # 후보자·제N차관·특수문자 조직명·한자명 (v1.2 확장)
    ("한국방송공사사장후보자 박장범 예.", "박장범", "한국방송공사사장후보자"),
    ("방송통신위원장후보자 이진숙 예.", "이진숙", "방송통신위원장후보자"),
    ("보건복지부제2차관 이형훈 예.", "이형훈", "보건복지부제2차관"),
    ("국토교통부제1차관 진현환 예, 그렇습니다.", "진현환", "국토교통부제1차관"),
    ("중앙선거관리위원회사무총장 김용빈 예.", "김용빈", "중앙선거관리위원회사무총장"),
    ("육군참모총장 박안수 예.", "박안수", "육군참모총장"),
    ("한국은행총재 이창용 예.", "이창용", "한국은행총재"),
    ("쿠팡㈜대표이사 박대준 예.", "박대준", "쿠팡㈜대표이사"),
    ("(전)육군특수전사령관 곽종근 그렇습니다.", "곽종근", "(전)육군특수전사령관"),
    ("드론작전사령관 김용대 예.", "김용대", "드론작전사령관"),
    ("한국방송공사감사 박찬욱 예.", "박찬욱", "한국방송공사감사"),
    ("독립기념관장 김형석 예.", "김형석", "독립기념관장"),
    ("柳榮夏 위원 알겠습니다.", "柳榮夏", "위원"),
]

# 발언자로 잡히면 안 되는 잡음 (사람 아님)
GARBAGE_HEADERS = [
    "출장 위원(1인)",
    "위원 아닌 출석 의원(2인)",
    "소위원회 직접 회부계엄법 일부개정법률안",
    "청가 위원(1인) 이준석 (2024. 5. 31. 안철수 의원 대표발의)",
]


def _run_speaker_cases(cases) -> list[str]:
    """(header, 기대 이름, 기대 role) 케이스 실행 — 실패 목록 반환 + 케이스별 출력."""
    failures = []
    for header, exp_name, exp_role in cases:
        name, role, _ = _extract_speaker(header)
        ok = (name == exp_name and role == exp_role)
        mark = "✓" if ok else "✗"
        if not ok:
            failures.append(f"{header!r} → {name!r}/{role!r} (기대 {exp_name}/{exp_role})")
        print(f"  {mark} {header[:34]!r} → name={name!r} role={role!r}"
              + ("" if ok else f"  [기대: {exp_name}/{exp_role}]"))
    return failures


def test_regression_cases():
    print("=== 회귀 테스트 (v1.0 기존 패턴) ===")
    failures = _run_speaker_cases(REGRESSION_CASES)
    assert not failures, failures


def test_new_cases():
    print("=== 신규 패턴 테스트 (v1.1) ===")
    failures = _run_speaker_cases(NEW_CASES)
    assert not failures, failures


def test_garbage_headers():
    print("=== 잡음 제외 테스트 ===")
    failures = []
    for header in GARBAGE_HEADERS:
        blocked_by_hdr = bool(_NON_SPEAKER_HDR.match(header))
        name, _, _ = _extract_speaker(header)
        # 헤더 패턴에서 걸리거나, 이름이 안 뽑히면 (파서 본문의 _NON_SPEAKER_NAMES 필터 포함) OK
        ok = blocked_by_hdr or not name or name in _NON_SPEAKER_NAMES
        mark = "✓" if ok else "✗"
        if not ok:
            failures.append(f"{header!r} → name={name!r}")
        print(f"  {mark} {header[:34]!r} → 제외됨={ok} (hdr차단={blocked_by_hdr}, name={name!r})")
    assert not failures, failures


def main() -> None:
    test_regression_cases()
    print()
    test_new_cases()
    print()
    test_garbage_headers()
    total = len(REGRESSION_CASES) + len(NEW_CASES) + len(GARBAGE_HEADERS)
    print(f"\n결과: {total}/{total} 통과")


if __name__ == "__main__":
    main()
