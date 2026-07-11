"""POL-8 이슈 분석 주입 순수 로직 테스트 — DB·LLM 없이 실행.
실행: python tests/test_issue_context.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issue_context import build_issue_block, detect_issue  # noqa: E402

INDEX = [
    {"issue_id": "medical-reform", "title": "의정 갈등·의대 정원",
     "seed_keywords": ["의대 정원", "의정 갈등", "전공의", "의료대란", "의료개혁"]},
    {"issue_id": "martial-law", "title": "12·3 비상계엄",
     "seed_keywords": ["비상계엄", "계엄"]},
    {"issue_id": "empty-issue", "title": "키워드 없음", "seed_keywords": []},
]


def test_detect_issue():
    assert detect_issue("의대 정원 증원 논의 정리해줘", INDEX)["issue_id"] == "medical-reform"
    # 최다 우선: 키워드 2개 매칭 > 1개
    assert detect_issue("전공의 이탈과 의료대란 정리", INDEX)["issue_id"] == "medical-reform"
    assert detect_issue("국정감사 일정 알려줘", INDEX) is None            # 무매칭
    # 동률(각 1개) → None
    assert detect_issue("전공의 발언과 계엄 발언 비교", INDEX) is None
    assert detect_issue("", INDEX) is None


PARTY_DATA = {
    "issue_id": "medical-reform", "title": "의정 갈등·의대 정원", "mapping_quality": "ok",
    "periods": [{"from": "2024-05-30", "to": "2025-06-03", "ruling": "국민의힘"},
                {"from": "2025-06-04", "to": None, "ruling": "더불어민주당"}],
    "parties": [
        {"party": "더불어민주당", "side_by_period": ["야당", "여당"], "actor_count": 2,
         "stance_dist": {"support": 1, "oppose": 0, "concern": 1, "mixed": 0, "no_stance": 0},
         "actors": [{"speaker": "김A", "stance": "support"}, {"speaker": "이B", "stance": "concern"}]},
        {"party": "정부측", "side_by_period": None, "actor_count": 1,
         "stance_dist": {"support": 1, "oppose": 0, "concern": 0, "mixed": 0, "no_stance": 0},
         "actors": [{"speaker": "장관C", "stance": "support"}]},
    ],
}
TIMELINE = {"months": [
    {"month": "2024-06", "corpus_turns": 99, "mapped_turns": 40, "mapped_core_turns": 31},
    {"month": "2024-07", "corpus_turns": 80, "mapped_turns": 35, "mapped_core_turns": 28},
    {"month": "2024-08", "corpus_turns": 10, "mapped_turns": 3, "mapped_core_turns": 2},
    {"month": "2024-09", "corpus_turns": 50, "mapped_turns": 20, "mapped_core_turns": 19},
]}
ACTORS = [{"speaker": "김A", "n_turns": 9}, {"speaker": "증인X", "n_turns": 8},
          {"speaker": "장관C", "n_turns": 7}]


def test_build_issue_block():
    block = build_issue_block(PARTY_DATA, TIMELINE, ACTORS)
    assert block.startswith("[이슈: 의정 갈등·의대 정원]")
    assert "코퍼스 분석 기준" in block
    assert "⚠" not in block                                   # ok 품질이면 경고 없음
    assert "더불어민주당 2명(찬1·반0·우1·혼0·무0) [야당→여당]" in block
    assert "정부측 1명(찬1·반0·우0·혼0·무0)" in block           # 배지 없음
    # 피크: core 상위 3 내림차순 — 2024-08(2턴)은 탈락
    assert "- 발언 피크: 2024-06(31턴), 2024-07(28턴), 2024-09(19턴)" in block
    # 행위자: 구도에 없는 증인X 는 제외, 정부측 표기
    assert "- 주요 행위자: 김A(9턴, 찬성), 장관C(정부측, 7턴, 찬성)" in block
    assert "증인X" not in block


def test_build_issue_block_low_and_fallback():
    low = dict(PARTY_DATA, mapping_quality="low")
    zero_core = {"months": [
        {"month": "2024-06", "corpus_turns": 42, "mapped_turns": 0, "mapped_core_turns": 0}]}
    block = build_issue_block(low, zero_core, [])
    assert "⚠ 이 이슈의 자동 매핑 정밀도는 기준 미달" in block
    assert "- 발언 피크: 2024-06(42턴)" in block               # corpus 대체
    assert "주요 행위자" not in block                            # actors 없으면 줄 생략
    # 타임라인 None·빈 달이면 피크 줄 자체 생략
    assert "발언 피크" not in build_issue_block(PARTY_DATA, None, [])


if __name__ == "__main__":
    test_detect_issue()
    test_build_issue_block()
    test_build_issue_block_low_and_fallback()
    print("all passed")
