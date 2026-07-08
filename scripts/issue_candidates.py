"""이슈 후보 탐사 (POL-3 단계 1) — 코퍼스에서 이슈 후보 40~60개를 근거와 함께 추출.

네 가지 신호를 교차한다 (docs/issue_module_spec.md):
  1. 시계열 스파이크 — 위원회×월 발언량 급증 (사건형 후보)
  2. agenda 빈발 줄 — 안건 섹션 반복 의제 (정책형 후보)
  3. LLM 표본 요약 — 위원회×분기 표본 청크의 반복 주제
  4. query_logs — 실사용 질문 교차

산출: data/issues/candidates_report.md (사용자 검수용 — 확정은 사람이 한다)
실행: python scripts/issue_candidates.py
"""

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
OUT_DIR = ROOT / "data" / "issues"
REPORT = OUT_DIR / "candidates_report.md"
NORMALIZED_DIR = ROOT / "data" / "v1" / "normalized"

SPIKE_RATIO = 1.8       # 월 발언량 ≥ 위원회 월 중앙값 × 이 배율 → 스파이크
SAMPLE_PER_CELL = 30    # 위원회×분기당 LLM 요약 표본 청크 수
MIN_AGENDA_LEN = 8      # 이보다 짧은 안건 줄은 잡음 ("산회" 등)
_MODEL = "gpt-4o-mini"


def detect_spikes(rows: list[tuple], ratio: float = SPIKE_RATIO) -> list[dict]:
    """(committee, 'YYYY-MM', turn_count) 목록 → 스파이크 목록 (ratio 내림차순).

    중앙값 기준 — 평균은 스파이크 자신에게 끌려간다. 월이 3개 미만인 위원회는
    중앙값이 무의미하므로 판단하지 않는다.
    """
    by_com = defaultdict(list)
    for com, month, cnt in rows:
        by_com[com].append((month, cnt))
    out = []
    for com, months in by_com.items():
        if len(months) < 3:
            continue
        for month, cnt in months:
            # 현재 행을 제외한 다른 행들의 중앙값
            other_counts = [c for m, c in months if m != month]
            med = median(other_counts)
            if med <= 0:
                continue
            if cnt >= ratio * med:
                out.append({"committee": com, "month": month, "count": cnt,
                            "median": med, "ratio": round(cnt / med, 2)})
    out.sort(key=lambda s: s["ratio"], reverse=True)
    return out


def top_agenda_lines(lines: list[str], top_n: int = 40) -> list[tuple[str, int]]:
    """agenda 섹션 줄들 → (정규화 줄, 빈도) 상위 top_n. 짧은 줄(의사진행 잡음)은 제외."""
    counter = Counter(
        " ".join(line.split())
        for line in lines
        if len(" ".join(line.split())) >= MIN_AGENDA_LEN
    )
    return counter.most_common(top_n)


def parse_topics(content: str) -> list[str]:
    """LLM 요약 응답 '{"topics": [...]}' → 주제 목록. 형식 위반은 빈 목록 (후보 누락 무해)."""
    try:
        topics = json.loads(content).get("topics")
    except (json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(topics, list):
        return []
    return [t for t in topics if isinstance(t, str)]
