"""
enrichment 실태 조사 (POL-1) — ETL-5 필드의 커버리지·분포 통계 + 스팟체크 샘플 추출.

배경: policy_enricher_v1 (rule-based) 이 만든 5개 필드는 생성 후 한 번도 검증 없이
쌓여 있다. 3단계 분석(쟁점·입장·행위자)의 재료가 되는지 실태부터 확인한다
("게이트의 게이트" 교훈 — 검증 안 된 데이터 위에 분석을 쌓지 않는다).

산출: data/v1/reports/enrichment_audit_{ts}.json
  - stats: 필드별 분포·커버리지 (SQL)
  - samples: 필드별 무작위 샘플 (Claude 가 원문 대조 판독하는 스팟체크 입력)
    · 추출 정확도(precision) 샘플: 값이 있는 청크 25건
    · 누락(recall) 참고 샘플: bill_refs·mentions 는 빈 청크 10건 추가

실행: python scripts/enrichment_audit.py
"""

import io
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from db import init_pool, close_pool, get_conn  # noqa: E402

OUT_DIR = Path(__file__).parent.parent / "data" / "v1" / "reports"
SAMPLE_N = 25
EMPTY_SAMPLE_N = 10
SEED = 20260703  # 재현 가능한 샘플링


def rows(cur, sql, params=None):
    cur.execute(sql, params or [])
    return cur.fetchall()


def main():
    init_pool()
    report = {"audit_version": "pol1_v1", "created_at": datetime.now().isoformat(),
              "seed": SEED, "stats": {}, "samples": {}}

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT setseed(0.20260703)")
        cur.execute("SELECT count(*) FROM chunks")
        total = cur.fetchone()[0]
        report["stats"]["total_chunks"] = total

        # ── policy_domain: 분포 + committee 와의 정보 중복 검사 ──────────────
        report["stats"]["policy_domain"] = {
            "distribution": dict(rows(cur, "SELECT policy_domain, count(*) FROM chunks GROUP BY 1 ORDER BY 2 DESC")),
            "distinct_committee_domain_pairs": rows(cur, """
                SELECT count(*) FROM (SELECT DISTINCT committee_id, policy_domain FROM chunks) t
            """)[0][0],
            "distinct_committees": rows(cur, "SELECT count(DISTINCT committee_id) FROM chunks")[0][0],
        }

        # ── utterance_type / stance_signals: 분포 ───────────────────────────
        for field in ("utterance_type", "stance_signals"):
            report["stats"][field] = {
                "distribution": dict(rows(cur, f"SELECT {field}, count(*) FROM chunks GROUP BY 1 ORDER BY 2 DESC")),
            }

        # ── bill_refs / mentions: 커버리지 + 최빈값 ──────────────────────────
        for field, top_n in (("bill_refs", 30), ("mentions", 20)):
            nonempty = rows(cur, f"SELECT count(*) FROM chunks WHERE jsonb_array_length({field}) > 0")[0][0]
            top = rows(cur, f"""
                SELECT v, count(*) FROM chunks, jsonb_array_elements_text({field}) AS v
                GROUP BY v ORDER BY count(*) DESC LIMIT {top_n}
            """)
            report["stats"][field] = {
                "coverage": round(nonempty / total, 4),
                "nonempty_chunks": nonempty,
                "top_values": [[v, c] for v, c in top],
            }

        # ── 스팟체크 샘플 (원문 앞 400자 + 필드값) ───────────────────────────
        def sample(where, n):
            return rows(cur, f"""
                SELECT chunk_id, speaker, role, left(text, 400),
                       policy_domain, bill_refs, utterance_type, stance_signals, mentions
                FROM chunks WHERE {where} ORDER BY random() LIMIT {n}
            """)

        def pack(r):
            return {"chunk_id": r[0], "speaker": r[1], "role": r[2], "text": r[3],
                    "policy_domain": r[4], "bill_refs": r[5],
                    "utterance_type": r[6], "stance_signals": r[7], "mentions": r[8]}

        # utterance_type·stance 는 전 청크가 값을 가지므로 일반 무작위 (is_short 제외 —
        # "예." 같은 의사진행 발언은 stance 판정 대상이 아님)
        report["samples"]["utterance_stance"] = [pack(r) for r in sample("NOT is_short", SAMPLE_N)]
        report["samples"]["bill_refs_nonempty"] = [pack(r) for r in sample("jsonb_array_length(bill_refs) > 0", SAMPLE_N)]
        report["samples"]["bill_refs_empty"] = [pack(r) for r in sample("jsonb_array_length(bill_refs) = 0 AND NOT is_short", EMPTY_SAMPLE_N)]
        report["samples"]["mentions_nonempty"] = [pack(r) for r in sample("jsonb_array_length(mentions) > 0", SAMPLE_N)]
        report["samples"]["mentions_empty"] = [pack(r) for r in sample("jsonb_array_length(mentions) = 0 AND NOT is_short", EMPTY_SAMPLE_N)]

    close_pool()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"enrichment_audit_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"총 청크: {total:,}")
    print(f"policy_domain 분포: {report['stats']['policy_domain']['distribution']}")
    print(f"  위원회-도메인 조합 수: {report['stats']['policy_domain']['distinct_committee_domain_pairs']}"
          f" (위원회 수 {report['stats']['policy_domain']['distinct_committees']} 와 같으면 1:1 중복)")
    print(f"utterance_type: {report['stats']['utterance_type']['distribution']}")
    print(f"stance_signals: {report['stats']['stance_signals']['distribution']}")
    print(f"bill_refs 커버리지: {report['stats']['bill_refs']['coverage']:.1%}, top5: {report['stats']['bill_refs']['top_values'][:5]}")
    print(f"mentions 커버리지: {report['stats']['mentions']['coverage']:.1%}, top5: {report['stats']['mentions']['top_values'][:5]}")
    print(f"\n리포트: {out}")


if __name__ == "__main__":
    main()
