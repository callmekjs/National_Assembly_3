"""POL-5 입장 판정 스팟체크 — 판독용 리포트 (게이트·POL-7 시작점).

입장은 5택·주관적이라 하드 게이트(≥90%)는 비현실적. 무작위 N개 판정을 발언 원문과
대조 판독하는 리포트를 만들어 일치도 기준선을 측정한다(사람 판독 후 계산).

실행: python scripts/issue_stance_spotcheck.py --issue medical-reform
"""
import argparse
import io
import random
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SAMPLE_N = 40
RNG_SEED = 42


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool, get_conn
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    init_pool()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, s.stance, c.meeting_date::text,
                   left(string_agg(c.text, ' ' ORDER BY c.chunk_index), 500)
            FROM issue_stances s JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, s.stance, c.meeting_date
        """, (args.issue,))
        allrows = cur.fetchall()
    close_pool()
    picked = allrows if len(allrows) <= SAMPLE_N else random.Random(RNG_SEED).sample(allrows, SAMPLE_N)
    picked.sort(key=lambda r: r[0])
    lines = [f"# 입장 판정 스팟체크 — {args.issue} (판독용)", "",
             "> 판정이 맞으면 [O], 틀리면 [X] 로 표기하고 옳은 입장을 적어라.",
             "> 입장: support(찬성)/oppose(반대)/concern(우려)/neutral(중립)/none(입장없음)",
             f"> 표본 {len(picked)} / 전체 {len(allrows)}", ""]
    for turn_id, sp, role, stance, date, text in picked:
        lines += [f"- [ ] `{turn_id}` ({date}) {sp or ''} {role or ''} — 판정: **{stance}**",
                  f"      {text}", ""]
    out = ROOT / "data" / "issues" / f"stance_spotcheck_{args.issue}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {out} — 사람 판독 후 일치도 계산")


if __name__ == "__main__":
    main()
