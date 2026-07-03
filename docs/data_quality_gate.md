# 데이터 품질 게이트 (quality_gate)

quality_gate는 ETL [5] qa_pairer_v2 완료 후, [6] jsonl_to_postgres 실행 전에 반드시 통과해야 한다.
기준을 초과한 경우 PostgreSQL 적재를 차단한다.

---

## 실행 위치

```
[5] qa_pairer_v2 완료
  ↓
[Q] quality_gate      ← 여기서 검사
  ↓ 통과 시만
[6] jsonl_to_postgres
```

---

## 체크 항목 10개

### 1. meeting_date null 비율

| 항목 | 기준 | 동작 |
|------|------|------|
| `meeting_date` 필드가 null인 청크 비율 | **5% 이상** | **BLOCK — 적재 중단** |

**이유**: meeting_date가 없으면 날짜 기반 질문("2024년 6월에 논의된 내용은?")이 완전히 망가진다.

---

### 2. speaker 누락 비율

| 항목 | 기준 | 동작 |
|------|------|------|
| `speaker` 필드가 null인 청크 비율 | **10% 이상** | **BLOCK — 적재 중단** |

**예외 가능성** (문서에 기록, 게이트에서는 포함):
- 위원장 발언 중 마커 없이 진행하는 경우
- 보고문, 결의문 등 특정 section_type
- 추후 이 예외를 반영한 보정 speaker 로직 추가 고려

---

### 3. committee 누락 여부

| 항목 | 기준 | 동작 |
|------|------|------|
| `committee` 필드가 null/빈 문자열인 청크 | **1건이라도** | **BLOCK — 적재 중단** |

**이유**: committee 필드는 처음부터 반드시 있어야 한다. 나중에 추가하면 전체 재적재가 필요하다.

---

### 4. 빈 텍스트 청크 비율

| 항목 | 기준 | 동작 |
|------|------|------|
| `text` 또는 `embed_text`가 빈 문자열/null인 청크 비율 | **5% 이상** | **BLOCK — 적재 중단** |

**이유**: 빈 청크는 검색 결과를 오염시키고 임베딩 API 비용을 낭비한다.

---

### 5. 너무 짧은 청크 비율

| 항목 | 기준 | 동작 |
|------|------|------|
| `text` 길이가 100자 미만인 청크 비율 | **20% 이상** | **WARNING — 중단 후보** |

**동작**: 자동 중단하지 않고 경고를 리포트에 기록한다. 비율이 매우 높으면 chunker_v2 병합 규칙 재확인.

---

### 6. 너무 긴 청크 비율

| 항목 | 기준 | 동작 |
|------|------|------|
| `text` 길이가 3000자를 초과하는 청크 수 | 전체의 **5% 이상** | **WARNING** |

**동작**: 자동 중단하지 않고 경고만 기록한다. 임베딩 토큰 비용과 검색 품질에 영향을 줄 수 있다.

---

### 7. 중복 chunk_id

| 항목 | 기준 | 동작 |
|------|------|------|
| 동일한 `chunk_id`가 2건 이상 존재 | **1건이라도** | **BLOCK — 적재 중단** |

**이유**: chunk_id는 출처 원문 조회의 기준이다. 중복 시 upsert가 잘못된 데이터를 덮어쓴다.

---

### 8. PDF 마커 인식률

| 항목 | 기준 | 동작 |
|------|------|------|
| 해당 PDF에서 발언자 마커(◯/○/◎)가 발견된 페이지 비율 | **body 페이지 중 10% 미만** | **WARNING — 수동 확인 필요** |

**동작**: 자동 중단하지 않는다. 리포트에 "parser 규칙 수동 확인 필요" 메시지 출력.

---

### 9. qa_pair 격리 확인

| 항목 | 기준 | 동작 |
|------|------|------|
| `chunk_type`이 `qa_pair`도 `utterance`도 아닌 행 존재 | **1건이라도** | **BLOCK — 적재 중단** |
| qa_pair 청크 중 질문/답변이 분리 안 된 행 | 존재 시 | **WARNING** |

**이유**: qa_pair와 utterance가 섞이면 질문 유형별 검색 격리가 불가능해진다.

---

### 10. source 추적 가능 여부

| 항목 | 기준 | 동작 |
|------|------|------|
| `source_id`, `file_name`, `page_start`, `page_end` 중 하나라도 null | **비율 1% 이상** | **BLOCK — 적재 중단** |

**이유**: 출처 원문 조회는 서비스의 신뢰 핵심 기능이다. source 추적이 불가능한 청크는 적재하지 않는다.

---

## 동작 요약표

| # | 항목 | 기준 | 동작 |
|---|------|------|------|
| 1 | meeting_date null 비율 | 5% 이상 | **BLOCK** |
| 2 | speaker 누락 비율 | 10% 이상 | **BLOCK** |
| 3 | committee 누락 | 1건이라도 | **BLOCK** |
| 4 | 빈 텍스트 청크 비율 | 5% 이상 | **BLOCK** |
| 5 | 100자 미만 청크 비율 | 20% 이상 | WARNING |
| 6 | 3000자 초과 청크 비율 | 5% 이상 | WARNING |
| 7 | 중복 chunk_id | 1건이라도 | **BLOCK** |
| 8 | PDF 마커 인식률 낮음 | body 10% 미만 | WARNING |
| 9 | chunk_type 불일치 | 1건이라도 | **BLOCK** |
| 10 | source 추적 불가 비율 | 1% 이상 | **BLOCK** |

BLOCK 항목이 하나라도 발생하면 [6] jsonl_to_postgres를 실행하지 않는다.

---

## 리포트 형식

출력: `data/v2/quality/{source_id}/quality_report.json`

```json
{
  "source_id": "과방위_20240605_1차_123456",
  "checked_at": "2026-07-01T10:00:00",
  "total_chunks": 1234,
  "result": "BLOCK",
  "checks": [
    {
      "check_id": 1,
      "name": "meeting_date_null_rate",
      "value": 0.03,
      "threshold": 0.05,
      "status": "OK"
    },
    {
      "check_id": 2,
      "name": "speaker_missing_rate",
      "value": 0.12,
      "threshold": 0.10,
      "status": "BLOCK",
      "message": "speaker 누락 비율 12.0% — 기준 10% 초과"
    }
  ],
  "warnings": [
    {
      "check_id": 5,
      "name": "short_chunk_rate",
      "value": 0.22,
      "threshold": 0.20,
      "message": "100자 미만 청크 22.0% — chunker_v2 병합 규칙 확인 권장"
    }
  ],
  "block_reason": "speaker_missing_rate 기준 초과"
}
```

`result`는 `OK` 또는 `BLOCK` 중 하나다.
