"""
답변 모델 비교표 — report_content_eval.py 결과 여러 개를 나란히 놓는다.

무엇을 비교하는가
    같은 20문항 평가셋·같은 검색·같은 재순위 설정에서 **답변 모델만** 바꿨을 때의
    내용 정확성. 따라서 표에 보이는 차이는 모델 차이로 읽어도 된다.

비용에 대한 경고
    `gpt-5.6` 계열의 단가는 확인되지 않았다(지식 시점 이후 모델). 그래서 이 표는
    금액이 아니라 **출력 토큰 수**를 싣는다 — 토큰은 실측이고 금액은 추정이다.
    추정 금액을 표에 실으면 그 숫자가 근거처럼 읽히기 때문에 싣지 않는다.
    실단가가 확인되면 `answer.PRICES` 에 넣을 것.

실행
    python scripts/compare_answer_models.py A.json B.json C.json --label mini sol terra
"""

import argparse
import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--label", nargs="*", default=None)
    args = ap.parse_args()

    ds = [load(f) for f in args.files]
    labels = args.label or [Path(f).stem for f in args.files]
    W = max(max(len(x) for x in labels), 8)

    print("=" * (22 + (W + 3) * len(ds)))
    print("답변 모델 비교 — 같은 평가셋·같은 검색·같은 재순위, 답변 모델만 다름")
    print("=" * (22 + (W + 3) * len(ds)))
    head = "  " + " ".join(f"{l:>{W}}" for l in labels)
    print(f"{'':<20}{head}")

    def row(name: str, fn, fmt="{}"):
        vals = " ".join(f"{fmt.format(fn(d)):>{W}}" for d in ds)
        print(f"  {name:<18}{vals}")

    row("전체 통과", lambda d: f"{d['pass']}/{d['n']}")
    row("통과율", lambda d: f"{d['pass'] / d['n']:.1%}")
    row("합격 기준", lambda d: f"{d['criteria_met']}/{d['criteria_total']}")
    row("모순(환각)", lambda d: f"{d['contradictions']}건")

    ok = [[r for r in d["rows"] if "error" not in r] for d in ds]
    # 길이는 비용의 대리 지표다 — 단가를 모르는 모델이 있으므로 이것으로 판단한다
    avg = " ".join(f"{sum(r['chars'] for r in o) // max(len(o), 1):>{W}}" for o in ok)
    print(f"  {'평균 길이(자)':<17}{avg}")

    # 유형별
    types = sorted({r["type"] for o in ok for r in o})
    print(f"\n  {'[유형별 통과]':<18}")
    for t in types:
        cells = []
        for o in ok:
            sub = [r for r in o if r["type"] == t]
            cells.append(f"{sum(1 for r in sub if r['overall'] == 'pass')}/{len(sub)}")
        print(f"  {t:<18}" + " ".join(f"{c:>{W}}" for c in cells))

    # 문항별 — 어디서 갈렸는지
    ids = [r["id"] for r in ds[0]["rows"]]
    maps = [{r["id"]: r for r in o} for o in ok]
    print(f"\n  {'[문항별]':<18}" + "   (O=통과 X=미통과, 괄호는 모순 건수)")
    for qid in ids:
        cells = []
        for m in maps:
            r = m.get(qid)
            if not r:
                cells.append("-")
                continue
            mark = "O" if r["overall"] == "pass" else "X"
            n = len(r.get("contradictions") or [])
            cells.append(f"{mark}({n})" if n else mark)
        flip = "  ←" if len({c[0] for c in cells}) > 1 else ""
        print(f"  {qid:<18}" + " ".join(f"{c:>{W}}" for c in cells) + flip)

    print("\n  ※ 단가 미확인 모델이 있어 금액은 싣지 않는다 — 토큰·통과율로 판단할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
