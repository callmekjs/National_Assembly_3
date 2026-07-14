"""
행위자 프로필 (POL-2) — 특정 인물의 발언 활동을 한 번의 호출로 요약한다.

설계 결정 (2026-07-03, POL-1 실태 조사 판정 반영):
  - 집계는 turn 단위 (count DISTINCT turn_id) — 청크 단위는 긴 발언이 분할되어
    중복 카운트되는 왜곡 (chunk_id = {turn_id}_chunk_{nnn})
  - utterance_types 는 question/statement 이진만 — motion 은 POL-1 에서 과소검출 판정
  - top_mentions 는 별칭 정규화 후 집계 — 금융위/금융위원회 이중 카운트 병합,
    그룹 대표명은 최장 표기(정식명)
  - 이름 매칭은 별칭 확장 — "유영하" 조회 시 한자(柳榮夏) 발언 포함
  - 대표 발언 = 최근 5건 (is_short 제외) — "대표성" 판정은 주관적이라 MVP 는
    설명 가능한 단순 기준. 원문은 chunk_id → /citations 연결
  - 여야 이력은 정권 구간(RULING_PERIODS)별 라벨 — 시점 의존 여야를 그대로 노출
"""

from psycopg2.extras import RealDictCursor

from aliases import expand_aliases
from answer import display_speaker
from db import get_conn
from issues import aggregate_stances
from party import RULING_PERIODS, member_party, party_label


def canonical_org(org: str) -> str:
    """기관 표기 → 별칭 그룹의 정식명(최장 표기). 그룹이 없으면 그대로."""
    return max(expand_aliases(org), key=len)


def build_party_history(name: str) -> tuple[str | None, list[dict]]:
    """(정당, 정권 구간별 여야 라벨 이력). members 미등록이면 (None, [])."""
    party = member_party(name)
    if party is None:
        return None, []
    history = []
    for start, end, _ in RULING_PERIODS:
        period = f"{start.isoformat()} ~ " + ("" if end.year == 9999 else end.isoformat())
        history.append({"period": period.strip(), "label": party_label(name, start.isoformat(), "의원")})
    return party, history


_STANCE_KEYS = ("support", "oppose", "concern", "neutral", "none")


def fold_issue_stances(rows: list[dict]) -> list[dict]:
    """(issue_id, title, stance, n) 집계 행 → 이슈별 대표 라벨 + 카운트. 순수 함수.

    대표 라벨은 issues.aggregate_stances 재사용 — 쟁점 매트릭스(POL-5)와 동일 규칙이라
    두 화면의 라벨이 어긋나지 않는다. 정렬은 발언 수 내림차순."""
    by_issue: dict[str, dict] = {}
    for r in rows:
        it = by_issue.setdefault(r["issue_id"], {
            "issue_id": r["issue_id"], "title": r["title"],
            "counts": {s: 0 for s in _STANCE_KEYS},
        })
        if r["stance"] in it["counts"]:  # 도메인 밖 값 방어 — 스키마에 CHECK 없음
            it["counts"][r["stance"]] += r["n"]
    out = []
    for it in by_issue.values():
        flat = [{"stance": s} for s, n in it["counts"].items() for _ in range(n)]
        out.append({**it, "stance": aggregate_stances(flat),
                    "total_turns": sum(it["counts"].values())})
    out.sort(key=lambda x: -x["total_turns"])
    return out


def search_members(q: str, limit: int = 10) -> list[dict]:
    """의원 이름 부분일치 검색 — 프로필 자동완성용. members(320명) 대상, 이름 오름차순.

    LIKE 이스케이프는 검색 모듈 규칙 재사용 — 이름에 %·_ 가 올 일은 없지만
    사용자 입력이므로 방어."""
    from search_keyword import _like_escape
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT name, party FROM members WHERE name LIKE %s ORDER BY name LIMIT %s",
            (f"%{_like_escape(q.strip())}%", limit),
        )
        return [dict(r) for r in cur.fetchall()]


def actor_issue_stances(variants: list[str]) -> list[dict]:
    """의원의 이슈별 입장 (POL-9) — issue_stances 역조회, 별칭 목록으로 매칭."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT s.issue_id, i.title, s.stance, count(*) AS n
            FROM issue_stances s JOIN issues i USING (issue_id)
            WHERE s.speaker = ANY(%s)
            GROUP BY s.issue_id, i.title, s.stance
            ORDER BY s.issue_id
        """, (variants,))
        return fold_issue_stances(cur.fetchall())


def actor_profile(name: str) -> dict | None:
    """인물 프로필 집계. 발언 기록이 없으면 None (엔드포인트에서 404)."""
    variants = list(expand_aliases(name.strip()))

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT count(DISTINCT turn_id) AS turns,
                   count(DISTINCT source_id) AS meetings,
                   min(meeting_date) AS first, max(meeting_date) AS last
            FROM chunks WHERE speaker = ANY(%s)
            """,
            (variants,),
        )
        totals = cur.fetchone()
        if totals["turns"] == 0:
            return None

        cur.execute(
            """
            SELECT co.name AS committee, count(DISTINCT ch.turn_id) AS turns
            FROM chunks ch JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.speaker = ANY(%s)
            GROUP BY co.name ORDER BY turns DESC
            """,
            (variants,),
        )
        by_committee = cur.fetchall()

        cur.execute(
            """
            SELECT to_char(meeting_date, 'YYYY-MM') AS month, count(DISTINCT turn_id) AS turns
            FROM chunks WHERE speaker = ANY(%s)
            GROUP BY 1 ORDER BY 1
            """,
            (variants,),
        )
        by_month = cur.fetchall()

        # question/statement 이진 비율 (motion 은 POL-1 과소검출 판정으로 제외)
        cur.execute(
            """
            SELECT utterance_type, count(DISTINCT turn_id) AS turns
            FROM chunks
            WHERE speaker = ANY(%s) AND utterance_type IN ('question', 'statement')
            GROUP BY 1
            """,
            (variants,),
        )
        ut = {r["utterance_type"]: r["turns"] for r in cur.fetchall()}
        ut_total = sum(ut.values()) or 1
        utterance_types = {k: round(v / ut_total, 3) for k, v in sorted(ut.items())}

        # turn 단위 집계 — mentions 는 turn 에서 추출되어 분할 청크마다 복사되므로
        # 청크 단위 count(*) 는 긴 발언을 중복 카운트한다. 별칭 병합 후에도 같은
        # turn 이 두 번 세지지 않도록 (org, turn_id) 쌍을 집합으로 센다.
        cur.execute(
            """
            SELECT DISTINCT v AS org, turn_id
            FROM chunks, jsonb_array_elements_text(mentions) AS v
            WHERE speaker = ANY(%s)
            """,
            (variants,),
        )
        merged: dict[str, set] = {}
        for r in cur.fetchall():
            key = canonical_org(r["org"])
            merged.setdefault(key, set()).add(r["turn_id"])
        top_mentions = [
            {"org": org, "count": len(turn_ids)}
            for org, turn_ids in sorted(merged.items(), key=lambda kv: -len(kv[1]))[:10]
        ]

        cur.execute(
            """
            SELECT ch.chunk_id, ch.meeting_date::text AS date, co.name AS committee,
                   left(ch.text, 150) AS snippet
            FROM chunks ch JOIN committees co ON co.committee_id = ch.committee_id
            WHERE ch.speaker = ANY(%s) AND NOT ch.is_short AND ch.chunk_index = 1
            ORDER BY ch.meeting_date DESC, ch.chunk_id DESC
            LIMIT 5
            """,
            (variants,),
        )
        recent = cur.fetchall()

    party, history = build_party_history(name)
    return {
        "name": name,
        "display_name": display_speaker(name),
        "party": party,
        "party_history": history,
        "totals": {
            "turns": totals["turns"], "meetings": totals["meetings"],
            "first": str(totals["first"]), "last": str(totals["last"]),
        },
        "by_committee": by_committee,
        "by_month": by_month,
        "utterance_types": utterance_types,
        "top_mentions": top_mentions,
        "recent_utterances": recent,
        "issue_stances": actor_issue_stances(variants),
    }
