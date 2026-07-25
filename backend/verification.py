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


def comparison_coverage(sources: list[dict]) -> dict:
    """비교 질문의 진영 커버리지 — 서로 다른 정당명이 2개 이상이어야 진짜 비교.

    '당시 여당/야당' 라벨은 시점 기준이지 정당 기준이 아니다 (eval_019: 전부
    더불어민주당인데 시점차로 여야가 갈려 '여야 대립'으로 오독).
    """
    parties = sorted({p for s in sources if (p := core_party(s.get("party")))})
    return {"core_parties": parties, "covered": len(parties) >= 2}


# ── 화자·진영 이중 사용 (spec §2-2, eval_068) ────────────────────────────────

_SIDE_FRAME_RULING = re.compile(r"(여당|찬성)(\s*측)?(\s*(에서는|에서|소속|의원들?|입장|의|인))?\s*$")
_SIDE_FRAME_OPPO = re.compile(r"(야당|반대)(\s*측)?(\s*(에서는|에서|소속|의원들?|입장|의|인))?\s*$")


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
    """같은 화자가 답변 안에서 여당/야당/찬성/반대 양쪽 프레이밍에 배치됐는가.

    화자명 직전 수식(여당 측/찬성 측/야당 소속/반대 측 등)만 프레이밍으로 인정 — 주변 창 방식은
    상대 진영 반응 서술('야당 반대에도 불구하고')을 오탐해 폐기 (2026-07-25 리뷰).
    찬성/반대는 여당/야당으로 정규화 (spec §2-2 "여당/야당/찬성/반대 대조 키워드").
    직전 수식 방식은 괄호 정당명 등 개입 텍스트에 약함 — 정밀도 우선 (minor trade-off).
    순수 정규식, LLM 불필요.
    """
    for s in cited_sources:
        for name in _speaker_keys(s.get("speaker")):
            hits = [m.start() for m in re.finditer(re.escape(name), answer)]
            if len(hits) < 2:
                continue
            frames = {}  # {pos: '여당'|'야당'|None}
            for pos in hits:
                # 화자명 직전 12자에서 프레이밍 찾기
                before = answer[max(0, pos - 12): pos]
                if _SIDE_FRAME_RULING.search(before):
                    frames[pos] = '여당'
                elif _SIDE_FRAME_OPPO.search(before):
                    frames[pos] = '야당'
                else:
                    frames[pos] = None
            # 같은 화자의 서로 다른 등장이 여당·야당 양쪽으로 명시 프레이밍되면 True
            if None not in frames.values() and len(set(frames.values())) > 1:
                return True
    return False


# ── 거짓 Q-A 짝짓기 (spec §3, eval_029) ──────────────────────────────────────

_QA_VERB = re.compile(r"질(?:문|의)")
_QA_ASKER = re.compile(r"[가-힣]{2,4}\s*(?:위원|의원)")
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
# '(?<![가-힣])정부' 는 '의정부' 오폭 방지. '정부측'은 그대로 정부 주어로 취급.
_GOV_SUBJECT = re.compile(
    r"외교부|통일부|국방부|법무부|행정안전부|기획재정부|보건복지부|국토교통부"
    r"|산업통상자원부|과학기술정보통신부|환경부|교육부|고용노동부|문화체육관광부"
    r"|여성가족부|해양수산부|중소벤처기업부|농림축산식품부|금융위원회|방송통신위원회"
    r"|공정거래위원회|금융감독원|경찰청|검찰청|국세청|관세청|통계청|기상청|소방청"
    r"|질병관리청|대통령실|(?<![가-힣])정부"
)

# 답변 속 "이름+직함" — 화자 귀속 검출용. 일반어 프리픽스는 이름이 아니다.
_NAMED_SPEAKER = re.compile(r"([가-힣]{2,4})\s*(?:위원장|위원|의원|장관|차관|청장|처장|총리|후보자|대변인)")
_NOT_NAMES = frozenset([
    "해당", "관련", "소속", "여당", "야당", "양당", "양측", "모든", "다른", "일부",
    "동일", "같은", "각각", "국회", "정부", "당시", "여러", "다수", "소수", "상임",
    # 조직명 프리픽스 (금융위원회의 '금융' 등이 화자명으로 오인되지 않도록)
    "금융", "방송", "공정", "외교", "통일", "국방", "법무", "행정", "기획", "보건",
])


def speaker_role_consistency(answer: str, cited_sources: list[dict]) -> list[str]:
    """문장 단위 화자·기관 귀속 검사 → detail 문자열 목록.

    (a) 미등장 화자: 문장의 '이름+직함'이 인용 근거 화자 어디에도 없음 (환각 귀속)
    (b) 기관 귀속: 문장 주어가 정부 기관(명시 또는 문단 승계)인데 그 문장의 인용
        화자가 정부측이 아님 — 승계 기반은 'inherited' 표시 (spec 2차 휴리스틱)
    거절 문장(확인 불가 공시)은 검사하지 않는다.
    """
    by_n = {s["n"]: s for s in cited_sources}
    all_speaker_keys: set[str] = set()
    for s in cited_sources:
        all_speaker_keys |= _speaker_keys(s.get("speaker"))

    flags: list[str] = []
    inherited_gov = False
    prev_para = None
    for sent, pi in _paragraph_sentences(answer):
        if pi != prev_para:
            inherited_gov = False
            prev_para = pi
        if _REFUSAL_SENT.search(sent):
            continue
        names = [m for m in _NAMED_SPEAKER.findall(sent) if m not in _NOT_NAMES]
        gov_explicit = bool(_GOV_SUBJECT.search(sent))
        cited = _cited_in(sent, by_n)

        if names:
            # (a) 명시 화자 — 인용이 있는 문장에서 이름이 근거 화자 목록에 전무하면 환각
            if cited and not any(n in all_speaker_keys for n in names):
                flags.append(f"미등장 화자 '{names[0]}' 에 발언 귀속: {sent[:40]}")
            inherited_gov = False  # 화자명이 나오면 기관 승계 끊김 (명시 주어 전환)
        elif gov_explicit or inherited_gov:
            # (b) 정부 기관 주어 — 인용 화자가 정부측이 아니면 귀속 오류
            for s in cited:
                if s.get("party") != "정부측" and speaker_group(s.get("role")) != "government":
                    suffix = "" if gov_explicit else " (inherited)"
                    flags.append(f"정부 기관 서술에 비정부 발언 [{s['n']}] 인용{suffix}: {sent[:40]}")
        if gov_explicit:
            inherited_gov = True
    return flags


# ── 정당 라벨 일치 (spec §0-1·§4-2, eval_057) ────────────────────────────────

_PARTY_NAMES = (
    "더불어민주당|국민의힘|더불어민주연합|국민의미래|조국혁신당|개혁신당"
    "|진보당|기본소득당|사회민주당|새로운미래|무소속"
)
_NAME_PARTY = re.compile(rf"([가-힣]{{2,4}})\s*(?:위원장|위원|의원)?\s*\(\s*({_PARTY_NAMES})\s*\)")


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
    2024-08 의 윤 정부 비판 발언이 현 정부 비판으로 오독)."""
    by_n = {s["n"]: s for s in cited_sources}
    flags = []
    for sent, _ in _paragraph_sentences(answer):
        if not _CURRENT_GOV.search(sent) or _REFUSAL_SENT.search(sent):
            continue
        for s in _cited_in(sent, by_n):
            d = date.fromisoformat(str(s.get("date"))[:10])
            if d < _CURRENT_START:
                flags.append(f"[{s['n']}] {s['date']} (이전 정권기) 발언을 현 정부 서술에 인용")
    return flags
