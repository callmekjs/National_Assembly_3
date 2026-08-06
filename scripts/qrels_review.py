"""
[EVAL-3] qrels_review — 등급 라벨(qrels) 제작 3단계: 사람 검수 큐

전수 검수는 하지 않는다. ~2,450쌍을 사람이 다 보면 몇 시간이고, 그렇게 만든 라벨은
피로 때문에 오히려 품질이 떨어진다 (POL-7 에서 40건 라벨링도 인지 부담이 컸다는
사용자 피드백 실측). 대신 **심판이 틀렸을 가능성이 높은 쌍만** 골라 올린다.

큐 선정 기준 (강한 신호 순)
    A. old_disagree  — 옛 criteria 판정과 심판 등급이 엇갈림. **가장 강한 신호**:
                       서로 독립적인 두 판정이 다르다는 뜻이라 한쪽은 반드시 틀렸다.
    B. unjudged      — 심판이 빠뜨렸거나 호출이 실패한 쌍 (유실을 통과로 만들지 않기)
    C. top_but_zero  — 하이브리드 상위 5위 안인데 등급 0. 검색이 크게 틀렸거나 심판이 틀림
    D. lone_two      — 축 하나만 찾았는데 등급 2. 확증이 약한 정답 후보
    E. low_conf      — 심판이 스스로 확신 없다고 표기
    F. boundary_one  — 등급 1(경계)의 표본. strict/loose 를 가르는 지점이라 표본 확인

    ※ 확신도(E)에만 의존하지 않는 이유: 프롬프트를 명확히 하자 low confidence 가
      17%→0% 로 떨어졌다. 심판은 자기가 틀렸을 때 그걸 모르는 경우가 많다.

우선순위
    평가 지표(R@5·R@10·nDCG@5)를 실제로 바꾸는 쌍이 먼저다 = 하이브리드 상위 10위
    안에 있는 후보. 하위 순위 쌍은 라벨이 틀려도 점수에 거의 영향이 없다.

출력
    data/eval/qrels_review_queue.md    — 사람이 읽고 판단하는 시트
    data/eval/qrels_review_answers.txt — **사용자가 답을 적는 파일** (아래 참조)
    data/eval/qrels_review_queue.json  — 기계용 원본(19개 키). 사람이 손댈 필요 없다

검수 방법 (사용자)
    시트(.md)를 위에서부터 읽고, 심판 등급에 **동의하면 그냥 넘어간다.**
    틀렸다고 생각되는 것만 답안지(.txt)에 "번호 = 등급" 으로 적는다.
    전부 볼 필요 없다 — 답안지 맨 위 `검수한_마지막_번호` 에 어디까지 봤는지만
    적으면 그 뒤는 미검수로 정직하게 기록된다.
    (JSON 을 직접 고치는 방식은 키가 19개라 부담이 커서 답안지로 대체했다)

실행
    python scripts/qrels_review.py             # 큐 생성 (기본 상한 60건)
    python scripts/qrels_review.py --cap 100   # 상한 조정
"""

import argparse
import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from retrieval_eval import is_relevant  # noqa: E402  (옛 criteria 판정 재사용)

EVAL_SET = PROJECT_ROOT / "data" / "eval" / "retrieval_eval_set.json"
POOL = PROJECT_ROOT / "data" / "eval" / "qrels_pool.jsonl"
JUDGED = PROJECT_ROOT / "data" / "eval" / "qrels_judged.jsonl"
OUT_MD = PROJECT_ROOT / "data" / "eval" / "qrels_review_queue.md"
OUT_JSON = PROJECT_ROOT / "data" / "eval" / "qrels_review_queue.json"
OUT_ANS = PROJECT_ROOT / "data" / "eval" / "qrels_review_answers.txt"

DEFAULT_CAP = 60
RANK_IMPACT = 10   # 이 순위 안이면 지표에 영향 (R@10 기준)


def load_jsonl(path: Path, skip_meta=True) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if skip_meta and r.get("_meta"):
            continue
        rows.append(r)
    return rows


def best_hybrid_rank(found_by: dict) -> int:
    """하이브리드 계열 축에서의 최선 순위 (없으면 큰 값)."""
    ranks = [v for k, v in found_by.items() if k.startswith("hybrid")]
    return min(ranks) if ranks else 999


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP, help="큐 상한 (기본 60)")
    args = ap.parse_args()

    for p in (POOL, JUDGED):
        if not p.exists():
            print(f"[FAIL] 파일 없음: {p}")
            sys.exit(1)

    questions = {q["id"]: q for q in json.loads(EVAL_SET.read_text(encoding="utf-8"))["questions"]}
    pool = {}
    for row in load_jsonl(POOL):
        for c in row["candidates"]:
            pool[(row["qid"], c["chunk_id"])] = (row, c)

    items = []
    for j in load_jsonl(JUDGED):
        key = (j["qid"], j["chunk_id"])
        if key not in pool:
            continue
        row, c = pool[key]
        q = questions.get(j["qid"], {})
        crit = q.get("criteria") or {}
        grade = j.get("grade")

        # 옛 criteria 판정 — 독립된 제2 의견 (내용 조건이 있는 문항에서만 유의미)
        has_content_crit = bool(crit.get("text_any") or crit.get("speaker_any"))
        old_rel = None
        if has_content_crit:
            old_rel = is_relevant(
                {"speaker": c.get("speaker"), "text": c.get("text"),
                 "committee": c.get("committee"), "date": c.get("date")}, crit)

        rank = best_hybrid_rank(c["found_by"])
        reasons = []
        if grade is None:
            reasons.append("unjudged")
        else:
            if old_rel is not None and old_rel != (grade >= 1):
                reasons.append("old_disagree")
            if grade == 0 and rank <= 5:
                reasons.append("top_but_zero")
            if grade == 2 and len(c["found_by"]) == 1:
                reasons.append("lone_two")
            if j.get("confidence") == "low":
                reasons.append("low_conf")
            if grade == 1:
                reasons.append("boundary_one")
        if not reasons:
            continue

        # 층(stratum) = 가장 강한 사유 하나. 층별 할당량으로 뽑아야 표본이 한 종류로
        # 쏠리지 않는다 (2026-08-05 실측: 가중치 정렬만 쓰니 60건이 전부
        # old_disagree+top_but_zero·등급 0 으로 채워져, 심판이 **오답을 2점으로 준**
        # 반대 방향 오류를 표본이 아예 못 봤다 — 오답률 추정 자체가 불가능해진다)
        stratum = next(r for r in ("unjudged", "old_disagree", "lone_two",
                                   "top_but_zero", "low_conf", "boundary_one")
                       if r in reasons)
        impact = 1 if rank <= RANK_IMPACT else 0
        items.append({
            "qid": j["qid"], "chunk_id": j["chunk_id"], "text_sha1": j["text_sha1"],
            "question": row["question"], "qtype": row.get("type"),
            "speaker": c.get("speaker"), "committee": c.get("committee"), "date": c.get("date"),
            "text": (c.get("text") or "")[:280],
            "prev": (c.get("prev") or "")[:120], "next": (c.get("next") or "")[:120],
            "judge_grade": grade, "judge_reason": j.get("reason"),
            "confidence": j.get("confidence"), "old_criteria": old_rel,
            "hybrid_rank": rank if rank < 999 else None,
            "found_by": c["found_by"], "queue_reasons": reasons,
            "_stratum": stratum, "_sort": (-impact, rank),
            "human_grade": None,   # ← 사용자가 채우는 칸 (동의하면 비워둠)
        })

    total = len(items)

    # 층별 할당 — 두 방향의 오류를 모두 보게 한다.
    #   심판이 정답을 깎았나(top_but_zero·old_disagree) / 오답을 올렸나(lone_two)
    #   경계 등급은 strict·loose 를 가르는 지점이라 반드시 표본에 넣는다.
    SHARE = {"unjudged": 1.00,       # 미판정은 전부 (있으면 소수)
             "old_disagree": 0.30,
             "lone_two": 0.20,       # 오답을 2점으로 준 경우 — 반대 방향 오류
             "boundary_one": 0.20,
             "top_but_zero": 0.20,
             "low_conf": 0.10}
    by_stratum: dict[str, list] = {}
    for it in items:
        by_stratum.setdefault(it["_stratum"], []).append(it)
    for lst in by_stratum.values():
        lst.sort(key=lambda x: x["_sort"])

    queued, seen = [], set()
    for name, share in SHARE.items():
        quota = len(by_stratum.get(name, [])) if share >= 1.0 else round(args.cap * share)
        for it in by_stratum.get(name, [])[:quota]:
            if it["chunk_id"] not in seen:
                seen.add(it["chunk_id"])
                queued.append(it)
    # 할당 미달분은 영향도 순으로 채운다 (층이 비어 있을 수 있으므로)
    if len(queued) < args.cap:
        for it in sorted(items, key=lambda x: x["_sort"]):
            if len(queued) >= args.cap:
                break
            if it["chunk_id"] not in seen:
                seen.add(it["chunk_id"])
                queued.append(it)
    queued = queued[: args.cap]
    queued.sort(key=lambda x: x["_sort"])
    for it in queued:
        it.pop("_sort", None)
        it.pop("_stratum", None)

    OUT_JSON.write_text(json.dumps(
        {"total_candidates_flagged": total, "cap": args.cap, "queued": len(queued),
         "note": "human_grade 에 0/1/2 를 적으면 심판 등급을 덮어쓴다. 동의하면 비워둘 것.",
         "items": queued}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 사람이 읽는 시트
    LABEL = {2: "2 (직접 답변)", 1: "1 (관련만)", 0: "0 (무관)", None: "미판정"}
    REASON_KO = {"unjudged": "심판 누락", "old_disagree": "옛 기준과 불일치",
                 "top_but_zero": "상위인데 0점", "lone_two": "축 1개만 찾은 2점",
                 "low_conf": "심판 확신 낮음", "boundary_one": "경계 등급 1"}
    lines = [
        "# qrels 검수 시트",
        "",
        f"- 표시 대상 **{len(queued)}건** (전체 후보 중 의심 {total}건, 상한 {args.cap})",
        "- **전부 볼 필요 없습니다.** 위에서부터 보다가 시간이 없으면 멈추세요 — 미검수는 그대로 기록됩니다.",
        "- 심판 등급에 **동의하면 넘어가고**, 틀렸으면 `qrels_review_queue.json` 의 "
        "해당 항목 `human_grade` 에 올바른 등급(0/1/2)을 적으세요.",
        "- 판단 기준: **2**=이 발언만 읽어도 질문의 답을 안다 / **1**=주제는 맞지만 답은 아님 / **0**=무관",
        "- ⚠ 가장 흔한 오판은 **사건이 다른데 표현이 겹치는 경우**입니다 (예: 티메프 질문에 전세사기 발언).",
        "",
    ]
    for n, it in enumerate(queued, start=1):
        why = " · ".join(REASON_KO.get(r, r) for r in it["queue_reasons"])
        lines += [
            f"## {n}. [{it['qid']}] {it['question']}",
            "",
            f"- **심판 판정**: {LABEL[it['judge_grade']]} — {it['judge_reason']}",
            f"- **큐에 오른 이유**: {why}",
            f"- 발언자 {it['speaker']} · {it['committee']} · {it['date']}"
            + (f" · 하이브리드 {it['hybrid_rank']}위" if it["hybrid_rank"] else ""),
            "",
        ]
        if it["prev"]:
            lines.append(f"> (앞) {it['prev']}")
        lines.append(f"> **{it['text']}**")
        if it["next"]:
            lines.append(f"> (뒤) {it['next']}")
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    # 답안지 — 19개 키가 든 107KB JSON 을 손으로 고치는 부담을 없앤다 (POL-7 에서
    # 40건 라벨링도 인지 부담이 컸다는 사용자 피드백). 최소 입력 원칙:
    #   ① 어디까지 봤는지 숫자 하나  ② 틀렸다고 본 것만 "번호=등급" 으로 적기
    # 동의한 건 아무것도 안 적는다. 미검수와의 구별은 ①번 숫자로 한다.
    ans = [
        "# qrels 검수 답안지",
        "#",
        "# 읽을 시트: data/eval/qrels_review_queue.md  (같은 번호를 씁니다)",
        "#",
        "# [1] 어디까지 봤는지 숫자만 적으세요. 중간에 멈춰도 됩니다.",
        "#     그 뒤 번호는 '미검수'로 정직하게 기록됩니다.",
        "",
        "검수한_마지막_번호 = ",
        "",
        "# [2] 심판이 틀렸다고 본 것만 적으세요. 동의한 건 안 적어도 됩니다.",
        "#     형식:  번호 = 등급     (등급은 0 / 1 / 2)",
        "#     예:    7 = 2           ← 7번은 2점이어야 한다",
        "#",
        "# 등급 기준: 2=이것만 읽어도 답을 안다 / 1=주제만 맞다 / 0=무관",
        "# 자주 나오는 오판: 사건이 다른데 표현만 겹치는 것",
        "#                  (예: 티메프 질문에 전세사기 발언)",
        "",
        "# ── 참고용 목록 (심판 판정) ─────────────────────────────",
    ]
    for n, it in enumerate(queued, start=1):
        ans.append(f"#  {n:2d}. [{LABEL[it['judge_grade']]}] {it['question'][:34]}"
                   f" | {it['speaker']}·{it['committee']}")
    OUT_ANS.write_text("\n".join(ans) + "\n", encoding="utf-8")

    from collections import Counter
    cnt = Counter(r for it in items for r in it["queue_reasons"])
    print(f"의심 {total}건 중 상위 {len(queued)}건을 큐에 올림 (상한 {args.cap})")
    print("  사유별:", ", ".join(f"{REASON_KO.get(k, k)} {v}" for k, v in cnt.most_common()))
    print(f"→ {OUT_MD.relative_to(PROJECT_ROOT)}   (읽기용 시트)")
    print(f"→ {OUT_ANS.relative_to(PROJECT_ROOT)}  ★ 여기에 답을 적으세요")
    print(f"→ {OUT_JSON.relative_to(PROJECT_ROOT)} (기계용 — 손댈 필요 없음)")


if __name__ == "__main__":
    main()
