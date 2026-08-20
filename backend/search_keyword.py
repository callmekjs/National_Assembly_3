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
  - 개별 토큰 일치:   +IDF 가중치 0.25~3.0 (토큰·별칭당, 희소할수록 큼 — 2026-08-07)
    이전에는 일률 +1 이었고, 그래서 "정부"(7.30%)와 "티메프"(0.02%)가 같은 값이었다.
    아래 token_weight 주석 참조.
"""

import json
import math
import os
import re
from pathlib import Path

from psycopg2.extras import RealDictCursor

from aliases import expand_aliases
from db import get_conn
from query_parser import content_tokens

MAX_TERMS = 8  # 토큰 폭발 방지

_LAW_TOKEN = re.compile(r"(?:법|법안|법률안|개정안)$")


def _legal_phrases(q: str) -> list[str]:
    """질문에서 법률명으로 보이는 2~5어절 구문을 검색 앵커로 뽑는다."""
    tokens = content_tokens(q)
    phrases: list[str] = []
    for index, token in enumerate(tokens):
        if not _LAW_TOKEN.search(token):
            continue
        start = max(0, index - 4)
        for size in range(min(5, index - start + 1), 1, -1):
            phrase = " ".join(tokens[index - size + 1:index + 1])
            if phrase not in phrases:
                phrases.append(phrase)
        if len(phrases) >= 2:
            break
    return phrases[:2]

# ── IDF 가중치 (2026-08-07) ────────────────────────────────────────────────
# 없을 때 생기던 문제: 점수가 "토큰 하나 맞으면 +1" 이라 모든 단어가 같은 값이었다.
# 코퍼스에서 "정부"는 30,696건(7.30%), "티메프"는 99건(0.02%) — 310배 차이인데
# 둘 다 +1 이었다. 그래서 "티메프 사태의 정부 책임 범위를 두고 여당과 야당의 시각은"
# 질문에서 '사태·정부·책임·여당·야당' 5개를 가진 **국방위 안보 발언이 1위**가 되고
# '티메프' 하나만 가진 진짜 근거를 눌렀다 (상위 30건 중 티메프 포함 6건).
# 비교 질문이 모델 3종에서 모두 0/3 이던 원인 — 생성이 아니라 검색이었다.
_DF_PATH = Path(__file__).parent.parent / "data" / "token_df.json"
_IDF_MIN, _IDF_MAX = 0.25, 3.0   # 가중치 상·하한 — 한 토큰이 순위를 독점하지 않게
_df_cache: dict | None = None


def _df_table() -> dict:
    """표가 없으면 빈 표 — 그 경우 모든 토큰이 같은 가중치를 받아 옛 동작과 같아진다
    (전부 3.0 은 전부 1.0 과 순위가 동일하다).

    KEYWORD_IDF=0 으로 끌 수 있다. A/B 측정용 — 숨김용 평가셋에서 이 수정의 효과만
    떼어 재려면 껐다 켤 수 있어야 한다."""
    global _df_cache
    if _df_cache is None:
        if os.environ.get("KEYWORD_IDF") == "0":
            _df_cache = {"total": 0, "df": {}}
        else:
            try:
                _df_cache = json.loads(_DF_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _df_cache = {"total": 0, "df": {}}
    return _df_cache


def token_weight(term: str) -> float:
    """토큰의 IDF 가중치. 표에 없으면 드문 토큰으로 보고 최대 가중치.

    드묾을 기본값으로 두는 이유: 표는 상위 빈도 토큰만 담는다. 흔한데 빠질 확률은
    낮고(흔할수록 표본에 잡힌다), 반대로 드문 토큰을 흔하다고 잘못 깎으면 고유명사
    질문이 무너진다 — 이 도메인에서 가장 흔한 질문 유형이다.
    """
    t = _df_table()
    total, df = t.get("total") or 0, t.get("df") or {}
    n = df.get(term)
    if not total or n is None:
        return _IDF_MAX
    # log(전체/문서수) 를 [_IDF_MIN, _IDF_MAX] 로 자른다. 0.02% → 상한, 7% → 하한 근처.
    idf = math.log(total / max(n, 1))
    return max(_IDF_MIN, min(_IDF_MAX, idf / 3.0))


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

    phrases = _legal_phrases(q)
    if not phrases and " " in q:      # 법률명이 없을 때만 전체 구문을 후보로 사용
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
    for tok in tokens:                              # 토큰 일치 — IDF 가중 (희소할수록 큼)
        # 가중치는 코드가 계산한 float 이라 리터럴로 넣어도 주입 위험이 없다.
        # (%s 로 바인딩하면 psycopg2 가 numeric 으로 넘겨 곱셈 타입이 꼬인다)
        score_parts.append(f"COALESCE((ch.text ILIKE %s)::int, 0) * {token_weight(tok):.4f}")
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
               -- 600자: 리랭커가 보는 길이(reranker._MAX_DOC_CHARS 와 같아야 함).
               -- 1200자로 늘려봤으나 nDCG@5 가 0.710→0.653 으로 떨어지고 비용은 1.6배가 돼
               -- 되돌렸다 (2026-08-06 실측). 후보 30개를 한 번에 주는 listwise 방식이라
               -- 후보당 길이를 늘리면 총 입력이 커져 판단이 흐려지는 것으로 보인다.
               -- 주의: 이 주석에 퍼센트 기호를 쓰지 말 것 — psycopg2 가 SQL 문자열 안의
               -- 그 기호를 파라미터 자리표시자로 해석해 IndexError 가 난다 (여기서 겪음).
               left(ch.text, 600) AS snippet,
               ({score_sql}) AS score
        FROM chunks ch
        JOIN committees co ON co.committee_id = ch.committee_id
        WHERE {" AND ".join(where)}
        -- chunk_id 타이브레이커 필수 (감사 2026-08-05): 토큰 하나만 맞은 행이 대량으로
        -- 같은 점수를 갖는데(IDF 가중 후에도 같은 토큰이면 같은 값), 동점 구간의 행
        -- 순서는 Postgres 가 보장하지 않는다 → 같은 질문·같은 데이터인데 실행마다
        -- 상위 K 가 바뀌고, 그 순위가 그대로
        -- RRF 입력이 되어 하이브리드 결과까지 흔들렸다 (평가 재현성의 원인 ②).
        -- 별칭 순서 고정(aliases.expand_aliases)과 짝을 이루는 수정 — 둘 다 있어야
        -- 검색이 결정적이 된다.
        ORDER BY score DESC, ch.meeting_date DESC, ch.chunk_id DESC
        LIMIT %s
    """
    params.append(limit)

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()
