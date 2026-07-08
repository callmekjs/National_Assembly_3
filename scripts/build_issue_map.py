"""이슈↔청크 매핑 파이프라인 (POL-3 단계 2).

흐름 (docs/issue_module_spec.md — 사용자 결정 3):
  issues_seed.json → 이슈별 [후보 수집(하이브리드 축 직접 호출) → 저점수 컷
  → gpt-4o-mini 배치 관련도 판정] → issues·issue_chunks 적재 (통과분만 — 누락 > 오염)

실행:
  python scripts/build_issue_map.py --dry-run     # 후보 수·예상 비용만
  python scripts/build_issue_map.py               # 전체 이슈 매핑
  python scripts/build_issue_map.py --issue martial-law   # 단일 이슈 재실행 (시드 수정 시)
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

ROOT = Path(__file__).parent.parent
SEED_PATH = ROOT / "data" / "issues" / "issues_seed.json"

MAP_VERSION = "v1.0"     # 매핑 방법 버전 — 수집·컷·판정 방식이 바뀌면 올린다
BATCH_SIZE = 20          # LLM 판정 배치 크기
DOC_CHARS = 600          # 판정에 보여줄 청크 발췌 길이 (reranker 와 동일)
PER_QUERY_VEC = 100      # seed_query 당 벡터 후보 수 (hnsw.ef_search=100 이 상한)
PER_KEYWORD_KW = 300     # seed_keyword 당 키워드 후보 수
_MODEL = "gpt-4o-mini"

_REQUIRED = ("issue_id", "title", "type", "description",
             "seed_keywords", "seed_queries", "anchor_meetings")


def load_seed(path: Path) -> list[dict]:
    """issues_seed.json 로드 + 검증. 시드 오류는 매핑 전체를 오염시키므로 즉시 실패."""
    issues = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for i, issue in enumerate(issues):
        for f in _REQUIRED:
            if f not in issue:
                raise ValueError(f"이슈 #{i}: 필수 필드 '{f}' 누락")
        if issue["type"] not in ("event", "policy"):
            raise ValueError(f"{issue['issue_id']}: type 은 event|policy (got {issue['type']!r})")
        if not issue["seed_keywords"] or not issue["seed_queries"]:
            raise ValueError(f"{issue['issue_id']}: seed_keywords·seed_queries 는 비울 수 없음")
        if issue["issue_id"] in seen:
            raise ValueError(f"issue_id 중복: {issue['issue_id']}")
        seen.add(issue["issue_id"])
    return issues


def cut_candidates(cands: dict[str, dict], threshold: float) -> dict[str, dict]:
    """저점수 컷 (1차 필터) — grounding 사전차단과 같은 기준:
    키워드 매치도 없고 벡터 유사도도 임계값 미만이면 LLM 판정에 보낼 가치가 없다."""
    return {
        cid: c for cid, c in cands.items()
        if c["kw_hit"] or (c["vec_score"] is not None and c["vec_score"] >= threshold)
    }


def make_batches(items: list, size: int = BATCH_SIZE) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def parse_judge_response(content: str, batch_size: int) -> list[int] | None:
    """판정 응답 → 관련 번호 목록. 구조 자체가 틀리면 None(재시도 신호),
    개별 항목 오류(범위 밖·비정수)는 그 항목만 버린다 (누락 우선)."""
    try:
        nums = json.loads(content).get("relevant")
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(nums, list):
        return None
    return [n for n in nums if isinstance(n, int) and 0 <= n < batch_size]
