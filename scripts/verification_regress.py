"""
검증층 1단계 회귀 스모크 — 확정 실패 8건 재실행 → flag 발생 여부 리포트.

목표 (spec §7-3): 탐지율 7/8 (eval_011 은 §4-3 결정 ④ 2단계 이월로 미커버).
주의: flag 는 "오류가 재발했을 때" 뜬다 — 이번 생성에서 LLM 이 오류를 내지
않으면 flag 0 이 정상이다. 따라서 이 스크립트는 (a) 예외 없이 완주하는지
(b) flag·답변을 사람이 대조할 리포트를 남기는지가 합격 기준이고,
flag 개수 자체는 사람 판정 재료다 (자동채점 fail 의 절반은 과잉감점 — 기존 교훈).

실행: python scripts/verification_regress.py
비용: gpt-4o-mini 8회 (~$0.005) — 신규 검증 연산 자체는 $0.
출력: data/eval/verification_regress_report.md

주의 (구현 시 실측): answer_eval_set_router.json 의 최상위 구조는 브리프의
{"items": [...]} 가 아니라 리스트 자체다. 문항 dict 에 "mode" 키는 없어
qa 모드로 고정 실행한다 (eval 셋 전체가 qa 라우터 평가용).
"""

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

TARGET_IDS = ["eval_011", "eval_013", "eval_019", "eval_029",
              "eval_035", "eval_055", "eval_057", "eval_068"]
EVAL_SET = Path(__file__).parent.parent / "data" / "eval" / "answer_eval_set_router.json"
REPORT = Path(__file__).parent.parent / "data" / "eval" / "verification_regress_report.md"


def main():
    import db

    db.init_pool()  # scripts/answer_eval_build.py 와 동일한 커넥션 풀 초기화 패턴
    from answer import generate_answer

    items = {it["id"]: it for it in json.loads(EVAL_SET.read_text(encoding="utf-8"))}
    lines = ["# 검증층 1단계 회귀 스모크 (확정 8건)", ""]
    flagged = 0
    for eid in TARGET_IDS:
        item = items[eid]
        result = generate_answer(item["query"], mode=item.get("mode", "qa"))
        v = result.get("verification") or {"flags": [], "detail": {}}
        if v["flags"]:
            flagged += 1
        print(f"{eid}: flags={v['flags']}")
        lines += [
            f"## {eid} ({item.get('type', '?')})",
            f"- query: {item['query']}",
            f"- flags: `{v['flags']}`",
            f"- detail: `{json.dumps(v['detail'], ensure_ascii=False)}`",
            f"- grounding 강등: {'예 (FULL→PARTIAL)' if v['flags'] else '아니오'}",
            "- answer:", "```", result["answer"], "```", "",
        ]
    lines.insert(2, f"**flag 발생: {flagged}/8** (사람 대조 필요 — flag 0 이어도 이번 생성이 정상이면 통과)")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nflag {flagged}/8 — 리포트: {REPORT}")


if __name__ == "__main__":
    main()
