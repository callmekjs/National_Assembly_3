"""답변 평가셋 3단계 — LLM-judge 로 4기준 1차 자동 채점.

채점 4기준 (각 pass/fail/na + 근거):
    ① faithfulness      근거 블록에 없는 내용을 지어내지 않았나 (환각)
    ② citation_accuracy [n] 이 실제 그 주장의 근거를 가리키나
    ③ classification    여야·정당·정부측 분류가 맞나 (틀리면 fail, 분류 없으면 na)
    ④ refusal           근거 없을 때 제대로 거절했나 / 근거 있는데 과잉거절 안 했나

심판에게 문항별 grading_notes·must_not_checklist 를 함께 제공 — 이게 채점 정확도의 핵심.
심판도 틀릴 수 있으므로 이 결과는 '초안'이고, 4단계 사람 검수로 확정한다.

입력: data/eval/answer_eval_set.json (2단계 산출)
출력: 같은 파일의 grades 필드 채움 + 콘솔 요약

실행: python scripts/answer_eval_judge.py
"""

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EVAL_SET = Path(__file__).parent.parent / "data" / "eval" / "answer_eval_set.json"
JUDGE_MODEL = "gpt-4o"  # 채점은 피채점(gpt-4o-mini)보다 상위 모델로 — 자기채점 편향 완화

SYSTEM = """당신은 국회 회의록 RAG 답변을 채점하는 엄격한 심사관이다.
평가 대상 답변은 '근거 블록'만 보고 작성돼야 한다. 아래 4기준을 각각 판정하라.

① faithfulness (근거 충실성): 답변의 모든 사실 주장이 근거 블록 안에 있는가?
   근거에 없는 사람·날짜·기관·수치·정책효과를 하나라도 지어냈으면 fail.
② citation_accuracy (인용 정확성): [n] 번호가 실제로 그 주장을 뒷받침하는 근거를
   가리키는가? 엉뚱한 근거를 인용했으면 fail. 인용이 아예 없으면 na.
③ classification (분류 정확성): 여야·정당·정부측·증인 분류가 정확한가?
   틀리게 분류했으면 fail. 답변이 분류를 언급하지 않으면 na.
④ refusal (거절 적절성): 질문이 요구한 근거가 없으면 '확인할 수 없다'고 정직하게
   거절했는가? 근거 없이 억지 답변하면 fail. 반대로 근거가 충분한데 과잉 거절해도 fail.
   expect_refusal=true 문항은 반드시 거절해야 pass.

각 기준은 "pass"/"fail"/"na" 중 하나. overall 은 fail 이 하나라도 있으면 "fail",
아니면 "pass". 반드시 아래 JSON 만 출력:
{"faithfulness":"...","citation_accuracy":"...","classification":"...","refusal":"...","overall":"...","notes":"한 줄 근거"}"""


def judge_one(client, item: dict) -> dict:
    must_not = "\n".join(f"  - {m}" for m in item.get("must_not_checklist", [])) or "  (없음)"
    user = f"""[질문] {item['query']}
[expect_refusal] {item['expect_refusal']}
[채점 지침] {item.get('grading_notes','(없음)')}
[금지 사항(하면 fail)]
{must_not}

[시스템 답변]
{item['answer']}

[인용된 근거 요약]
{json.dumps(item.get('citations', []), ensure_ascii=False)}

[LLM 이 실제로 본 근거 블록]
{item.get('source_block') or '(사전차단 — 근거 블록 없음)'}"""

    resp = client.chat.completions.create(
        model=JUDGE_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": user}],
    )
    return json.loads(resp.choices[0].message.content)


def main() -> None:
    from search_vector import _get_client

    client = _get_client()
    items = json.load(open(EVAL_SET, encoding="utf-8"))

    from collections import Counter
    overall = Counter()
    fails = []
    for i, item in enumerate(items, 1):
        try:
            g = judge_one(client, item)
        except Exception as e:
            g = {"faithfulness": "na", "citation_accuracy": "na", "classification": "na",
                 "refusal": "na", "overall": "error", "notes": f"채점 오류: {type(e).__name__}"}
        item["grades"].update({
            "faithfulness": g.get("faithfulness"),
            "citation_accuracy": g.get("citation_accuracy"),
            "classification": g.get("classification"),
            "refusal": g.get("refusal"),
            "overall": g.get("overall"),
            "judge_notes": g.get("notes", ""),
            "human_reviewed": False,
        })
        overall[g.get("overall")] += 1
        if g.get("overall") != "pass":
            fails.append((item["id"], item["type"], g))
        if i % 10 == 0:
            print(f"  {i}/{len(items)} 채점")

    Path(EVAL_SET).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 자동 채점 완료: {len(items)}문항 ===")
    print("overall:", dict(overall))
    passed = overall.get("pass", 0)
    print(f"1차 통과율: {passed}/{len(items)} = {passed/len(items):.1%}")
    print(f"\n=== 검수 대상 (fail/na/error) {len(fails)}건 ===")
    for qid, typ, g in fails:
        flags = [k for k in ("faithfulness", "citation_accuracy", "classification", "refusal")
                 if g.get(k) == "fail"]
        print(f"  {qid} ({typ}): {'/'.join(flags) or g.get('overall')} — {g.get('notes','')[:70]}")
    print("\n다음: 위 목록을 사람이 검수해 grades.human_reviewed=true 로 확정")


if __name__ == "__main__":
    main()
