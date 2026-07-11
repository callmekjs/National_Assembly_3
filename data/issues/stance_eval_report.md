# 입장 판정 eval — medical-reform

> **⚠️ 판정자 출처**: 아래 "사람" 축은 실제로는 **Claude(fable-5) 블라인드 교차 판정**이다 — 사용자
> 결정(2026-07-11)으로 사람 라벨링을 LLM 교차 판정으로 대체. gpt-4o-mini 판정을 보지 않은 격리
> 서브에이전트가 동일 rubric·동일 500자 발췌로 판정. **사람 검증 기준선이 아니므로** POL-5 ✅ 승격
> 근거로는 한 단계 약함(교차검증 수준). 판정 중 시트에 사용자의 부분 라벨 15건이 남아 있었고
> 판정자가 전건 독립 재판정(12건 덮어씀) — 완전한 무오염 블라인드는 아님을 기록.

- 공통 40건, **일치율 0.675** (하드 게이트 없음, 기준선)

## 혼동행렬 (행=Claude 교차판정, 열=gpt-4o-mini)

| 사람\LLM | support | oppose | concern | neutral | none |
|---|---|---|---|---|---|
| support | 7 | 0 | 5 | 0 | 0 |
| oppose | 0 | 3 | 0 | 1 | 0 |
| concern | 2 | 3 | 12 | 0 | 0 |
| neutral | 0 | 0 | 1 | 5 | 0 |
| none | 0 | 0 | 0 | 1 | 0 |

## 불일치 13건

- `복지위_20240613_52087_52087_turn_0047` 사람=oppose / LLM=neutral
- `복지위_20240619_52088_52088_turn_0021` 사람=concern / LLM=oppose
- `복지위_20240626_52089_52089_turn_0422` 사람=concern / LLM=oppose
- `복지위_20240626_52089_52089_turn_0518` 사람=support / LLM=concern
- `복지위_20240626_52089_52089_turn_0779` 사람=support / LLM=concern
- `복지위_20240821_52223_52223_turn_1123` 사람=concern / LLM=oppose
- `복지위_20241114_52498_52498_turn_0005` 사람=support / LLM=concern
- `복지위_20250718_55057_55057_turn_0680` 사람=concern / LLM=support
- `복지위_20250818_55172_55172_turn_0106` 사람=support / LLM=concern
- `복지위_20250818_55172_55172_turn_0261` 사람=neutral / LLM=concern
- `복지위_20250820_55195_55195_turn_1083` 사람=support / LLM=concern
- `복지위_20250826_55208_55208_turn_0682` 사람=none / LLM=neutral
- `복지위_20251117_55809_55809_turn_0495` 사람=concern / LLM=support
