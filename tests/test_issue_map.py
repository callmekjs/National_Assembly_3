"""이슈 매핑 순수 로직 테스트 — DB·LLM 없이 실행.

실행: python tests/test_issue_map.py  (pytest 도 지원)
"""
import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":  # pytest 캡처와 충돌 방지
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_issue_map import (  # noqa: E402
    cut_candidates, load_seed, make_batches, parse_judge_response,
)


def check(name: str, cond: bool, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


_VALID_ISSUE = {
    "issue_id": "test-issue", "title": "테스트", "type": "event",
    "description": "테스트 이슈.", "seed_keywords": ["키워드"],
    "seed_queries": ["질문 하나"], "anchor_meetings": [],
}


def _write_seed(tmp_path: Path, issues) -> Path:
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_seed_valid(tmp_path):
    issues = load_seed(_write_seed(tmp_path, [_VALID_ISSUE]))
    check("정상 로드", len(issues) == 1 and issues[0]["issue_id"] == "test-issue")


def test_load_seed_rejects_missing_field(tmp_path):
    bad = {k: v for k, v in _VALID_ISSUE.items() if k != "description"}
    try:
        load_seed(_write_seed(tmp_path, [bad]))
        check("필수 필드 누락 거부", False)
    except ValueError:
        check("필수 필드 누락 거부", True)


def test_load_seed_rejects_dup_id(tmp_path):
    try:
        load_seed(_write_seed(tmp_path, [_VALID_ISSUE, dict(_VALID_ISSUE)]))
        check("issue_id 중복 거부", False)
    except ValueError:
        check("issue_id 중복 거부", True)


def test_load_seed_rejects_bad_type(tmp_path):
    bad = dict(_VALID_ISSUE, type="both")
    try:
        load_seed(_write_seed(tmp_path, [bad]))
        check("type 은 event|policy 만", False)
    except ValueError:
        check("type 은 event|policy 만", True)


def test_cut_candidates():
    cands = {
        "c1": {"vec_score": 0.55, "kw_hit": False},   # 유사도 통과
        "c2": {"vec_score": 0.20, "kw_hit": True},    # 키워드 매치로 통과
        "c3": {"vec_score": 0.20, "kw_hit": False},   # 둘 다 미달 → 컷
        "c4": {"vec_score": None, "kw_hit": False},   # 벡터 무점수·키워드 없음 → 컷
        "c5": {"vec_score": 0.40, "kw_hit": False},   # 경계값 = 통과 (이상)
    }
    kept = cut_candidates(cands, threshold=0.4)
    check("컷 결과", sorted(kept) == ["c1", "c2", "c5"], sorted(kept))


def test_make_batches():
    check("20개 분할", [len(b) for b in make_batches(list(range(45)), size=20)] == [20, 20, 5])
    check("빈 입력", make_batches([], size=20) == [])


def test_parse_judge_response():
    check("정상", parse_judge_response('{"relevant": [0, 2]}', 5) == [0, 2])
    check("빈 목록도 정상", parse_judge_response('{"relevant": []}', 5) == [])
    check("JSON 아님 → None", parse_judge_response("0, 2번이 관련", 5) is None)
    check("relevant 없음 → None", parse_judge_response('{"order": [1]}', 5) is None)
    check("범위 밖 번호는 버림", parse_judge_response('{"relevant": [0, 99]}', 5) == [0])
    check("정수 아닌 항목은 버림", parse_judge_response('{"relevant": [0, "1"]}', 5) == [0])
    check("중복 번호는 dedup", parse_judge_response('{"relevant": [0, 0, 2]}', 5) == [0, 2])


def test_sample_rows_deterministic():
    from issue_spotcheck import sample_rows
    rows = list(range(100))
    a, b = sample_rows(rows, n=10, seed=42), sample_rows(rows, n=10, seed=42)
    check("seed 고정 재현", a == b and len(a) == 10, (a, b))
    check("표본보다 적으면 전부", sample_rows([1, 2], n=10, seed=42) == [1, 2])


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_load_seed_valid(tmp)
        test_load_seed_rejects_missing_field(tmp)
        test_load_seed_rejects_dup_id(tmp)
        test_load_seed_rejects_bad_type(tmp)
    test_cut_candidates()
    test_make_batches()
    test_parse_judge_response()
    test_sample_rows_deterministic()
    print("ALL PASS")
