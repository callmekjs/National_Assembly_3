"""
[3] parser_v1
발언자 마커(◯/◎)를 기준으로 발언 턴을 구조화한다.
출력: data/v1/parsed/{source_id}/turns.jsonl

실행:
    python scripts/parser_v1.py              # 전체
    python scripts/parser_v1.py 과방위 외통위  # 특정 위원회만
"""

import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

from stage_io import report_failures, write_jsonl_atomic

if __name__ == "__main__":  # import 시(테스트 등) 부작용 방지 — 직접 실행할 때만 래핑
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PARSER_VERSION = "v1.2"

# 발언자 추출 실패로 버려진 헤더 (run 종료 시 리포트 저장 — 조용한 폐기 방지)
DROPPED_HEADERS: Counter = Counter()

INPUT_ROOT  = Path(__file__).parent.parent / "data" / "v1" / "normalized"
OUTPUT_ROOT = Path(__file__).parent.parent / "data" / "v1" / "parsed"

# ── 발언자 마커 ───────────────────────────────────────────────────────────────
# turns_quality_gate._MARKER_RE 와 같은 집합 유지 — 한쪽만 바꾸면 발언이 앞 턴에
# 이어붙거나(파서) 가짜 경고(게이트)가 난다. ○(U+25CB)는 2026-07-07 전수 조사에서
# 실사용 0회 확인으로 제외.
MARKER_RE = re.compile(r"[◯◎]")

# ── 비발언 헤더 패턴 — 이 패턴으로 시작하는 헤더는 턴에서 제외 ─────────────────
_NON_SPEAKER_HDR = re.compile(
    r"^(출석|정부측|기타\s*참석자?|위원\s*선임|의안\s*회부|예비심사기간|청원\s*회부|"
    r"계획서\s*송부|보고서\s*송부|행정입법\s*제출|수석전문위원\s*명단|"
    r"전문위원\s*(?:명단|현황)|처리된\s*의안|보고사항|부록|"
    r"출장\s*위원|위원\s*아닌\s*출석|소위원회\s*직접\s*회부|청가\s*위원|청가\s*의원)"
)

# 파싱 결과에서 제외할 speaker 이름
_NON_SPEAKER_NAMES = frozenset([
    "출석", "정부측", "기타", "참석자",
    "행정입법", "계획서", "보고서", "예비심사기간",
    "청원", "보고사항", "부록", "의안",
    "출장", "위원", "소위원회", "청가",
])

# ── 역할(직책) 키워드 ──────────────────────────────────────────────────────────
ROLE_FIRST_KW = ("위원장", "소위원장", "위원회위원장")  # ◯위원장 {이름}
ROLE_LAST_KW = (
    "위원", "의원", "장관", "차관", "청장", "원장", "부장",
    "과장", "전문위원", "수석전문위원", "입법조사관",
    "국장", "실장", "본부장", "처장", "대표", "사장", "간사",
)
NOT_NAME = frozenset(["어떤", "여러", "각", "해당", "본", "다음", "이", "그", "저"])

# 부처명+직책 선행: "외교부장관 조태열"
MINISTRY_ROLE_RE = re.compile(
    r"^([가-힣]{2,10}(?:부|처|청|원|실|국)(?:장관|차관|청장|원장|실장|국장))\s+([가-힣]{2,5})$"
)
# 역할 선행: "위원장 최민희"
ROLE_FIRST_RE = re.compile(
    r"^(" + "|".join(re.escape(k) for k in ROLE_FIRST_KW) + r")\s+([가-힣]{2,5})$"
)
# 역할 선행(일반형): "증인 유영상", "경찰청장 조지호", "보건복지부제2차관 이형훈",
# "한국방송공사사장후보자 박장범", "쿠팡㈜대표이사 박대준" (v1.1 도입, v1.2 대폭 확장)
# - 접두부: 조직명 (숫자 '제2차관', 특수문자 '㈜'/'(전)' 포함)
# - 접미부: 직책 접미사 (버려진 헤더 리포트 기반 선정 — parser_dropped_headers.txt)
# - 이름부: 한국명(2~7자), 한자명(柳榮夏), 익명처리(박00)
ROLE_FIRST_GENERAL_RE = re.compile(
    r"^("
    r"[가-힣0-9㈜()·&]{1,26}"
    r"(?:위원장|부위원장|장관|차관|청장|차장|처장|원장|실장|국장|과장|계장"
    r"|본부장|부장|소장|총장|총재|의장|단장|사장|관장|팀장|센터장|서장"
    r"|사령관|정책관|조정관|심의관|기획관|관리관|담당관|협력관|조사관|검사관"
    r"|교육관|정보관|분석관|기술관|행정관|대변인|비서관|보좌관"
    r"|후보자|대표이사|대표|이사장|이사|감사|위원장대리|직무대리|직무대행|권한대행|대리|대행|위원|간사)"
    r"|증인|참고인|진술인|공술인|전문위원|수석전문위원|위원장대리|입법조사관"
    r")\s+([가-힣]{2,7}|[一-鿿豈-﫿]{2,5}|[가-힣]{1,3}[0-9○]{2})$"
)
# 이름 선행: "김현 위원", "柳榮夏 위원" (한자 이름 지원 — v1.2)
# 한자 범위: 기본 블록(U+4E00~) + 호환 블록(U+F900~, PDF 추출 한자에 흔함: 柳=U+F9C9)
ROLE_LAST_RE = re.compile(
    r"^([가-힣]{2,5}|[一-鿿豈-﫿]{2,5})\s+("
    + "|".join(re.escape(k) for k in ROLE_LAST_KW) + r")$"
)
# 이름만
NAME_ONLY_RE = re.compile(r"^([가-힣]{2,5})$")


def _fmt_date(raw: str) -> str:
    """'20240611' → '2024-06-11'."""
    s = str(raw).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def parse_speaker(header: str) -> tuple[str | None, str | None]:
    """마커 직후 헤더에서 (name, role)을 추출한다."""
    h = header.strip()

    m = MINISTRY_ROLE_RE.match(h)
    if m:
        return m.group(2), m.group(1)

    m = ROLE_FIRST_RE.match(h)
    if m:
        return m.group(2), m.group(1)

    m = ROLE_FIRST_GENERAL_RE.match(h)
    if m:
        name = m.group(2)
        if name in NOT_NAME:
            return None, None
        return name, m.group(1)

    m = ROLE_LAST_RE.match(h)
    if m:
        name, role = m.group(1), m.group(2)
        if name in NOT_NAME:
            return None, None
        return name, role

    m = NAME_ONLY_RE.match(h)
    if m:
        name = m.group(1)
        if name in NOT_NAME:
            return None, None
        return name, None

    return None, None


def _extract_speaker(header_line: str) -> tuple[str | None, str | None, str]:
    """
    header_line에서 (name, role, remaining_text)를 추출한다.
    발언자 마커 직후 텍스트 전체를 받아 이름/역할을 분리하고 나머지를 반환.
    """
    tokens = header_line.split(" ", 2)
    name, role, extra = None, None, ""

    if len(tokens) >= 2:
        for take in (2, 1):
            candidate = " ".join(tokens[:take])
            n, r = parse_speaker(candidate)
            if n:
                name, role = n, r
                extra = " ".join(tokens[take:])
                break

    if not name:
        n, r = parse_speaker(header_line)
        if n:
            name, role = n, r

    return name, role, extra


def split_turns(pages: list[dict]) -> list[dict]:
    """
    normalized pages → turns.

    처리 방식 (v2.2):
    - 페이지/세그먼트를 순서대로 순회
    - body segment만 파싱 (cover/agenda/report 제외)
    - segment가 마커 없이 시작하면 → 직전 turn의 continuation으로 이어붙임
    - page_end를 continuation이 발견된 페이지로 갱신
    """
    meta = pages[0]
    source_id    = meta["source_id"]
    committee    = meta["committee"]
    folder       = meta["folder"]
    file_name    = meta["file_name"]
    meeting_date = _fmt_date(meta.get("date_hint", ""))

    turns: list[dict] = []
    turn_counter = 0

    for page in pages:
        page_num = page["page"]
        segs = page.get("segments", [])

        # body segment 목록 구성
        if segs:
            # 첫 body segment 인덱스
            first_body_idx = next(
                (i for i, s in enumerate(segs) if s.get("section_type") == "body"),
                None,
            )

            # 첫 body segment 이전의 cover/unknown/mixed 세그먼트를 continuation 처리
            # (normalizer가 페이지 경계 연속 텍스트를 cover로 분류하는 경우 대응)
            if first_body_idx is not None and page_num > 1 and turns:
                _SKIP_TYPES = frozenset(("agenda", "report", "attendance"))
                for pre_seg in segs[:first_body_idx]:
                    if pre_seg.get("section_type") in _SKIP_TYPES:
                        continue
                    pre_text = pre_seg.get("text", "").strip()
                    if not pre_text:
                        continue
                    pre_markers = [m.start() for m in MARKER_RE.finditer(pre_text)]
                    if not pre_markers:
                        turns[-1]["text"] = (turns[-1]["text"] + " " + pre_text).strip()
                        turns[-1]["page_end"] = page_num
                    elif pre_markers[0] > 0:
                        cont = pre_text[:pre_markers[0]].strip()
                        if cont:
                            turns[-1]["text"] = (turns[-1]["text"] + " " + cont).strip()
                            turns[-1]["page_end"] = page_num

            body_segs = [
                (idx, seg) for idx, seg in enumerate(segs)
                if seg.get("section_type") == "body" and seg.get("text", "").strip()
            ]
        else:
            # 구형 데이터: segments 없으면 top-level body 사용
            if page.get("section_type") == "body" and page.get("text", "").strip():
                body_segs = [(0, {"section_type": "body", "text": page["text"]})]
            else:
                body_segs = []

        for seg_idx, seg in body_segs:
            seg_text = seg["text"].strip()
            if not seg_text:
                continue

            # 이 segment 안에서 마커 위치 전부 찾기
            marker_positions = [m.start() for m in MARKER_RE.finditer(seg_text)]

            # ── [1] 마커가 전혀 없는 segment ────────────────────────────────────
            # 전체 텍스트가 직전 turn의 continuation
            if not marker_positions:
                if turns:
                    turns[-1]["text"] = (turns[-1]["text"] + " " + seg_text).strip()
                    turns[-1]["page_end"] = page_num
                continue

            # ── [2] 마커 전 텍스트 → 직전 turn의 continuation ────────────────
            first_marker = marker_positions[0]
            if first_marker > 0:
                pre_text = seg_text[:first_marker].strip()
                if pre_text and turns:
                    turns[-1]["text"] = (turns[-1]["text"] + " " + pre_text).strip()
                    turns[-1]["page_end"] = page_num

            # ── [3] 각 마커 구간 → 새 turn ──────────────────────────────────
            marker_positions.append(len(seg_text))  # 종료 경계

            for i in range(len(marker_positions) - 1):
                chunk_start = marker_positions[i]
                chunk_end   = marker_positions[i + 1]
                # 마커 문자(1자) 제외한 나머지
                chunk = seg_text[chunk_start + 1 : chunk_end]

                lines       = chunk.split("\n", 1)
                header_line = lines[0].strip()
                body_text   = lines[1].strip() if len(lines) > 1 else ""

                # 비발언 헤더 제외
                if _NON_SPEAKER_HDR.match(header_line):
                    continue

                name, role, extra = _extract_speaker(header_line)

                # 비발언 이름 제외 (버려지는 헤더는 리포트용으로 기록)
                if not name or name in _NON_SPEAKER_NAMES:
                    if not name:
                        DROPPED_HEADERS[header_line[:60]] += 1
                    continue

                text = (extra + " " + body_text).strip() if extra else body_text

                turn_counter += 1
                turns.append({
                    "source_id":            source_id,
                    "committee":            committee,
                    "folder":               folder,
                    "file_name":            file_name,
                    "meeting_date":         meeting_date,
                    "turn_id":              f"{source_id}_turn_{turn_counter:04d}",
                    "speaker":              name,
                    "role":                 role,
                    "header_raw":           header_line,
                    "text":                 text,
                    "page":                 page_num,
                    "page_start":           page_num,
                    "page_end":             page_num,
                    "source_segment_type":  "body",
                    "source_segment_index": seg_idx,
                    "parser_version":       PARSER_VERSION,
                })

    return turns


def parse_source(source_id: str) -> tuple[int, str | None]:
    in_path  = INPUT_ROOT / source_id / "normalized.jsonl"
    out_dir  = OUTPUT_ROOT / source_id
    out_path = out_dir / "turns.jsonl"

    if not in_path.exists():
        return 0, f"normalized.jsonl 없음: {in_path}"
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

    turns = split_turns(pages)
    write_jsonl_atomic(out_path, turns)
    return len(turns), None


def main() -> None:
    targets = set(sys.argv[1:])

    if not INPUT_ROOT.exists():
        print(f"정규화 데이터 없음: {INPUT_ROOT}")
        print("먼저 normalizer_v1.py를 실행하세요.")
        sys.exit(1)

    source_ids = sorted(p.name for p in INPUT_ROOT.iterdir() if p.is_dir())
    if targets:
        source_ids = [s for s in source_ids if any(s.startswith(t) for t in targets)]

    total_turns = 0
    failures: list[tuple[str, str]] = []
    for source_id in source_ids:
        n, err = parse_source(source_id)
        if err:
            print(f"  [오류] {source_id}: {err}")
            failures.append((source_id, err))
        else:
            print(f"  ✓ {source_id}  ({n}턴)")
            total_turns += n

    print(f"\n파싱 완료 — 총 {total_turns}턴 / 오류 {len(failures)}개")
    print(f"출력 위치: {OUTPUT_ROOT}")

    # 버려진 헤더 리포트 — 새 발언자 패턴 발견용
    if DROPPED_HEADERS:
        report_dir = OUTPUT_ROOT.parent / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "parser_dropped_headers.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# 발언자 추출 실패로 버려진 헤더 (parser {PARSER_VERSION})\n")
            f.write(f"# 총 {sum(DROPPED_HEADERS.values())}건 / {len(DROPPED_HEADERS)}유형\n\n")
            for hdr, cnt in DROPPED_HEADERS.most_common():
                f.write(f"{cnt:6d}  {hdr}\n")
        print(f"버려진 헤더: {sum(DROPPED_HEADERS.values())}건 → {report_path}")

    fail_path = report_failures("parser", failures)
    if fail_path:
        print(f"실패 목록: {fail_path}")
        sys.exit(1)


if __name__ == "__main__":
    main()
