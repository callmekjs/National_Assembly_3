"""
[EVAL-2] qrels_judge — 등급 라벨(qrels) 제작 2단계: LLM 심판 채점

qrels_pool.py 가 모은 후보를 (질문, 근거) 쌍으로 심판에게 보여주고 3등급으로 받는다.

    2 = 이 발언이 질문에 **직접 답한다** (근거로 쓸 수 있음)
    1 = 주제는 관련 있으나 **답은 아니다** (배경·언급 수준)
    0 = 무관

왜 3등급인가
    기존 잣대는 0/1 이진이라 "찾았나"만 볼 수 있었다. 리랭커는 "찾은 것을 위로
    올리는" 일을 하므로 등급이 없으면 그 효과를 측정할 수 없다 — nDCG 를 쓰려면
    등급이 필요하고, strict(2점만 정답)/loose(1점 이상) 를 나눠 보려 해도 필요하다.

왜 앞뒤 맥락을 함께 주는가
    회의록 청크는 중앙값 36자, 81%가 150자 미만이다. "예, 그렇습니다" 한 줄만 떼서
    보여주면 심판이 무엇에 대한 답인지 알 수 없다. 실제 답변 생성도 앞뒤 턴을 함께
    넣으므로 심판에게도 같은 조건을 준다 (조건을 맞추지 않으면 라벨이 실제 사용
    조건과 어긋난다).

**심판도 틀린다 — 그래서 확신도를 함께 받는다**
    과거 answer_eval 에서 자동 채점 fail 21건 중 10건이 과잉 감점이었다(실측).
    심판을 무비판적으로 믿으면 새 자도 같은 방식으로 샌다. 그래서
      - 등급과 함께 confidence(high/low) 와 한 줄 근거를 받고
      - low confidence·경계 등급을 qrels_review 큐로 올려 사람이 표본 검수한다
    검수 결과로 심판 오답률을 계산해 리포트에 병기한다 — 그 숫자 없이는 새 점수도
    믿을 근거가 없다.

출력
    data/eval/qrels_judged.jsonl — 후보 1쌍당 1줄
    {"qid", "chunk_id", "grade", "confidence", "reason", "text_sha1", "found_by"}

실행
    python scripts/qrels_judge.py                # 전체
    python scripts/qrels_judge.py --limit 2      # 앞 2문항만
    python scripts/qrels_judge.py --resume       # 이미 채점된 쌍은 건너뜀
"""

import argparse
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from search_vector import _get_client                    # noqa: E402

POOL_PATH = PROJECT_ROOT / "data" / "eval" / "qrels_pool.jsonl"
EVAL_SET = PROJECT_ROOT / "data" / "eval" / "retrieval_eval_set_v2.json"
OUT_PATH = PROJECT_ROOT / "data" / "eval" / "qrels_judged.jsonl"

MODEL = "gpt-4o-mini"
BATCH = 8            # 한 번의 호출에 넣을 후보 수 (index-keyed 출력으로 유실 방지)
WORKERS = 8          # 동시 호출 수
MAX_TEXT = 700       # 후보 본문 절단 (앞뒤 맥락 300자씩과 합쳐 토큰 예산 보호)

# 단가 (USD / 1M tokens) — 비용 추정용
PRICE_IN, PRICE_OUT = 0.15, 0.60

SYSTEM = """당신은 검색 평가용 관련도 심판이다. 국회 회의록에서 검색된 발언이
질문에 얼마나 관련 있는지, **주어진 합격 기준에 비추어** 3등급으로 판정한다.

등급 기준 — 합격 기준을 몇 개 만족하는가로 정한다:
- 2 = **합격 기준을 모두 만족**한다. 이 발언이 질문의 답(또는 답의 핵심 일부)이다.
- 1 = 주제·대상은 관련 있으나 **합격 기준 중 일부를 만족하지 못한다**. 배경 언급, 지나가는 참조, 절차 발언.
- 0 = 질문과 무관하다.

판정 규칙:
- **합격 기준을 하나씩 대조하라.** 인상으로 판정하지 말고 기준별로 만족 여부를 따진다.
- '앞뒤 맥락'은 그 발언이 무엇에 대한 것인지 파악하는 보조 자료다. 등급은 '발언 본문'을
  기준으로 매기되, 맥락 덕분에 본문이 질문에 답하는 것이 확인되면 반영한다.
- **★사건·사안이 다르면 표현이 아무리 비슷해도 0 또는 1이다.★** 이것이 가장 흔한 오판이다.
  예: "티메프 사태 피해자 구제" 질문에 전세사기 피해주택 매입 발언은 '피해자 구제'가
  겹쳐도 다른 사건이므로 0이다.
- 위원회 조건은 **그 회의에서 나온 발언인가**를 뜻한다. 발언자의 소속 위원회는 따지지 않는다.
- "예, 그렇습니다" 같은 짧은 응답은 맥락상 질문의 답을 확정하는 경우에만 2다.
- 발언의 정치적 입장이나 사실 여부는 평가하지 마라. 오직 **기준을 만족하는가**만 본다.
- 확신이 서지 않으면 confidence 를 "low" 로 표기하라.

반드시 아래 JSON 만 출력한다:
{"results":[{"i":후보번호,"grade":0|1|2,"confidence":"high"|"low","reason":"어느 기준이 충족/미충족인지 한 줄"}]}
후보 번호(i)는 입력에 주어진 번호를 그대로 쓴다. 모든 후보에 대해 하나씩 출력한다."""


def render_candidate(i: int, c: dict) -> str:
    parts = [f"[후보 {i}]",
             f"발언자: {c.get('speaker') or '미상'}"
             + (f" ({c['role']})" if c.get("role") else ""),
             f"위원회: {c.get('committee')}  날짜: {c.get('date')}"]
    if c.get("prev"):
        parts.append(f"(앞 맥락) {c['prev']}")
    parts.append(f"발언 본문: {(c.get('text') or '')[:MAX_TEXT]}")
    if c.get("next"):
        parts.append(f"(뒤 맥락) {c['next']}")
    return "\n".join(parts)


def judge_batch(question: str, batch: list[tuple[int, dict]],
                criteria: list[str] | None = None) -> tuple[list[dict], int, int]:
    """후보 묶음 1개 채점 → (결과 목록, 입력토큰, 출력토큰).

    index-keyed 출력을 강제한다 — 과거 POL-5 배치 판정에서 순서 기반 매칭이
    38% 유실을 냈던 전례가 있어 번호로 되짚는다.
    """
    body = "\n\n".join(render_candidate(i, c) for i, c in batch)
    resp = _get_client().chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM},
            # 합격 기준을 함께 넘긴다 — 이 평가셋을 다시 만든 이유가 "기준으로 판정"이다.
            # 기준 없이 "관련 있나"만 물으면 심판이 인상으로 답하고 단어 겹침으로 흐른다.
            {"role": "user", "content": f"질문: {question}\n\n합격 기준:\n"
                                        + "\n".join(f"  {i}. {c}"
                                                    for i, c in enumerate(criteria or [], 1))
                                        + f"\n\n{body}"},
        ],
    )
    raw = json.loads(resp.choices[0].message.content or "{}").get("results", [])
    by_i = {}
    for r in raw:
        try:
            by_i[int(r["i"])] = r
        except (KeyError, TypeError, ValueError):
            continue

    out = []
    for i, c in batch:
        r = by_i.get(i)
        if r is None:
            # 심판이 빠뜨린 후보 — 조용히 0 으로 두지 않는다. 미판정으로 남겨
            # 검수 큐가 반드시 집어가게 한다 (유실을 통과로 만들지 않기)
            out.append({"chunk_id": c["chunk_id"], "grade": None, "confidence": "low",
                        "reason": "심판 응답 누락", "found_by": c["found_by"],
                        "text_sha1": c["text_sha1"]})
            continue
        grade = r.get("grade")
        grade = grade if grade in (0, 1, 2) else None
        out.append({
            "chunk_id": c["chunk_id"],
            "grade": grade,
            "confidence": "low" if (r.get("confidence") != "high" or grade is None) else "high",
            "reason": str(r.get("reason", ""))[:200],
            "found_by": c["found_by"],
            "text_sha1": c["text_sha1"],
        })
    return out, resp.usage.prompt_tokens, resp.usage.completion_tokens


def load_pool(limit: int = 0) -> list[dict]:
    rows = []
    for line in POOL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("_meta"):
            continue
        rows.append(r)
    return rows[:limit] if limit else rows


def load_done() -> set:
    if not OUT_PATH.exists():
        return set()
    done = set()
    for line in OUT_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            done.add((r["qid"], r["chunk_id"]))
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N문항만")
    ap.add_argument("--resume", action="store_true", help="이미 채점된 쌍은 건너뜀")
    args = ap.parse_args()

    if not POOL_PATH.exists():
        print(f"[FAIL] 풀 파일 없음: {POOL_PATH} — 먼저 qrels_pool.py 를 실행하세요")
        sys.exit(1)

    global CRIT
    CRIT = {q["id"]: q["pass_criteria"]
            for q in json.loads(EVAL_SET.read_text(encoding="utf-8"))["questions"]}
    pool = load_pool(args.limit)
    done = load_done() if args.resume else set()
    if done:
        print(f"resume — 이미 채점된 {len(done):,}쌍 건너뜀")

    # (질문, 후보묶음) 작업 목록
    tasks = []
    for row in pool:
        cands = [c for c in row["candidates"] if (row["qid"], c["chunk_id"]) not in done]
        for s in range(0, len(cands), BATCH):
            chunk = [(i, c) for i, c in enumerate(cands[s:s + BATCH], start=s)]
            tasks.append((row["qid"], row["question"], chunk, CRIT.get(row["qid"], [])))

    if not tasks:
        print("채점할 후보가 없습니다 (모두 완료)")
        return

    total_pairs = sum(len(t[2]) for t in tasks)
    print(f"{len(pool)}문항 / {total_pairs:,}쌍 / 배치 {len(tasks)}개 / 동시 {WORKERS}")

    t0 = time.time()
    in_tok = out_tok = 0
    results: list[dict] = []
    failed = 0

    mode = "a" if (args.resume and OUT_PATH.exists()) else "w"
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open(mode, encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(judge_batch, q, ch, cr): (qid, ch) for qid, q, ch, cr in tasks}
        for n, fut in enumerate(as_completed(futs), start=1):
            qid, ch = futs[fut]
            try:
                got, i_tok, o_tok = fut.result()
            except Exception as e:
                # 배치 실패도 통과로 만들지 않는다 — 미판정으로 기록해 검수 큐가 집어가게
                failed += 1
                got = [{"chunk_id": c["chunk_id"], "grade": None, "confidence": "low",
                        "reason": f"심판 호출 실패: {type(e).__name__}",
                        "found_by": c["found_by"], "text_sha1": c["text_sha1"]}
                       for _i, c in ch]
                i_tok = o_tok = 0
            in_tok += i_tok
            out_tok += o_tok
            for g in got:
                g["qid"] = qid
                f.write(json.dumps(g, ensure_ascii=False) + "\n")
                results.append(g)
            f.flush()
            if n % 25 == 0 or n == len(tasks):
                cost = (in_tok * PRICE_IN + out_tok * PRICE_OUT) / 1e6
                print(f"  {n}/{len(tasks)} 배치  ({time.time() - t0:.0f}초, ${cost:.3f})")

    dist = {g: sum(1 for r in results if r["grade"] == g) for g in (2, 1, 0)}
    unjudged = sum(1 for r in results if r["grade"] is None)
    low = sum(1 for r in results if r["confidence"] == "low")
    cost = (in_tok * PRICE_IN + out_tok * PRICE_OUT) / 1e6

    print(f"\n{len(results):,}쌍 채점 / {time.time() - t0:.0f}초 / ${cost:.3f}")
    print(f"  등급 2(직접 답변) {dist[2]:,}  /  1(관련) {dist[1]:,}  /  0(무관) {dist[0]:,}")
    print(f"  미판정 {unjudged}  /  low confidence {low:,} ({low / max(1, len(results)):.0%})")
    if failed:
        print(f"⚠ 배치 호출 실패 {failed}건 — 해당 쌍은 미판정으로 기록됨 (--resume 로 재시도)")
    print(f"→ {OUT_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
