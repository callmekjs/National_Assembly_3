# POL-4 쟁점 타임라인 — 설계 (spec)

> 2026-07-09 브레인스토밍 확정. 목표: 이슈별 월별 발언 추이를 **병행 2축**(코퍼스 직접
> 집계 + 매핑 표본)으로 반환하는 API. POL-5(입장)·POL-6(구도)·POL-8(브리핑)의 시간 기반.

## 배경 — 왜 병행인가

POL-3 매핑은 **분기 층화 표본**(분기당 상한)이라 월별 매핑 청크 수가 실제 코퍼스 발언량에
비례하지 않는다. 계엄 이슈 실측(2026-07-09):

| 월 | 코퍼스 직접 | 매핑 전체 | 매핑 core | 매핑 포착률 |
|----|------------|----------|----------|-----------|
| 2024-12 (계엄 발생) | 1,478 | 166 | 75 | **5%** |
| 2025-09 (한산) | 148 | 55 | 30 | **20%** |
| 2026-03 (한산) | 44 | 29 | 11 | 25% |

→ 매핑만 그리면 분기 상한이 한산한 달을 상대적으로 부풀려 **피크가 눌린다**(최종리뷰가
Critical로 지적한 "가짜 성장 곡선"). 코퍼스만 그리면 키워드 노이즈(내란·탄핵소추가 계엄과
무관하게 뜬 것)가 섞인다. **두 축은 서로 다른 것을 측정** — 코퍼스=키워드 재현율(볼륨 모양),
매핑=LLM 정밀도(실질 논의, 상한 있음). 병행하면 두 선의 간격 자체가 "그달은 스침이
많았나 실질 토론이 많았나"를 드러낸다.

## 사용자 결정 (2026-07-09)

1. **타임라인 축 = 병행** (코퍼스 직접 + 매핑 core). 매핑 단독(피크 왜곡)·코퍼스 단독
   (노이즈) 모두 기각. 실측 근거는 위 표.
2. **응답 범위 = 월별 추이만** (MVP). 참여 위원회 분포·주요 회의 목록은 POL-8 통합 때.
3. **월 갭 채우기** — 첫~마지막 활동월 사이 빈 달은 0으로 채워 선이 끊기지 않게.
4. **키워드 노이즈 노출** — 코퍼스 선에 시드 키워드 혼입(타이슈 참조)을 일부러 남긴다.
   두 선 간격이 정보이고, 매핑 후보 수집과 같은 키워드를 써 재현·일관성 유지.

## API

`GET /issues/{issue_id}/timeline`

응답:
```json
{
  "issue_id": "martial-law",
  "title": "12·3 비상계엄과 탄핵 정국",
  "months": [
    {"month": "2024-12", "corpus_turns": 1478, "mapped_turns": 166, "mapped_core_turns": 75},
    {"month": "2025-01", "corpus_turns": 241,  "mapped_turns": 24,  "mapped_core_turns": 15}
  ]
}
```

세 수치 모두 **turn 단위**(`count(DISTINCT turn_id)`) — 청크 분할이 긴 발언을 중복
카운트하는 왜곡 방지 (actors.py POL-2 교훈, issue_chunks 스키마 주석에 명시된 설계).

## 구조

- **`backend/issues.py` 신설** — actors.py 패턴(모듈에 집계 함수, main.py는 얇은 라우트
  + 404). 현재 main.py 인라인 `list_issues` 도 이 모듈로 이관(응집도 — POL-4가 이슈 API를
  확장하는 시점).
- **`main.py`** — `GET /issues/{id}/timeline` 라우트 추가(issues.issue_timeline 호출,
  None이면 404), `GET /issues` 는 issues.list_issues 위임으로 교체.

## 데이터 흐름

**쿼리 A — 코퍼스 직접 볼륨** (② 주선): 시드 keywords 로 전체 chunks ILIKE 검색
```sql
SELECT to_char(meeting_date,'YYYY-MM') AS month, count(DISTINCT turn_id) AS corpus_turns
FROM chunks
WHERE text ILIKE ANY(%s)   -- 각 keyword → %kw% 패턴, search_keyword._like_escape 재사용
GROUP BY 1
```

**쿼리 B — 매핑 볼륨** (③ 덧선): issue_chunks 조인, 월별 core/전체. turn 집계는
`chunks.turn_id`(NOT NULL 권위) 사용 — `issue_chunks.turn_id` 는 nullable 이라 재매핑 시
NULL 이 섞이면 과소 카운트 위험 (현재 NULL 0건이나 방어)
```sql
SELECT to_char(c.meeting_date,'YYYY-MM') AS month,
       count(DISTINCT c.turn_id) AS mapped_turns,
       count(DISTINCT c.turn_id) FILTER (WHERE ic.judge='llm_core') AS mapped_core_turns
FROM issue_chunks ic JOIN chunks c ON c.chunk_id=ic.chunk_id
WHERE ic.issue_id=%s
GROUP BY 1
```

**병합 (파이썬)**: 두 결과를 month 키로 합쳐 정렬 + 갭 채우기. **갭 범위 = 두 계열의
합집합** — 등장하는 모든 월의 최소~최대 사이 빈 달을 0으로 채운다(코퍼스·매핑 중 어느
쪽이든 등장한 달을 경계로).

## 예외 처리

- 없는 issue_id → 404 (actors.py `/actors/{name}` 패턴)
- 코퍼스 0 / 매핑 0인 달 → 각각 0으로 반환 (사건 이전 키워드 혼입이 실제로 이럼 — 정보 유지)
- 시드 keywords 없는 이슈 → 코퍼스 쿼리 건너뛰고 매핑 선만 (방어적; 실제 24개엔 다 있음)
- 읽기 전용, 기존 `get_conn()` 풀 사용 — 부작용 없음

## 테스트 (LLM·DB 없는 순수 로직 우선 — 프로젝트 관례)

- `merge_months`: 두 집계 병합 + 갭 채우기 — 갭 있는 입력, 한쪽만 있는 달, 단일 달 경계
- `build_keyword_patterns`: keywords → 이스케이프된 ILIKE 패턴 (`%`·`_` 포함 키워드 방어)
- API 스모크: 계엄 curl → 실측(2024-12 corpus 1478/mapped 166/core 75)과 일치, 없는 이슈 404

## 범위 밖 (안 함)

참여 위원회 분포·주요 회의 목록(POL-8), 프론트엔드 UI(MVP는 JSON API), 이슈별 답변
연동(POL-8).

## 알려진 한계 (문서화)

- **코퍼스 축은 키워드 재현율 신호** — 노이즈 포함(타이슈 키워드 혼입). 정밀 볼륨 아님.
- **매핑 축은 분기 상한** — 절대 볼륨 아님, 상대 추이도 피크 압축됨. core 만 POL-5/6 소비.
- **매핑은 스냅샷** — 코퍼스 2026-06-30 까지 (map_version 추적, POL-3 스펙과 동일).
