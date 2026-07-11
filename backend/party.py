"""
발언자 정당·여야 판정 (정당 모듈 — 3단계 행위자 분석 1차).

배경 (2026-07-03 실측, docs/party_module_spec.md):
  "여야별로 정리" 질문에서 LLM 이 근거에 없는 정당 소속을 추측 생성
  (같은 위원이 여야 양쪽에 등장하는 자기모순까지). 프롬프트 방어는 불완전 —
  근본 해결은 코드가 근거 블록에 정당을 직접 표기해 추측할 필요를 없애는 것.

판정:
  party_label("엄태영", "2025-09-24", "위원") → "국민의힘(당시 야당)"
  - 자격(role) 필수: 국회의원 자격만 정당 라벨, 자격 불명(None)은 무표기 (2026-07-07)
  - 여야는 시점 의존: 코퍼스 기간(2024-05~2026-06) 중 정권교체(2025-06-04 취임)
  - 무소속은 여야 없이 "무소속"
  - 서로 다른 정당의 동명이인 → None (틀린 라벨보다 무표기 — 신뢰 원칙.
    현재 22대 유일 동명이인 박지원 2명은 둘 다 더불어민주당이라 무해)
  - 미등록 이름(장관·증인·참고인) → None

매칭:
  이름과 한자명 모두 NFKC 정규화 키로 등록 — DB 발언자의 호환용 한자
  (柳 U+F9C9)도 표준 한자(U+67F3) 의원 레코드와 매칭된다.

캐시: 첫 사용 시 members 테이블 1회 로드 (질의마다 DB 조회 없음).
한계: 최종 당적 스냅샷 — 임기 중 탈당 등 변동 미추적 (spec "알려진 한계").
"""

import re
import unicodedata
from datetime import date

from db import get_conn

# 여야 판정표: (시작일, 종료일, 집권당) — 2025-06-03 조기대선, 06-04 취임
RULING_PERIODS = [
    (date(2024, 5, 30), date(2025, 6, 3), "국민의힘"),
    (date(2025, 6, 4), date(9999, 12, 31), "더불어민주당"),
]

# 위성정당 → 모정당 (여야 판정 전용 — 표기는 위성정당 그대로, 2026-07-03 사용자 결정.
# 국민의미래 의원을 국민의힘 정권에서 "야당"으로 계산하는 오류 방지)
SATELLITE_PARENT = {
    "국민의미래": "국민의힘",
    "더불어민주연합": "더불어민주당",
}

# 발언 자격(role) 게이트 (2026-07-03 사용자 규칙 — claude.txt):
# 정당·여야 라벨은 "국회의원 자격"의 발언에만 단다. 정동영처럼 의원 겸 장관인 인물이
# 장관 자격으로 발언한 것에 여당 라벨이 붙던 버그의 수정 — 이름이 아니라 자격으로 판정.
ASSEMBLY_ROLES = {"위원", "위원장", "소위원장", "위원장대리", "간사", "의원", "조정위원장"}

# 국회 소속 스태프 — 행정부 패턴("수석"·"실장" 등) 오폭 방지를 위해 먼저 걸러 무표기
STAFF_ROLES = {"전문위원", "수석전문위원", "입법조사관", "행정실장", "사무처장", "사무총장"}

# 증인·참고인·진술인 — 정당 무표기, 출석 지위는 role 로 이미 표기됨 (분류는 프롬프트가 지시)
WITNESS_ROLES = {"증인", "참고인", "진술인"}

# 행정부 자격 — 여당이 아니라 "정부측" (통일부장관·경찰청장·금융위원장·협력관 등)
# 후보자(장관후보자·위원장후보자 등)는 아직 행정부 소속이 아님 — 정부측 라벨 없이
# 직함 그대로 둔다 (2026-07-03 사용자 규칙 2차. role 이 speaker 줄에 이미 표기됨)
_NOMINEE_ROLE = re.compile(r"후보자?$")

# "…위원장"(금융위원장·공정거래위원장 등 행정기관장)은 국회 위원장과 겹쳐 보이지만,
# 국회 쪽(위원장·소위원장·조정위원장 등)은 ASSEMBLY_ROLES exact 매치가 먼저 걸러낸다
_EXECUTIVE_ROLE = re.compile(
    r"장관|차관|청장|처장|차장|총장|국무위원|대통령|비서관|대변인|협력관|정부위원|위원장$"
)

_AMBIGUOUS = "__ambiguous__"  # 서로 다른 정당의 동명이인 표식

_party_map: dict[str, str] | None = None


def _norm(name: str) -> str:
    """매칭 키 정규화 — 호환용 한자(U+F9C9 등)를 표준형으로."""
    return unicodedata.normalize("NFKC", (name or "").strip())


def _build_map(rows: list[tuple[str, str | None, str]]) -> dict[str, str]:
    """(name, hanja_name, party) 목록 → 정규화 키 → 정당. 순수 함수 (테스트용 분리).

    같은 키가 서로 다른 정당을 가리키면 _AMBIGUOUS 로 무효화한다.
    """
    m: dict[str, str] = {}
    for name, hanja, party in rows:
        for key in filter(None, (_norm(name), _norm(hanja) if hanja else None)):
            if key in m and m[key] != party:
                m[key] = _AMBIGUOUS
            else:
                m.setdefault(key, party)
    return m


def _load_map() -> dict[str, str]:
    global _party_map
    if _party_map is None:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT name, hanja_name, party FROM members")
            _party_map = _build_map(cur.fetchall())
    return _party_map


def ruling_party(meeting_date: str) -> str | None:
    """회의 날짜(YYYY-MM-DD)의 집권당."""
    d = date.fromisoformat(str(meeting_date)[:10])
    for start, end, party in RULING_PERIODS:
        if start <= d <= end:
            return party
    return None


def member_party(speaker: str | None) -> str | None:
    """이름 → 정당 (자격 게이트 없이 members 조회만 — 행위자 프로필용).

    동명이인이 서로 다른 정당이면 None (party_label 과 동일 원칙).
    """
    if not speaker:
        return None
    party = _load_map().get(_norm(speaker))
    return None if party in (None, _AMBIGUOUS) else party


def speaker_group(role: str | None) -> str:
    """발언 자격(role) → 그룹. party_label 게이트와 동일 판정의 재사용 함수 (POL-6).

    "assembly"(국회의원) | "government"(행정부) | "witness"(증인·참고인·진술인) |
    "staff"(국회 스태프) | "unknown"(미상·후보자·자격불명).
    판정 순서는 기존 party_label 과 동일 — 후보자 검사가 행정부 패턴보다 먼저
    ("위원장후보자" 가 위원장$ 에 오폭하지 않도록).
    """
    if role in ASSEMBLY_ROLES:
        return "assembly"
    if role is None:
        return "unknown"
    if role in STAFF_ROLES:
        return "staff"
    if role in WITNESS_ROLES:
        return "witness"
    if _NOMINEE_ROLE.search(role):
        return "unknown"
    if _EXECUTIVE_ROLE.search(role):
        return "government"
    return "unknown"


def party_label(
    speaker: str | None, meeting_date: str | None, role: str | None = None
) -> str | None:
    """발언자의 분류 라벨. 자격(role) 우선 판정 — 판정 불가면 None (무표기가 원칙).

    - 국회의원 자격(ASSEMBLY_ROLES) → "정당(당시 여야)" / "무소속"
    - 행정부 자격 → "정부측" (members 에 있는 겸직 의원이라도 — 정동영 통일부장관 사례)
    - 국회 스태프·증인·참고인·진술인·미상·자격 불명(role=None) → None
    """
    if not speaker or not meeting_date:
        return None

    # 자격 불명(None)도 무표기 — role=NULL 이 게이트를 우회해 의원과 동명인
    # 증인에게 정당이 붙을 수 있던 구멍 (2026-07-07 수정). 실측: role=NULL 은
    # 전체 0.12%(494청크), 의원 이름 일치 114청크 — 라벨 손실은 미미하고
    # "자격이 확인된 발언에만 라벨" 원칙이 구조적으로 보장된다.
    group = speaker_group(role)
    if group == "government":
        return "정부측"
    if group != "assembly":
        return None

    party = _load_map().get(_norm(speaker))
    if party is None or party == _AMBIGUOUS:
        return None
    if party == "무소속":
        return "무소속"
    ruling = ruling_party(meeting_date)
    if ruling is None:
        return party
    side = "여당" if SATELLITE_PARENT.get(party, party) == ruling else "야당"
    return f"{party}(당시 {side})"
