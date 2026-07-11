"""POL-7 블라인드 라벨 시트 생성 — 사용자가 직접 5택을 매길 시트.

issue_stances(medical-reform) 의 turn 을 seed=42 로 40건 표본추출하되 **LLM 판정 stance 를
숨긴다**(블라인드). 상단에 build_issue_stance._SYSTEM 과 동일한 5택 정의(rubric)를 넣어
사람·LLM 이 같은 기준으로 판정하게 한다.

실행: python scripts/stance_label_sheet.py --issue medical-reform
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

# build_issue_stance._SYSTEM 의 5택 정의와 문구 일치 (사람·LLM 동일 기준)
_RUBRIC = [
    "- support: 쟁점의 정책·조치를 지지·찬성한다.",
    "- oppose: 반대·철회를 주장한다.",
    "- concern: 방향엔 동의하나 부작용·속도·방식에 우려를 표한다 (조건부).",
    "- neutral: 입장 없이 다룬다 (사실 확인·질의·중계).",
    "- none: 입장 판정 불가 (순수 절차·인사·다른 주제 경유).",
]


def sample_turns(rows: list, n: int = SAMPLE_N, seed: int = RNG_SEED) -> list:
    """seed 재현 가능 표본 최대 n 건 → turn_id(첫 원소) 정렬."""
    picked = rows if len(rows) <= n else random.Random(seed).sample(rows, n)
    return sorted(picked, key=lambda r: r[0])


def render_sheet(issue_id: str, picked: list, total: int) -> str:
    """블라인드 라벨 시트 마크다운. stance 는 출력하지 않는다."""
    lines = [
        f"# 입장 블라인드 라벨 — {issue_id}",
        "",
        "> 각 발언을 읽고 `입장:` 뒤에 support|oppose|concern|neutral|none 중 하나를 적으세요.",
        "> LLM 판정은 숨겨져 있습니다 (블라인드). 아래 5택 정의를 기준으로 판정하세요.",
        *[f"> {r}" for r in _RUBRIC],
        f"> 표본 {len(picked)} / 전체 {total}",
        "",
    ]
    for turn_id, speaker, role, date, text in picked:
        lines += [
            f"- `{turn_id}` ({date}) {speaker or ''} {role or ''}".rstrip(),
            "      입장: ",
            f"      {text}",
            "",
        ]
    return "\n".join(lines)


def fetch_stance_turns(issue_id: str) -> list:
    """issue_stances 의 turn — (turn_id, speaker, role, date, 500자 텍스트). stance 는 조회하되
    시트엔 넣지 않음(표본 모집단 = 판정된 turn)."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT s.turn_id, s.speaker, s.role, c.meeting_date::text,
                   left(string_agg(c.text, ' ' ORDER BY c.chunk_index), 500)
            FROM issue_stances s JOIN chunks c ON c.turn_id = s.turn_id
            WHERE s.issue_id = %s
            GROUP BY s.turn_id, s.speaker, s.role, c.meeting_date
        """, (issue_id,))
        return cur.fetchall()


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    init_pool()
    rows = fetch_stance_turns(args.issue)
    close_pool()
    if not rows:
        print(f"[FAIL] issue_stances 비어 있음: {args.issue} — POL-5 먼저 실행"); sys.exit(1)
    picked = sample_turns(rows)
    md = render_sheet(args.issue, picked, total=len(rows))
    out = ROOT / "data" / "issues" / f"stance_labels_{args.issue}.md"
    out.write_text(md, encoding="utf-8")
    print(f"저장: {out} — 표본 {len(picked)}/{len(rows)}. 사용자가 `입장:` 40건 기입 후 stance_eval.py 실행")


if __name__ == "__main__":
    main()
