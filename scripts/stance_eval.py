"""POL-7 입장 판정 eval — 블라인드 사람 라벨 vs LLM 판정 일치도.

순수 로직(parse_label_sheet·agreement)은 DB·LLM 없이 테스트 가능.
CLI 는 라벨 파일 파싱 → issue_stances 조회 → 일치도·혼동행렬 → JSON·리포트 저장.

실행:
  python scripts/stance_eval.py --issue medical-reform
"""
import argparse
import io
import json
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

STANCES = ("support", "oppose", "concern", "neutral", "none")
_TURN_RE = re.compile(r"`([^`]+)`")
_LABEL_RE = re.compile(r"입장:\s*([A-Za-z]+)")
_ITEM_RE = re.compile(r"^- `([^`]+)`", re.MULTILINE)   # 항목 불릿만 — 안내문 백틱 제외


def parse_label_sheet(text: str) -> dict[str, str]:
    """라벨 파일 → {turn_id: stance}. 백틱 turn_id 줄 뒤 `입장: <토큰>` 줄을 짝짓는다.
    빈칸·허용밖 토큰은 제외(경고는 호출측에서 시트 항목 수와 비교해 판단)."""
    labels: dict[str, str] = {}
    cur: str | None = None
    for line in text.splitlines():
        m = _TURN_RE.search(line)
        if m:
            cur = m.group(1)
            continue
        if "입장:" in line and cur is not None:
            lm = _LABEL_RE.search(line)
            if lm:
                tok = lm.group(1).lower()
                if tok in STANCES and cur not in labels:
                    labels[cur] = tok
            cur = None  # 입장: 줄을 소비 — 빈칸이어도 다음 turn_id 까지 대기
    return labels


def agreement(human: dict[str, str], llm: dict[str, str]) -> dict:
    """공통 turn_id 에서 일치율 + 혼동행렬(사람→LLM) + 불일치 목록. 공통 0건이면 방어."""
    common = sorted(set(human) & set(llm))
    matrix = {h: {c: 0 for c in STANCES} for h in STANCES}
    disagreements = []
    agree = 0
    for t in common:
        h, m = human[t], llm[t]
        if h in matrix and m in matrix[h]:
            matrix[h][m] += 1
        if h == m:
            agree += 1
        else:
            disagreements.append({"turn_id": t, "human": h, "llm": m})
    n = len(common)
    return {
        "n": n,
        "agreement": round(agree / n, 3) if n else None,
        "matrix": matrix,
        "disagreements": disagreements,
    }


def fetch_llm_stances(issue_id: str) -> dict[str, str]:
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT turn_id, stance FROM issue_stances WHERE issue_id = %s", (issue_id,))
        return {t: s for t, s in cur.fetchall()}


def write_outputs(issue_id, human, result, out_json, out_md) -> None:
    """eval 자산 JSON(재실행용) + 사람 판독 리포트 md 저장."""
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(
        {"issue_id": issue_id, "rng_seed": 42, "labels": human},
        ensure_ascii=False, indent=1), encoding="utf-8")

    agr = result["agreement"]
    lines = [f"# 입장 판정 eval — {issue_id}", "",
             f"- 공통 {result['n']}건, **일치율 {agr if agr is not None else 'N/A'}** (하드 게이트 없음, 기준선)",
             "", "## 혼동행렬 (행=사람, 열=LLM)", "",
             "| 사람\\LLM | " + " | ".join(STANCES) + " |",
             "|" + "---|" * (len(STANCES) + 1)]
    for h in STANCES:
        lines.append(f"| {h} | " + " | ".join(str(result['matrix'][h][c]) for c in STANCES) + " |")
    lines += ["", f"## 불일치 {len(result['disagreements'])}건", ""]
    for d in result["disagreements"]:
        lines.append(f"- `{d['turn_id']}` 사람={d['human']} / LLM={d['llm']}")
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    from db import init_pool, close_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True)
    args = ap.parse_args()
    root = Path(__file__).parent.parent
    labels_path = root / "data" / "issues" / f"stance_labels_{args.issue}.md"
    if not labels_path.exists():
        print(f"[FAIL] 라벨 파일 없음: {labels_path} — stance_label_sheet.py 먼저 실행"); sys.exit(1)
    sheet_text = labels_path.read_text(encoding="utf-8")
    human = parse_label_sheet(sheet_text)
    if not human:
        print(f"[FAIL] 기입된 라벨 0건 — `입장:` 뒤에 5택을 기입하세요"); sys.exit(1)

    init_pool()
    llm = fetch_llm_stances(args.issue)
    close_pool()

    result = agreement(human, llm)
    out_json = root / "data" / "eval" / f"stance_eval_{args.issue}.json"
    out_md = root / "data" / "issues" / "stance_eval_report.md"
    write_outputs(args.issue, human, result, out_json, out_md)

    items = _ITEM_RE.findall(sheet_text)
    missing = [t for t in items if t not in human]
    if missing:
        print(f"[WARN] 시트 {len(items)}건 중 {len(missing)}건 미기입/무효 제외:")
        for t in missing:
            print(f"  - {t}")
    print(f"[OK] 공통 {result['n']}건 일치율 {result['agreement']} — {out_json.name}, {out_md.name} 저장")


if __name__ == "__main__":
    main()
