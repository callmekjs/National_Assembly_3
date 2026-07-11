"""POL-6 여야 구도 순수 로직 테스트 — DB 없이 실행.
실행: python tests/test_party_stances.py  (pytest 도 지원)
"""
import io
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from issues import (  # noqa: E402
    LOW_QUALITY_ISSUES, actor_group, party_composition, party_sides,
)


def _actor(sp, party, stance, roles):
    return {"speaker": sp, "party": party, "stance": stance, "roles": roles}


def test_actor_group():
    assert actor_group(["위원", "위원"]) == "assembly"
    assert actor_group(["보건복지부장관"]) == "government"
    assert actor_group(["위원", "통일부장관"]) == "assembly"   # 동률 → assembly 우선 (겸직)
    assert actor_group(["증인"]) == "witness"
    assert actor_group([None]) == "unknown"
    assert actor_group(["증인", "증인", "위원"]) == "witness"  # 최빈 우선


def test_party_composition():
    actors = [
        _actor("김A", "더불어민주당", "support", ["위원"]),
        _actor("이B", "더불어민주당", "concern", ["위원"]),
        _actor("박C", "국민의힘", "oppose", ["위원"]),
        _actor("장관D", None, "support", ["보건복지부장관"]),
        _actor("증인E", None, "no_stance", ["증인"]),
        _actor("스태프F", None, "no_stance", ["수석전문위원"]),
        _actor("무소속G", "무소속", "mixed", ["위원"]),
        _actor("미상H", None, "no_stance", ["위원"]),
    ]
    rows = party_composition(actors)
    names = [r["party"] for r in rows]
    # 의원 정당 수 내림차순 → 특수행("정부측", "무소속/미상") 맨 뒤 고정 순서
    assert names == ["더불어민주당", "국민의힘", "정부측", "무소속/미상"], names
    dem = rows[0]
    assert dem["actor_count"] == 2
    assert dem["stance_dist"] == {"support": 1, "oppose": 0, "concern": 1, "mixed": 0, "no_stance": 0}
    assert dem["actors"] == [{"speaker": "김A", "stance": "support"},
                             {"speaker": "이B", "stance": "concern"}]
    # 증인·스태프는 어느 행에도 없음
    everyone = [a["speaker"] for r in rows for a in r["actors"]]
    assert "증인E" not in everyone and "스태프F" not in everyone
    # 무소속 + 정당 미상(의원 자격인데 members 미등록) 통합
    assert rows[-1]["actor_count"] == 2
    assert rows[2]["party"] == "정부측" and rows[2]["actor_count"] == 1


def test_party_sides():
    ps = party_sides(["더불어민주당", "국민의힘", "국민의미래"])
    assert len(ps["periods"]) == 2
    assert ps["periods"][0] == {"from": "2024-05-30", "to": "2025-06-03", "ruling": "국민의힘"}
    assert ps["periods"][1]["from"] == "2025-06-04" and ps["periods"][1]["to"] is None
    assert ps["periods"][1]["ruling"] == "더불어민주당"
    assert ps["sides"]["더불어민주당"] == ["야당", "여당"]
    assert ps["sides"]["국민의힘"] == ["여당", "야당"]
    assert ps["sides"]["국민의미래"] == ["여당", "야당"]   # 위성정당 → 모정당 기준


def test_low_quality_issues():
    assert "martial-law" in LOW_QUALITY_ISSUES
    assert len(LOW_QUALITY_ISSUES) == 7


if __name__ == "__main__":
    test_actor_group()
    test_party_composition()
    test_party_sides()
    test_low_quality_issues()
    print("all passed")
