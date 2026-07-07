"""운영 품질 리포트 (A+ 로드맵 기준 6·7) — query_logs 집계.

출력 3부:
    1. 기간 요약 — grounding 분포, 지연 p50/p95, 비용 합계, 피드백
    2. 일별 추이 — 질의 수·비용·평균 지연
    3. 검토 큐 — PARTIAL+무인용(ungrounded 후보)·invalid_citations 행:
       거절 문구 신종 변형·프롬프트 위반의 발굴 재료 (grounding.py 운영 루프)

실행:
    python scripts/quality_report.py            # 최근 7일
    python scripts/quality_report.py --days 30
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from dotenv import load_dotenv

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).parent.parent / ".env")


def build_report(days: int) -> dict:
    from db import get_conn
    from psycopg2.extras import RealDictCursor

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE grounding = 'FULL')    AS full_n,
                   count(*) FILTER (WHERE grounding = 'PARTIAL') AS partial_n,
                   count(*) FILTER (WHERE grounding = 'REFUSED') AS refused_n,
                   count(*) FILTER (WHERE grounding = 'NONE')    AS none_n,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)  AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                   sum((usage ->> 'est_cost_usd')::float)  AS cost_usd,
                   count(rating) AS rated, avg(rating) AS avg_rating
            FROM query_logs
            WHERE created_at >= now() - make_interval(days => %s)
            """,
            (days,),
        )
        summary = dict(cur.fetchone())

        cur.execute(
            """
            SELECT created_at::date AS day, count(*) AS n,
                   round(avg(latency_ms)) AS avg_ms,
                   round(sum((usage ->> 'est_cost_usd')::float)::numeric, 4) AS cost_usd
            FROM query_logs
            WHERE created_at >= now() - make_interval(days => %s)
            GROUP BY 1 ORDER BY 1
            """,
            (days,),
        )
        daily = [dict(r) for r in cur.fetchall()]

        # 검토 큐: 판정 신뢰가 흔들리는 행 — 거절 문구 신종 변형·프롬프트 위반 후보
        cur.execute(
            """
            SELECT query_id, created_at::date AS day, grounding, question,
                   left(answer, 120) AS answer_head, invalid_citations
            FROM query_logs
            WHERE created_at >= now() - make_interval(days => %s)
              AND (
                    (grounding = 'PARTIAL' AND citations = '[]'::jsonb)
                    OR invalid_citations <> '[]'::jsonb
                  )
            ORDER BY created_at DESC LIMIT 50
            """,
            (days,),
        )
        review_queue = [dict(r) for r in cur.fetchall()]

    return {"summary": summary, "daily": daily, "review_queue": review_queue}


def main() -> None:
    days = 7
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    import db

    db.init_pool()
    r = build_report(days)
    s = r["summary"]

    print(f"=== 최근 {days}일 품질 리포트 ===")
    n = s["n"] or 0
    if n == 0:
        print("질의 없음")
        return
    print(f"질의 {n}건 — FULL {s['full_n']} / PARTIAL {s['partial_n']} / "
          f"REFUSED {s['refused_n']} / NONE {s['none_n']}")
    print(f"지연 p50 {s['p50']:.0f}ms / p95 {s['p95']:.0f}ms")
    cost = s["cost_usd"] or 0.0
    print(f"LLM 비용 합계 ${cost:.4f} (사전차단분 제외)")
    if s["rated"]:
        print(f"피드백 {s['rated']}건, 평균 {float(s['avg_rating']):.2f}/5")

    print("\n--- 일별 ---")
    for d in r["daily"]:
        print(f"  {d['day']}  {d['n']:>4}건  평균 {d['avg_ms']}ms  ${d['cost_usd'] or 0}")

    q = r["review_queue"]
    print(f"\n--- 검토 큐 (무인용 PARTIAL·invalid citation) — {len(q)}건 ---")
    for row in q[:10]:
        print(f"  [{row['day']} {row['grounding']}] {row['question'][:40]}")
        print(f"    → {row['answer_head']}")
    if len(q) > 10:
        print(f"  ... 외 {len(q) - 10}건")


if __name__ == "__main__":
    main()
