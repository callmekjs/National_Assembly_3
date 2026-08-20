# LLM 품질 루프 실행 순서

이 문서는 `C:\National_Assembly_3`에서 블라인드 평가를 재현하는 순서다. 앞 단계가
실패하면 다음 단계로 넘어가지 않는다. RRF·Git commit/push·배포는 포함하지 않는다.

## 0. 현재 상태

과거 공개된 1·2차 블라인드는 모두 폐기했으며 현재 제품 점수로 재사용하지 않는다.
round3는 기존 source 282개를 제외한 신규 후보 360건, blind/reserve 작성 원문 120건,
dev 작성 원문 18건, SQL 부정 증명 blind 10건·dev 2건까지 준비됐다. 질문·골드 작성,
원문 독립 검토, G2~G6 통과와 최종 블라인드 단일 실행은 아직 남아 있다.

## 0.5 외부 호출 전 source·승인 예산 사전검사

아래 비용 원장은 사용자가 round3 추가 11달러를 명시적으로 승인한 뒤에만 새로 만든다.
이 명령 자체는 API를 호출하지 않는다. 기존 전체 누적 비용 원장은 별도로 보존하고,
round3 원장은 이번 승인액만 증분 집계한다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\summarize_api_spend.py" `
  --runs-dir "$root\data\llm_loop_v2\round3\runs" `
  --output "$root\data\llm_loop_v2\round3\api_spend_summary.json" `
  --approved-cap 11 --baseline-spend 0 --additional-cap 11

& $py -B "$root\scripts\llm_loop_v2\preflight_round3_sources.py" `
  --dev-queue "$root\data\llm_loop_v2\round3\dev_authoring_queue_v2.jsonl" `
  --queue "$root\data\llm_loop_v2\round3\authoring_queue_v3.jsonl" `
  --dev-unanswerable "$root\data\llm_loop_v2\round3\dev_unanswerable.jsonl" `
  --dev-negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --blind-unanswerable "$root\data\llm_loop_v2\round3\blind_unanswerable.jsonl" `
  --blind-negative-proofs "$root\data\llm_loop_v2\round3\blind_negative_proofs.jsonl" `
  --spend "$root\data\llm_loop_v2\round3\api_spend_summary.json" `
  --output "$root\data\llm_loop_v2\round3\source_preflight.json"
```

`source_ready=true`, `budget_ready=true`, `READY_FOR_AUTHORING` 세 값이 모두 확인돼야 다음
단계로 이동한다. 현재 승인 전 보고서는 source 검사 전부 PASS, 예산만 부족한
`WAITING_FOR_BUDGET`이다.

## 1. 답변 가능 골드 120개 작성

```powershell
$root = 'C:\National_Assembly_3'
$py = "$root\backend\.venv\Scripts\python.exe"
& $py -B "$root\scripts\llm_loop_v2\author_gold.py" `
  --repo $root `
  --queue "$root\data\llm_loop_v2\round3\authoring_queue_v3.jsonl" `
  --output "$root\data\llm_loop_v2\round3\authoring_results.jsonl" `
  --model gpt-5.6-sol --effort low --max-additional-cost 2.5 `
  --max-completion-tokens 1800 `
  --prior-ledger "$root\data\llm_loop_v2\authoring_results_round2.jsonl.calls.jsonl"
```

개발용 답변 가능 18건은 블라인드 큐와 다른 출력·ledger로 작성한다. 새 회차 비용 상한은
과거 ledger 총액과 분리해 적용하되, 두 값은 감사 출력에 함께 남는다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\author_gold.py" `
  --repo $root `
  --queue "$root\data\llm_loop_v2\round3\dev_authoring_queue_v2.jsonl" `
  --output "$root\data\llm_loop_v2\round3\dev_authoring_results.jsonl" `
  --model gpt-5.6-sol --effort low --max-additional-cost 0.5 `
  --max-completion-tokens 1800 `
  --prior-ledger "$root\data\llm_loop_v2\authoring_results_round2.jsonl.calls.jsonl"
```

중간에 끊겨도 성공한 ID는 다시 호출하지 않는다. 실패 시도는 `errors.jsonl`, 성공은
`results.jsonl`에 분리하고 두 원장의 usage를 비용 상한에 모두 포함한다. 비용을 넘으면
마지막 호출까지 장부에 남긴 뒤 멈춘다.

## 2. 원문 일치 자동검사

```powershell
& $py -B "$root\scripts\llm_loop_v2\validate_authored_gold.py" `
  --queue "$root\data\llm_loop_v2\round3\authoring_queue_v3.jsonl" `
  --results "$root\data\llm_loop_v2\round3\authoring_results.jsonl" `
  --report "$root\data\llm_loop_v2\round3\authoring_validation.json" `
  --require-complete

& $py -B "$root\scripts\llm_loop_v2\validate_authored_gold.py" `
  --queue "$root\data\llm_loop_v2\round3\dev_authoring_queue_v2.jsonl" `
  --results "$root\data\llm_loop_v2\round3\dev_authoring_results.jsonl" `
  --report "$root\data\llm_loop_v2\round3\dev_authoring_validation.json" `
  --require-complete
```

ID·원문·메타데이터·핵심값 불일치가 하나라도 있으면 실패한다. 자동검사는 의미 검토를
대체하지 않는다.

## 3. 블라인드·예비 130개와 dev 20개 명시 검토

```powershell
& $py -B "$root\scripts\llm_loop_v2\render_gold_review.py" `
  --results "$root\data\llm_loop_v2\round3\authoring_results.jsonl" `
  --unanswerable "$root\data\llm_loop_v2\round3\blind_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\blind_negative_proofs.jsonl" `
  --markdown "$root\docs\llm_loop\ROUND3_GOLD_REVIEW_PACKET.md" `
  --approvals "$root\data\llm_loop_v2\round3\gold_reviews_pending.jsonl"

& $py -B "$root\scripts\llm_loop_v2\render_gold_review.py" `
  --results "$root\data\llm_loop_v2\round3\dev_authoring_results.jsonl" `
  --unanswerable "$root\data\llm_loop_v2\round3\dev_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --markdown "$root\docs\llm_loop\ROUND3_DEV_REVIEW_PACKET.md" `
  --approvals "$root\data\llm_loop_v2\round3\dev_reviews_pending.jsonl" `
  --expected-answerable 18 --expected-unanswerable 2 `
  --title "round3 개발 골드 20문항 명시 검토"
```

각 항목의 질문 범위·원문 지지·주장 범위·답변 필수값·원문 참고값을 실제로 대조한다.
검토한 파일은 `round3\gold_reviews_completed.jsonl`과
`round3\dev_reviews_completed.jsonl`로 별도 저장한다. 이번 승인 기록은
`Codex independent source review (not human)`이며 사람 검수로 표시하지 않는다. 다섯 체크가
모두 true이고 문항별 검토자·ISO 시각·구체 메모가 있어야 승인 확정 스크립트가 통과한다.
자동검사 PASS만으로 130개를 일괄 승인할 수 없다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\approve_codex_source_review.py" `
  --results "$root\data\llm_loop_v2\round3\authoring_results.jsonl" `
  --validation "$root\data\llm_loop_v2\round3\authoring_validation.json" `
  --unanswerable "$root\data\llm_loop_v2\round3\blind_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\blind_negative_proofs.jsonl" `
  --reviewed-checklist "$root\data\llm_loop_v2\round3\gold_reviews_completed.jsonl" `
  --output "$root\data\llm_loop_v2\round3\gold_approvals.jsonl"

& $py -B "$root\scripts\llm_loop_v2\approve_codex_source_review.py" `
  --results "$root\data\llm_loop_v2\round3\dev_authoring_results.jsonl" `
  --validation "$root\data\llm_loop_v2\round3\dev_authoring_validation.json" `
  --unanswerable "$root\data\llm_loop_v2\round3\dev_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --reviewed-checklist "$root\data\llm_loop_v2\round3\dev_reviews_completed.jsonl" `
  --output "$root\data\llm_loop_v2\round3\dev_approvals.jsonl" `
  --expected-answerable 18 --expected-unanswerable 2
```

## 4. 블라인드 100개·예비 30개 조립과 동결

구형 `dev.jsonl`은 질문에 없는 gold 필터가 14/20건이라 재사용하지 않는다. 아래에서 신규
dev 18+2를 먼저 조립한 뒤 블라인드·예비와 source 중복 0을 확인한다. 동결기는 세 split의
질문 파서 필터가 gold와 100% 일치하지 않으면 즉시 실패한다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\materialize_dev.py" `
  --queue "$root\data\llm_loop_v2\round3\dev_authoring_queue_v2.jsonl" `
  --results "$root\data\llm_loop_v2\round3\dev_authoring_results.jsonl" `
  --unanswerable "$root\data\llm_loop_v2\round3\dev_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --approvals "$root\data\llm_loop_v2\round3\dev_approvals.jsonl" `
  --output "$root\data\llm_loop_v2\round3\dev.jsonl"

& $py -B "$root\scripts\llm_loop_v2\materialize_blind.py" `
  --dev "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --queue "$root\data\llm_loop_v2\round3\authoring_queue_v3.jsonl" `
  --results "$root\data\llm_loop_v2\round3\authoring_results.jsonl" `
  --unanswerable "$root\data\llm_loop_v2\round3\blind_unanswerable.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\blind_negative_proofs.jsonl" `
  --approvals "$root\data\llm_loop_v2\round3\gold_approvals.jsonl" `
    --blind-output "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --reserve-output "$root\data\llm_loop_v2\round3\reserve.jsonl"

& $py -B "$root\scripts\llm_loop_v2\freeze_dataset.py" `
  --dev "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --blind "$root\data\llm_loop_v2\round3\blind_gold.jsonl" `
  --reserve "$root\data\llm_loop_v2\round3\reserve.jsonl" `
  --output-dir "$root\data\llm_loop_v2\round3\frozen"
```

생성된 `dataset_manifest.json`의 해시가 이후 모든 실행의 기준이다.
최종 실행에는 정답이 제거된 `round3\frozen\blind_questions.jsonl`만 입력한다.

G3는 검색을 완전히 건너뛰고 검토된 답변 가능 dev 18건의 gold turn만 제품 생성기에 넣는다.
이웃 turn과 이슈 집계도 비활성화해 생성·검증 자체의 한계를 분리한다.

```powershell
$g3Run = "$root\data\llm_loop_v2\round3\runs\g3-gold-context"
& $py -B "$root\scripts\llm_loop_v2\run_gold_context.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" --output-dir $g3Run `
  --expected-records 18 --max-additional-cost 0.35
& $py -B "$root\scripts\llm_loop_v2\score_deterministic.py" `
  --dataset "$g3Run\answerable_dataset.jsonl" `
  --results "$g3Run\results.jsonl" --output-dir $g3Run
& $py -B "$root\scripts\llm_loop_v2\prepare_judge_inputs.py" `
  --repo $root --dataset "$g3Run\answerable_dataset.jsonl" `
  --results "$g3Run\results.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --output "$g3Run\judge_inputs.jsonl"
& $py -B "$root\scripts\llm_loop_v2\judge_semantic.py" `
  --repo $root --inputs "$g3Run\judge_inputs.jsonl" `
  --output "$g3Run\semantic_judgments.jsonl" `
  --model gpt-5.6-sol --effort high --max-additional-cost 0.40
& $py -B "$root\scripts\llm_loop_v2\summarize_gate.py" `
  --dataset "$g3Run\answerable_dataset.jsonl" `
  --deterministic "$g3Run\deterministic_scores.jsonl" `
  --judgments "$g3Run\semantic_judgments.jsonl" --output-dir $g3Run `
  --expected-records 18 --expected-unanswerable 0 --profile generation
```

G3는 의미·인용 95% 이상, 잘못된 거절 0, 근거 없는 주장 0을 모두 만족해야 한다. 18건에서는
한 건 실패가 94.44%이므로 사실상 18/18 통과가 필요하다.

G4는 폐기된 과거 답변을 최종 점수가 아니라 검증기 보정셋으로만 사용한다. 독립 의미판정이
정상으로 확인한 답변과 근거 없는 구체 주장 사례를 현재 검증기에 재생한다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\calibrate_verifier_from_judgments.py" `
  --inputs "$root\data\llm_loop_v2\runs\baseline-v2-valid\judge_inputs.jsonl" `
  --judgments "$root\data\llm_loop_v2\runs\baseline-v2-valid\semantic_judgments.jsonl" `
  --inputs "$root\data\llm_loop_v2\runs\round2-blind-final\semantic_inputs_portfolio.jsonl" `
  --judgments "$root\data\llm_loop_v2\runs\round2-blind-final\semantic_judgments_portfolio.jsonl" `
  --output "$root\data\llm_loop_v2\round3\verifier_calibration_report.json"
```

근거 없는 구체 주장 차단 100%, 독립 정상 답변 오차단 2% 이하를 모두 만족해야 한다.
답변 가능 질문의 잘못된 거절과 근거에는 있지만 질문 범위를 벗어난 주장은 검증기 단독으로
판별할 수 있다고 포장하지 않고 G3·G5 독립 의미게이트에서 별도로 0건을 요구한다.

신규 dev 20건은 먼저 전체 E2E와 독립 의미채점을 통과해야 한다. 여기서 실패하면 예비나
블라인드를 열지 않고 원인 수정 후 새 실행 폴더로 dev를 다시 검사한다.

```powershell
$devRun = "$root\data\llm_loop_v2\round3\runs\dev-g5"
& $py -B "$root\scripts\llm_loop_v2\run_baseline.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" --output-dir $devRun `
  --max-additional-cost 0.50
& $py -B "$root\scripts\llm_loop_v2\audit_run.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --results "$devRun\results.jsonl" --expected-records 20 `
  --output "$devRun\audit.json"
& $py -B "$root\scripts\llm_loop_v2\analyze_retrieval_trace.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --results "$devRun\results.jsonl" --output "$devRun\retrieval_trace_report.json"
& $py -B "$root\scripts\llm_loop_v2\score_deterministic.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --results "$devRun\results.jsonl" --output-dir $devRun
& $py -B "$root\scripts\llm_loop_v2\prepare_judge_inputs.py" `
  --repo $root --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --results "$devRun\results.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\dev_negative_proofs.jsonl" `
  --output "$devRun\judge_inputs.jsonl"
& $py -B "$root\scripts\llm_loop_v2\judge_semantic.py" `
  --repo $root --inputs "$devRun\judge_inputs.jsonl" `
  --output "$devRun\semantic_judgments.jsonl" `
  --model gpt-5.6-sol --effort high --max-additional-cost 0.50
& $py -B "$root\scripts\llm_loop_v2\summarize_gate.py" `
  --dataset "$root\data\llm_loop_v2\round3\dev.jsonl" `
  --deterministic "$devRun\deterministic_scores.jsonl" `
  --judgments "$devRun\semantic_judgments.jsonl" --output-dir $devRun `
  --expected-records 20 --expected-unanswerable 2 --profile portfolio
```

G5가 PASS인 코드·프롬프트·모델 설정을 예비 실행 전에 동결한다.

```powershell
& $py -B "$root\scripts\llm_loop_v2\freeze_runtime.py" `
  --repo $root --manifest "$root\data\llm_loop_v2\round3\frozen\runtime_manifest.json"
```

예비 30개는 최종 블라인드를 열기 전에 세 번 실행해 반복 실패를 찾는다.

```powershell
foreach ($n in 1..3) {
  $stabilityRun = "$root\data\llm_loop_v2\round3\runs\reserve-stability-$n"
  & $py -B "$root\scripts\llm_loop_v2\freeze_runtime.py" `
    --repo $root --manifest "$root\data\llm_loop_v2\round3\frozen\runtime_manifest.json" --verify
  & $py -B "$root\scripts\llm_loop_v2\run_baseline.py" `
    --dataset "$root\data\llm_loop_v2\round3\reserve.jsonl" --output-dir $stabilityRun `
    --max-additional-cost 0.70
  & $py -B "$root\scripts\llm_loop_v2\audit_run.py" `
    --dataset "$root\data\llm_loop_v2\round3\reserve.jsonl" `
    --results "$stabilityRun\results.jsonl" --expected-records 30 `
    --output "$stabilityRun\run_audit.json"
  & $py -B "$root\scripts\llm_loop_v2\analyze_retrieval_trace.py" `
    --dataset "$root\data\llm_loop_v2\round3\reserve.jsonl" `
    --results "$stabilityRun\results.jsonl" `
    --output "$stabilityRun\retrieval_trace_report.json"
  & $py -B "$root\scripts\llm_loop_v2\score_deterministic.py" `
    --dataset "$root\data\llm_loop_v2\round3\reserve.jsonl" `
    --results "$stabilityRun\results.jsonl" --output-dir $stabilityRun
}

& $py -B "$root\scripts\llm_loop_v2\summarize_stability.py" `
  --dataset "$root\data\llm_loop_v2\round3\reserve.jsonl" `
  --results "$root\data\llm_loop_v2\round3\runs\reserve-stability-1\results.jsonl" `
  --results "$root\data\llm_loop_v2\round3\runs\reserve-stability-2\results.jsonl" `
  --results "$root\data\llm_loop_v2\round3\runs\reserve-stability-3\results.jsonl" `
  --output-dir "$root\data\llm_loop_v2\round3\runs\reserve-stability-summary"
```

90회 노출 중 자동 실패가 하나라도 있으면 코드를 수정하고 새로운 실행 ID로 세 번 다시
검사한다. 이 안정성 규칙은 의미 정확도 검사를 대체하지 않는다.

집계기는 세 실행의 dataset·모델 설정·runtime SHA-256이 하나라도 다르면 안정성 점수를
만들지 않는다.

## 5. 최종 블라인드 단일 실행과 실행 감사

코드·프롬프트를 더 수정하지 않은 상태에서 새 실행 폴더를 하나만 만든다.

```powershell
$run = "$root\data\llm_loop_v2\round3\runs\blind-final"
& $py -B "$root\scripts\llm_loop_v2\freeze_runtime.py" `
  --repo $root --manifest "$root\data\llm_loop_v2\round3\frozen\runtime_manifest.json" --verify
& $py -B "$root\scripts\llm_loop_v2\run_baseline.py" `
  --dataset "$root\data\llm_loop_v2\round3\frozen\blind_questions.jsonl" --output-dir $run `
  --max-additional-cost 1.90

& $py -B "$root\scripts\llm_loop_v2\audit_run.py" `
  --dataset "$root\data\llm_loop_v2\round3\frozen\blind_questions.jsonl" `
  --results "$run\results.jsonl" --expected-records 100 `
  --output "$run\audit.json"

& $py -B "$root\scripts\llm_loop_v2\analyze_retrieval_trace.py" `
  --dataset "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --results "$run\results.jsonl" `
  --output "$run\retrieval_trace_report.json"
```

100개 ID, 질문, 필터, HTTP 200, 응답 구조, 단계별 검색 trace가 모두 맞아야 채점한다.
각 citation의 `support_chunk_ids`와 사용자 `/citations/{chunk_id}` 본문, 의미채점 입력은
답변 모델이 실제 본 동일 turn 문맥이어야 한다.
RRF Recall@10 85%, 최종 문맥 Recall@5 85%, 유형별 RRF Recall@10 75%, 필터 오류 0을
모두 통과하지 못하면 생성·의미 채점 전에 중단한다.

## 6. 규칙 채점과 하드 게이트

```powershell
& $py -B "$root\scripts\llm_loop_v2\score_deterministic.py" `
  --dataset "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --results "$run\results.jsonl" --output-dir $run

& $py -B "$root\scripts\llm_loop_v2\summarize_hard_veto.py" `
  --dataset "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --deterministic "$run\deterministic_scores.jsonl" `
  --output "$run\gate_summary_early.json" --expected-records 100 --profile portfolio
```

이 명령이 실패 코드를 반환하면 하드 기준 미달이다. 그 시점에 최종 판정을 `FAIL_EARLY`로
기록하고 비용이 드는 의미 채점을 실행하지 않는다. 하드 기준을 모두 통과하고 승인 예산이
충분할 때만 아래 독립 의미 채점을 실행한다.

```powershell

& $py -B "$root\scripts\llm_loop_v2\prepare_judge_inputs.py" `
  --repo $root --dataset "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --results "$run\results.jsonl" `
  --negative-proofs "$root\data\llm_loop_v2\round3\blind_negative_proofs.jsonl" `
  --output "$run\judge_inputs.jsonl"

& $py -B "$root\scripts\llm_loop_v2\judge_semantic.py" `
  --repo $root --inputs "$run\judge_inputs.jsonl" `
  --output "$run\semantic_judgments.jsonl" `
  --model gpt-5.6-sol --effort high --max-additional-cost 2.0
```

## 7. 조건부 최종 게이트와 기록

```powershell
& $py -B "$root\scripts\llm_loop_v2\summarize_gate.py" `
  --dataset "$root\data\llm_loop_v2\round3\blind.jsonl" `
  --deterministic "$run\deterministic_scores.jsonl" `
  --judgments "$run\semantic_judgments.jsonl" `
  --output-dir $run --expected-records 100 --profile portfolio

& $py -B "$root\scripts\llm_loop_v2\summarize_api_spend.py" `
  --runs-dir "$root\data\llm_loop_v2\round3\runs" `
  --output "$root\data\llm_loop_v2\round3\api_spend_summary.json"
```

`gate_summary.json`이 정확도 90% 이상·인용 95% 이상·치명적 환각 0·답변 불가
거절 100%를 모두 만족해야 `PASS`다. 하드 게이트가 실패하면 의미 채점과 조건부 집계를
실행하지 않고 해당 블라인드를 폐기한다. 결과는 `STATUS.md`, `SCOREBOARD.md`,
`RUN_LOG.md`, `FINAL_REPORT.md`에 원본 경로와 한계를 포함해 기록한다.
