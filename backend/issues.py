"""쟁점 API (POL-3 목록 + POL-4 타임라인).

타임라인 설계 (docs/superpowers/specs/2026-07-09-pol4-issue-timeline-design.md):
  이슈별 월별 발언 추이를 병행 2축으로 반환한다.
  - corpus_turns: 시드 키워드로 전체 chunks ILIKE 검색한 월별 turn 수 (재현율 축,
    키워드 노이즈 포함 — 두 선 간격이 "스침 많은 달"을 드러냄)
  - mapped_turns / mapped_core_turns: issue_chunks 매핑의 월별 turn 수 (정밀도 축,
    분기 상한 있음). core 만 POL-5·POL-6 이 소비.
  집계는 turn 단위(actors.py 교훈). 매핑은 chunks.turn_id(NOT NULL 권위) 사용.
"""

from psycopg2.extras import RealDictCursor

from db import get_conn
from search_keyword import _like_escape


def build_keyword_patterns(keywords: list[str]) -> list[str]:
    """시드 키워드 → ILIKE 부분일치 패턴 (내용 이스케이프, 양끝 % 와일드카드)."""
    return [f"%{_like_escape(k)}%" for k in keywords]


def _month_range(months: list[str]) -> list[str]:
    """'YYYY-MM' 목록의 최소~최대 사이 모든 달을 오름차순으로. 빈 목록이면 []."""
    if not months:
        return []
    lo, hi = min(months), max(months)
    y, m = int(lo[:4]), int(lo[5:7])
    hy, hm = int(hi[:4]), int(hi[5:7])
    out = []
    while (y, m) <= (hy, hm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def merge_months(corpus: dict, mapped: dict) -> list[dict]:
    """두 월별 집계를 합집합 범위로 병합 + 빈 달 0 채움. month 오름차순."""
    all_months = list(corpus.keys()) + list(mapped.keys())
    rows = []
    for month in _month_range(all_months):
        mt, mc = mapped.get(month, (0, 0))
        rows.append({
            "month": month,
            "corpus_turns": corpus.get(month, 0),
            "mapped_turns": mt,
            "mapped_core_turns": mc,
        })
    return rows
