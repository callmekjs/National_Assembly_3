# POL-7 입장 판정 eval (블라인드 라벨) — 설계 (spec)

> 2026-07-09 브레인스토밍 확정. 목표: POL-5 입장 판정의 품질을 **사람 블라인드 라벨**로
> 검증해 기준선(일치도)을 기록하고, 프롬프트 변경마다 재실행 가능한 eval 자산을 만든다.
> 범위: medical-reform 40건 (로드맵 POL-7 "수동 라벨 30~50건 + 기준선 점수 기록").

## 전제

- **사람 게이트**: 라벨링은 사용자가 직접 한다. LLM 판정을 다른 LLM이 검증하면 무의미
  (LLM이 LLM 채점). 도구는 라벨링을 쉽게 + 라벨 후 일치도 계산만 담당.
- POL-5 판정은 gpt-4o-mini 5택(support/oppose/concern/neutral/none), issue_stances 저장.
- **24개 판정은 미검증 파일럿 품질** — 이 eval 이 medical-reform 한정 기준선을 준다.

## 사용자 결정 (2026-07-09)

1. **블라인드 라벨링** — LLM 판정을 **숨기고** 사용자가 발언만 보고 5택을 직접 매긴다.
   검토(O/X) 방식은 앵커링 편향(실제보다 일치도 부풀림)으로 기각.
2. **범위 = medical-reform 40건** — seed=42 재현 가능 표본. 전체 24개 혼합은
   이슈별 맥락 전환 부담으로 기각(후속).
3. **같은 rubric 제공** — 라벨 파일 상단에 LLM 이 쓴 것과 동일한 5택 정의를 넣어
   사람·LLM 이 같은 기준으로 판정 → 공정 비교.
4. **eval셋 JSON 저장** — 사람 라벨을 `data/eval/stance_eval_medical-reform.json`
   (turn_id→정답)으로 보존 → 프롬프트 변경마다 재실행(검색 eval셋과 동일 방식).

## 흐름

1. **블라인드 라벨 파일 생성** (`scripts/stance_label_sheet.py`):
   issue_stances(medical-reform) + chunks 조인, **`ORDER BY turn_id` 후 seed=42로 40건**
   재현 가능 샘플을 뽑되 **stance 컬럼을 숨긴다**(블라인드). 각 항목: turn_id, speaker, role,
   date, 발언 전문(500자),
   빈칸 `입장: `(사용자 기입). 상단에 5택 정의(rubric) + 기입 안내.
   출력: `data/issues/stance_labels_medical-reform.md`.
2. **사용자 라벨링** — 각 `입장: ` 뒤에 support|oppose|concern|neutral|none 중 하나 기입.
3. **일치도 계산** (`scripts/stance_eval.py`):
   - 라벨 파일 파싱(turn_id→사람 라벨). 미기입·오타 토큰은 경고 후 제외.
   - issue_stances 에서 LLM 판정 조회, turn_id 로 조인.
   - **전체 일치도**(사람==LLM 비율) + **혼동 행렬**(5×5, 사람 라벨 × LLM 판정) 계산.
   - `data/eval/stance_eval_medical-reform.json`(turn_id→사람 라벨) 저장 — 재사용 자산.
   - `data/issues/stance_eval_report.md`에 일치도·혼동행렬·불일치 항목 목록 기록.

## 저장 형식

`data/eval/stance_eval_medical-reform.json`:
```json
{"issue_id": "medical-reform", "rng_seed": 42,
 "labels": {"복지위_20240613_..._turn_0047": "neutral", ...}}
```

## 계산 (순수 로직 — 테스트 대상)

- `parse_label_sheet(text) -> dict[turn_id, stance]` — 라벨 파일에서 turn_id·기입 라벨 추출.
  빈칸/허용밖 토큰은 제외(경고). 순수 함수(파일 무관).
- `agreement(human: dict, llm: dict) -> dict` — 공통 turn_id 에서 일치율 + 혼동행렬
  (사람 라벨 → LLM 판정 카운트) + 불일치 목록. 공통 0건이면 방어.

## 정직한 처리 (문서화)

- **하드 게이트 없음** — 입장은 5택·주관적이라 ≥90% 같은 통과 기준 비현실적. 기준선 기록.
- 예상 혼동: concern↔oppose(조건부 반대 vs 반대), neutral↔none(질의 vs 절차). 혼동행렬로
  어느 경계가 약한지 드러내 프롬프트 정의 보강의 근거(후속).
- **표본 한정** — medical-reform 40건 기준선. 다른 이슈·전체 품질 일반화 아님.

## 테스트

- `parse_label_sheet`: 정상 기입·빈칸 제외·허용밖 토큰 제외·turn_id 추출.
- `agreement`: 완전 일치(100%)·부분·공통 0건·혼동행렬 카운트 정합.

## 범위 밖 (후속)

타임라인 정합성 검사(로드맵 POL-7 다른 축), 전체 24개 이슈 라벨, 하드 게이트·임계값,
불일치 기반 프롬프트 자동 보정.
