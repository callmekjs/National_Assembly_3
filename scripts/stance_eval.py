"""POL-7 입장 판정 eval — 블라인드 사람 라벨 vs LLM 판정 일치도.

순수 로직(parse_label_sheet·agreement)은 DB·LLM 없이 테스트 가능.
CLI 는 라벨 파일 파싱 → issue_stances 조회 → 일치도·혼동행렬 → JSON·리포트 저장.

실행:
  python scripts/stance_eval.py --issue medical-reform
"""
import io
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

STANCES = ("support", "oppose", "concern", "neutral", "none")
_TURN_RE = re.compile(r"`([^`]+)`")
_LABEL_RE = re.compile(r"입장:\s*([A-Za-z]+)")


def parse_label_sheet(text: str) -> dict[str, str]:
    """라벨 파일 → {turn_id: stance}. 백틱 turn_id 줄 뒤 `입장: <토큰>` 줄을 짝짓는다.
    빈칸·허용밖 토큰은 제외(경고는 호출측에서 시트 항목 수와 비교해 판단)."""
    labels: dict[str, str] = {}
    cur: str | None = None
    for line in text.splitlines():
        m = _TURN_RE.search(line)
        if m:
            cur = m.group(1)
            continue
        lm = _LABEL_RE.search(line)
        if lm and cur is not None:
            tok = lm.group(1).lower()
            if tok in STANCES and cur not in labels:
                labels[cur] = tok
            cur = None  # `입장:` 줄을 소비 — 다음 turn_id 까지 대기
    return labels
