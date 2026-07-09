"""이슈 매핑 2단 등급화 (POL-3 — 사용자 결정 4).

흐름 (docs/issue_module_spec.md — 사용자 결정 4): issue_chunks 의 기존 매핑(전량,
judge 무관)을 이슈별로 다시 읽어 gpt-4o-mini 배치 판정으로 core(실질 논의) /
mention(스치는 언급·절차 발언) 두 등급으로 분류하고 judge 컬럼을 갱신한다.
행은 삭제하지 않는다 (POL-4 타임라인은 전체 소비, POL-5·POL-6·게이트는 core만).

실행:
  python scripts/issue_tier_pass.py --dry-run              # 청크 수·예상 비용만
  python scripts/issue_tier_pass.py                        # 전체 이슈 등급화
  python scripts/issue_tier_pass.py --issue coupang-issues  # 단일 이슈만
  python scripts/issue_tier_pass.py --issue X --judge-model gpt-4o --batch-size 10
      # 등급화 모델·배치 재지정 (기본은 이슈별 judge_model/judge_batch 필드)

재실행해도 안전 (idempotent): judge 가 이미 llm_core/llm_mention 이어도 전량 다시
읽어 재분류·덮어쓴다 — 프롬프트·모델을 바꿔 재등급화할 때 그대로 쓴다.
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

if __name__ == "__main__":  # import 시(테스트) 부작용 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from build_issue_map import (  # noqa: E402
    MAX_TRANSIENT_RETRIES, _est_cost_usd, _transient_errors,
    fetch_texts, load_seed, make_batches,
)

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"

TIER_VERSION = "t1.1"    # 등급화 방법 버전 — 기록용 (DB 컬럼 없음, 로그 출력만)
# t1.1 (2026-07-09): t1.0 게이트 판독(87.5% FAIL)에서 확인된 core 오염 2유형을 명시 차단 —
# ① 다른 사안이 중심인 발언(교차 이슈 오염) ② 법안 상정·의사진행 등 절차 발언
BATCH_SIZE = 20          # LLM 등급화 배치 크기
_MODEL = "gpt-4o-mini"


_TIER_SYSTEM = """당신은 국회 회의록 발언이 특정 쟁점에서 어떤 비중으로 다뤄지는지 등급을 매기는 도우미다.
쟁점 정의와 번호 매긴 '이미 관련 판정된' 발언 목록이 주어진다. 각 발언을 두 등급으로 분류한다:
- core: 쟁점의 사건·정책을 실질적으로 논의한다 (질의·답변·주장·보고의 중심 주제가 바로 이 쟁점).
- mention: 쟁점을 스치듯 언급한다 (다른 주제를 논의하는 중 참조·시점 언급·나열, 인사말·절차 발언 포함).
반드시 mention 으로 분류해야 하는 경우:
- 발언의 중심 주제가 이 쟁점이 아닌 다른 사안·다른 쟁점이고, 이 쟁점은 배경·예시·수사로만 등장한다.
- 법안 상정 선언, 심사 순서·진행 요청, 표결 처리 방식 항의 등 의사진행·절차 발언이다 (쟁점 단어가 들어 있어도).
확신이 없으면 mention 으로 분류한다 — core 등급의 순도를 우선한다.
반드시 아래 JSON 만 출력: {"core": [core 등급인 발언 번호 목록]}"""


def parse_tier_response(content: str, batch_size: int) -> list[int] | None:
    """등급화 응답 → core 번호 목록. 계약은 build_issue_map.parse_judge_response 와
    동일하되 key 만 다르다: 구조 자체가 틀리면 None(재시도 신호), 개별 항목 오류
    (범위 밖·비정수)는 그 항목만 버린다."""
    try:
        nums = json.loads(content).get("core")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(nums, list):
        return None
    filtered = [n for n in nums if isinstance(n, int) and 0 <= n < batch_size]
    return list(dict.fromkeys(filtered))  # 순서 보존 dedup


def _classify_batch(client, issue: dict, batch: list[tuple[str, dict]],
                     model: str = _MODEL) -> list[int] | None:
    """배치 1개 등급화. 형식 위반 1회 재시도, 일시 오류는 지수 백오프
    (build_issue_map._judge_batch 와 동일 패턴)."""
    docs = "\n".join(
        f"[{i}] ({m['committee']} {m['date']}) {m['speaker'] or ''} {m['role'] or ''}: {m['text']}"
        for i, (_, m) in enumerate(batch)
    )
    user = (f"쟁점: {issue['title']}\n정의: {issue['description']}\n\n발언 목록:\n{docs}")
    for attempt in range(2):          # 형식 위반 재시도 1회
        delay = 2
        for retry in range(MAX_TRANSIENT_RETRIES):  # 일시 오류 재시도
            try:
                resp = client.chat.completions.create(
                    model=model, temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": _TIER_SYSTEM},
                              {"role": "user", "content": user}],
                )
                break
            except _transient_errors() as e:
                if retry == MAX_TRANSIENT_RETRIES - 1:
                    raise
                print(f"[retry] {type(e).__name__} — {delay}s 대기")
                time.sleep(delay)
                delay = min(delay * 2, 60)
        result = parse_tier_response(resp.choices[0].message.content, len(batch))
        if result is not None:
            return result
    return None  # 2회 모두 형식 위반 → 판정 보류 (기존 judge 유지, 누락 우선)


def store_tiers(issue_id: str, core_ids: list[str], mention_ids: list[str]) -> tuple[int, int]:
    """이슈 단위 한 트랜잭션으로 judge 갱신. 판정 보류 청크(chunk_id 가 둘 다에
    없음)는 UPDATE 대상에서 빠져 기존 judge 값을 그대로 유지한다."""
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        n_core = 0
        if core_ids:
            cur.execute(
                "UPDATE issue_chunks SET judge = 'llm_core' "
                "WHERE issue_id = %s AND chunk_id = ANY(%s)",
                (issue_id, core_ids),
            )
            n_core = cur.rowcount
        n_mention = 0
        if mention_ids:
            cur.execute(
                "UPDATE issue_chunks SET judge = 'llm_mention' "
                "WHERE issue_id = %s AND chunk_id = ANY(%s)",
                (issue_id, mention_ids),
            )
            n_mention = cur.rowcount
    return n_core, n_mention


def classify_issue(client, issue: dict, judge_model: str = _MODEL,
                    batch_size: int = BATCH_SIZE, dry_run: bool = False) -> dict:
    """이슈 1개 등급화. 기존 judge 값과 무관하게 issue_chunks 전량을 다시 읽어
    분류한다 (재실행 idempotent). 판정 보류 청크는 dropped 로 집계하고
    core+mention+dropped == total 을 검증한다 (조용한 유실 금지)."""
    from db import get_conn
    t0 = time.time()
    iid = issue["issue_id"]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT chunk_id FROM issue_chunks WHERE issue_id = %s ORDER BY chunk_id",
                     (iid,))
        chunk_ids = [r[0] for r in cur.fetchall()]
    total = len(chunk_ids)
    if dry_run:
        return {"issue_id": iid, "total": total, "est_cost": round(_est_cost_usd(total), 3)}

    meta = fetch_texts(chunk_ids)
    missing = [cid for cid in chunk_ids if cid not in meta]
    if missing:
        print(f"[WARN] {iid}: 메타 조회 누락 {len(missing)}건 — 판정 보류 (기존 judge 유지)")
    items = [(cid, meta[cid]) for cid in chunk_ids if cid in meta]

    core_ids: list[str] = []
    mention_ids: list[str] = []
    dropped = len(missing)
    throttle = judge_model != _MODEL  # 30k TPM 429 실측 대응 — mini 외 모델은 배치 간 대기
    for batch in make_batches(items, size=batch_size):
        result = _classify_batch(client, issue, batch, model=judge_model)
        if result is None:
            dropped += len(batch)
            print(f"[WARN] {iid}: 배치 등급화 보류(형식 위반) — {len(batch)}건, 기존 judge 유지")
        else:
            core_set = set(result)
            for i, (cid, _m) in enumerate(batch):
                (core_ids if i in core_set else mention_ids).append(cid)
        if throttle:
            time.sleep(3)

    n_core, n_mention = store_tiers(iid, core_ids, mention_ids)
    if n_core + n_mention + dropped != total:
        raise RuntimeError(
            f"{iid}: 행수 불일치 (core {n_core} + mention {n_mention} + 보류 {dropped} "
            f"!= 전체 {total})")
    return {"issue_id": iid, "total": total, "core": n_core, "mention": n_mention,
            "dropped": dropped, "secs": round(time.time() - t0, 1)}


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from db import init_pool, close_pool
    from search_vector import _get_client

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="청크 수·예상 비용만")
    ap.add_argument("--issue", help="단일 이슈만 등급화 (issue_id)")
    ap.add_argument("--judge-model", default=None,
                     help="등급화 LLM (기본: 이슈별 judge_model 필드, 없으면 " + _MODEL + ")")
    ap.add_argument("--batch-size", type=int, default=None,
                     help="등급화 배치 크기 (기본: 이슈별 judge_batch 필드, 없으면 "
                          f"{BATCH_SIZE})")
    args = ap.parse_args()

    issues = load_seed(SEED_PATH)
    if args.issue:
        issues = [i for i in issues if i["issue_id"] == args.issue]
        if not issues:
            print(f"[FAIL] issue_id 없음: {args.issue}")
            sys.exit(1)

    init_pool()
    client = None if args.dry_run else _get_client()
    print(f"TIER_VERSION={TIER_VERSION}")

    failures = []
    total_cost = 0.0
    for issue in issues:
        # 등급화 모델·배치: CLI 플래그(명시) > 이슈별 필드 > 기본값 (build_issue_map 과 동일 우선순위)
        judge_model = args.judge_model or issue.get("judge_model") or _MODEL
        batch_size = args.batch_size or issue.get("judge_batch") or BATCH_SIZE
        if judge_model != _MODEL:
            print(f"[WARN] {issue['issue_id']}: judge_model={judge_model} — dry-run 비용 추정은 "
                  f"{_MODEL} 단가 기준이라 실제와 다를 수 있음")
        try:
            r = classify_issue(client, issue, judge_model=judge_model, batch_size=batch_size,
                                dry_run=args.dry_run)
        except Exception as e:
            failures.append((issue["issue_id"], f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {issue['issue_id']}: {type(e).__name__}: {e}")
            continue
        total_cost += r.get("est_cost", 0)
        print(f"[{'DRY' if args.dry_run else 'OK'}] {json.dumps(r, ensure_ascii=False)}")
    close_pool()

    if args.dry_run:
        print(f"예상 등급화 입력 비용 합계: ~${total_cost:.2f}")
    if failures:  # 조용한 유실 금지 — 실패 이슈를 남기고 비정상 종료
        print(f"[FAIL] {len(failures)}개 이슈 실패: {[f[0] for f in failures]}")
        sys.exit(1)
    print("전체 완료")


if __name__ == "__main__":
    main()
