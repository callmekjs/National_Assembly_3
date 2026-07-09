"""POL-5 입장 판정 파싱 순수 테스트 — DB·LLM 없이 실행.
실행: python tests/test_issue_stance.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_issue_stance import parse_stance_response  # noqa: E402


def check(name, cond, got=None):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — got: {got!r}"))
    assert cond, f"{name} — got: {got!r}"


def test_parse_stance_response():
    ok = '{"stances":["support","oppose","concern","neutral","none"]}'
    check("정상 5개", parse_stance_response(ok, 5) == ["support", "oppose", "concern", "neutral", "none"])
    check("길이 불일치 → None", parse_stance_response('{"stances":["support"]}', 3) is None)
    check("허용 밖 토큰 → None", parse_stance_response('{"stances":["yes","no"]}', 2) is None)
    check("JSON 아님 → None", parse_stance_response("support, oppose", 2) is None)
    check("stances 키 없음 → None", parse_stance_response('{"x":[]}', 0) is None)


if __name__ == "__main__":
    test_parse_stance_response()
    print("all passed")
