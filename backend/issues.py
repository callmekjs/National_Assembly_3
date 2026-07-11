"""쟁점 API (POL-3 목록 + POL-4 타임라인).

타임라인 설계 (docs/superpowers/specs/2026-07-09-pol4-issue-timeline-design.md):
  이슈별 월별 발언 추이를 병행 2축으로 반환한다.
  - corpus_turns: 시드 키워드로 전체 chunks ILIKE 검색한 월별 turn 수 (재현율 축,
    키워드 노이즈 포함 — 두 선 간격이 "스침 많은 달"을 드러냄)
  - mapped_turns / mapped_core_turns: issue_chunks 매핑의 월별 turn 수 (정밀도 축,
    분기 상한 있음). core 만 POL-5·POL-6 이 소비.
  집계는 turn 단위(actors.py 교훈). 매핑은 chunks.turn_id(NOT NULL 권위) 사용.
"""

from collections import Counter
from psycopg2.extras import RealDictCursor

from db import get_conn
from search_keyword import _like_escape


def build_keyword_patterns(keywords: list[str]) -> list[str]:
    """시드 키워드 → ILIKE 부분일치 패턴 (내용 이스케이프, 양끝 % 와일드카드)."""
    return [f"%{_like_escape(k)}%" for k in keywords]


def _month_range(months: list[str]) -> list[str]:
    """'YYYY-MM' 목록의 최소~최대 사이 모든 달을 오름차순으로. 빈 목록이면 [].
    None/빈 문자열(nullable meeting_date 유래)은 걸러 min/max TypeError 방지."""
    months = [m for m in months if m]
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
        keywords = (row["seed"] or {}).get("seed_keywords", [])

        corpus: dict[str, int] = {}
        patterns = build_keyword_patterns(keywords)
        if patterns:
            cur.execute(
                """
                SELECT to_char(meeting_date, 'YYYY-MM') AS month,
                       count(DISTINCT turn_id) AS corpus_turns
                FROM chunks
                WHERE text ILIKE ANY(%s)
                  AND meeting_date IS NOT NULL
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
              AND c.meeting_date IS NOT NULL
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


_STANCE_DIRS = ("support", "oppose", "concern")  # 방향(입장) 발언만 카운트


def aggregate_stances(rows: list[dict]) -> str:
    """한 행위자의 발언 stance 목록 → 행위자 레벨 라벨.
    입장 발언(support/oppose/concern)만 카운트. 0개면 no_stance. 최다가 대표.
    support·oppose 둘 다 있고 각각 입장발언의 1/3 이상이면 mixed."""
    counts = {s: 0 for s in _STANCE_DIRS}
    for r in rows:
        if r["stance"] in counts:
            counts[r["stance"]] += 1
    total = sum(counts.values())
    if total == 0:
        return "no_stance"
    if counts["support"] > 0 and counts["oppose"] > 0 \
            and counts["support"] >= total / 3 and counts["oppose"] >= total / 3:
        return "mixed"
    return max(_STANCE_DIRS, key=lambda s: counts[s])


# POL-3 core 게이트(≥90%) 미달 7개 — 매핑 정밀도 경고 대상 (progress.md POL-3 후속 기록,
# core 86.2% 마감 2026-07-08). 게이트 재실행 시 이 목록도 갱신할 것.
LOW_QUALITY_ISSUES = frozenset({
    "martial-law", "lee-jinsook-kcc", "ytn-privatization", "public-broadcasting",
    "small-business", "conscription-welfare", "itaewon-disaster",
})

_COMP_STANCES = ("support", "oppose", "concern", "mixed", "no_stance")  # 행위자 대표 라벨
_GROUP_PRIORITY = ("assembly", "government", "witness", "staff", "unknown")  # 동률 우선순위
_SPECIAL_ROWS = ("정부측", "무소속/미상")  # 항상 맨 뒤, 이 순서


def actor_group(roles: list) -> str:
    """행위자의 이슈 내 role 목록 → 최빈 그룹. 동률이면 _GROUP_PRIORITY 순 (겸직 의원 우선)."""
    from party import speaker_group
    counts = Counter(speaker_group(r) for r in roles)
    return max(_GROUP_PRIORITY, key=lambda g: (counts.get(g, 0), -_GROUP_PRIORITY.index(g)))


def party_composition(actors: list[dict]) -> list[dict]:
    """행위자 목록 → 정당별 구도 행 (POL-6). actors 원소: speaker/party/stance/roles.

    assembly → 정당 행(무소속·미상은 "무소속/미상"), government → "정부측",
    witness·staff → 제외. 정렬: 수 내림차순 → 정당명, 특수행 맨 뒤."""
    rows: dict[str, dict] = {}
    for a in actors:
        g = actor_group(a["roles"])
        if g in ("witness", "staff"):
            continue
        if g == "government":
            key = "정부측"
        elif g == "assembly" and a["party"] and a["party"] != "무소속":
            key = a["party"]
        else:
            key = "무소속/미상"
        row = rows.setdefault(key, {"party": key, "actor_count": 0,
                                    "stance_dist": {s: 0 for s in _COMP_STANCES},
                                    "actors": []})
        row["actor_count"] += 1
        row["stance_dist"][a["stance"]] += 1
        row["actors"].append({"speaker": a["speaker"], "stance": a["stance"]})
    ordered = sorted((r for r in rows.values() if r["party"] not in _SPECIAL_ROWS),
                     key=lambda r: (-r["actor_count"], r["party"]))
    ordered += [rows[k] for k in _SPECIAL_ROWS if k in rows]
    return ordered


def party_sides(parties: list[str]) -> dict:
    """정권교체 구간 목록 + 정당별 여야 (POL-6 보조 필드). 위성정당은 모정당 기준."""
    from party import RULING_PERIODS, SATELLITE_PARENT
    periods = [{"from": s.isoformat(), "to": None if e.year == 9999 else e.isoformat(),
                "ruling": p} for s, e, p in RULING_PERIODS]
    sides = {party: ["여당" if SATELLITE_PARENT.get(party, party) == pr["ruling"] else "야당"
                     for pr in periods] for party in parties}
    return {"periods": periods, "sides": sides}


def issue_stances(issue_id: str) -> dict | None:
    """이슈 행위자 입장 매트릭스. 이슈 없거나 판정 데이터 없으면 None.
    발언별 stance 를 speaker 로 묶어 집계(aggregate_stances) + 입장별 카운트 + 근거 인용."""
    from party import member_party
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, s.stance, c.meeting_date::text AS date,
                   min(c.chunk_id) AS chunk_id,
                   left(string_agg(c.text, ' ' ORDER BY c.chunk_index), 160) AS snippet
            FROM issue_stances s
            JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, s.stance, c.meeting_date
            ORDER BY s.speaker, c.meeting_date
        """, (issue_id,))
        stance_rows = cur.fetchall()
    if not stance_rows:
        return None

    by_speaker: dict[str, list] = {}
    for r in stance_rows:
        by_speaker.setdefault(r["speaker"], []).append(r)

    actors = []
    for speaker, rs in by_speaker.items():
        counts = {s: 0 for s in ("support", "oppose", "concern", "neutral", "none")}
        for r in rs:
            counts[r["stance"]] = counts.get(r["stance"], 0) + 1
        label = aggregate_stances(rs)
        # 근거: 대표 라벨을 뒷받침하는 발언(혼재면 support+oppose 양쪽), 없으면 전부
        support_set = {"support", "oppose"} if label == "mixed" else {label}
        cites = [r for r in rs if r["stance"] in support_set] or rs
        actors.append({
            "speaker": speaker,
            "party": member_party(speaker),
            "stance": label,
            "counts": counts,
            "citations": [{"turn_id": r["turn_id"], "stance": r["stance"], "date": r["date"],
                           "chunk_id": r["chunk_id"], "snippet": r["snippet"]} for r in cites],
        })
    actors.sort(key=lambda a: sum(a["counts"].values()), reverse=True)
    return {"issue_id": issue_id, "title": row["title"], "actors": actors}


def issue_party_stances(issue_id: str) -> dict | None:
    """이슈 정당 구도 (POL-6). 이슈 없거나 판정 데이터 없으면 None.

    발언 rows → 행위자(speaker)별 대표 라벨(aggregate_stances) + role 목록
    → party_composition 으로 정당 행 → side_by_period 보조 필드 부착."""
    from party import member_party
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT title FROM issues WHERE issue_id = %s", (issue_id,))
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute("""
            SELECT speaker, role, stance FROM issue_stances
            WHERE issue_id = %s ORDER BY speaker, turn_id
        """, (issue_id,))
        srows = cur.fetchall()
    if not srows:
        return None

    by_speaker: dict[str, list] = {}
    for r in srows:
        by_speaker.setdefault(r["speaker"], []).append(r)
    actors = [{"speaker": sp, "party": member_party(sp), "stance": aggregate_stances(rs),
               "roles": [r["role"] for r in rs]} for sp, rs in by_speaker.items()]

    parties = party_composition(actors)
    ps = party_sides([r["party"] for r in parties if r["party"] not in _SPECIAL_ROWS])
    for r in parties:
        r["side_by_period"] = ps["sides"].get(r["party"])  # 특수행은 None
    return {"issue_id": issue_id, "title": row["title"],
            "mapping_quality": "low" if issue_id in LOW_QUALITY_ISSUES else "ok",
            "periods": ps["periods"], "parties": parties}
