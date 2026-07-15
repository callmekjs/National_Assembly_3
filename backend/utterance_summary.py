"""최근 발언 한 줄 요약 (의원 프로필) — LLM 1회 호출 + DB 영구 캐시.

코퍼스가 정적이라 발언 요약은 한 번 생성하면 영구 유효 — utterance_summaries 에
캐시한다. 전체 의원(320명)×3건을 전부 생성해도 총 ~$0.2 로 비용이 자체 상한이라
guard(비용 상한·rate limit) 개편 없이 안전. LLM 실패 시 빈 dict — 호출부가 원문
스니펫으로 폴백한다.

교훈 재사용 (POL-5): 배치 다항목 출력은 index-keyed 형식 필수 — 자유 서술은
항목 유실(실측 38%)을 낳는다.
"""
import logging
import re

from db import get_conn
from search_vector import _get_client

logger = logging.getLogger("uvicorn.error")

MODEL = "gpt-4o-mini"

_SYSTEM = (
    "국회 회의록 발언을 요약한다. 각 발언의 핵심 주장이나 요구를 40자 이내 한 문장으로, "
    "'~ 지적', '~ 촉구', '~ 설명' 같은 명사형 종결로 쓴다. 발언에 없는 내용을 지어내지 "
    "않는다. 반드시 발언 수만큼 '번호. 요약' 형식으로만 출력한다."
)

_ENSURED = False


def _ensure_table() -> None:
    """캐시 테이블 준비 — 운영 캐시라 스키마 재적재와 독립적으로 자가 생성."""
    global _ENSURED
    if _ENSURED:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS utterance_summaries (
                chunk_id   text PRIMARY KEY,
                summary    text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        conn.commit()
    _ENSURED = True


def parse_indexed(text: str, n: int) -> dict[int, str]:
    """'1. 요약' / '2) 요약' 줄들 → {번호: 요약}. 범위 밖 번호·빈 요약은 버린다."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
        if not m:
            continue
        idx, summary = int(m.group(1)), m.group(2).strip()
        if 1 <= idx <= n and summary:
            out[idx] = summary
    return out


def summarize_utterances(items: list[dict]) -> dict[str, str]:
    """[{chunk_id, text}] → {chunk_id: 한 줄 요약}. 캐시 우선, 미스만 LLM 1회.

    어떤 실패든 확보된 것만 반환 — 요약은 부가 정보라 프로필 응답을 막지 않는다.
    """
    if not items:
        return {}
    try:
        _ensure_table()
        ids = [it["chunk_id"] for it in items]
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_id, summary FROM utterance_summaries WHERE chunk_id = ANY(%s)",
                (ids,),
            )
            cached = dict(cur.fetchall())

        missing = [it for it in items if it["chunk_id"] not in cached]
        if not missing:
            return cached

        numbered = "\n\n".join(
            f"{i + 1}. {it['text']}" for i, it in enumerate(missing)
        )
        resp = _get_client().chat.completions.create(
            model=MODEL,
            temperature=0.2,
            max_tokens=80 * len(missing),
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": numbered},
            ],
        )
        parsed = parse_indexed(resp.choices[0].message.content or "", len(missing))

        fresh = {
            missing[i - 1]["chunk_id"]: summary for i, summary in parsed.items()
        }
        if fresh:
            with get_conn() as conn, conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO utterance_summaries (chunk_id, summary) VALUES (%s, %s) "
                    "ON CONFLICT (chunk_id) DO NOTHING",
                    list(fresh.items()),
                )
                conn.commit()
        return {**cached, **fresh}
    except Exception:
        logger.warning("최근 발언 요약 실패 — 원문 스니펫으로 폴백", exc_info=True)
        return {}
