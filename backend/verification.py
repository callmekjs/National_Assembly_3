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
