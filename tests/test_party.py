"""
party 모듈(정당 모듈) 단위 테스트 — DB 없이 판정 로직만 검증.

검사 항목:
    1. 여야 경계일 (2025-06-03 국민의힘 여당 / 06-04 더불어민주당 여당)
    2. NFKC 매칭 — 호환용 한자(柳 U+F9C9) 발언자 → 표준 한자 의원
    3. 서로 다른 정당 동명이인 → None / 같은 정당 동명이인 → 정상
    4. 무소속 → "무소속" (여야 없음), 미등록(장관·증인) → None
    5. 위성정당 정규화 (build_members.normalize_party)
    6. aliases 자동 확장 — 유영하 그룹에 호환용+표준 한자 공존 (병합 확인)

한자는 눈으로 코드포인트 구분이 안 되므로 이스케이프로 명시한다 (aliases.py 관례).
실행: python tests/test_party.py
"""

import io
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import party  # noqa: E402
from party import _build_map, party_label, ruling_party  # noqa: E402
from build_members import normalize_party  # noqa: E402

# 파일 상단 관례대로 이스케이프로 명시 — 리터럴로 두면 에디터의 유니코드 정규화가
# 호환용(U+F9C9)을 표준(U+67F3)으로 소리 없이 바꿔 NFKC 테스트가 무력화된다
YU_STD = "\u67F3\u69AE\u590F"     # 柳榮夏 표준 한자 (API 형)
YU_COMPAT = "\uF9C9\u69AE\u590F"  # 柳榮夏 호환용 한자 (DB 저장형)


def label(name, date):
    """의원 자격 기준 판정 — 2026-07-07 role 필수화(자격 불명은 무표기) 이후
    순수 정당·여야 로직 테스트는 자격을 명시해 호출한다."""
    return party_label(name, date, "의원")


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"  # pytest 에서도 실패가 실패로 잡히게


# 테스트용 맵 주입 (DB 미사용)
ROWS = [
    ("엄태영", "嚴泰永", "국민의힘"),
    ("김병주", "金炳周", "더불어민주당"),
    ("유영하", YU_STD, "국민의힘"),
    ("박지원", "朴芝源", "더불어민주당"),   # 같은 정당 동명이인 (실제 22대)
    ("박지원", "朴智元", "더불어민주당"),
    ("김철수", "金哲洙", "국민의힘"),       # 다른 정당 동명이인 (가상 — 방어 코드 검증)
    ("김철수", "金鐵水", "더불어민주당"),
    ("윤종오", None, "무소속"),
]


def _install_base_map():
    # 각 테스트가 직접 호출 — 모듈 import 시 1회 주입하면 pytest 에서 다른 테스트
    # 파일(test_actors)이 _party_map 을 덮어쓴 뒤 실행될 때 오염된 맵을 쓰게 된다
    party._party_map = _build_map(ROWS)


def test_ruling_periods():
    _install_base_map()
    check("여야: 2025-06-03 집권당 국민의힘", ruling_party("2025-06-03") == "국민의힘")
    check("여야: 2025-06-04 집권당 더불어민주당", ruling_party("2025-06-04") == "더불어민주당")

    got = label("엄태영", "2024-09-24")
    check("여야: 정권교체 전 국민의힘=여당", got == "국민의힘(당시 여당)", got)
    got = label("엄태영", "2025-09-24")
    check("여야: 정권교체 후 국민의힘=야당", got == "국민의힘(당시 야당)", got)
    got = label("김병주", "2025-11-27")
    check("여야: 정권교체 후 민주당=여당", got == "더불어민주당(당시 여당)", got)


def test_nfkc_matching():
    _install_base_map()
    got = label(YU_COMPAT, "2025-01-01")
    check("NFKC: 호환용 한자 발언자 매칭", got == "국민의힘(당시 여당)", got)
    got = label("유영하", "2025-01-01")
    check("NFKC: 한글 이름도 매칭", got == "국민의힘(당시 여당)", got)


def test_duplicates():
    _install_base_map()
    got = label("박지원", "2025-01-01")
    check("동명이인: 같은 정당이면 정상 표기", got == "더불어민주당(당시 야당)", got)
    got = label("김철수", "2025-01-01")
    check("동명이인: 다른 정당이면 None", got is None, got)


def test_edge():
    _install_base_map()
    check("무소속: 여야 없이 표기", label("윤종오", "2025-01-01") == "무소속")
    check("미등록(장관·증인): None", label("조태열", "2025-01-01") is None)
    check("None 안전", label(None, "2025-01-01") is None and label("엄태영", None) is None)


def test_normalize_party():
    # 2026-07-03 사용자 결정: 위성정당 표기 유지 (모정당 치환 기각)
    check("위성: 국민의미래 표기 그대로", normalize_party("국민의미래") == ("국민의미래", "국민의미래"))
    check("위성: 더불어민주연합 표기 그대로", normalize_party("더불어민주연합")[0] == "더불어민주연합")
    check("이력: 마지막 항목 채택", normalize_party("한나라당/국민의힘")[0] == "국민의힘")
    check("일반 정당은 그대로", normalize_party("조국혁신당")[0] == "조국혁신당")


def test_satellite_side():
    # 표기는 위성정당, 여야 판정만 모정당 기준
    rows = ROWS + [("강선영", "姜善英", "국민의미래"), ("전종덕", "全鍾德", "더불어민주연합")]
    party._party_map = _build_map(rows)
    got = label("강선영", "2024-09-01")
    check("위성 여야: 국민의미래 → 국힘 정권에서 여당", got == "국민의미래(당시 여당)", got)
    got = label("강선영", "2025-09-01")
    check("위성 여야: 정권교체 후 야당", got == "국민의미래(당시 야당)", got)
    got = label("전종덕", "2025-09-01")
    check("위성 여야: 더불어민주연합 → 민주 정권에서 여당", got == "더불어민주연합(당시 여당)", got)


def test_role_gate():
    # 2026-07-03 사용자 규칙: 정당·여야는 국회의원 자격 발언에만 (claude.txt)
    party._party_map = _build_map(ROWS + [("정동영", "鄭東泳", "더불어민주당")])

    got = party_label("정동영", "2025-09-01", "통일부장관")
    check("자격: 의원 겸 장관이라도 장관 발언은 정부측", got == "정부측", got)
    got = party_label("정동영", "2025-09-01", "의원")
    check("자격: 같은 인물의 의원 발언은 정당 라벨", got == "더불어민주당(당시 여당)", got)
    got = party_label("정동영", "2025-07-14", "통일부장관후보자")
    check("자격: 후보자는 무표기 — 직함 그대로 (아직 행정부 아님)", got is None, got)
    got = party_label("김병환", "2024-07-22", "금융위원장후보자")
    check("자격: 위원장후보자도 무표기", got is None, got)

    check("자격: 미등록 장관도 정부측", party_label("조태열", "2025-01-01", "외교부장관") == "정부측")
    check("자격: 행정부 위원장(금융위원장) 정부측",
          party_label("김병환", "2025-01-01", "금융위원장") == "정부측")
    check("자격: 협력관 정부측",
          party_label("홍진석", "2024-08-28", "통일부북한정보협력관") == "정부측")

    got = party_label("엄태영", "2025-09-24", "위원")
    check("자격: 국회 '위원'은 정당 라벨 유지", got == "국민의힘(당시 야당)", got)
    got = party_label("김병주", "2025-11-27", "위원장")
    check("자격: 국회 '위원장'은 행정부 패턴에 오폭 안 됨", got == "더불어민주당(당시 여당)", got)

    check("자격: 증인은 무표기", party_label("류광진", "2024-10-01", "증인") is None)
    check("자격: 참고인 무표기", party_label("주병기", "2024-10-01", "참고인") is None)
    check("자격: 국회 스태프(수석전문위원) 무표기 — '수석' 오폭 방지",
          party_label("김일수", "2024-10-01", "수석전문위원") is None)
    check("자격: 미상 role 무표기", party_label("엄태영", "2025-09-24", "OO연구소장") is None)
    check("자격: role=None(불명)도 무표기 — 동명 증인 오라벨 방지",
          party_label("엄태영", "2025-09-24", None) is None)


def test_aliases_merge():
    from aliases import expand_aliases
    group = expand_aliases("유영하")
    check("별칭: 유영하 그룹에 호환용+표준 한자 공존",
          YU_COMPAT in group and YU_STD in group, sorted(group))


def main():
    test_ruling_periods()
    test_nfkc_matching()
    test_duplicates()
    test_edge()
    test_normalize_party()
    test_satellite_side()
    test_role_gate()
    test_aliases_merge()
    print("\nALL PASS")


if __name__ == "__main__":
    main()
