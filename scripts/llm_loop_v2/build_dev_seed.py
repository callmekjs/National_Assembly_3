"""검토한 원문 후보로 신규 개발문제 20개를 만든다.

질문과 정답은 현재 RAG 검색 결과가 아니라 candidate_sources.jsonl의 원문을 보고 작성했다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ITEMS: list[dict[str, Any]] = [
    {
        "candidate": "candidate-002", "category": "synthesis",
        "question": "2024년 11월 19일 보건복지위원회에서 박민수 차관은 의료기관 평가 결과 공개에 대해 어떤 입장과 보완책을 제시했는가?",
        "answer": "박민수 차관은 국민의 알권리와 의료 질 개선을 위한 공개 원칙에는 동의했지만, 의료기관 서열화와 수도권 대형병원 쏠림을 우려했다. 구체적인 공개 범위는 하위법령에 위임하고 단계적으로 시행하며, 준비를 고려해 시행시기를 1년으로 조정해 달라고 요청했다.",
        "required": ["평가 결과 공개 원칙에 동의했다.", "의료기관 서열화와 수도권 대형병원 쏠림을 우려했다.", "공개 범위를 하위법령에 위임하고 단계적으로 시행하자고 했다.", "시행시기를 1년으로 변경해 달라고 요청했다."],
        "forbidden": ["평가 결과를 전면 비공개해야 한다고 주장했다."],
        "exact": [{"type": "person", "value": "박민수"}, {"type": "date", "value": "2024-11-19"}, {"type": "number", "value": "1년"}],
    },
    {
        "candidate": "candidate-004", "category": "fact",
        "question": "2024년 11월 27일 국방위원회에서 김선호 차관은 시설 설립 문제를 어떤 방식으로 먼저 해결해야 한다고 답했는가?",
        "answer": "김선호 차관은 권한과 예산이 지방자치단체에 넘어간 만큼 지자체와 협의해 설립 방안을 만드는 것이 우선이라고 답했다. 국고 지원으로 되돌리면 기존 보조금 절차에 역행하고 다른 지자체에도 같은 지원 문제가 생길 수 있다고 설명했다.",
        "required": ["지방자치단체와 협의해 설립 방안을 만드는 것이 우선이라고 했다.", "국고 지원 환원은 기존 보조금 절차와 다른 지자체 지원 문제를 낳을 수 있다고 했다."],
        "forbidden": ["시설 설립을 포기해야 한다고 답했다."],
        "exact": [{"type": "person", "value": "김선호"}, {"type": "committee", "value": "국방위원회"}],
    },
    {
        "candidate": "candidate-005", "category": "synthesis",
        "question": "김윤덕 국토교통부장관후보자는 수도권 유휴 부지를 활용한 주택 공급을 진전시키기 위해 무엇이 필요하다고 보았는가?",
        "answer": "김윤덕 후보자는 주민 반대와 지방자치단체와의 이견 때문에 유휴 부지 활용이 어렵다고 보았다. 양질의 주택 공급이라는 방향이 맞다면 부지를 잘 선정하고 장관이 현장을 찾아 주민과 대화·협상하는 적극행정과 현장행정이 필요하다고 말했다.",
        "required": ["주민 반대와 지방자치단체 이견을 어려움으로 들었다.", "부지 선정과 주민 대화·협상을 포함한 적극행정과 현장행정이 필요하다고 했다."],
        "forbidden": ["유휴 부지 활용 정책을 폐기하겠다고 했다."],
        "exact": [{"type": "person", "value": "김윤덕"}, {"type": "organization", "value": "국토교통부"}],
    },
    {
        "candidate": "candidate-006", "category": "fact",
        "question": "조지호 경찰청장은 음주단속 현장에서 감지와 측정이 어떤 순서로 진행되며 감지 불응 시 무엇을 확인한다고 설명했는가?",
        "answer": "조지호 경찰청장은 먼저 음주감지기를 사용하고, 감지되면 측정 단계로 넘어간다고 설명했다. 감지에도 불응하면 혈색, 냄새, 걸음걸이 같은 외표를 종합해 음주 여부를 추정한다고 했다.",
        "required": ["음주감지기를 먼저 사용하고 감지되면 측정한다고 했다.", "감지 불응 시 혈색·냄새·걸음걸이를 종합한다고 했다."],
        "forbidden": ["감지기 없이 바로 채혈한다고 설명했다."],
        "exact": [{"type": "person", "value": "조지호"}],
    },
    {
        "candidate": "candidate-008", "category": "synthesis",
        "question": "2026년 4월 30일 과학기술정보방송통신위원회에서 강대훈 전문위원은 원자력진흥법 개정안 2건의 내용과 검토상 유의점을 어떻게 설명했는가?",
        "answer": "두 개정안은 원자력진흥종합계획에 기술인력 양성·처우 개선과 관련 예산을 포함하거나, 기술개발·산업 육성과 예산 지원 근거까지 두려는 내용이다. 강대훈 전문위원은 정책 기반 강화 필요성은 인정하면서도 이미 기술개발과 인력 양성 및 관련 예산이 종합계획에 포함·집행되고 있다는 점을 고려해야 한다고 설명했다.",
        "required": ["개정안이 기술인력 양성·처우 개선 또는 기술개발·산업 육성과 예산 근거를 담는다고 설명했다.", "정책 기반 강화 필요성을 인정했다.", "현재도 관련 내용과 예산이 종합계획에 포함·집행된다는 점을 고려해야 한다고 했다."],
        "forbidden": ["두 개정안 모두 불필요하다고 결론냈다."],
        "exact": [{"type": "person", "value": "강대훈"}, {"type": "number", "value": "2건"}],
    },
    {
        "candidate": "candidate-009", "category": "entity_date",
        "question": "2025년 7월 14일 외교통일위원회에서 정동영 후보자는 통일부 조직과 정원에 어떤 변화가 있었고 복원에 어떤 어려움이 있다고 말했는가?",
        "answer": "정동영 후보자는 통일부 정원 81명이 줄고 남북회담사무국, 교류협력국, 개성공단 지원사무국, 남북연락사무소가 사실상 없어졌다고 말했다. 복원 방향에는 동의하지만 행정안전부가 없앤 정원의 원상회복에 부정적일 수 있어 외교통일위원회의 도움이 필요하다고 했다.",
        "required": ["통일부 정원 81명이 줄었다고 했다.", "남북회담사무국·교류협력국·개성공단 지원사무국·남북연락사무소가 사실상 없어졌다고 했다.", "정원 복원에는 행정안전부 동의가 어려울 수 있다고 했다."],
        "forbidden": ["통일부 정원이 81명 늘었다고 말했다."],
        "exact": [{"type": "person", "value": "정동영"}, {"type": "number", "value": "81명"}, {"type": "date", "value": "2025-07-14"}],
    },
    {
        "candidate": "candidate-012", "category": "synthesis",
        "question": "허영 위원은 금융 샌드박스 지정 혁신기업의 인허가 문제를 해결하기 위해 어떤 제도 개선을 제안했는가?",
        "answer": "허영 위원은 샌드박스 지정 기업이 인허가 단계에서도 인센티브를 받아 사업을 이어갈 수 있어야 한다고 말했다. 또한 영국이나 싱가포르처럼 제한된 사업 범위에서 먼저 허가하고 관리 능력이 검증되면 범위를 넓히는 스몰 라이선스 제도를 제안했다.",
        "required": ["샌드박스 지정 기업에 인허가 단계의 인센티브가 필요하다고 했다.", "제한적으로 허가한 뒤 검증되면 확대하는 스몰 라이선스 제도를 제안했다."],
        "forbidden": ["금융 샌드박스 제도를 폐지하자고 제안했다."],
        "exact": [{"type": "person", "value": "허영"}, {"type": "organization", "value": "영국"}, {"type": "organization", "value": "싱가포르"}],
    },
    {
        "candidate": "candidate-013", "category": "entity_date",
        "question": "안규백 국방부장관이 보고한 2024회계연도 국방부 전체 세출예산 현액, 집행액, 이월액, 불용액은 각각 얼마인가?",
        "answer": "전체 세출예산 현액은 44조 547억 원, 집행액은 42조 5048억 원, 이월액은 7787억 원, 불용액은 7711억 원이다.",
        "required": ["세출예산 현액은 44조 547억 원이다.", "42조 5048억 원을 집행했다.", "7787억 원을 이월했다.", "7711억 원을 불용처리했다."],
        "forbidden": ["불용액이 7787억 원이라고 바꾸어 말했다."],
        "exact": [{"type": "number", "value": "44조 547억 원"}, {"type": "number", "value": "42조 5048억 원"}, {"type": "number", "value": "7787억 원"}, {"type": "number", "value": "7711억 원"}],
    },
    {
        "candidate": "candidate-016", "category": "entity_date",
        "question": "박대출 위원이 기록을 근거로 언급한 직접 입영 연기 신청은 몇 번이며 각각 어떤 사유였는가?",
        "answer": "직접 입영 연기 신청은 네 번이었다. 1993년 6월 22일과 9월 14일에는 졸업 예정, 1994년 3월 15일에는 시국 관련 수형자, 1995년 1월 23일에는 형제 동시 군복무를 사유로 신청했다고 말했다.",
        "required": ["직접 연기 신청은 네 번이라고 했다.", "1993년 두 차례는 졸업 예정 사유였다.", "1994년 3월 15일은 시국 관련 수형자 사유였다.", "1995년 1월 23일은 형제 동시 군복무 사유였다."],
        "forbidden": ["네 번 모두 대학 재학으로 자동 연기됐다고 말했다."],
        "exact": [{"type": "number", "value": "4번"}, {"type": "date", "value": "1994-03-15"}, {"type": "date", "value": "1995-01-23"}],
    },
    {
        "candidate": "candidate-017", "category": "fact",
        "question": "박민규 위원은 방송시장 경쟁상황 평가에서 어떤 시장 변화가 지적됐지만 정작 무엇이 평가 대상에서 빠졌다고 문제를 제기했는가?",
        "answer": "박민규 위원은 넷플릭스의 수요 독점력 강화와 OTT 경쟁 매체의 성장으로 한국 방송시장 침체가 구조화·장기화될 수 있다는 평가를 언급했다. 그러나 정작 OTT가 방송시장 경쟁상황 평가 대상에는 포함되지 않았다고 지적했다.",
        "required": ["넷플릭스의 수요 독점력과 OTT 성장에 따른 방송시장 침체 위험을 언급했다.", "OTT가 평가 대상에서 빠졌다고 지적했다."],
        "forbidden": ["OTT가 이미 평가의 핵심 대상이라고 말했다."],
        "exact": [{"type": "person", "value": "박민규"}, {"type": "organization", "value": "넷플릭스"}],
    },
    {
        "candidate": "candidate-018", "category": "fact",
        "question": "2025년 11월 14일 외교통일위원회에서 안철수 위원은 APEC 한중 정상회담의 안보 분야에서 무엇이 빠졌다고 지적했는가?",
        "answer": "안철수 위원은 경제 분야 합의와 달리 안보 분야가 실망스러웠다며 서해 불법 구조물 문제가 공개적으로 전혀 언급되지 않았다고 지적했다. 비공개 회담에서는 이 문제가 논의됐는지도 물었다.",
        "required": ["서해 불법 구조물 문제가 공개적으로 언급되지 않았다고 지적했다.", "비공개 회담에서 논의됐는지 물었다."],
        "forbidden": ["서해 불법 구조물 철거에 합의했다고 말했다."],
        "exact": [{"type": "person", "value": "안철수"}, {"type": "date", "value": "2025-11-14"}, {"type": "organization", "value": "APEC"}],
    },
    {
        "candidate": "candidate-020", "category": "entity_date",
        "question": "2025년 11월 11일 보건복지위원회에서 이형훈 차관은 의료 인력양성 수급 및 적정 관리 예산을 얼마 증액하는 방안에 수용 의견을 냈으며 그 배경은 무엇인가?",
        "answer": "이형훈 차관은 7억 원 증액 방안에 수용 의견을 냈다. 해당 예산이 2024년 6억 원에서 2025년 3억 원으로 줄었고, 2026년 3억 원 편성 상태에서 교육 대상 확대 등을 위해 2024년 수준으로 늘릴 필요가 있다고 설명했다.",
        "required": ["7억 원 증액 방안을 수용했다.", "2024년 6억 원에서 2025년 3억 원으로 감액됐다고 설명했다.", "교육 대상 확대와 2024년 수준 회복 필요성을 들었다."],
        "forbidden": ["7억 원 감액 방안을 수용했다고 말했다."],
        "exact": [{"type": "person", "value": "이형훈"}, {"type": "number", "value": "7억 원"}, {"type": "number", "value": "6억 원"}, {"type": "number", "value": "3억 원"}],
    },
    {
        "candidate": "candidate-025", "category": "comparison_timeline",
        "question": "임명현 전문위원은 출연형 R&D와 투자형 R&D의 자금 회수·재투자 방식을 어떻게 구분했고 어떤 불일치를 지적했는가?",
        "answer": "출연형 R&D는 출연금으로 연구를 수행하고 기술료를 다시 출연·투자하는 방식이며, 투자형 R&D는 지분 투자 수익을 R&D에 재투자하는 방식이라고 구분했다. 기존 출연형 R&D에서 생긴 기술료를 투자형 기금에 사용하는 것은 두 생태계 사이에 불일치가 있다고 지적했다.",
        "required": ["출연형 R&D는 기술료를 재출연·재투자하는 방식이라고 했다.", "투자형 R&D는 지분 투자 수익을 재투자하는 방식이라고 했다.", "출연형 기술료를 투자형 기금에 쓰는 것은 불일치가 있다고 했다."],
        "forbidden": ["두 방식의 생태계가 완전히 같다고 설명했다."],
        "exact": [{"type": "person", "value": "임명현"}],
    },
    {
        "candidate": "candidate-026", "category": "entity_date",
        "question": "류광준 본부장은 제도 개선을 서둘러야 하는 근거로 설문 찬성률과 예산 적용 시점을 어떻게 제시했는가?",
        "answer": "류광준 본부장은 진행 중인 설문에서 80% 이상이 제도 개선에 찬성한다고 말했다. 또한 상반기 중 법을 개정하지 못하면 2027년 예산에 적용하기 어려워 다시 1년을 놓칠 수 있다고 설명했다.",
        "required": ["설문에서 80% 이상이 찬성한다고 말했다.", "상반기 법 개정이 안 되면 2027년 예산 적용이 어렵다고 했다.", "다시 1년을 놓칠 수 있다고 했다."],
        "forbidden": ["설문 반대가 80% 이상이라고 말했다."],
        "exact": [{"type": "person", "value": "류광준"}, {"type": "number", "value": "80% 이상"}, {"type": "date", "value": "2027"}],
    },
    {
        "candidate": "candidate-028", "category": "comparison_timeline",
        "question": "곽상언 위원은 2025년 6월 소비자심리지수와 대선 이후 소상공인체감지수를 어떻게 비교하고 해석했는가?",
        "answer": "곽상언 위원은 2025년 6월 소비자심리지수가 108.7로 100을 넘어 경기 낙관을 나타낸다고 설명했다. 반면 대선 이후 소상공인체감지수는 70으로 이전 47.6보다 크게 올랐지만 여전히 100 미만이어서 소상공인은 경기를 비관적으로 본다고 해석했다.",
        "required": ["소비자심리지수 108.7은 경기 낙관을 뜻한다고 했다.", "소상공인체감지수는 47.6에서 70으로 올랐다고 했다.", "70은 여전히 100 미만이어서 소상공인이 비관적이라고 해석했다."],
        "forbidden": ["소상공인체감지수가 100을 넘어 낙관적이라고 말했다."],
        "exact": [{"type": "number", "value": "108.7"}, {"type": "number", "value": "70"}, {"type": "number", "value": "47.6"}],
    },
    {
        "candidate": "candidate-030", "category": "fact",
        "question": "박상혁 위원은 인사청문경과보고서를 어떤 의견으로 채택해야 한다고 요청했고 그 이유로 어떤 정책 문제를 들었는가?",
        "answer": "박상혁 위원은 후보자에게 금융·경제 위기를 극복할 자질과 경험이 부족하다며 인사청문경과보고서를 부적격 의견으로 채택해 달라고 요청했다. 해결이 필요한 문제로 PF와 가계부채를 들었다.",
        "required": ["인사청문경과보고서를 부적격 의견으로 채택해 달라고 했다.", "후보자의 자질·역량·경험 부족을 이유로 들었다.", "PF와 가계부채 문제를 언급했다."],
        "forbidden": ["적격 의견으로 단독 채택해 달라고 요청했다."],
        "exact": [{"type": "person", "value": "박상혁"}],
    },
    {
        "candidate": "candidate-032", "category": "synthesis",
        "question": "강희업 국토교통부제2차관은 여러 특례에 대한 부처 반대가 있었음에도 법의 제도적 기반을 먼저 마련하자고 한 이유를 무엇이라고 설명했는가?",
        "answer": "강희업 차관은 특례, 예비타당성조사 면제, 보조금 문제에 대한 각 부처의 부담과 반대로 법이 계속 논란에 머물러 진행되지 못했다고 설명했다. 우선 제도적 기반을 만들고 사업 과정에서 부족한 점을 계속 개정하는 편이 법 집행의 효율성에 낫다고 판단했다고 말했다.",
        "required": ["특례·예타 면제·보조금 문제에 대한 부처 반대가 있었다고 했다.", "논란으로 법이 진행되지 못했다고 했다.", "제도적 기반을 먼저 만들고 부족한 부분을 개정하는 편이 효율적이라고 했다."],
        "forbidden": ["모든 특례를 먼저 확정해야만 법을 만들 수 있다고 했다."],
        "exact": [{"type": "person", "value": "강희업"}, {"type": "organization", "value": "국토교통부"}],
    },
    {
        "candidate": "candidate-033", "category": "entity_date",
        "question": "박용수 인사혁신처차장이 설명한 직위해제자 결원 보충 제한기간 단축의 세 가지 조건은 무엇인가?",
        "answer": "첫째 직위해제자의 직급·직위·직무 특성상 기관 업무수행에 현저한 지장이 우려되고, 둘째 긴급한 결원 보충 필요성이 인정되며, 셋째 인사혁신처와 협의해야 한다.",
        "required": ["기관 업무수행에 현저한 지장이 우려돼야 한다.", "긴급한 결원 보충 필요성이 인정돼야 한다.", "인사혁신처와 협의해야 한다."],
        "forbidden": ["기관이 아무 조건 없이 제한기간을 단축할 수 있다고 설명했다."],
        "exact": [{"type": "person", "value": "박용수"}, {"type": "number", "value": "3가지"}, {"type": "organization", "value": "인사혁신처"}],
    },
    {
        "candidate": "candidate-039", "category": "entity_date",
        "question": "민병덕 위원은 공정거래 사건 처리 지연을 설명하며 카르텔 담합과 독과점 사건의 평균 처리기간을 각각 얼마라고 언급했는가?",
        "answer": "민병덕 위원은 카르텔 담합 사건이 평균 670일, 독과점 사건이 56개월 걸린다고 언급했다. 이는 뉴질랜드나 대만의 6배 수준이라고 덧붙이며 인력 충원 필요성을 제기했다.",
        "required": ["카르텔 담합 사건 평균 처리기간을 670일이라고 했다.", "독과점 사건 평균 처리기간을 56개월이라고 했다.", "뉴질랜드나 대만의 6배라고 했다."],
        "forbidden": ["카르텔 담합 사건이 평균 56일이라고 말했다."],
        "exact": [{"type": "person", "value": "민병덕"}, {"type": "number", "value": "670일"}, {"type": "number", "value": "56개월"}, {"type": "number", "value": "6배"}],
    },
    {
        "candidate": "candidate-001", "category": "synthesis",
        "question": "서일준 위원은 반도체와 조선산업의 경쟁력 문제를 주 52시간제와 연결해 어떤 입장을 밝혔는가?",
        "answer": "서일준 위원은 산업 경쟁력의 핵심이 인력과 R&D인데 근로시간 상한으로 인력이 추가 소득을 위해 다른 일을 하고 지역도 약해진다고 주장했다. 반도체와 조선산업의 지속 가능성을 위해 근로시간 운영에 일정한 여지를 둬야 한다는 입장을 밝혔다.",
        "required": ["산업 경쟁력의 핵심으로 인력과 R&D를 들었다.", "근로시간 상한 때문에 협력업체 직원들이 추가 일을 한다고 말했다.", "산업 경쟁력을 위해 운영에 여지를 둬야 한다고 했다."],
        "forbidden": ["주 52시간제를 즉시 전면 폐지하는 법이 확정됐다고 말했다."],
        "exact": [{"type": "person", "value": "서일준"}, {"type": "number", "value": "주 52시간"}],
    },
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = {
        item["candidate_id"]: item
        for item in (json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines())
    }
    if len(ITEMS) != 20:
        raise SystemExit(f"개발문제는 정확히 20개여야 함: {len(ITEMS)}")
    records = []
    for index, spec in enumerate(ITEMS, start=1):
        source = candidates[spec["candidate"]]
        records.append({
            "schema_version": "2.0",
            "id": f"dev-{index:03d}",
            "split": "dev",
            "category": spec["category"],
            "question": spec["question"],
            "mode": "qa",
            "answerable": True,
            "filters": {
                "committee": source["committee_filter"],
                "date_from": source["date"],
                "date_to": source["date"],
            },
            "gold": {
                "answer": spec["answer"],
                "required_claims": spec["required"],
                "forbidden_claims": spec["forbidden"],
                "exact_values": spec["exact"],
                "evidence": [{
                    "chunk_id": source["chunk_id"],
                    "source_id": source["source_id"],
                    "quote": source["text"],
                    "speaker": source["speaker"],
                    "role": source["role"],
                    "committee": source["committee"],
                    "date": source["date"],
                    "page_start": source["page_start"],
                }],
                "refusal_reason": None,
            },
            "provenance": {
                "selection_method": "source_first_v2",
                "created_at": "2026-08-17T22:00:00+09:00",
                "review_status": "source_checked",
            },
        })
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "PASS", "records": len(records), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
