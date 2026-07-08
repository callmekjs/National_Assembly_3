"""이슈 매핑 스팟체크 (POL-3 단계 3) — 정밀도 게이트의 재료를 만든다.

  1. 이슈당 무작위 10청크(seed 고정) 발췌 → 판독용 마크다운 (사람이 O/X 판독)
  2. anchor_meetings 포함 여부 — 재현율 참고 체크 (게이트 아님, 경보 신호)

게이트 (docs/issue_module_spec.md): 이슈 평균 정밀도 ≥90%.
미달 이슈는 description/시드 보정 후 build_issue_map.py --issue 재실행.

실행: python scripts/issue_spotcheck.py
"""

import io
import json
import random
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"
REPORT = ROOT / "data" / "issues" / "spotcheck_report.md"

SAMPLE_N = 10
RNG_SEED = 42


def sample_rows(rows: list, n: int = SAMPLE_N, seed: int = RNG_SEED) -> list:
    """seed 고정 무작위 표본 — 재실행해도 같은 표본 (enrichment_audit 패턴)."""
    if len(rows) <= n:
        return list(rows)
    return random.Random(seed).sample(rows, n)


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool, get_conn

    issues = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    init_pool()
    lines = ["# 이슈 매핑 스팟체크 (판독용)", "",
             "> 각 발언이 이슈와 '실질적으로' 관련 있으면 [O], 스치는 언급·무관이면 [X] 로 표기.",
             "> 게이트: 이슈 평균 정밀도 ≥90% — 미달 이슈는 시드 보정 후 --issue 재실행.", ""]
    with get_conn() as conn, conn.cursor() as cur:
        for issue in issues:
            iid = issue["issue_id"]
            cur.execute("SELECT chunk_id FROM issue_chunks WHERE issue_id = %s ORDER BY chunk_id",
                        (iid,))
            all_ids = [r[0] for r in cur.fetchall()]
            picked = sample_rows(all_ids)
            lines += [f"## {issue['title']} (`{iid}`) — 매핑 {len(all_ids)}청크, 표본 {len(picked)}", ""]
            if picked:
                cur.execute("""
                    SELECT c.chunk_id, co.name, c.meeting_date, c.speaker, c.role,
                           left(c.text, 400)
                    FROM chunks c JOIN committees co ON co.committee_id = c.committee_id
                    WHERE c.chunk_id = ANY(%s)
                    ORDER BY c.chunk_id
                """, (picked,))
                for cid, com, d, sp, role, text in cur.fetchall():
                    lines += [f"- [ ] `{cid}` ({com} {d}) {sp or ''} {role or ''}:",
                              f"      {text}", ""]
            # 앵커 회의 포함 여부 (재현율 경보)
            for src in issue.get("anchor_meetings", []):
                cur.execute("""
                    SELECT count(*) FROM issue_chunks ic
                    JOIN chunks c ON c.chunk_id = ic.chunk_id
                    WHERE ic.issue_id = %s AND c.source_id = %s
                """, (iid, src))
                n = cur.fetchone()[0]
                mark = "OK" if n > 0 else "**MISS**"
                lines += [f"- 앵커 {src}: {mark} ({n}청크)", ""]
    close_pool()
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {REPORT} — 사람 판독 후 정밀도 계산")


if __name__ == "__main__":
    main()
