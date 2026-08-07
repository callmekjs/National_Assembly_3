"""
[EVAL-6] report_content_eval — 리포트 모드 **내용 정확성** 채점

`report_eval.py`(형식)와 짝. 이쪽은 "답이 맞았나"를 묻는다.

왜 새로 만드는가 — 기존 답변 채점표(answer_eval_*)가 재는 것
    ① faithfulness    준 근거 블록 밖의 말을 지어냈나
    ② citation_accuracy  [n] 이 맞는 근거를 가리키나
    ③ classification  여야·정당 분류가 맞나
    ④ refusal         근거 없을 때 거절했나
    **넷 중 어느 것도 "질문에 대한 답이 맞았나"를 묻지 않는다.**
    검색이 엉뚱한 회의록을 가져와도, 그걸 얌전히 요약하고 [n]만 잘 달면 전원 통과다.
    답변이 **검색 결과에 대해** 채점될 뿐 **사실에 대해** 채점되지 않는다 —
    옛 retrieval_eval 의 R@5 0.983 과 같은 종류의 동어반복이다 (2026-08-06 폐기).
    부수 문제: `must_not_checklist` 가 75문항 전부 비어 있는데(코드 주석은 이를
    "채점 정확도의 핵심"이라 부른다), 사람 검수는 21/75 뿐이다.

이 채점기가 다른 점
    **정답지를 따로 둔다.** `모범답안.json` 은 검색과 무관하게 만들어졌다 —
    질문별로 코퍼스 전체에서 날짜를 가리지 않고 근거를 모아 종합했고, 사람이 20/20
    검토했다. 합격 기준 3개도 거기서 나왔다. 따라서 검색이 틀리면 **떨어진다.**

채점 항목 (문항당)
    조건1~3   `pass_criteria` 각각을 리포트가 충족했는가 (충족/미충족)
    모순      모범답안·원문과 **어긋나는** 주장이 있는가 (있으면 실패)
    미확인    정답지에 없는 주장 — 실패로 세지 않고 **따로 센다**
              (모범답안은 종합이지 전수가 아니다. 없다고 곧 거짓은 아니다.)

심판 설계
    - 상위 모델 사용. 피채점(gpt-4o-mini)과 같은 모델로 채점하면 자기채점 편향이 낀다.
    - **"틀린 걸 찾아라" 식으로 몰지 않는다.** 2026-08-06 실측: 적대적 프레이밍을 주자
      60건 중 27건을 오답으로 찍었는데 사람이 15건을 직접 검토한 결과 원래 판정이 맞았다.
      좋은 것은 좋다고 하도록 지시한다.
    - 전문(全文)으로 채점한다. 잘린 텍스트로 채점해 두 번 실패한 적이 있다.

실행
    python scripts/report_content_eval.py               # 답변 가능 18문항
    python scripts/report_content_eval.py --limit 3     # 앞 3문항 (점검용)
    python scripts/report_content_eval.py --mode qa     # qa 모드로 채점
"""

import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

GOLD = ROOT / "data" / "eval" / "모범답안.json"
OUT_DEFAULT = ROOT / "data" / "eval" / "report_content_report.json"

JUDGE_MODEL = "gpt-5.6-sol"      # 피채점(gpt-4o-mini)보다 상위 — 자기채점 편향 완화
_MAX_SOURCE_CHARS = 700          # 정답지 원문 발췌 길이 (심판 입력용)
_MAX_SOURCES = 12                # 문항당 원문 개수 상한

SYSTEM = """당신은 국회 회의록 기반 답변을 채점하는 심사관이다.

당신에게는 **정답지**가 주어진다 — 사람이 검토한 모범답안과 그 근거 원문이다.
채점 대상 답변이 그 정답지에 비추어 질문에 제대로 답했는지 판정하라.

각 '합격 기준'에 대해:
  충족   — 답변이 그 기준을 실제로 만족한다
  미충족 — 답변이 그 기준을 다루지 않았거나 틀리게 다뤘다

그리고 답변 전체에 대해:
  모순   — 정답지나 근거 원문과 **어긋나는** 주장. 발언자를 바꿔 말하거나, 없는
           사실을 만들거나, 다른 사건을 이 사건인 것처럼 서술한 경우.
  미확인 — 정답지에 없지만 어긋나지도 않는 주장. 모범답안은 종합이지 전수가
           아니므로, 정답지에 없다는 이유만으로 거짓이라 단정하지 말 것.

중요한 태도:
- **좋은 답변은 좋다고 하라.** 흠을 찾아내는 것이 목적이 아니다. 기준을 실제로
  충족했으면 충족이다. 표현이 모범답안과 달라도 내용이 맞으면 충족이다.
- 답변이 모범답안보다 짧거나 요약적인 것은 그 자체로 미충족이 아니다.
- 판단이 애매하면 미충족이 아니라 충족 쪽으로 두고, 이유를 notes 에 남겨라.

반드시 아래 JSON 만 출력:
{"criteria":[{"n":1,"verdict":"충족|미충족","why":"한 줄"}, ...],
 "contradictions":[{"claim":"문제 문장","why":"무엇과 어긋나는지"}],
 "unverified":["정답지에 없는 주장", ...],
 "overall":"pass|fail",
 "notes":"한 줄 총평"}
overall 은 기준이 모두 충족이고 모순이 없을 때만 pass."""


def build_user_msg(item: dict, report: str) -> str:
    crits = "\n".join(f"  {i}. {c}" for i, c in enumerate(item["pass_criteria"], 1))
    srcs = []
    for s in (item.get("sources") or [])[:_MAX_SOURCES]:
        who = s.get("speaker_display") or s.get("speaker") or ""
        srcs.append(f"- ({s.get('committee','')} {s.get('date','')}) {who} "
                    f"[{s.get('party','')}]: {(s.get('text') or '')[:_MAX_SOURCE_CHARS]}")
    return f"""[질문]
{item['q']}

[합격 기준]
{crits}

[정답지 — 사람이 검토한 모범답안]
{item['answer']}

[정답지 — 근거 원문]
{chr(10).join(srcs) or '(없음)'}

[채점 대상 답변]
{report}"""


def judge(client, item: dict, report: str) -> dict:
    resp = client.chat.completions.create(
        model=JUDGE_MODEL,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": build_user_msg(item, report)}],
    )
    return json.loads(resp.choices[0].message.content)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N문항만 (0=전부)")
    ap.add_argument("--mode", default="report", choices=["report", "qa"])
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    # 숨김용(held-out) 평가셋을 가리킬 때 쓴다. 같은 채점 로직을 그대로 적용해야
    # 두 점수를 비교할 수 있으므로 채점기를 복제하지 않고 정답지만 바꾼다.
    ap.add_argument("--gold", default=str(GOLD), help="정답지 JSON 경로")
    args = ap.parse_args()

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    # 답 없는 문항은 '기준 충족'으로 잴 대상이 아니다 — 거절 여부는 report_eval 의 몫
    items = [g for g in gold if g.get("type") != "unanswerable" and g.get("answer")]
    if args.limit:
        items = items[: args.limit]

    from db import close_pool, init_pool
    init_pool()
    from answer import generate_answer
    from search_vector import _get_client
    client = _get_client()

    rows, t0 = [], time.time()
    for i, it in enumerate(items, 1):
        try:
            res = generate_answer(it["q"], mode=args.mode)
            report = res["answer"]
        except Exception as e:
            print(f"[{i}/{len(items)}] {it['id']} 답변 생성 실패: {type(e).__name__}: {e}")
            rows.append({"id": it["id"], "error": f"생성: {type(e).__name__}: {e}"})
            continue
        try:
            v = judge(client, it, report)
        except Exception as e:
            print(f"[{i}/{len(items)}] {it['id']} 채점 실패: {type(e).__name__}: {e}")
            rows.append({"id": it["id"], "error": f"채점: {type(e).__name__}: {e}"})
            continue

        crits = v.get("criteria") or []
        met = sum(1 for c in crits if c.get("verdict") == "충족")
        contra = v.get("contradictions") or []
        unver = v.get("unverified") or []
        rows.append({"id": it["id"], "type": it.get("type"), "q": it["q"],
                     "mode": args.mode, "chars": len(report), "report": report,
                     "criteria": crits, "met": met, "n_criteria": len(crits),
                     "contradictions": contra, "unverified": unver,
                     "overall": v.get("overall"), "notes": v.get("notes", "")})
        mark = "PASS" if v.get("overall") == "pass" else "FAIL"
        print(f"[{i}/{len(items)}] {it['id']:<5} {mark}  기준 {met}/{len(crits)}"
              f"  모순 {len(contra)}  미확인 {len(unver)}   {v.get('notes','')[:44]}")

    close_pool()

    ok = [r for r in rows if "error" not in r]
    print("\n" + "=" * 72)
    print(f"리포트 내용 정확성 — {len(ok)}문항 · {args.mode} 모드 ({time.time() - t0:.0f}초)")
    if not ok:
        print("  측정된 문항 없음")
        return 1

    npass = sum(1 for r in ok if r["overall"] == "pass")
    tot_c = sum(r["n_criteria"] for r in ok)
    met_c = sum(r["met"] for r in ok)
    n_contra = sum(len(r["contradictions"]) for r in ok)
    print(f"  전체 통과     {npass}/{len(ok)} = {npass / len(ok):.1%}")
    print(f"  합격 기준     {met_c}/{tot_c} = {met_c / tot_c:.1%}")
    print(f"  모순 주장     {n_contra}건" + ("  ← 환각" if n_contra else ""))
    print(f"  미확인 주장   {sum(len(r['unverified']) for r in ok)}건 (참고용)")

    by = {}
    for r in ok:
        b = by.setdefault(r["type"], [0, 0])
        b[0] += r["overall"] == "pass"
        b[1] += 1
    print("  유형별       " + "  ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(by.items())))

    fails = [r for r in ok if r["overall"] != "pass"]
    if fails:
        print(f"\n  미통과 {len(fails)}건 — 사람 검토 대상")
        for r in fails:
            miss = [f"조건{c['n']}" for c in r["criteria"] if c.get("verdict") != "충족"]
            print(f"    {r['id']} ({r['type']}): {', '.join(miss) or '모순'}  {r['notes'][:50]}")

    Path(args.out).write_text(json.dumps(
        {"measured_at": datetime.now().isoformat(timespec="seconds"),
         "mode": args.mode, "judge_model": JUDGE_MODEL, "n": len(ok),
         "pass": npass, "criteria_met": met_c, "criteria_total": tot_c,
         "contradictions": n_contra, "rows": rows},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  저장: {args.out}")
    print("  ※ 이 판정은 LLM 심판의 1차 채점이다 — 사람 검수로 확정할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
