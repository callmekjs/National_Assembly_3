"""
[2] normalizer_v1
페이지 텍스트에서 잡음을 제거하고 section_type 및 segments를 생성한다.
출력: data/v1/normalized/{source_id}/normalized.jsonl

실행:
    python scripts/normalizer_v1.py              # 전체
    python scripts/normalizer_v1.py 과방위 외통위  # 특정 위원회만
"""

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from stage_io import report_failures, write_jsonl_atomic

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

NORMALIZER_VERSION = "v1.0"

INPUT_ROOT  = Path(__file__).parent.parent / "data" / "v1" / "extract"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "normalized"

# ── [1] 헤더/페이지번호 제거 패턴 ─────────────────────────────────────────────

# 핵심: "제415회-과학기술정보방송통신제1차(2024년6월11일)"
_RE_MEETING_HEADER = re.compile(r"제\d+회[-–—]?.{0,50}\d+차\s*\(\d{4}년\d+월\d+일\)")

# 전체 라인이 헤더인 경우 (숫자 prefix/suffix 포함)
#   "제415회-...제1차(2024년6월11일) 3"
#   "4 제415회-...제1차(2024년6월11일)"
_RE_HEADER_LINE = re.compile(r"^\s*\d*\s*제\d+회.{0,60}\)\s*\d*\s*$")

# "과학기술정보방송통신위원회회의록" 같은 단독 위원회 타이틀
_RE_COMMITTEE_TITLE = re.compile(r"^[가-힣]{4,25}(위원회|소위원회)(회의록)?$")

# "제415회국회 제 1 호"
_RE_SESSION_NO = re.compile(r"^제\d+회\s*국회\s+제\s*\d+\s*호\s*$")

# 임시회/정기회 구분 라인: "(임시회)", "(정기회)"
_RE_SESSION_TYPE = re.compile(r"^\s*\([임정][시기]회\)\s*$")

# 순수 페이지 번호
_RE_PAGE_NUM = re.compile(r"^\s*\d{1,4}\s*$")

# 구분선
_RE_DIVIDER = re.compile(r"^[\s─━\-=／*]{5,}$")

# 쪽 표기: "- 3 -"  "― 3 ―"
_RE_PAGE_MARK = re.compile(r"^[\s\-―–]+\d{1,4}[\s\-―–]+$")

# ── [2] 섹션 경계 마커 ────────────────────────────────────────────────────────

# 보고사항/명단 시작: 【보고사항】, ■보고사항, ▶처리된 의안 등
_RE_REPORT_START = re.compile(
    r"^【[^】]{0,20}】"
    r"|^[■□▶▷◆◇]\s*(보고사항|처리된\s*의안|감사결과|위원\s*선임|간사\s*선임|수석전문위원)"
)

# 의사일정 시작
_RE_AGENDA_START = re.compile(
    r"^의\s*사\s*일\s*정"
    r"|^상\s*정\s*된\s*안\s*건"
    r"|^\d+\.\s*(보고|심사|제정|개정|동의|승인|의결|청문|질의|보고)\b"
)

# 표지 시그널
_RE_COVER_SIGNAL = re.compile(
    r"국\s*회\s*사\s*무\s*처|일\s*시\s*\d{4}년|장\s*소\s*.{1,20}(위원회|회의실)"
    r"|[임정][시기]\s*회"
)

# 발언자 마커 — line-level match용 (segment_page에서 .match() 사용)
_RE_SPEAKER_LINE = re.compile(r"^[◯◎]")
# 발언자 마커 — text 전체 검색용 (has_speaker_marker 판정)
_RE_SPEAKER_ANY  = re.compile(r"[◯◎]")

# ── [2] 줄바꿈 복원 휴리스틱 ──────────────────────────────────────────────────

# 이 글자로 끝나면 → 공백 (문장/절 경계 또는 연결어미)
# 서: 어서/에서/아서, 고: 하고/이고, 면: 하면/되면
_SPACE_AFTER = set("다요죠까냐네며서고면")
# 조사로 끝나면 → 공백 (구 경계)
_JOSA_LAST   = set("가이을를은는의에도만")
# 다음 줄이 이 형태소로 시작하면 반드시 붙임 (동사 어미 연속)
_RE_FORCE_JOIN_NEXT = re.compile(r"^(겠|습니|니다|ㅂ니|십니|었|았|셨)")


def _join_or_space(prev: str, nxt: str) -> str:
    """
    prev + nxt 연결 방식 결정.
    반환: prev + " " + nxt  또는  prev + nxt
    """
    if not prev or not nxt:
        return prev + nxt

    p_last  = prev[-1]
    n_first = nxt[0]

    # 다음 줄이 마커/특수기호로 시작 → caller가 줄 분리 처리
    if n_first in ("◯", "◎", "【", "■", "□", "▶", "▷", "◆", "◇"):
        return prev

    p_is_korean = "가" <= p_last <= "힣"
    n_is_korean = "가" <= n_first <= "힣"

    if not (p_is_korean and n_is_korean):
        return prev + " " + nxt

    # ① 문장 종결 어미 → 공백
    if p_last in _SPACE_AFTER:
        return prev + " " + nxt

    # ② 조사 끝 → 공백
    if p_last in _JOSA_LAST:
        return prev + " " + nxt

    # ③ 다음 줄이 동사 연속 형태소로 시작 → 붙이기
    #    예: 하 + 겠습니다, 드 + 립니다, 개의하 + 겠습니다
    if _RE_FORCE_JOIN_NEXT.match(nxt):
        return prev + nxt

    # ④ 이전 줄의 마지막 토큰이 3자 이하 → 단어 중간 절단 → 붙이기
    #    예: "...멋진 진" + "짜", "...국가경" + "쟁력", "...보" + "호"
    tokens = prev.split()
    last_token = tokens[-1] if tokens else prev
    if len(last_token) <= 3:
        return prev + nxt

    # ⑤ 기본값: 공백
    return prev + " " + nxt


# ── 라인 정리 ─────────────────────────────────────────────────────────────────

def _is_noise_line(line: str) -> bool:
    """True면 이 라인을 삭제한다."""
    s = line.strip()
    if not s:
        return False  # 빈 줄은 섹션 구분용으로 유지
    if _RE_HEADER_LINE.match(s):
        return True
    if _RE_SESSION_NO.match(s):
        return True
    if _RE_COMMITTEE_TITLE.match(s):
        return True
    if _RE_SESSION_TYPE.match(s):
        return True
    if _RE_PAGE_NUM.match(s):
        return True
    if _RE_DIVIDER.match(s):
        return True
    if _RE_PAGE_MARK.match(s) and len(s) < 12:
        return True
    return False


def _clean_lines(raw_text: str) -> list[str]:
    """잡음 라인 제거 + 연속 공백 정리."""
    result = []
    for line in raw_text.splitlines():
        if _is_noise_line(line):
            continue
        line = re.sub(r"[ \t]{2,}", " ", line)
        result.append(line)
    return result


def _restore_breaks(lines: list[str]) -> list[str]:
    """
    연속 한글 라인을 공백으로 이어붙인다.
    마커(◯ 등) 앞에서는 반드시 줄을 나눈다.
    의사일정·상정된 안건·보고사항 시작 줄도 반드시 분리한다 (1페이지 agenda 보장).
    """
    if not lines:
        return lines

    result: list[str] = []
    for line in lines:
        if not result:
            result.append(line)
            continue

        s_prev = result[-1].rstrip()
        s_curr = line.lstrip()

        # 빈 줄이면 그냥 추가 (섹션 구분)
        if not s_prev or not s_curr:
            result.append(line)
            continue

        p_last  = s_prev[-1]
        n_first = s_curr[0]

        # 현재 줄이 섹션 경계 마커로 시작 → 무조건 분리
        if n_first in ("◯", "◎", "【", "■", "□", "▶", "▷", "◆", "◇"):
            result.append(line)
            continue

        # 의사일정·상정된 안건·보고사항 → 분리 (1페이지에도 적용)
        if _RE_AGENDA_START.match(s_curr) or _RE_REPORT_START.match(s_curr):
            result.append(line)
            continue

        p_is_korean = "가" <= p_last <= "힣"
        n_is_korean = "가" <= n_first <= "힣"

        if p_is_korean and n_is_korean:
            joined = _join_or_space(s_prev, s_curr)
            result[-1] = joined
        else:
            result.append(line)

    return result


# ── 섹션 분류 ─────────────────────────────────────────────────────────────────

def _segment_page(cleaned_lines: list[str], page_num: int) -> list[dict]:
    """
    정리된 라인 목록을 섹션 블록으로 나눈다.
    반환: [{"section_type": str, "text": str}, ...]
    """
    segments: list[dict] = []
    buf: list[str] = []

    # 초기 타입
    cur_type = "cover" if page_num <= 2 else "body"
    in_report = False

    def flush(t: str) -> None:
        text = "\n".join(buf).strip()
        if text:
            segments.append({"section_type": t, "text": text})
        buf.clear()

    for line in cleaned_lines:
        s = line.strip()

        # ── 보고사항 마커 → report 시작
        if _RE_REPORT_START.match(s):
            flush(cur_type)
            cur_type = "report"
            in_report = True
            buf.append(line)
            continue

        # ── 발언자 마커 → body 시작 (report 구간 제외)
        if _RE_SPEAKER_LINE.match(s) and not in_report:
            if cur_type != "body":
                flush(cur_type)
                cur_type = "body"
            buf.append(line)
            continue

        # ── 의사일정 마커 → agenda 시작 (body/report 이전만)
        if _RE_AGENDA_START.match(s) and cur_type not in ("body", "report"):
            if cur_type != "agenda":
                flush(cur_type)
                cur_type = "agenda"
            buf.append(line)
            continue

        buf.append(line)

    flush(cur_type)

    # 세그먼트가 아예 없으면 페이지 전체를 단일 블록으로
    if not segments:
        full = "\n".join(cleaned_lines).strip()
        if full:
            segments.append({"section_type": cur_type, "text": full})

    return segments


def _dominant_type(segments: list[dict]) -> str:
    """세그먼트 중 텍스트가 가장 긴 타입을 대표 타입으로 반환."""
    if not segments:
        return "unknown"
    types = {s["section_type"] for s in segments}
    if len(types) == 1:
        return segments[0]["section_type"]
    longest = max(segments, key=lambda s: len(s["text"]))
    return "mixed" if len(types) > 1 else longest["section_type"]


# ── 메인 처리 ─────────────────────────────────────────────────────────────────

def normalize_page(page: dict) -> dict:
    """단일 페이지 dict를 정규화해 반환한다."""
    raw_text = page.get("text", "")

    cleaned_lines  = _clean_lines(raw_text)
    restored_lines = _restore_breaks(cleaned_lines)
    segments       = _segment_page(restored_lines, page["page"])

    cleaned_text   = "\n".join(restored_lines).strip()
    section_type   = _dominant_type(segments)
    header_removed = bool(_RE_MEETING_HEADER.search(raw_text))

    # body 세그먼트에서만 발언자 마커 탐색 (^앵커 버그 수정)
    body_text   = "\n".join(s["text"] for s in segments if s["section_type"] == "body")
    has_speaker = bool(_RE_SPEAKER_ANY.search(body_text))

    return {
        **page,
        "text":               cleaned_text,
        "section_type":       section_type,
        "segments":           segments,
        "has_speaker_marker": has_speaker,
        "removed_header":     header_removed,
        "normalizer_version": NORMALIZER_VERSION,
        "normalized_at":      datetime.now(timezone.utc).isoformat(),
    }


def normalize_source(source_id: str) -> tuple[int, str | None]:
    in_path  = INPUT_ROOT / source_id / "pages.jsonl"
    out_dir  = OUTPUT_ROOT / source_id
    out_path = out_dir / "normalized.jsonl"

    if not in_path.exists():
        return 0, f"pages.jsonl 없음: {in_path}"
    if out_path.exists():
        return 0, None  # 이미 처리됨

    pages: list[dict] = []
    with open(in_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pages.append(json.loads(line))

    if not pages:
        return 0, "빈 파일"

    write_jsonl_atomic(out_path, (normalize_page(p) for p in pages))
    return len(pages), None


def main() -> None:
    targets = set(sys.argv[1:])

    if not INPUT_ROOT.exists():
        print(f"추출 데이터 없음: {INPUT_ROOT}")
        print("먼저 extractor_v1.py를 실행하세요.")
        sys.exit(1)

    source_ids = sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids
                      if any(s.startswith(t) for t in targets)]

    total_pages = 0
    failures: list[tuple[str, str]] = []
    for source_id in source_ids:
        n, err = normalize_source(source_id)
        if err:
            print(f"  [오류] {source_id}: {err}")
            failures.append((source_id, err))
        else:
            print(f"  ✓ {source_id}  ({n}페이지)")
            total_pages += n

    print(f"\n정규화 완료 — 총 {total_pages}페이지 / 오류 {len(failures)}개")
    print(f"출력 위치: {OUTPUT_ROOT}")
    fail_path = report_failures("normalizer", failures)
    if fail_path:
        print(f"실패 목록: {fail_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
