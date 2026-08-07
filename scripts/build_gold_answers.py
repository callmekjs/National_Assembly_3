"""
정답지(모범답안) 생성 — 평가셋 문항의 근거를 코퍼스에서 직접 훑어 종합한다.

**핵심 원칙: 시스템의 검색(hybrid_search)을 쓰지 않는다.**
    시스템 검색으로 근거를 모으면, 시스템이 못 찾은 근거는 정답지에도 없게 된다.
    그러면 채점은 "시스템이 자기가 찾은 것을 잘 요약했나"가 되고, 검색 실패는
    영원히 안 보인다 — 옛 retrieval_eval 의 R@5 0.983 과 같은 동어반복이다.
    여기서는 단순 SQL 로 **넓게** 훑고, 관련도로 줄 세워 상위를 취한다.

수집 설계 (과거에 겪은 실패를 반영)
    - `ORDER BY` 없이 `LIMIT` 을 걸면 물리적 순서(=이른 날짜)만 걸린다.
      2026-08-06 에 이 때문에 7문항이 "근거부족"으로 잘못 판정됐다 →
      키워드 적중 수 + 길이로 정렬한다.
    - 날짜를 가리지 않고 전 구간에서 모은다. 질문이 경과를 묻지 않아도, 같은 사안이
      여러 회의에 걸쳐 논의되므로 한 날짜만 보면 반쪽이 된다.
    - 발언자의 정당·위원회·날짜를 함께 싣는다 — 채점 기준이 이걸 요구한다.

산출: {id, q, type, pass_criteria, answer, dates, sources[...]}
    `answer` 는 LLM 종합 초안이다. **사람 검토를 거쳐야 정답지가 된다.**

실행
    python scripts/build_gold_answers.py data/eval/heldout_questions.json \
        --out data/eval/heldout_gold.json
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

SYNTH_MODEL = "gpt-5.6-sol"   # 정답지는 평가 대상보다 좋은 모델로 — 채점 기준이 되므로
MAX_EVIDENCE = 40             # 종합에 넣을 근거 수
MIN_LEN = 80                  # 이보다 짧은 발언은 의사진행·호명일 확률이 높다

SYSTEM = """당신은 국회 회의록에서 근거를 종합해 **모범답안**을 쓰는 조사관이다.
이 답안은 다른 답변을 채점하는 기준이 되므로, 정확성이 최우선이다.

규칙:
- 주어진 근거 발언들만 사용한다. 근거에 없는 사실·수치·인물을 만들지 않는다.
- 질문에 답하는 데 필요한 내용을 **날짜를 가리지 않고 종합**한다. 여러 회의에
  걸친 논의라면 전체를 아울러 정리한다.
- 발언자 이름과 정당을 정확히 쓴다. 근거에 적힌 표기를 그대로 따른다.
- 비교를 묻는 질문이면 양쪽을 **각각** 정리하고, 무엇이 어떻게 다른지 명시한다.
  한쪽 근거만 있으면 "○○ 측 발언은 확인되지 않는다"고 정직하게 적는다.
- 발언의 **취지를 뒤집지 않는다**. 화자가 A를 주장했으면 A로 적는다.
- 아직 결정되지 않은 것을 결정된 것처럼 쓰지 않는다
  ("감액 의견이 제기됐다" ≠ "감액했다").
- 1200~2500자. 서론·총평 없이 내용만.

반드시 아래 JSON 만 출력:
{"answer":"모범답안 본문","key_speakers":["이름(정당)", ...]}"""


def collect(cur, terms: list[str], committee: str | None, limit: int,
            parties: list[str] | None = None) -> list[dict]:
    """키워드 적중 수로 줄 세워 근거를 모은다. 날짜 제한 없음.

    parties 를 주면 그 정당 소속 발언만 — 진영별로 나눠 담아 쏠림을 막는 용도."""
    score = " + ".join(["COALESCE((ch.text ILIKE %s)::int, 0)"] * len(terms))
    params = [f"%{t}%" for t in terms]                      # 점수용
    where = ["(" + " OR ".join(["ch.text ILIKE %s"] * len(terms)) + ")"]
    params += [f"%{t}%" for t in terms]                     # 조건용
    params.append(MIN_LEN)
    where.append("length(ch.text) >= %s")
    if committee:
        where.append("co.name = %s")
        params.append(committee)
    if parties:
        where.append("m.party = ANY(%s)")
        params.append(list(parties))
    params.append(limit)
    cur.execute(f"""
        SELECT ch.chunk_id, ch.speaker, ch.role, co.name AS committee,
               ch.meeting_date, m.party, ch.text,
               ({score}) AS hits
        FROM chunks ch
        JOIN committees co ON co.committee_id = ch.committee_id
        LEFT JOIN members m ON m.name = ch.speaker
        WHERE {" AND ".join(where)}
        -- 적중 수 우선, 그다음 길이. chunk_id 로 동점을 고정해 재현 가능하게 한다.
        ORDER BY hits DESC, length(ch.text) DESC, ch.chunk_id
        LIMIT %s
    """, params)
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions", help="질문 정의 JSON")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from psycopg2.extras import RealDictCursor

    from db import close_pool, get_conn, init_pool
    from search_vector import _get_client

    qs = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    if args.limit:
        qs = qs[: args.limit]

    init_pool()
    client = _get_client()
    out, t0 = [], time.time()

    for i, q in enumerate(qs, 1):
        # 쏠림 방지 — 적중 수로만 줄 세우면 발언량이 많은 쪽이 전부를 차지한다.
        # 2026-08-07 실측: 계엄 질문에서 야당 28건 : 여당 1건이 잡혀 "여당 입장은
        # 확인 안 됨"이 정답이 될 뻔했다. 비교 질문의 정답지로는 못 쓴다.
        # 비교 축(위원회 또는 진영)마다 따로 담아 균형을 강제한다.
        buckets = q.get("committees") or [q.get("committee")]
        parties = q.get("party_buckets")     # 여야 대비 질문이면 진영 목록
        rows = []
        with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            if parties:
                per = max(MAX_EVIDENCE // len(parties), 8)
                for grp in parties:
                    rows += collect(cur, q["terms"], buckets[0], per, grp)
            else:
                per = max(MAX_EVIDENCE // max(len(buckets), 1), 8)
                for com in buckets:
                    rows += collect(cur, q["terms"], com, per)

        srcs = [{"date": str(r["meeting_date"]), "speaker": r["speaker"],
                 "role": r["role"], "party": r["party"], "committee": r["committee"],
                 "text": r["text"][:900]} for r in rows]
        block = "\n\n".join(
            f"({s['committee']} {s['date']}) {s['speaker']} "
            f"[{s['party'] or s['role'] or '-'}]: {s['text']}" for s in srcs)

        crits = "\n".join(f"  {j}. {c}" for j, c in enumerate(q["pass_criteria"], 1))
        try:
            resp = client.chat.completions.create(
                model=SYNTH_MODEL, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content":
                           f"[질문]\n{q['q']}\n\n[이 답안이 만족해야 할 기준]\n{crits}"
                           f"\n\n[근거 발언 {len(srcs)}건]\n{block}"}])
            v = json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"[{i}/{len(qs)}] {q['id']} 실패: {type(e).__name__}: {e}")
            continue

        dates = sorted({s["date"] for s in srcs})
        out.append({**q, "answer": v.get("answer", ""),
                    "key_speakers": v.get("key_speakers", []),
                    "n_sources": len(srcs), "dates": dates, "sources": srcs})
        coms = {}
        for s in srcs:
            coms[s["committee"]] = coms.get(s["committee"], 0) + 1
        print(f"[{i}/{len(qs)}] {q['id']:<5} 근거 {len(srcs):>2}건 "
              f"({', '.join(f'{k} {v}' for k, v in coms.items())}) "
              f"날짜 {len(dates)}개 → 답안 {len(v.get('answer', ''))}자")

    close_pool()
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(out)}건 저장: {args.out}  ({time.time() - t0:.0f}초)")
    print("※ 이 답안은 LLM 종합 초안이다 — 사람 검토를 거쳐야 정답지가 된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
