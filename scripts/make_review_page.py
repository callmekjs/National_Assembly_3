"""
검토용 화면 생성 — 리포트 내용 채점 결과(report_content_eval.py)를 사람이 읽을 수
있는 HTML 한 장으로 만든다.

왜 필요한가: LLM 심판의 판정은 1차 초안이다. 이 프로젝트에서 심판이 틀린 사례가
반복해서 나왔다 (2026-08-06 적대적 프레이밍으로 27/60 오판, 2026-08-07 "정책금리"
환각 미검출). 사람이 전문을 읽고 확정해야 하는데, JSON 을 직접 읽는 것은 부담이
크다 — 그래서 질문·합격기준·정답지·답변·심판판정을 한 화면에 나란히 놓는다.

두 파일을 주면 모델 비교 화면이 된다 (같은 질문의 두 답변을 나란히).

실행
    python scripts/make_review_page.py A.json                  # 한 개
    python scripts/make_review_page.py A.json B.json           # 두 개 비교
    python scripts/make_review_page.py A.json B.json --label mini sol
"""

import argparse
import html
import io
import json
import re
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
GOLD = ROOT / "data" / "eval" / "모범답안.json"
OUT = ROOT / "data" / "eval" / "리포트검토.html"


def md(text: str) -> str:
    """리포트 본문(Markdown 일부)을 최소한으로 HTML 화. 외부 의존성 없이."""
    out = []
    for line in html.escape(text or "").splitlines():
        s = line.strip()
        if s.startswith("### "):
            out.append(f"<h4>{s[4:]}</h4>")
        elif s.startswith("## "):
            out.append(f"<h3>{s[3:]}</h3>")
        elif s.startswith(("- ", "* ")):
            out.append(f"<li>{s[2:]}</li>")
        elif s:
            out.append(f"<p>{s}</p>")
    body = "\n".join(out)
    body = re.sub(r"(<li>.*?</li>\n?)+", lambda m: f"<ul>{m.group(0)}</ul>", body, flags=re.S)
    # 인용 번호를 눈에 띄게 — 근거 확인이 이 화면의 목적이다
    body = re.sub(r"\[(\d+)\]", r'<span class="cite">[\1]</span>', body)
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    return body


def card(r: dict, label: str) -> str:
    if "error" in r:
        return f'<div class="ans"><div class="mtag">{html.escape(label)}</div>' \
               f'<p class="err">실패: {html.escape(r["error"])}</p></div>'
    ok = r.get("overall") == "pass"
    crits = "".join(
        f'<li class="{"met" if c.get("verdict") == "충족" else "unmet"}">'
        f'<b>{"충족" if c.get("verdict") == "충족" else "미충족"}</b> '
        f'{html.escape(c.get("why", ""))}</li>'
        for c in (r.get("criteria") or []))
    contra = "".join(
        f'<li><b>{html.escape(c.get("claim", ""))}</b><br><span>{html.escape(c.get("why", ""))}</span></li>'
        for c in (r.get("contradictions") or []))
    unver = "".join(f"<li>{html.escape(u)}</li>" for u in (r.get("unverified") or []))
    return f"""<div class="ans">
  <div class="mtag">{html.escape(label)}
    <span class="pill {'p' if ok else 'f'}">{'통과' if ok else '미통과'}</span>
    <span class="dim">기준 {r.get('met', 0)}/{r.get('n_criteria', 0)} · 모순 {len(r.get('contradictions') or [])} · {r.get('chars', 0)}자</span>
  </div>
  <div class="report">{md(r.get('report', ''))}</div>
  <details><summary>심판 판정 (합격 기준별)</summary><ul class="crit">{crits}</ul>
    <p class="note">{html.escape(r.get('notes', ''))}</p></details>
  {f'<details open><summary class="bad">심판이 지적한 모순 {len(r.get("contradictions") or [])}건 — 진짜인지 확인해 주세요</summary><ul class="contra">{contra}</ul></details>' if contra else ''}
  {f'<details><summary>정답지에 없는 주장 {len(r.get("unverified") or [])}건 (거짓이라는 뜻은 아님)</summary><ul class="unver">{unver}</ul></details>' if unver else ''}
</div>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="report_content_eval 결과 JSON (1~2개)")
    ap.add_argument("--label", nargs="*", default=None, help="각 파일 이름표")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    datasets = [json.loads(Path(f).read_text(encoding="utf-8")) for f in args.files]
    labels = args.label or [d.get("mode", "?") + " / " + Path(f).stem
                            for d, f in zip(datasets, args.files)]
    gold = {g["id"]: g for g in json.loads(GOLD.read_text(encoding="utf-8"))}

    # 질문 순서는 첫 파일 기준, 나머지는 id 로 맞춘다
    by_id = [{r["id"]: r for r in d["rows"]} for d in datasets]
    ids = [r["id"] for r in datasets[0]["rows"]]

    summary = "".join(
        f'<div class="sum"><div class="lab">{html.escape(lab)}</div>'
        f'<div class="big">{d["pass"]}/{d["n"]}</div>'
        f'<div class="dim">기준 {d["criteria_met"]}/{d["criteria_total"]} · '
        f'모순 {d["contradictions"]}건</div></div>'
        for lab, d in zip(labels, datasets))

    # 목차 — 문항별 모델 판정을 점으로. 사람이 볼 곳(미통과)으로 바로 뛰게 한다.
    nav = []
    for qid in ids:
        dots = "".join(
            f'<i class="{"d-p" if (bi.get(qid) or {}).get("overall") == "pass" else "d-f"}"></i>'
            for bi in by_id)
        anyfail = any((bi.get(qid) or {}).get("overall") != "pass" for bi in by_id)
        nav.append(f'<a href="#{qid}" class="{"nf" if anyfail else ""}">{qid}{dots}</a>')
    navbar = f'<div class="nav"><b>목차</b> {"".join(nav)}' \
             f'<span class="dim">점 순서: {" · ".join(html.escape(l) for l in labels)}' \
             f' — 빨간 점이 미통과</span></div>'

    cards = []
    for qid in ids:
        g = gold.get(qid, {})
        crit = "".join(f"<li>{html.escape(c)}</li>" for c in (g.get("pass_criteria") or []))
        answers = "".join(card(bi[qid], lab) for bi, lab in zip(by_id, labels) if qid in bi)
        cards.append(f"""<section id="{qid}">
  <h2><span class="qid">{qid}</span> {html.escape(g.get('q', ''))}</h2>
  <div class="meta">{html.escape(g.get('type', ''))}</div>
  <div class="crit-box"><b>합격 기준</b><ol>{crit}</ol></div>
  <details><summary>정답지 (사람이 검토한 모범답안)</summary>
    <div class="gold">{md(g.get('answer', ''))}</div></details>
  <div class="grid{len(by_id)}">{answers}</div>
</section>""")

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>리포트 내용 검토</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--dim:#666;--line:#e3e3e3;--box:#f7f7f8;
--ok:#0a7d3f;--no:#c0392b;--cite:#0b5fff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e8ea;--dim:#9aa0a6;
--line:#2c3038;--box:#1e2127;--ok:#4ade80;--no:#f87171;--cite:#7aa2ff}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font:16px/1.7 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;max-width:1500px;margin:0 auto}}
h1{{font-size:26px;margin:0 0 4px}}
.top{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0 32px}}
.sum{{background:var(--box);border:1px solid var(--line);border-radius:10px;padding:14px 20px;min-width:190px}}
.sum .lab{{font-size:13px;color:var(--dim)}} .sum .big{{font-size:30px;font-weight:700}}
.dim{{color:var(--dim);font-size:13px;font-weight:400}}
section{{border-top:2px solid var(--line);padding-top:22px;margin-top:34px}}
h2{{font-size:19px;margin:0 0 4px;line-height:1.5}}
.qid{{background:var(--fg);color:var(--bg);border-radius:5px;padding:1px 8px;font-size:14px;margin-right:6px}}
.meta{{color:var(--dim);font-size:13px;margin-bottom:10px}}
.crit-box{{background:var(--box);border-left:3px solid var(--cite);padding:10px 16px;border-radius:0 8px 8px 0;margin-bottom:12px}}
.crit-box ol{{margin:6px 0 0;padding-left:22px}} .crit-box li{{margin:3px 0}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:12px}}
.grid1{{margin-top:12px}}
@media(max-width:1300px){{.grid3{{grid-template-columns:1fr}}}}
@media(max-width:1000px){{.grid2{{grid-template-columns:1fr}}}}
.ans{{border:1px solid var(--line);border-radius:10px;padding:14px 18px;background:var(--bg)}}
.mtag{{font-weight:700;font-size:14px;margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.pill{{border-radius:20px;padding:1px 11px;font-size:12px;color:#fff}}
.pill.p{{background:var(--ok)}} .pill.f{{background:var(--no)}}
.report{{font-size:15px}} .report h3{{font-size:15px;margin:14px 0 4px;color:var(--dim)}}
.report h4{{font-size:14px;margin:10px 0 3px}} .report p{{margin:5px 0}}
.report ul{{margin:5px 0;padding-left:20px}}
.cite{{color:var(--cite);font-weight:700;font-size:13px}}
.gold{{background:var(--box);padding:12px 16px;border-radius:8px;font-size:15px}}
details{{margin-top:10px}} summary{{cursor:pointer;font-size:14px;color:var(--dim)}}
summary.bad{{color:var(--no);font-weight:700}}
ul.crit,ul.contra,ul.unver{{font-size:14px;padding-left:20px;margin:6px 0}}
ul.crit li.met b{{color:var(--ok)}} ul.crit li.unmet b{{color:var(--no)}}
ul.contra li{{margin:8px 0}} ul.contra span{{color:var(--dim);font-size:13px}}
.note{{font-size:13px;color:var(--dim);margin:6px 0 0}}
.err{{color:var(--no)}}
.nav{{background:var(--box);border:1px solid var(--line);border-radius:10px;
padding:12px 16px;margin-bottom:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}}
.nav a{{color:var(--fg);text-decoration:none;border:1px solid var(--line);border-radius:6px;
padding:3px 9px;font-size:13px;display:inline-flex;align-items:center;gap:4px}}
.nav a.nf{{border-color:var(--no)}}
.nav i{{width:7px;height:7px;border-radius:50%;display:inline-block}}
.nav i.d-p{{background:var(--ok)}} .nav i.d-f{{background:var(--no)}}
</style></head><body>
<h1>리포트 내용 검토</h1>
<p class="dim">심판은 <b>gpt-5.6-sol</b>. 이 판정은 초안입니다 — 실제로 틀린 적이 있으니
직접 읽고 확정해 주세요. 특히 <span style="color:var(--no)">빨간 "모순"</span> 항목이
진짜인지 봐 주시면 됩니다.</p>
<div class="top">{summary}</div>
{navbar}
{"".join(cards)}
</body></html>"""

    Path(args.out).write_text(doc, encoding="utf-8")
    print(f"생성: {args.out}  ({len(ids)}문항, {len(datasets)}개 모델)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
