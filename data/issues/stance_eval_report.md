# 입장 판정 eval — medical-reform

> **판정자 = 사람 (사용자 블라인드 라벨링, 2026-07-11).** 이전 버전(Claude 교차 판정
> 67.5%)을 대체하는 **정식 사람 기준선**. 주의: 같은 날 세션에서 구 스팟체크 파일의
> LLM 판정 ~12건이 화면에 노출된 이력이 있어 완전 무오염은 아님(사용자에게 무시 지시).
>
> **3자 비교 (같은 40건)**: 사람 vs gpt-4o-mini **27.5%** / 사람 vs Claude **25.0%**
> / Claude vs gpt-4o-mini **67.5%**.
> 분포 — 사람: none 21·neutral 9·concern 7·support 3·oppose 0 (무입장 30/40)
> / 두 LLM: 입장 부여 각 33/40, none 0~1.
>
> **해석**: 갈림의 정체는 방향(찬반)이 아니라 **입장 유무 인식**이다. 사람과 LLM 이
> 둘 다 입장을 본 발언에선 방향 정면 충돌(support↔oppose) 0건. 그러나 사람이
> 무입장(주로 none)으로 본 21건에 두 LLM 모두 입장을 부여했다. 겹치는 설명 둘:
> ① LLM 이 질의·비판·중계성 발언에서 입장을 과잉 판정 (두 모델이 같은 편향 공유
> — 교차검증 67.5%가 품질을 과대평가했음을 시사), ② 사람의 none 용법이 rubric
> 정의(순수 절차·인사·딴 주제)보다 넓게 "명시적 주장 없음/확신 없음"으로 쓰임
> (라벨링 중 실토로 뒷받침). **후속 없이는 POL-5 ✅ 승격 불가** — rubric 의
> none/neutral 정의 재정렬 + 재라벨(또는 제2 라벨러) 후 재평가가 다음 단계.

## 혼동행렬 (행=사람, 열=LLM)

| 사람\LLM | support | oppose | concern | neutral | none |
|---|---|---|---|---|---|
| support | 2 | 0 | 0 | 1 | 0 |
| oppose | 0 | 0 | 0 | 0 | 0 |
| concern | 0 | 0 | 6 | 1 | 0 |
| neutral | 1 | 2 | 3 | 3 | 0 |
| none | 6 | 4 | 9 | 2 | 0 |

## 불일치 29건

- `복지위_20240613_52087_52087_turn_0047` 사람=concern / LLM=neutral
- `복지위_20240619_52088_52088_turn_0017` 사람=none / LLM=concern
- `복지위_20240619_52088_52088_turn_0021` 사람=none / LLM=oppose
- `복지위_20240626_52089_52089_turn_0422` 사람=none / LLM=oppose
- `복지위_20240626_52089_52089_turn_0518` 사람=none / LLM=concern
- `복지위_20240626_52089_52089_turn_1898` 사람=none / LLM=concern
- `복지위_20240716_52143_52143_turn_0309` 사람=none / LLM=concern
- `복지위_20240716_52143_52143_turn_0564` 사람=none / LLM=concern
- `복지위_20240716_52143_52143_turn_0636` 사람=none / LLM=oppose
- `복지위_20240821_52223_52223_turn_1123` 사람=neutral / LLM=oppose
- `복지위_20240823_52224_52224_turn_0528` 사람=neutral / LLM=support
- `복지위_20241114_52498_52498_turn_0005` 사람=none / LLM=concern
- `복지위_20250218_52741_52741_turn_0247` 사람=none / LLM=concern
- `복지위_20250218_52741_52741_turn_0564` 사람=none / LLM=concern
- `복지위_20250318_54433_54433_turn_0093` 사람=none / LLM=neutral
- `복지위_20250626_54935_54935_turn_0280` 사람=none / LLM=support
- `복지위_20250718_55057_55057_turn_0680` 사람=none / LLM=support
- `복지위_20250818_55172_55172_turn_0261` 사람=neutral / LLM=concern
- `복지위_20250820_55195_55195_turn_1083` 사람=none / LLM=concern
- `복지위_20250826_55208_55208_turn_0682` 사람=support / LLM=neutral
- `복지위_20250922_55411_55411_turn_0975` 사람=none / LLM=support
- `복지위_20250922_55411_55411_turn_0994` 사람=neutral / LLM=concern
- `복지위_20250922_55411_55411_turn_1121` 사람=none / LLM=neutral
- `복지위_20250923_55403_55403_turn_0181` 사람=none / LLM=support
- `복지위_20251111_55767_55767_turn_0556` 사람=neutral / LLM=oppose
- `복지위_20251111_55767_55767_turn_0584` 사람=neutral / LLM=concern
- `복지위_20251117_55809_55809_turn_0495` 사람=none / LLM=support
- `복지위_20251118_55851_55851_turn_0514` 사람=none / LLM=support
- `복지위_20260313_56377_56377_turn_0130` 사람=none / LLM=oppose
