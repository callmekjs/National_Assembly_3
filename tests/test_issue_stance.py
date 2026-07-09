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
    # index-keyed: {"items":[{"i":n,"stance":...}]} → 길이 batch 리스트(미판정 None)
    ok = '{"items":[{"i":0,"stance":"support"},{"i":1,"stance":"oppose"}]}'
    check("정상 2개", parse_stance_response(ok, 2) == ["support", "oppose"])
    check("누락 인덱스는 None", parse_stance_response('{"items":[{"i":0,"stance":"support"}]}', 2) == ["support", None])
    check("과다 인덱스 무시", parse_stance_response('{"items":[{"i":0,"stance":"support"},{"i":9,"stance":"oppose"}]}', 2) == ["support", None])
    check("허용밖 토큰은 그 위치 None", parse_stance_response('{"items":[{"i":0,"stance":"yes"}]}', 1) == [None])
    check("JSON 아님 → None", parse_stance_response("support, oppose", 2) is None)
    check("items 키 없음 → None", parse_stance_response('{"x":[]}', 1) is None)


if __name__ == "__main__":
    test_parse_stance_response()
    print("all passed")
