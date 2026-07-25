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

_SIDE_FRAME = re.compile(r"(여당|야당)(\s*측)?(\s*(에서는|에서|소속|의원들?|의|인))?\s*$")


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
    """같은 화자가 답변 안에서 여당·야당 양쪽 프레이밍에 배치됐는가.

    화자명 직전 수식(여당 측/야당 소속/야당인 등)만 프레이밍으로 인정 — 주변 창 방식은
    상대 진영 반응 서술('야당 반대에도 불구하고')을 오탐해 폐기 (2026-07-25 리뷰).
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
                m = _SIDE_FRAME.search(before)
                frames[pos] = m.group(1) if m else None
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
