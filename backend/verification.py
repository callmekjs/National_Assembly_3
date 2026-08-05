"""
답변-근거 자동 검증 층 1단계 (규칙 기반 — LLM 호출 없음, 비용 0).

spec: docs/superpowers/specs/2026-07-15-answer-verification-design.md
표적: 2026-07-15 확정 진짜 실패 8건의 공통 뿌리 — "검색이 표적을 못 채우면
생성이 메꾸는" 행동. 문서 등급(grounding)은 건드리지 않고 flag 만 만든다 —
main.py 가 flags 비어있지 않으면 FULL→PARTIAL 강등 (invalid_citations 와 같은 자리).

원칙 (spec §4-2 "정직한 처리"):
  - 오탐(잘못된 강등)이 미탐보다 해롭다 — 명시 주어가 있을 때만 강한 규칙 적용,
    문단 주어 승계 기반 flag 는 detail 에 inherited 표시
  - 규칙은 개별 예외 격리 — 검증층 버그가 답변 생성 실패로 번지지 않는다
    (issue_context 의 예외 처리 패턴)
"""

import logging
import re
from datetime import date

from party import RULING_PERIODS, speaker_group
from query_parser import classify_question

logger = logging.getLogger(__name__)

# ── 정당 라벨 파싱 (spec §2-1) ────────────────────────────────────────────────

_LABEL_SUFFIX = re.compile(r"\s*\(당시\s*(?:여당|야당)\)\s*$")


def core_party(party_label: str | None) -> str | None:
    """'더불어민주당(당시 여당)' → '더불어민주당'. None·'정부측'·'무소속' → None.

    무소속도 None — 어느 정당 진영도 대표하지 않으므로 비교 커버리지 집계에서
    제외한다 (무소속 1명 + 민주당 1명을 '정당 2개'로 세면 거짓 covered).
    """
    if not party_label:
        return None
    name = _LABEL_SUFFIX.sub("", party_label).strip()
    if name in ("", "정부측", "무소속"):
        return None
    return name


# 진영(side) 접미 추출 — core_party() 와 자매 함수. '정부측'은 그 자체가 진영이다
# (2026-07-26 F3 — 구 스펙 §2-1 "정부측 집계 제외"는 "정부 입장 vs 야당 비판" 같은
# 국회 도메인 최빈출 비교형을 구조적으로 오탐시킨 결함으로 최종 리뷰가 확정).
_SIDE_SUFFIX = re.compile(r"\(당시\s*(여당|야당)\)\s*$")


def _party_side(party_label: str | None) -> str | None:
    """'더불어민주당(당시 여당)' → '여당' / '정부측' → '정부측' / 그 외(무소속·
    None·접미 없음) → None (진영 미상 — 집계 제외)."""
    if not party_label:
        return None
    if party_label == "정부측":
        return "정부측"
    m = _SIDE_SUFFIX.search(party_label)
    return m.group(1) if m else None


def comparison_coverage(sources: list[dict]) -> dict:
    """비교 질문의 진영 커버리지 — 정당명 개수가 아니라 진영(side) 축으로 판정.

    2026-07-26 F3 재설계(spec §2-1 개정절 참고). 두 조건을 모두 요구한다:
    (1) side 종류 2개 이상 (여당/야당/정부측 중 서로 다른 것이 실제로 등장) —
        위성정당+모정당(둘 다 '당시 여당')이나 야당 2당처럼 정당명은 여럿이어도
        진영이 하나뿐이면 진짜 비교가 아니다 (같은 편끼리는 '비교'가 아니다).
    (2) 서로 다른 정체성(정당명, 정부측은 그 자체로 1개 정체성) 2개 이상 —
        '당시 여당/야당'라벨은 인용 발언 시점 기준이라, 같은 정당이 서로 다른
        회의 날짜로 인용되면 라벨이 여당/야당 양쪽으로 갈릴 수 있다(eval_019:
        더불어민주당 3건이 시점차로 여당 2건·야당 1건). side 조건만 보면 이
        경우도 '2개 진영'으로 잘못 셀 수 있어, 정체성 조건을 함께 요구해 걸러낸다.
    SATELLITE_PARENT 별도 병합은 불필요 — party.py party_label() 이 라벨 생성
    시점에 이미 위성정당을 모정당 기준 여야로 인코딩해 접미에 반영한다.
    """
    parties = sorted({p for s in sources if (p := core_party(s.get("party")))})
    gov_present = any(s.get("party") == "정부측" for s in sources)
    sides = sorted({sd for s in sources if (sd := _party_side(s.get("party")))})
    identity_count = len(parties) + (1 if gov_present else 0)
    covered = len(sides) >= 2 and identity_count >= 2
    return {"core_parties": parties, "sides": sides, "covered": covered}


# ── 화자·진영 이중 사용 (spec §2-2, eval_068) ────────────────────────────────

# 2026-07-26 F4: 진영 축(여당↔야당)과 찬반 축(찬성↔반대)은 서로 다른 두 축이다 —
# "야당 의원이 안건에 찬성"은 정상 협치 서술이지 모순이 아니다. 이전 구현은
# (여당|찬성) vs (야당|반대) 로 교차 등식화해 "여당=찬성, 야당=반대"를 코드에
# 내장했었는데, 이는 정치적으로 편향된 독해다(spec §2-2 개정절 참고). 각 축을
# 독립 정규식으로 분리하고, 같은 화자가 **같은 축**의 양극에 배치될 때만 flag.
_SIDE_FRAME = re.compile(r"(여당|야당)(\s*측)?(\s*(에서는|에서|소속|의원들?|입장|의|인))?\s*$")
_STANCE_FRAME = re.compile(r"(찬성|반대)(\s*측)?(\s*(에서는|에서|소속|의원들?|입장|의|인))?\s*$")


def _speaker_keys(speaker: str | None) -> set[str]:
    """'柳榮夏(유영하)' → {'柳榮夏','유영하'} / '김현' → {'김현'} — 병기 표기 분해."""
    if not speaker:
        return set()
    keys = {speaker}
    m = re.match(r"^(.+?)\((.+?)\)$", speaker)
    if m:
        keys.update({m.group(1), m.group(2)})
    return keys


def speaker_both_sides(answer: str, cited_sources: list[dict]) -> bool:
    """같은 화자가 답변 안에서 같은 축의 양극(여당↔야당 또는 찬성↔반대)에 배치됐는가.

    화자명 직전 수식(여당 측/찬성 측/야당 소속/반대 측 등)만 프레이밍으로 인정 — 주변 창 방식은
    상대 진영 반응 서술('야당 반대에도 불구하고')을 오탐해 폐기 (2026-07-25 리뷰).
    2026-07-26 F4: 진영 축(여당/야당)과 찬반 축(찬성/반대)은 **독립된 두 축**이다 —
    "야당 의원의 안건 찬성"(야당+찬성 교차 조합)은 여야 협치의 정상 서술이지 진영
    모순이 아니다. 같은 축의 양극(여당↔야당 또는 찬성↔반대)에만 flag, 교차 조합은
    무flag. 등장 중 한 번이라도 프레이밍이 없으면(None) 그 화자는 판정 보류 —
    직전 수식 방식은 괄호 정당명 등 개입 텍스트에 약해 정밀도 우선(minor trade-off).
    순수 정규식, LLM 불필요.
    """
    for s in cited_sources:
        for name in _speaker_keys(s.get("speaker")):
            hits = [m.start() for m in re.finditer(re.escape(name), answer)]
            if len(hits) < 2:
                continue
            frames = {}  # {pos: ('진영'|'찬반', 값) | None}
            for pos in hits:
                # 화자명 직전 12자에서 프레이밍 찾기
                before = answer[max(0, pos - 12): pos]
                m_side = _SIDE_FRAME.search(before)
                m_stance = _STANCE_FRAME.search(before)
                if m_side:
                    frames[pos] = ("진영", m_side.group(1))
                elif m_stance:
                    frames[pos] = ("찬반", m_stance.group(1))
                else:
                    frames[pos] = None
            if None in frames.values():
                continue
            # 같은 축에서 서로 다른 값(양극)으로 명시 프레이밍되면 True — 축이 다르면
            # (예: 진영='야당', 찬반='찬성') 교차 조합이라 모순이 아니다.
            by_axis: dict[str, set[str]] = {}
            for axis, value in frames.values():
                by_axis.setdefault(axis, set()).add(value)
            if any(len(values) > 1 for values in by_axis.values()):
                return True
    return False


# ── 거짓 Q-A 짝짓기 (spec §3, eval_029) ──────────────────────────────────────

_QA_VERB = re.compile(r"질(?:문|의)")
# 어절 경계(?<![가-힣]) + 위원(?!장) — "방송통신위원장"·"금융위원장" 같은 답변자
# 복합 직함 내부의 '위원'이 질문자 신호로 오매칭되지 않도록 (2026-07-26 F6,
# eval_070: 단일 대상 질문이 Q-A 짝으로 오분류됐다). _NAMED_SPEAKER 의 위원(?!회)
# 경계 가드와 같은 계열의 구조적 차단.
_QA_ASKER = re.compile(r"(?<![가-힣])[가-힣]{2,4}\s*(?:위원(?!장)|의원)")
_QA_ANSWERER = re.compile(r"장관|차관|총리|처장|청장|위원장|후보자|대통령")
_VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def qa_pair_question(question: str) -> bool:
    """질문이 'A 위원이 …질의… B 장관 …답변' Q-A 짝 패턴인가.

    질의·답변 두 동사 + 질문자(의원)·답변자(직함) 신호가 모두 있어야 True —
    '장관의 답변 내용은?' 같은 단일 대상 질문의 오탐 방지.
    """
    return (bool(_QA_VERB.search(question)) and "답변" in question
            and bool(_QA_ASKER.search(question)) and bool(_QA_ANSWERER.search(question)))


def _mentions_date(answer: str, iso_date: str) -> bool:
    """답변이 해당 날짜를 언급하는가 — 'YYYY년 M월' / ISO / 'M월 D일' 표기 인정."""
    d = str(iso_date)[:10]
    if not _VALID_DATE.match(d):
        return False
    try:
        year, month, day = int(d[:4]), int(d[5:7]), int(d[8:10])
    except (ValueError, IndexError):
        return False
    return (f"{year}년 {month}월" in answer or d in answer
            or f"{month}월 {day}일" in answer)


def qa_pairing_dates(answer: str, cited_sources: list[dict], question: str) -> bool:
    """Q-A 짝 질문에서 인용들이 서로 다른 회의(committee+date)를 가리키는데
    답변이 날짜 차이를 공시하지 않으면 True (flag).

    eval_029: 질문 인용 = 외통위 2024-11-11, 답변 인용 = 2025-02 업무보고 —
    서로 다른 회의를 같은 회의의 질의-답변으로 단정. 답변이 서로 다른 날짜를
    2개 이상 명시하면 정직한 공시로 보고 통과. 날짜 결측 source는 판정 제외
    (2026-07-25 리뷰: 예외 격리 — 검증층 크래시 방지).
    """
    if not qa_pair_question(question) or len(cited_sources) < 2:
        return False
    # 유효한 날짜의 source만 집계
    valid_sources = [s for s in cited_sources
                     if _VALID_DATE.match(str(s.get("date"))[:10])]
    meetings = {(s.get("committee"), str(s.get("date"))) for s in valid_sources}
    if len(meetings) < 2:
        return False
    dates = {str(s.get("date")) for s in valid_sources}
    disclosed = sum(1 for d in dates if _mentions_date(answer, d))
    return disclosed < 2


# ── 문장 단위 규칙 공통 (spec §4-2) ──────────────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CITE_RE = re.compile(r"\[(\d+)\]")
# 거절·부재공시 문장은 검사 제외 — "X의 발언은 확인할 수 없습니다"는 정직한 처리
_REFUSAL_SENT = re.compile(r"확인(할 수 없|되지 않|이 되지 않)|찾을 수 없")


def _paragraph_sentences(answer: str) -> list[tuple[str, int]]:
    """(문장, 문단 인덱스) 목록 — 문단은 개행 기준 (주어 승계의 경계)."""
    out = []
    for pi, para in enumerate(p for p in answer.split("\n") if p.strip()):
        for sent in _SENT_SPLIT.split(para.strip()):
            if sent.strip():
                out.append((sent.strip(), pi))
    return out


def _cited_in(sent: str, by_n: dict[int, dict]) -> list[dict]:
    return [by_n[n] for n in (int(m) for m in _CITE_RE.findall(sent)) if n in by_n]


# ── 화자/역할 귀속 (spec §4-2, eval_013·019) ─────────────────────────────────

# 정부 기관 주어 — 문장이 이 기관의 방안·입장 서술 흐름인지 판단.
# 어절 경계(?<![가-힣]) 는 전체 대안군에 공통 적용(2026-07-26 F1/F2 확장, replay
# 실측) — 원래 '정부'(단독어) 하나에만 붙어 있던 가드였는데, 국회 상임위원회
# 명칭이 정부기관명을 부분 문자열로 우연히 포함하는 충돌이 실측됐다: "과학기술정보
# 방송통신위원회"(국회 상임위) 안에 "방송통신위원회"(정부 방통위)가 어절 경계 없이
# 그대로 들어있어 eval_069 replay 에서 오매칭. '의정부' 가 '정부'를 오폭하던 문제와
# 동일 계열이라 전체 대안에 동일 가드를 일괄 적용한다.
_GOV_SUBJECT = re.compile(
    r"(?<![가-힣])(?:외교부|통일부|국방부|법무부|행정안전부|기획재정부|보건복지부|국토교통부"
    r"|산업통상자원부|과학기술정보통신부|환경부|교육부|고용노동부|문화체육관광부"
    r"|여성가족부|해양수산부|중소벤처기업부|농림축산식품부|금융위원회|방송통신위원회"
    r"|공정거래위원회|금융감독원|경찰청|검찰청|국세청|관세청|통계청|기상청|소방청"
    r"|질병관리청|대통령실|정부)"
)

# '화자명(정당명)' 표기 — party_label_consistency 의 정탐 대조용으로 정의되었지만
# _sentence_names() 가 화자 인식 보강용으로도 재사용한다 (2026-07-25). 사용처보다
# 위에 정의해 전방 참조를 피한다 (2026-07-25 재리뷰 Minor 4).
_PARTY_NAMES = (
    "더불어민주당|국민의힘|더불어민주연합|국민의미래|조국혁신당|개혁신당"
    "|진보당|기본소득당|사회민주당|새로운미래|무소속"
)
_PARTY_NAME_ONLY = re.compile(rf"^(?:{_PARTY_NAMES})$")
# (?<![가-힣]) 어절 경계 강제 — "…국민의힘(정당)" 같은 붙어쓰기 앞말 오폭 방지
# (최종 리뷰 M12 동승, 2026-07-26).
_NAME_PARTY = re.compile(rf"(?<![가-힣])([가-힣]{{2,4}})\s*(?:위원장|위원|의원)?\s*\(\s*({_PARTY_NAMES})\s*\)")

# 답변 속 "이름+직함" — 화자 귀속 검출용. 일반어 프리픽스는 이름이 아니다.
# 조직명 구조적 차단: 위원(?!회) 는 "위원회"의 "위원"을 직함이 아닌 것으로 처리
# 어절 경계 강제: (?<![가-힣]) 는 "정무위원회"의 "무위원" 등을 차단 (한글 중간에서 시작 불가)
# 이름-직함 공백 필수(\s+, 2026-07-26 최종 리뷰 F1): LLM 답변의 실명은 "김우영 위원"
# 처럼 띄어 쓴다 — 공백을 강제하면 "외교부장관"(부처명+직함 융합, 공백 없음) 같은
# 문형에서 접두 "외교부"가 이름으로 캡처되는 경로가 구조적으로 막힌다.
# 선택적 기관 접두(institution prefix) 허용: "조현 외교부장관은"처럼 이름과 직함
# 사이에 부처명이 낀 문형은 여전히 '조현'이 잡혀야 한다(환각 화자 검사 재현율 보존) —
# 접두가 부/처/청/실/원/위원회로 끝나면 직함 앞에서 소비하고 이름 그룹은 건드리지 않는다.
_NAMED_SPEAKER = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})\s+(?:[가-힣]{2,8}(?:부|처|청|실|원|위원회)\s*)?"
    r"(?:위원장|위원(?!회)|의원|장관|차관|청장|처장|총리|후보자|대변인|소위원장|부위원장)"
)
_NOT_NAMES = frozenset([
    "해당", "관련", "소속", "여당", "야당", "양당", "양측", "모든", "다른", "일부",
    "동일", "같은", "각각", "국회", "정부", "당시", "여러", "다수", "소수", "상임",
    # 대명사(2026-07-26 F1 확장, replay 실측 eval_004·046): "그는 외교부 장관에게"
    # 처럼 대명사+공백+기관+직함 구조가 _NAMED_SPEAKER 의 [이름]+공백+[기관접두]+
    # [직함] 패턴에 우연히 들어맞아 대명사 자체가 유령 이름으로 캡처됐다.
    "그는", "그가", "그녀는", "그녀가",
])
# 활용형 접두 — 이 접두로 시작하는 캡처는 이름이 아니다(2026-07-26 F1 확장,
# replay 실측 eval_066): "관련하여"(4자)는 _NOT_NAMES 의 "관련"(2자)과 정확히
# 일치하지 않아 exact-match 필터를 통과했다 — "관련" 활용형은 실명이 될 수 없다.
_NOT_NAME_PREFIXES = ("관련",)
# 직함 단독어 — _NAME_PARTY 는 "실명 직함(정당)" 구조를 기대하지만 직함이 "소위원장"
# 처럼 _NAME_PARTY 의 optional 직함군(위원장|위원|의원)에 없는 복합어면, 앞의 진짜
# 실명은 공백에 막혀 버려지고 직함 단어 자체가 "이름"으로 잘못 캡처된다
# (2026-07-25 회귀 스모크에서 실측: "복기왕 소위원장(더불어민주당)" → 이름='소위원장').
# 직함 접미로 끝나는 캡처는 화자명 후보에서 제외 — 실명이 이 접미로 끝나는 경우는 없다.
# _NAMED_SPEAKER 직함 10종에서 파생했던 1차 목록은 간사·(원내/당)대표 계열이 뚫려
# 있었다 (2026-07-25 재리뷰 실증: "박찬대 원내대표(정당)" → '원내대표', "김민석
# 간사(정당)" → '간사' 오탐) — '대표' 접미로 원내대표/당대표를 포괄하고, 간사·
# 사장·총장·대사를 추가했다. 소위원장·부위원장·의장·고문은 최종 리뷰 M12 동승 보강
# (2026-07-26) — _NAME_PARTY 의 optional 직함군에 없어 같은 함정에 뚫려 있었다.
_ROLE_SUFFIXES = (
    "위원장", "위원", "의원", "장관", "차관", "청장", "처장", "총리", "후보자", "대변인",
    "대표", "간사", "사장", "총장", "대사", "소위원장", "부위원장", "의장", "고문",
)

# 대명사 주어 승계 (2026-07-25 오탐 수정, eval_011·029): "그는/그가 …" 로 시작하는
# 문장은 직전 명시 화자를 승계한 것으로 본다 — 내포절 주어(예: "그는 통일부가 …")를
# 문장 주어로 오인해 gov 분기가 오발동하는 것을 막는다. 공백·따옴표 프리픽스 허용.
_PRONOUN_SUBJECT = re.compile(r"^[\s'\"“”‘’]*(?:그는|그녀는|그가|그녀가)")

# 비기관 일반 주어 두절 (2026-07-26 최종 리뷰 F2): 문장 맨 앞이 "…들은/…들이/
# 이들은/…측은/…측에서는/…에서는/…에서도" 처럼 명백한 비기관 집합 주어면, 문장 뒤쪽
# 목적어 위치의 기관 언급("외교부의 소극적인 대응을…")만으로 gov 분기가 발동하지
# 않는다. 매치된 주어구 자체에 _GOV_SUBJECT 가 없을 때만 스킵 — "외교부는"은 이
# 접미 목록에 없어(은/는 단독은 제외) 애초 불일치하므로 정부 기관 주어 판정이 그대로
# 유지된다(핀 보존). 앞말 상한 20자(2026-07-26 replay 재측정 확장, eval_048·069) —
# 국회 상임위원회 명칭("산업통상자원중소벤처기업위원회"·"과학기술정보방송통신위원회"
# 등)은 8자를 훌쩍 넘는 긴 복합어라 기존 8자 상한에 걸리지 않아, 문장 뒤쪽에 열거된
# 부처명이 목적어인데도 gov 분기가 오발동했다.
_GENERIC_SUBJECT_HEAD = re.compile(
    r"^[\s'\"“”‘’]*([가-힣]{1,20}(?:\s[가-힣]{1,8})?(?:들은|들이|이들은|측은|측에서는|에서는|에서도))"
)


def _gov_subject_skip(sent: str) -> bool:
    """문장 맨 앞의 비기관 일반 주어가 gov 분기를 스킵해야 하는가 (F2)."""
    m = _GENERIC_SUBJECT_HEAD.match(sent)
    return bool(m) and not _GOV_SUBJECT.search(m.group(1))


# 후보자 예외 (2026-07-26 F1 확장, replay 재측정 eval_049 실증). party.py 의
# _NOMINEE_ROLE 규칙과 동일 원칙 — "OO장관 후보자"는 아직 행정부 소속이 아니므로
# 정부측 서술이 아니다(party_label() 도 후보자에게 "정부측" 라벨을 붙이지 않는다).
# "외교부장관 후보자는…"처럼 부처/직함 뒤에 (공백 유무 무관) '후보자'가 바로 붙으면
# 이 문장의 기관 언급은 개인(예비 임명자) 자격 서술이지 기관 자체의 서술이 아니다.
_GOV_NOMINEE = re.compile(r"(?:" + _GOV_SUBJECT.pattern + r")[가-힣]{0,6}\s*후보자")


# 문장이 기관명으로 '시작'하는가 (F2 확장, 2026-07-26 — replay 재측정 실증).
# _PRONOUN_SUBJECT 승계는 "그는/그가" 명시 대명사만 잡아, 한국어에 훨씬 흔한
# 무표지 생략 주어("또한, …기관이 ~해줄 것을 요청했습니다" 류 안긴 절)는 못
# 잡았다 — replay 재측정에서 잔존 flag 18/58 의 지배적 원인으로 실증(eval_002·
# 003·008). 문장이 기관명으로 시작하지 않으면(=기관 언급이 문두 주어가 아니라
# 안긴 절·관형어 등 문장 중간에 있으면) 그 문장의 실제 주어는 문두에 없다는
# 뜻이므로, 문단에 이미 인물 화자가 확정돼 있고(para_has_named_speaker) 아직
# 기관이 이 문단의 진행 주어로 확정된 적 없으면(not inherited_gov) 인물 주어
# 승계로 본다.
_GOV_SUBJECT_HEAD = re.compile(r"^[\s'\"“”‘’]*(?:" + _GOV_SUBJECT.pattern + r")")


def _is_ghost_candidate(name: str) -> bool:
    """_NAMED_SPEAKER 캡처가 실명이 아니라 기관명·정당명 자체인지 (2026-07-26 F1).

    "외교부 장관은"(띄어쓰기형)처럼 이름 자리에 부처명이 그대로 들어오거나,
    "국민의힘 의원들은"처럼 정당명이 들어오면 이름 그룹에 정부기관/정당명이
    통째로 캡처된다 — _GOV_SUBJECT·_PARTY_NAMES 자체와 대조해 후보에서 제외.
    """
    return (bool(_GOV_SUBJECT.search(name)) or bool(_PARTY_NAME_ONLY.match(name))
            or name.startswith(_NOT_NAME_PREFIXES))


def _sentence_names(sent: str) -> list[str]:
    """문장 속 명시 화자명 — '이름+직함' 또는 '이름(정당명)' 표기 모두 인식.

    2026-07-25 오탐 수정(eval_019): "이재정(더불어민주당)은 …" 처럼 직함 없이
    정당명만 병기된 표기는 기존 _NAMED_SPEAKER 로 잡히지 않아 gov 분기가
    오발동했다 — party_label_consistency 의 _NAME_PARTY 를 재사용해 보강.
    단, _NAME_PARTY 캡처가 직함 단독어(_ROLE_SUFFIXES)로 끝나면 제외한다.
    2026-07-26 최종 리뷰 F1: 캡처 후보가 기관명·정당명 자체(_is_ghost_candidate)
    이면 실명이 아니므로 양쪽 경로 모두에서 제외한다.
    """
    names = [
        m for m in _NAMED_SPEAKER.findall(sent)
        if m not in _NOT_NAMES and not _is_ghost_candidate(m)
    ]
    names += [
        m.group(1) for m in _NAME_PARTY.finditer(sent)
        if m.group(1) not in _NOT_NAMES and not m.group(1).endswith(_ROLE_SUFFIXES)
        and not _is_ghost_candidate(m.group(1))
    ]
    return names


def speaker_role_consistency(answer: str, cited_sources: list[dict]) -> list[str]:
    """문장 단위 화자·기관 귀속 검사 → detail 문자열 목록.

    (a) 미등장 화자: 문장의 '이름+직함'이 인용 근거 화자 어디에도 없음 (환각 귀속)
    (b) 기관 귀속: 문장 주어가 정부 기관(명시 또는 문단 승계)인데 그 문장의 인용
        화자가 정부측이 아님 — 승계 기반은 'inherited' 표시 (spec 2차 휴리스틱)
    거절 문장(확인 불가 공시)은 검사하지 않는다.
    대명사 주어("그는/그가 …")는 같은 문단 내 직전 명시 화자를 승계 — gov 분기를
    타지 않고 inherited_gov 도 갱신하지 않는다 (2026-07-25 오탐 수정).
    비기관 일반 주어(…들은/이들은/…에서는, 2026-07-26 F2)로 시작하는 문장은 뒤쪽
    목적어 위치의 기관 언급만으로 gov 분기가 발동하지 않는다. inherited_gov 는
    화자명이 있는 문장(names 비어있지 않음)에서는 갱신하지 않는다 — 화자명 문장의
    목적어 기관 언급이 다음 문장으로 오염 승계되는 것을 막는다(F2 결정 ②).
    무표지 생략 주어 승계(2026-07-26 F2 확장, replay 실측): 대명사도 없고 기관명으로
    문장이 시작하지도 않는데(=기관 언급이 안긴 절·관형어) 문단에 이미 인물 화자가
    확정돼 있고 기관이 아직 이 문단의 진행 주어로 확정된 적 없으면, 인물 주어 승계로
    본다(gov 분기 스킵, inherited_gov 불변) — "또한, X가 Y했다는 점을 언급하며,
    Z부가 ~해줄 것을 요청했습니다" 류.
    """
    by_n = {s["n"]: s for s in cited_sources}
    all_speaker_keys: set[str] = set()
    for s in cited_sources:
        all_speaker_keys |= _speaker_keys(s.get("speaker"))

    flags: list[str] = []
    inherited_gov = False
    para_has_named_speaker = False
    prev_para = None
    for sent, pi in _paragraph_sentences(answer):
        if pi != prev_para:
            inherited_gov = False
            para_has_named_speaker = False
            prev_para = pi
        if _REFUSAL_SENT.search(sent):
            continue
        names = _sentence_names(sent)

        if not names and para_has_named_speaker and _PRONOUN_SUBJECT.match(sent):
            # 대명사 주어 승계 — 직전 명시 화자에 귀속, gov 분기 스킵 (inherited_gov 불변)
            continue

        if (not names and para_has_named_speaker and not inherited_gov
                and not _GOV_SUBJECT_HEAD.match(sent)):
            # 무표지 생략 주어 승계(F2 확장) — 대명사 없이도 인물 주어를 승계.
            # 기관이 이미 이 문단의 진행 주어면(inherited_gov) 이 스킵을 타지
            # 않는다 — eval_013 처럼 기관 주어가 계속 이어지는 문장은 여전히 검사.
            continue

        cited = _cited_in(sent, by_n)

        if names:
            # (a) 명시 화자 — 인용이 있는 문장에서 이름이 근거 화자 목록에 전무하면 환각
            if cited and not any(n in all_speaker_keys for n in names):
                flags.append(f"미등장 화자 '{names[0]}' 에 발언 귀속: {sent[:40]}")
            inherited_gov = False  # 화자명이 나오면 기관 승계 끊김 (명시 주어 전환)
            para_has_named_speaker = True
            continue

        # 명시 화자 없는 문장만 gov 분기 판정 — inherited_gov 갱신도 이 갈래에서만.
        gov_explicit = (bool(_GOV_SUBJECT.search(sent)) and not _gov_subject_skip(sent)
                         and not _GOV_NOMINEE.search(sent))
        if gov_explicit or inherited_gov:
            # (b) 정부 기관 주어 — 인용 화자가 정부측이 아니면 귀속 오류
            for s in cited:
                if s.get("party") != "정부측" and speaker_group(s.get("role")) != "government":
                    suffix = "" if gov_explicit else " (inherited)"
                    flags.append(f"정부 기관 서술에 비정부 발언 [{s['n']}] 인용{suffix}: {sent[:40]}")
        if gov_explicit:
            inherited_gov = True
    return flags


# ── 정당 라벨 일치 (spec §0-1·§4-2, eval_057) ────────────────────────────────
# _PARTY_NAMES · _NAME_PARTY 정의는 위 "화자/역할 귀속" 섹션으로 이동
# (_sentence_names 가 전방 참조 없이 재사용하도록, 2026-07-25 재리뷰 Minor 4).


def party_label_consistency(answer: str, cited_sources: list[dict]) -> list[str]:
    """답변 속 '화자명(정당명)' 을 그 화자의 주입 라벨과 문자열 대조 (결정적).

    위성정당은 표기 그대로가 원칙 (2026-07-03 사용자 결정) — 답변이
    '더불어민주연합'을 '더불어민주당'으로 바꿔 쓰면 다른 정당 오표기다 (eval_057).
    """
    injected: dict[str, str] = {}
    for s in cited_sources:
        label = s.get("party")
        p = "무소속" if label == "무소속" else core_party(label)
        if p:
            for k in _speaker_keys(s.get("speaker")):
                injected[k] = p
    flags = []
    for m in _NAME_PARTY.finditer(answer):
        name, stated = m.group(1), m.group(2)
        actual = injected.get(name)
        if actual and stated != actual:
            flags.append(f"{name}: 답변 '{stated}' ≠ 근거 주입 '{actual}'")
    return flags


# ── 키워드 포함률 (spec §4-2, eval_055) ──────────────────────────────────────

# 질문 속 기관 고유명사만 대상 — 일반명사는 오탐 위험이 커서 제외 (spec)
_ORG_TOKEN = re.compile(
    r"[가-힣]{2,10}(?:은행|공사|공단|공항|재단|진흥원|연구원|공제회|금고|거래소)"
)


def keyword_containment(answer: str, cited_sources: list[dict], question: str) -> list[str]:
    """질문의 기관 고유명사가 답변 문장에 주어로 쓰였는데, 그 문장이 인용한 근거
    본문에는 그 기관이 없으면 flag (표적 이탈 패딩 — eval_055 기업은행/우리은행)."""
    orgs = set(_ORG_TOKEN.findall(question))
    if not orgs:
        return []
    by_n = {s["n"]: s for s in cited_sources}
    flags = []
    for sent, _ in _paragraph_sentences(answer):
        if _REFUSAL_SENT.search(sent):
            continue
        cited = _cited_in(sent, by_n)
        if not cited:
            continue
        for org in orgs:
            if org in sent and not any(org in (s.get("text") or "") for s in cited):
                detail = f"'{org}' 이(가) 인용 근거 본문에 없음: {sent[:40]}"
                if detail not in flags:
                    flags.append(detail)
    return flags


# ── 정권기 일치 (spec §4-2, eval_035) ────────────────────────────────────────

_CURRENT_GOV = re.compile(r"현\s*정부|새\s*정부|현\s*정권|이번\s*정부")
_CURRENT_START = RULING_PERIODS[-1][0]  # 정권교체 경계 (party.py 재사용 — 신규 데이터 불필요)


def ruling_period_consistency(answer: str, cited_sources: list[dict]) -> list[str]:
    """'현 정부/새 정부' 서술 문장이 이전 정권기 발언을 인용하면 flag (eval_035 —
    2024-08 의 윤 정부 비판 발언이 현 정부 비판으로 오독). 날짜 결측 source는 판정 제외 (크래시 없음, 예외 격리).

    2026-07-26 F5: 문장이 해당 인용의 연월을 공시하면 통과 — "이는 현 정부 출범
    이전인 2024년 8월의 발언으로…"는 시점을 밝히고 교정하는 모범 문장이지 오류가
    아니다. 자매 규칙 qa_pairing_dates 의 _mentions_date 를 그대로 재사용해 계약을
    정합화한다(날짜를 공시하면 정직한 처리로 인정하는 동일 원칙).
    """
    by_n = {s["n"]: s for s in cited_sources}
    flags = []
    for sent, _ in _paragraph_sentences(answer):
        if not _CURRENT_GOV.search(sent) or _REFUSAL_SENT.search(sent):
            continue
        for s in _cited_in(sent, by_n):
            # 날짜 결측 source는 판정 제외 (크래시 방지)
            if not _VALID_DATE.match(str(s.get("date"))[:10]):
                continue
            d = date.fromisoformat(str(s.get("date"))[:10])
            if d < _CURRENT_START and not _mentions_date(sent, s["date"]):
                flags.append(f"[{s['n']}] {s['date']} (이전 정권기) 발언을 현 정부 서술에 인용")
    return flags


# ── 통합 진입점 (spec §6) ────────────────────────────────────────────────────

# 규칙이 죽었음을 알리는 flag (감사 2026-08-05). 이전에는 규칙이 예외로 죽으면
# detail["errors"] 에 이름만 남고 flags 는 비어 있었다 — main.py 는 flags 만 보므로
# **"검사해서 문제없음"과 "검사가 죽어서 못 함"이 완전히 같아 보였다.** 검증층이
# 통째로 썩어도 모든 답변이 FULL 로 나가는 구조였다.
#
# 이 flag 를 세우면 기존 강등 경로(main.py: flags 있으면 FULL→PARTIAL)를 그대로 타서
# "확인 못 한 답변을 FULL 이라 부르지 않는다"가 성립한다. 규칙 버그로 전 답변이
# PARTIAL 이 되는 부작용은 감수한다 — 조용히 FULL 을 내주는 쪽이 훨씬 나쁘고,
# 시끄러운 실패는 곧 발견되지만 조용한 실패는 영영 안 보인다.
INCOMPLETE_FLAG = "verification_incomplete"

# 규칙 실행 실패 누적 — /health 로 노출한다 (main.py 의 _log_failures 와 같은 패턴).
# 이 값이 0 이 아니면 검증층이 썩고 있다는 뜻이다.
_rule_failures = 0


def rule_failure_count() -> int:
    """규칙 실행 실패 누적 횟수 (0 이 정상). /health 노출용."""
    return _rule_failures


def note_rule_failure() -> None:
    """규칙 실패 1건 기록 — verify() 자체가 죽는 경로(answer.py 최후 방어)에서도 센다."""
    global _rule_failures
    _rule_failures += 1


def verify(
    question: str,
    answer: str,
    sources: list[dict],
    cited_numbers: list[int],
    question_types: set | None = None,
) -> dict:
    """규칙 전부 실행 → {"flags": [...], "detail": {...}}.

    - cited_numbers 에 해당하는 sources 만 대상 (spec §2-1) — 인용 0건(거절 답변)은
      검증하지 않는다 (REFUSED 에 flag 노이즈를 얹지 않는다)
    - 규칙별 예외 격리: 죽은 규칙은 detail["errors"] 에 이름을 남기고 계속 진행한다
      (검증층 버그가 답변 생성 실패로 번지지 않게 — issue_context 패턴).
      **다만 격리는 은폐가 아니다** — INCOMPLETE_FLAG 를 함께 세워 "확인 못 했다"가
      등급까지 전달되게 한다 (감사 2026-08-05).
    """
    detail: dict = {}
    flags: list[str] = []
    cited = [s for s in sources if s["n"] in set(cited_numbers)]
    if not cited:
        return {"flags": [], "detail": {}}

    types = question_types if question_types is not None else classify_question(question)

    def run(name, fn):
        try:
            return fn()
        except Exception:
            note_rule_failure()
            logger.warning(
                "verification 규칙 %s 실패 — 건너뜀 (누적 %d회, %s 로 강등)",
                name, rule_failure_count(), INCOMPLETE_FLAG, exc_info=True,
            )
            detail.setdefault("errors", []).append(name)
            if INCOMPLETE_FLAG not in flags:
                flags.append(INCOMPLETE_FLAG)
            return None

    if "compare" in types:
        cov = run("comparison_coverage", lambda: comparison_coverage(cited))
        if cov is not None:
            detail["comparison_coverage"] = cov
            if not cov["covered"]:
                flags.append("comparison_one_sided")

    if run("speaker_both_sides", lambda: speaker_both_sides(answer, cited)):
        flags.append("speaker_both_sides")

    if run("qa_pairing_dates", lambda: qa_pairing_dates(answer, cited, question)):
        flags.append("qa_pairing_date_mismatch")

    for flag_name, fn in (
        ("speaker_role_mismatch", lambda: speaker_role_consistency(answer, cited)),
        ("party_label_mismatch", lambda: party_label_consistency(answer, cited)),
        ("keyword_missing", lambda: keyword_containment(answer, cited, question)),
        ("ruling_period_mismatch", lambda: ruling_period_consistency(answer, cited)),
    ):
        found = run(flag_name, fn)
        if found:
            flags.append(flag_name)
            detail[flag_name] = found

    return {"flags": flags, "detail": detail}
