"""
[EVAL-5] report_eval — 리포트 모드 **구조 계약** 검사

무엇을 재는가
    리포트 모드(MODE_CONFIG["report"])가 시스템 프롬프트에서 약속한 출력 형식을
    실제로 지키는지 **기계적으로** 검사한다. LLM 심판을 쓰지 않으므로 결정적이고,
    비용은 답변 생성분뿐이며, 몇 번을 돌려도 같은 판정이 나온다.

무엇을 재지 않는가 (중요)
    답변 **내용의 정확성**은 재지 않는다. 그건 답변 평가셋(answer_eval_set.json)의
    일이고, 그 평가셋은 검색 평가셋이 받았던 것과 같은 정밀 검토를 아직 안 받았다
    (2026-08-06 에 retrieval_eval_set 을 v2 로 다시 쓴 이유와 동일한 의심).
    여기서 통과한다고 "리포트가 정확하다"가 아니라 **"리포트가 형식을 지킨다"** 이다.
    이 구분을 흐리면 옛 R@5 0.983 과 같은 종류의 자기기만이 된다.

    내용 규칙(발언자·정당·날짜 일관성)은 backend/verification.py 가 이미 본다.
    이 파일은 그와 겹치지 않는 구조 층만 담당한다.

검사 항목 (프롬프트가 명시적으로 요구한 것만 — 취향은 검사하지 않는다)
    C1 필수 섹션    '## 개요' '## 쟁점별 정리' '## 주요 발언 근거' '## 논의의 한계'
    C2 섹션 순서    위 순서대로 등장
    C3 인용 유효성  범위 밖 [n] 이 없다 (invalid_citations 비어 있음)
    C4 인용 밀도    사실 주장 문장에 [n] 이 붙는다 (섹션 제목·인용문 제외)
    C5 총평 금지    '## 논의의 한계'·'## 시사점' 밖에서 인용 없이 끝맺지 않는다
    C6 발언 발췌    '## 주요 발언 근거' 에 직접 인용("…")과 [n] 이 함께 있다
    C7 꼬리 거절문  "이 부분은/이 외의 … 확인할 수 없" 류 대상 없는 문장이 없다
    C8 시간순       경과·추이 질문이면 '## 쟁점별 정리' 항목이 과거→최근
    C9 근거 활용률  검색된 10건 중 실제 인용된 비율 (지표일 뿐, 합격 기준 아님)

실행
    python scripts/report_eval.py                  # 평가셋의 답변 가능 문항 전부
    python scripts/report_eval.py --limit 5        # 앞 5문항만 (비용 절약)
    python scripts/report_eval.py --json out.json  # 결과 저장
"""

import argparse
import io
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

EVAL_SET = ROOT / "data" / "eval" / "retrieval_eval_set_v2.json"

REQUIRED_SECTIONS = ["## 개요", "## 쟁점별 정리", "## 주요 발언 근거", "## 논의의 한계"]

# 총평 규칙의 예외 섹션 — 프롬프트가 "여기선 인용 없는 서술을 허용" 한 곳
NARRATIVE_SECTIONS = ("## 논의의 한계", "## 회의록상 드러난 정책적 시사점")

# 대상 없는 꼬리 거절 문장 (프롬프트가 명시적으로 금지)
DANGLING_REFUSAL_RE = re.compile(r"(이 부분은|이 외의|그 외의|나머지는)[^.\n]{0,40}확인할 수 없")

# 경과·추이를 묻는 질문 — 이때만 시간순 배열을 요구한다
TIMELINE_Q_RE = re.compile(r"경과|추이|변화|흐름|이후|과정|어떻게 진행|시간순")

CITATION_RE = re.compile(r"\[(\d+)\]")
QUOTE_RE = re.compile(r"[\"“][^\"”\n]{5,}[\"”]")
YEAR_MONTH_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월")


def sections(answer: str) -> dict[str, str]:
    """'## 제목' 기준으로 본문을 쪼갠다. 제목 → 그 아래 본문."""
    out, cur, buf = {}, None, []
    for line in answer.splitlines():
        if line.strip().startswith("## "):
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur, buf = line.strip(), []
        else:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def claim_sentences(text: str) -> list[str]:
    """사실 주장으로 볼 문장들. 제목·목록 기호·빈 줄은 제외."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+|\n", text):
        s = raw.strip().lstrip("-*·•0123456789. )")
        if len(s) >= 15 and not s.startswith("#"):
            out.append(s)
    return out


def check_report(question: str, result: dict) -> dict:
    """리포트 1건의 구조 계약 검사. {검사코드: (통과여부, 설명)} 반환."""
    ans = result.get("answer", "")
    secs = sections(ans)
    checks: dict[str, tuple[bool, str]] = {}

    # C1 필수 섹션
    missing = [s for s in REQUIRED_SECTIONS if s not in secs]
    checks["C1 필수섹션"] = (not missing, f"누락 {missing}" if missing else "4개 모두")

    # C2 섹션 순서 — 있는 것들만 상대 순서를 본다 (C1 이 누락을 따로 잡는다)
    order = [ans.index(s) for s in REQUIRED_SECTIONS if s in secs]
    checks["C2 섹션순서"] = (order == sorted(order), "" if order == sorted(order) else "순서 뒤바뀜")

    # C3 인용 유효성 — 범위 밖 번호
    invalid = result.get("invalid_citations") or []
    checks["C3 인용유효"] = (not invalid, f"범위 밖 {invalid}" if invalid else "")

    # C4 인용 밀도 — **문단 단위**로 본다.
    #
    # 문장 단위로 재면 과검출이 난다 (2026-08-07 실측): 모델은 "…라고 주장했습니다.
    # 그는 '…'라고 언급했습니다 [2]." 처럼 한 근거를 두 문장에 나눠 쓰고 인용을 끝에
    # 붙인다. 앞 문장을 위반으로 세면 멀쩡한 리포트가 27~60% 로 찍힌다 —
    # 검사기가 틀린 것이지 리포트가 틀린 게 아니다.
    # 반면 문단에 [n] 이 **하나도** 없으면 그 문단은 근거 없이 주장한 것이 맞다.
    # 실제로 이 기준이 '## 개요' 무인용 결함을 잡아냈다.
    body_secs = {k: v for k, v in secs.items() if not k.startswith(NARRATIVE_SECTIONS)}
    paras = [(k, p.strip()) for k, v in body_secs.items() for p in re.split(r"\n\s*\n", v)
             if len(p.strip()) >= 30]
    # 어느 섹션이 비었는지까지 남긴다 — 개수만으론 어디를 고칠지 알 수 없다
    bad_secs = sorted({k for k, p in paras if not CITATION_RE.search(p)})
    checks["C4 문단인용"] = (not bad_secs,
                          f"{len(bad_secs)}개 섹션 무인용: {', '.join(bad_secs)}" if bad_secs else "")

    # 참고 지표 — 문장 단위 인용률 (합격 기준 아님, 추이만 본다)
    claims = claim_sentences("\n".join(body_secs.values()))
    cited_n = sum(1 for s in claims if CITATION_RE.search(s))
    checks["_문장인용률"] = (True, f"{cited_n / len(claims):.0%}" if claims else "n/a")

    # C5 총평 금지 — 서술 예외 섹션이 아닌 곳에서 인용 없이 끝맺지 않는다
    last_sec = list(secs)[-1] if secs else ""
    tail_ok = last_sec.startswith(NARRATIVE_SECTIONS)
    if not tail_ok:
        tail = claim_sentences(secs.get(last_sec, ""))
        tail_ok = bool(tail) and bool(CITATION_RE.search(tail[-1]))
    checks["C5 총평금지"] = (tail_ok, "" if tail_ok else f"'{last_sec}' 가 무인용으로 끝남")

    # C6 발언 발췌 — 직접 인용 + [n]
    quotes_sec = secs.get("## 주요 발언 근거", "")
    has_q, has_c = bool(QUOTE_RE.search(quotes_sec)), bool(CITATION_RE.search(quotes_sec))
    checks["C6 발언발췌"] = (has_q and has_c,
                          "" if has_q and has_c else f"인용부호={has_q} 번호={has_c}")

    # C7 꼬리 거절문
    dangling = DANGLING_REFUSAL_RE.findall(ans)
    checks["C7 꼬리거절"] = (not dangling, f"{len(dangling)}건" if dangling else "")

    # C8 시간순 — 경과 질문일 때만
    if TIMELINE_Q_RE.search(question):
        ym = [(int(y), int(m)) for y, m in YEAR_MONTH_RE.findall(secs.get("## 쟁점별 정리", ""))]
        ok = ym == sorted(ym)
        checks["C8 시간순"] = (ok, f"{len(ym)}개 연월" if ok else f"역순 있음 {ym}")
    else:
        checks["C8 시간순"] = (True, "해당 없음")

    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N문항만 (0=전부)")
    ap.add_argument("--json", type=str, default="", help="결과 저장 경로")
    args = ap.parse_args()

    from db import close_pool, init_pool

    data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    qs = data["questions"] if isinstance(data, dict) else data
    # 답 없는 문항은 구조를 지킬 대상이 아니다 ("확인할 수 없습니다" 한 줄이 정답)
    qs = [q for q in qs if q.get("type") != "unanswerable"]
    if args.limit:
        qs = qs[: args.limit]

    init_pool()
    from answer import generate_answer

    rows, t0 = [], time.time()
    for i, q in enumerate(qs, 1):
        try:
            res = generate_answer(q["q"], mode="report")
        except Exception as e:                      # 1건 실패로 전체가 죽지 않게
            print(f"[{i}/{len(qs)}] {q['id']} 실패: {type(e).__name__}: {e}")
            rows.append({"id": q["id"], "error": f"{type(e).__name__}: {e}"})
            continue
        checks = check_report(q["q"], res)
        n_src = len(res.get("sources") or [])
        n_cited = len(res.get("cited_numbers") or [])
        failed = [k for k, (ok, _) in checks.items() if not ok and not k.startswith("_")]
        rows.append({"id": q["id"], "type": q.get("type"), "chars": len(res["answer"]),
                     "sources": n_src, "cited": n_cited,
                     "checks": {k: {"ok": ok, "note": note} for k, (ok, note) in checks.items()},
                     "failed": failed})
        mark = "PASS" if not failed else "FAIL"
        print(f"[{i}/{len(qs)}] {q['id']:<5} {mark}  {len(res['answer']):>5}자  "
              f"근거 {n_cited}/{n_src}" + (f"  ← {', '.join(failed)}" if failed else ""))
    close_pool()

    ok_rows = [r for r in rows if "error" not in r]
    print("\n" + "=" * 72)
    print(f"리포트 구조 계약 — {len(ok_rows)}문항 ({time.time() - t0:.0f}초)")
    if not ok_rows:
        print("  측정된 문항 없음")
        return 1

    # '_' 로 시작하는 코드는 합격 기준이 아니라 참고 지표 — 통과 수를 세지 않는다
    for code in ok_rows[0]["checks"]:
        if code.startswith("_"):
            continue
        n = sum(1 for r in ok_rows if r["checks"][code]["ok"])
        bad = [f"{r['id']}({r['checks'][code]['note']})"
               for r in ok_rows if not r["checks"][code]["ok"]]
        print(f"  {code:<12} {n}/{len(ok_rows)}" + (f"   실패: {', '.join(bad[:4])}" if bad else ""))

    allpass = sum(1 for r in ok_rows if not r["failed"])
    used = sum(r["cited"] for r in ok_rows) / max(sum(r["sources"] for r in ok_rows), 1)
    avg = sum(r["chars"] for r in ok_rows) / len(ok_rows)
    print(f"\n  전 항목 통과   {allpass}/{len(ok_rows)}")
    print(f"  C9 근거 활용률 {used:.0%}   (지표일 뿐 — 합격 기준 아님)")
    print(f"  평균 길이      {avg:.0f}자")
    print("\n  ※ 이 점수는 '형식 준수'다. 내용의 정확성은 재지 않는다.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"measured_at": datetime.now().isoformat(timespec="seconds"),
             "n": len(ok_rows), "all_pass": allpass, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  저장: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
