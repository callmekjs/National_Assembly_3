"""
키워드 검색 (RAG-2).

방식: pg_trgm GIN 인덱스 + ILIKE 부분 문자열 매칭
  - 결정 근거 (2026-07-02 실측): FTS(simple)는 한국어 조사 붙은 형태를 놓침
    ("티메프" 99건 중 78건만, 21% 손실). 부분 문자열은 조사 무관 + 인덱스 후 2~13ms.
  - 마스터 문서 3-5: 주요 토큰은 OR 조건 (AND-only 는 0건 위험)

점수:
  - 발언자 이름 일치: +3  (인물 질문 대응)
  - 역할(직책) 일치:  +2  ("경찰청장 직무대행" 같은 직책 질문 대응)
  - 전체 구문 일치:   +2  ("AI 기본법" 이 통째로 있으면 가산)
  - 개별 토큰 일치:   +1  (토큰·별칭당)
"""

from psycopg2.extras import RealDictCursor

from aliases import expand_aliases
from db import get_conn
from query_parser import content_tokens

MAX_TERMS = 8  # 토큰 폭발 방지


def _like_escape(term: str) -> str:
    """ILIKE 패턴 특수문자 이스케이프 — 질문 속 "50%" 의 % 가 와일드카드로 해석돼
    "50" 포함 전부와 매칭(점수 오염)되는 것 방지. Postgres 기본 ESCAPE 는 백슬래시."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pat(term: str) -> str:
    """검색어 → 부분일치 ILIKE 패턴 (내용은 이스케이프, 양끝 % 만 와일드카드)."""
    return f"%{_like_escape(term)}%"


def _terms_from_query(q: str) -> tuple[list[str], list[str]]:
    """
    질문 → (구문 후보, 토큰 후보). 각 후보는 별칭으로 확장된다.

    토큰화는 content_tokens() 사용 (2026-07-02 개선):
    조사 제거 + 불용어 필터 — "정부의", "반응은" 같은 변별력 없는 토큰이
    점수를 오염시키던 문제 해결 (eval 실측으로 발견).
    """
    q = q.strip()
    tokens: list[str] = []
    for tok in content_tokens(q):
        tokens.extend(expand_aliases(tok))

    phrases: list[str] = []
    if " " in q:                      # 여러 단어면 전체 구문도 후보
        phrases.extend(expand_aliases(q))

    # 중복 제거 + 상한
    seen = set()
    uniq_tokens = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            uniq_tokens.append(t)
    return phrases[:2], uniq_tokens[:MAX_TERMS]


def keyword_search(
    q: str,
    committee: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """키워드 검색. 점수 내림차순 결과 반환 (RAG-4 하이브리드의 한 축).

    committee 는 약칭 하나 또는 목록 — 복수 위원회 질문(2026-07-03) 대응.
    """
    phrases, tokens = _terms_from_query(q)
    if not phrases and not tokens:
        return []

    score_parts, match_conds, params = [], [], []

    # 주의: NULL 컬럼에서 `NULL ILIKE x` 는 NULL → 합계 전체가 NULL 이 되고
    #       ORDER BY DESC 에서 NULL 이 맨 위로 올라간다. 반드시 COALESCE 로 0 처리.
    for ph in phrases:                              # 구문 일치 +2
        score_parts.append("COALESCE((ch.text ILIKE %s)::int, 0) * 2")
        params.append(_pat(ph))
    for tok in tokens:                              # 토큰 일치 +1
        score_parts.append("COALESCE((ch.text ILIKE %s)::int, 0)")
        params.append(_pat(tok))
    for tok in tokens:                              # 발언자 일치 +3
        score_parts.append("COALESCE((ch.speaker ILIKE %s)::int, 0) * 3")
        params.append(_pat(tok))
    for tok in tokens:                              # 역할(직책) 일치 +2
        score_parts.append("COALESCE((ch.role ILIKE %s)::int, 0) * 2")
        params.append(_pat(tok))

    score_sql = " + ".join(score_parts)

    # WHERE: 토큰·구문 중 하나라도 본문·발언자·역할에 존재 (OR — 마스터 3-5)
    for term in phrases + tokens:
        match_conds.append("ch.text ILIKE %s")
        params.append(_pat(term))
    for tok in tokens:
        match_conds.append("ch.speaker ILIKE %s")
        params.append(_pat(tok))
    for tok in tokens:
        match_conds.append("ch.role ILIKE %s")
        params.append(_pat(tok))
    where = ["(" + " OR ".join(match_conds) + ")"]

    if committee:
        where.append("co.name = ANY(%s)")
        params.append([committee] if isinstance(committee, str) else list(committee))
    if date_from:
        where.append("ch.meeting_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("ch.meeting_date <= %s")
        params.append(date_to)

    sql = f"""
        SELECT ch.chunk_id, ch.source_id, ch.speaker, ch.role,
               co.name AS committee, ch.meeting_date,
               ch.page_start, ch.is_short,
               left(ch.text, 200) AS snippet,
               ({score_sql}) AS score
        FROM chunks ch
        JOIN committees co ON co.committee_id = ch.committee_id
        WHERE {" AND ".join(where)}
        ORDER BY score DESC, ch.meeting_date DESC
        LIMIT %s
    """
    params.append(limit)

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
