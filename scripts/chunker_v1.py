"""
[6] chunker_v1
enriched_turns.jsonl을 RAG 검색에 적합한 chunk로 분할한다.

분할 규칙:
  - 짧은 turn (< 150자): 단독 청크로 생성 (short 플래그)
  - 일반 turn (150~2500자): turn 1개 = chunk 1개
  - 긴 turn (> 2500자): 문장 단위로 분할

입력 : data/v1/enriched/{source_id}/enriched_turns.jsonl
출력 : data/v1/chunks/{source_id}/chunks_v1.jsonl

실행:
    python scripts/chunker_v1.py              # 전체
    python scripts/chunker_v1.py 과방위 외통위  # 특정 위원회만
"""

import io
import json
import re
import sys
from pathlib import Path

from stage_io import report_failures, write_jsonl_atomic

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CHUNKER_VERSION = "v1.1"   # v1.1: 한국어 문장분할 보강 (공백 없는 경계 + 길이 강제 분할)

INPUT_ROOT  = Path(__file__).parent.parent / "data" / "v1" / "enriched"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "chunks"

SHORT_THRESHOLD = 150   # 자 미만 → short chunk
LONG_THRESHOLD  = 2500  # 자 초과 → 분할
HARD_SPLIT_MAX  = 3000  # 문장 경계 못 찾을 때 강제 절단 상한 (임베딩 8,192토큰 방어)

# 문장 경계: 구두점(. ! ? 。) 뒤에서 분할. 공백이 있든(정상) 없든("했습니다.그리고"
# — PDF 추출로 공백 소실) 모두 자른다. 소수점 숫자(3.14)는 뒤가 숫자라 제외.
_SENT_SPLIT = re.compile(r"(?<=[.!?。])(?=\s)|(?<=[.!?。])(?=[^\d\s])")


def _embed_text(turn: dict, text: str) -> str:
    """임베딩 대상 텍스트: 메타 접두사 + 본문."""
    committee    = turn.get("committee", "")
    meeting_date = turn.get("meeting_date", "")
    speaker      = turn.get("speaker", "")
    role         = turn.get("role") or ""
    role_str     = f" {role}" if role else ""
    return f"{committee} {meeting_date} {speaker}{role_str} 발언: {text}"


def _hard_split(s: str, size: int = HARD_SPLIT_MAX) -> list[str]:
    """문장 경계가 없는 초장문을 size 단위로 강제 절단 (임베딩 토큰 한도 방어).
    한 '문장'이 8,192토큰을 넘으면 임베딩 배치 전체가 400 으로 죽던 문제 방지."""
    return [s[i:i + size] for i in range(0, len(s), size)]


def _split_long(text: str, max_len: int = LONG_THRESHOLD) -> list[str]:
    """긴 텍스트를 문장 단위로 나눠 max_len 이하 청크로 묶는다.
    구두점이 없어 한 조각이 여전히 너무 길면 길이 기준으로 강제 분할한다."""
    sentences = []
    for sent in _SENT_SPLIT.split(text):
        sentences.extend(_hard_split(sent) if len(sent) > HARD_SPLIT_MAX else [sent])

    chunks, buf = [], ""
    for sent in sentences:
        if buf and len(buf) + len(sent) + 1 > max_len:
            chunks.append(buf.strip())
            buf = sent
        else:
            buf = (buf + " " + sent).strip() if buf else sent
    if buf:
        chunks.append(buf.strip())
    return chunks or [text]


def make_chunks(turns: list[dict]) -> list[dict]:
    chunks = []
    for i, turn in enumerate(turns):
        text     = turn.get("text", "").strip()
        turn_id  = turn.get("turn_id", "")

        # context 미리 준비
        ctx_before = ""
        if i > 0:
            prev = turns[i - 1]
            ctx_before = f"{prev.get('speaker','')} {prev.get('role','') or ''}: {prev.get('text','')[:80]}"
        ctx_after = ""
        if i < len(turns) - 1:
            nxt = turns[i + 1]
            ctx_after = f"{nxt.get('speaker','')} {nxt.get('role','') or ''}: {nxt.get('text','')[:80]}"

        if len(text) > LONG_THRESHOLD:
            parts = _split_long(text)
        else:
            parts = [text]

        for j, part in enumerate(parts, start=1):
            chunk_id = f"{turn_id}_chunk_{j:03d}"
            chunks.append({
                "chunk_id":        chunk_id,
                "turn_id":         turn_id,
                "chunk_type":      "utterance",
                "chunk_index":     j,
                "chunk_total":     len(parts),
                "source_id":       turn.get("source_id"),
                "committee":       turn.get("committee"),
                "folder":          turn.get("folder"),
                "file_name":       turn.get("file_name"),
                "meeting_date":    turn.get("meeting_date"),
                "speaker":         turn.get("speaker"),
                "role":            turn.get("role"),
                "page_start":      turn.get("page_start"),
                "page_end":        turn.get("page_end"),
                "text":            part,
                "context_before":  ctx_before,
                "context_after":   ctx_after,
                "embed_text":      _embed_text(turn, part),
                "is_short":        len(part) < SHORT_THRESHOLD,
                # policy enrichment 필드 전달
                "policy_domain":   turn.get("policy_domain"),
                "bill_refs":       turn.get("bill_refs", []),
                "utterance_type":  turn.get("utterance_type"),
                "stance_signals":  turn.get("stance_signals"),
                "mentions":        turn.get("mentions", []),
                "parser_version":  turn.get("parser_version"),
                "chunker_version": CHUNKER_VERSION,
            })

    return chunks


def chunk_source(source_id: str) -> tuple[int, str | None]:
    in_path  = INPUT_ROOT  / source_id / "enriched_turns.jsonl"
    out_dir  = OUTPUT_ROOT / source_id
    out_path = out_dir / "chunks_v1.jsonl"

    if not in_path.exists():
        return 0, f"enriched_turns.jsonl 없음: {in_path}"
    if out_path.exists():
        return 0, None  # 이미 처리됨

    turns = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                turns.append(json.loads(line))

    if not turns:
        return 0, "빈 파일"

    chunks = make_chunks(turns)
    write_jsonl_atomic(out_path, chunks)
    return len(chunks), None


def main() -> None:
    targets = set(sys.argv[1:])

    if not INPUT_ROOT.exists():
        print(f"enriched 데이터 없음: {INPUT_ROOT}")
        print("먼저 policy_enricher_v1.py를 실행하세요.")
        sys.exit(1)

    source_ids = sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids if any(s.startswith(t) for t in targets)]

    total = 0
    failures: list[tuple[str, str]] = []
    for sid in source_ids:
        n, err = chunk_source(sid)
        if err:
            print(f"  [오류] {sid}: {err}")
            failures.append((sid, err))
        else:
            print(f"  ✓ {sid}  ({n}청크)")
            total += n

    print(f"\n청킹 완료 — 총 {total}청크 / 오류 {len(failures)}개")
    print(f"출력 위치: {OUTPUT_ROOT}")
    fail_path = report_failures("chunker", failures)
    if fail_path:
        print(f"실패 목록: {fail_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
