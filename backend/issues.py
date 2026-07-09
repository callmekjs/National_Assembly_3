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


def issue_timeline(issue_id: str) -> dict | None:
    """이슈 월별 발언 추이 (병행 2축). 이슈 미존재 시 None → 라우트에서 404.

    corpus: 시드 키워드 ILIKE 로 전체 chunks 월별 turn 수 (키워드 없으면 건너뜀).
    mapped: issue_chunks 조인 월별 turn 수(전체/core), chunks.turn_id 사용.
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title, seed FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        keywords = (row["seed"] or {}).get("keywords", [])

        corpus: dict[str, int] = {}
        patterns = build_keyword_patterns(keywords)
        if patterns:
            cur.execute(
                """
                SELECT to_char(meeting_date, 'YYYY-MM') AS month,
                       count(DISTINCT turn_id) AS corpus_turns
                FROM chunks
                WHERE text ILIKE ANY(%s)
                GROUP BY 1
                """,
                (patterns,),
            )
            corpus = {r["month"]: r["corpus_turns"] for r in cur.fetchall()}

        cur.execute(
            """
            SELECT to_char(c.meeting_date, 'YYYY-MM') AS month,
                   count(DISTINCT c.turn_id) AS mapped_turns,
                   count(DISTINCT c.turn_id) FILTER (WHERE ic.judge = 'llm_core')
                       AS mapped_core_turns
            FROM issue_chunks ic JOIN chunks c ON c.chunk_id = ic.chunk_id
            WHERE ic.issue_id = %s
            GROUP BY 1
            """,
            (issue_id,),
        )
        mapped = {r["month"]: (r["mapped_turns"], r["mapped_core_turns"])
                  for r in cur.fetchall()}

    return {"issue_id": issue_id, "title": row["title"],
            "months": merge_months(corpus, mapped)}


def list_issues() -> dict:
    """쟁점 사전 목록 (POL-3). main.py 인라인에서 이관 — 이슈 API 응집."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT i.issue_id, i.title, i.type, i.description,
                   count(ic.chunk_id)          AS chunk_count,
                   count(DISTINCT ic.turn_id)  AS turn_count,
                   count(*) FILTER (WHERE ic.judge = 'llm_core') AS core_chunk_count
            FROM issues i LEFT JOIN issue_chunks ic USING (issue_id)
            GROUP BY i.issue_id, i.title, i.type, i.description
            ORDER BY chunk_count DESC, issue_id
        """)
        return {"issues": cur.fetchall()}
