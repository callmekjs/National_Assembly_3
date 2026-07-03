"""
Grounding 판정 (RAG-7) — 답변의 신뢰등급 FULL/PARTIAL/REFUSED/NONE (마스터 4-9).

판정 방식: 규칙 기반 + 유사도 사전차단 (2026-07-03 확정. LLM 판정자는 4단계 때 재검토).
비용 0, 결정론적 — 같은 입력이면 항상 같은 등급이라 테스트·회귀 감지가 쉽다.

사전차단 (LLM 호출 전):
  - 검색 0건 → NONE
  - 벡터 최고 유사도 < threshold AND 키워드 매치 0건 → REFUSED (LLM 비용 절약)
    키워드 매치가 있으면 차단하지 않는다 — 고유명사 질문은 벡터 유사도가
    낮아도 정답일 수 있음 (ETL-8 실측)

threshold 는 .env 의 GROUNDING_SIM_THRESHOLD (기본 0.4) — 하드코딩 금지.
무작위 유사도 기준선 0.386 실측에서 나온 경험적 초기값이라 조정 가능해야 한다.

사후 판정 (RAG-6 응답의 신호):
  인용 있음 + 거절 문구 없음 → FULL
  인용 있음 + 거절 문구 있음 → PARTIAL (일부만 확인)
  인용 없음 + 거절 문구 있음 → REFUSED
  인용 없음 + 거절 문구 없음 → PARTIAL + ungrounded (무인용 주장 = 프롬프트 위반)
  invalid_citations 있으면 FULL 이어도 PARTIAL 강등 (인용 신뢰 불가)

거절 문구는 부분 문자열("확인할 수 없")로 감지 — LLM 이 어순을 바꿔 거절하는
실측("제공된 회의록에서 X는 확인할 수 없습니다") 때문에 exact match 금지.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# 거절 표현 감지 2단 (스모크 실측: LLM 이 고정 문구를 다양하게 변형 —
# "확인되지 않습니다", "포함되어 있지 않습니다", "언급이 없습니다")
#
# - 엄격 패턴: 인용이 *있는* 답변에 적용 (FULL→PARTIAL 판단). 넓은 패턴을 쓰면
#   발언 인용("근거가 없다고 지적했습니다[1]")을 거절로 오탐해 FULL 을 부당 강등한다
# - 넓은 패턴: 인용이 *없는* 답변에 적용 (REFUSED vs ungrounded 구분).
#   인용 없는 답변은 거절 아니면 환각이라 넓게 잡는 쪽이 안전하다
# 새 변형 발견 시 여기 추가 + 회귀 테스트 — query_logs 의 PARTIAL+ungrounded 행이 후보다
REFUSAL_STRICT = re.compile(r"확인(할 수 없|되지 않|이 되지 않)")

# report 모드의 "## 논의의 한계" 섹션은 프롬프트가 요구한 정직성 장치 —
# 여기 담긴 "세부 내용은 확인할 수 없다"류 서술을 거절로 세면 모든 브리핑이
# PARTIAL 로 자기강등된다 (2026-07-03 실측). 인용 있는 답변의 판정에서 제외.
_LIMITS_SECTION = re.compile(r"##\s*논의의 한계.*?(?=\n##\s|\Z)", re.S)
REFUSAL_BROAD = re.compile(
    r"확인(할 수 없|되지 않|이 되지 않)"
    r"|포함(되어|하고) 있지 않"
    r"|언급(이|은|도)? ?(없|되지 않)"
    r"|찾을 수 없"
    r"|나와 ?있지 않"
)


def sim_threshold() -> float:
    """사전차단 유사도 임계 — 호출 시점에 읽어 .env 변경이 재기동 없이도 테스트 가능."""
    return float(os.environ.get("GROUNDING_SIM_THRESHOLD", "0.4"))


def pre_gate(hits: list[dict]) -> str | None:
    """LLM 호출 전 판정. "NONE"/"REFUSED" 면 LLM 을 호출하지 말 것. None 이면 통과."""
    if not hits:
        return "NONE"
    has_keyword = any(h.get("kw_rank") is not None for h in hits)
    if has_keyword:
        return None
    vec_scores = [h["vec_score"] for h in hits if h.get("vec_score") is not None]
    if vec_scores and max(vec_scores) < sim_threshold():
        return "REFUSED"
    return None


def judge(result: dict) -> tuple[str, bool]:
    """LLM 답변 후 판정 → (grounding 등급, ungrounded 경고 여부)."""
    cited = bool(result.get("cited_numbers"))
    answer = result.get("answer") or ""

    if cited:
        refused = bool(REFUSAL_STRICT.search(_LIMITS_SECTION.sub("", answer)))
        level, ungrounded = ("PARTIAL" if refused else "FULL"), False
    elif REFUSAL_BROAD.search(answer):
        level, ungrounded = "REFUSED", False
    else:
        level, ungrounded = "PARTIAL", True

    if result.get("invalid_citations") and level == "FULL":
        level = "PARTIAL"
    return level, ungrounded
