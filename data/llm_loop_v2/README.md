# LLM Loop v2 데이터

이 폴더는 새 평가 루프 전용이다. 기존 `data/eval` 자료를 복사하거나 정답으로 사용하지 않는다.

최종 생성 파일:

- `schema.json`: 질문과 정답 형식
- `dev.jsonl`: 개발문제 20개
- `blind_questions.jsonl`: 최종 질문 100개
- `blind_gold.jsonl`: 최종 정답과 원문 근거
- `reserve.jsonl`: 예비문제 30개
- `dataset_manifest.json`: 파일 해시와 동결 시각
- `runs/`: 실행별 설정, 원본 답변, 점수, 실패 분류

현재 준비 파일:

- `candidate_sources.jsonl`: 개발문제 검토 후보 40개
- `dev.jsonl`: 원문 검토를 마친 개발문제 20개
- `blind_source_pool.jsonl`: 개발문제와 회의가 겹치지 않는 블라인드·예비 후보 260개
- `blind_authoring_queue.jsonl`: 위험 후보를 제외한 답변 가능 90개·예비 30개 작성 큐
- `blind_unanswerable_draft.jsonl`: 실제 회의 존재와 발언자 부재를 DB로 증명한 10개
- `negative_control_proofs.jsonl`: 답변 불가 10개의 로컬 DB 카운트 증명
- `api_spend_summary.json`: 저장된 전체 토큰의 공식 단가 재집계

`blind_source_pool.jsonl`과 `blind_authoring_queue.jsonl`은 아직 최종 문제나 정답이 아니다.
새 원문 작성 전송 승인 후 120개를 작성하고 자동검사와 130개 명시 검토를 거쳐야 한다.
그 전에는 `materialize_blind.py`가 `blind_gold.jsonl`과 `reserve.jsonl` 생성을 거부한다.
