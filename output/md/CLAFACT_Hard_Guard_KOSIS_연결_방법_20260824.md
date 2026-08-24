# CLAFACT Hard Guard와 KOSIS 연결 개선 방법

작성일: 2026-08-24  
대상: CLAFACT 파이프라인 개선을 논의하는 팀원·멘토·평가자

## 1. 먼저 결론

KOSIS 통과율을 높이기 위해 Hard Guard 규칙부터 느슨하게 만드는 것은 안전하지 않습니다.

현재 1,542건 원장을 구조적으로 다시 확인한 결과, R3 대상 668건은 다음 세 가지 문제로 나뉩니다.

| 구분 | Claim 수 | 의미 |
|---|---:|---|
| 정규화 KOSIS catalog 후보 없음 | 245 | Guard 문제가 아니라 공식 metadata 연결 범위 문제 |
| 정규화 후보가 있지만 전부 Guard 거절 | 170 | 단위·주기·기간·지역 등 구조 충돌 문제 |
| 구조 Guard 생존 후보가 있음 | 253 | 의미와 Evidence Cell을 추가로 검증할 대상 |

따라서 현재의 안전한 개선 순서는 다음과 같습니다.

> **catalog coverage 확대 → 단위·주기 충돌 교정 → 의미 검증 → Evidence Cell → 공식값·계산·Verdict**

## 2. 이번 결과를 해석하는 방법

전체 1,542건 중 이번 후보검색과 Hard Guard 점검 대상은 668건입니다.

| 지표 | 건수 | 비율 |
|---|---:|---:|
| 전체 원장 | 1,542 | 100.0% |
| 이번 R3 대상 | 668 | 전체의 43.3% |
| 12슬롯 조인 성공 | 668 | 대상의 100.0% |
| 정규화 catalog 후보 보유 | 423 | 대상의 63.3% |
| 구조 Guard 생존 후보 보유 | 253 | 대상의 37.9% |
| 정규화 후보 전부 거절 | 170 | 대상의 25.4% |
| 정규화 catalog 후보 없음 | 245 | 대상의 36.7% |
| table 자동 선택 | 0 | 0.0% |

정규화 후보가 있는 423건만 분모로 보면 253건, 즉 59.8%가 구조 Guard 이후 후보를 하나 이상 남겼습니다.

하지만 253건이 KOSIS 정답 표를 찾았다는 뜻은 아닙니다.

- 생존 후보가 정확히 1개인 Claim: 37건
- 생존 후보가 2개 이상인 Claim: 216건
- 자동 table 선택: 0건

216건은 구조적으로 가능한 표가 여러 개이므로 indicator 의미, population, dimension, ITEM/OBJ/PRD 좌표를 더 확인해야 합니다.

## 3. Claim 분리, 12슬롯, Hard Guard는 다른 작업

### 3.1 Claim 분리

Claim 분리는 한 문장을 독립적으로 검증할 수 있는 최소 주장으로 나누는 작업입니다.

예시 문장:

> 취업자는 20만 명 증가한 2,800만 명이다.

검증 가능한 Claim:

1. 현재 취업자 수는 2,800만 명이다.
2. 취업자 수는 비교 시점보다 20만 명 증가했다.

현재값 2,800만 명과 증가값 20만 명은 같은 표를 쓰더라도 검증 역할과 계산 방법이 다릅니다. 이 둘을 하나의 Claim으로 두면 어떤 값을 검증했는지 불분명해집니다.

### 3.2 12슬롯 구조화

원자 Claim을 다음 12개 정보칸으로 구조화합니다.

| 슬롯 | 질문 |
|---|---|
| `indicator` | 무엇을 측정하는가? |
| `value` | 기사에서 주장한 값은 무엇인가? |
| `unit` | 명, %, 건, 원 중 무엇인가? |
| `time` | 어느 시점 또는 기간인가? |
| `frequency` | 월, 분기, 연 중 어떤 주기인가? |
| `region` | 전국, 시도, 시군구 중 어디인가? |
| `population` | 전체 또는 특정 집단인가? |
| `dimension` | 성별, 연령, 산업 등 어떤 구분인가? |
| `comparison` | 무엇과 비교했는가? |
| `calculation` | 직접값, 증감, 증감률 등 어떤 계산인가? |
| `condition` | 계절조정 등 추가 조건이 있는가? |
| `source_hint` | 작성기관·통계명 단서가 있는가? |

`target_value_role`은 여러 숫자 중 현재값·증감값·비율 중 무엇을 검증할지를 정하는 제어 정보입니다. 12번째 슬롯을 하나 더 늘리는 개념이 아니라, 검증 대상을 선택하기 위한 별도 역할 정보입니다.

### 3.3 Hard Guard

Hard Guard는 붙어 있는 KOSIS 후보가 Claim과 구조적으로 충돌하는지 검사합니다.

- 단위가 맞는가?
- 월·분기·연 주기가 맞는가?
- 전국·시도·시군구 수준이 맞는가?
- 성별·연령 등 필요한 차원이 있는가?
- 요청한 시점의 공식값이 제공되는가?
- 판단에 필요한 공식 metadata가 있는가?

Hard Guard 생존은 다음을 보장하지 않습니다.

- indicator의 의미가 정확히 같다는 보장
- 정답 table ID라는 보장
- ITEM/OBJ/PRD Evidence Cell이 맞다는 보장
- 공식값과 기사값의 최종 판정이 맞다는 보장

## 4. Hard Guard 거절 원인

정규화 후보가 있지만 전부 거절된 170개 Claim에서 거절 코드가 한 번 이상 나타난 Claim 수입니다. 한 Claim에 여러 코드가 함께 나타날 수 있으므로 합계는 170보다 큽니다.

| 거절 코드 | Claim 수 | 확인할 내용 |
|---|---:|---|
| `UNIT_CONFLICT` | 123 | Claim 단위와 공식 표의 단위·배율 |
| `FREQUENCY_CONFLICT` | 104 | 월·분기·연 주기 |
| `TIME_NOT_AVAILABLE` | 31 | 요청 기간과 실제 제공 기간 |
| `REGION_GRANULARITY_CONFLICT` | 19 | 전국·시도·시군구 수준 |
| `AGE_DIMENSION_REQUIRED` | 7 | 연령 조건을 담는 공식 항목 |
| `SEX_DIMENSION_REQUIRED` | 3 | 성별 조건을 담는 공식 항목 |
| `METADATA_INCOMPLETE` | 2 | Guard 판단에 필요한 metadata |

단위와 주기 충돌이 많다는 사실만으로 Hard Guard가 잘못됐다고 결론 내릴 수는 없습니다.

다음 네 원인을 대표 Claim 20건씩 나눠 확인해야 합니다.

1. Claim을 잘못 분리했는가?
2. 12슬롯을 잘못 채웠는가?
3. 관련 없는 KOSIS 후보를 붙였는가?
4. 공식 metadata가 불완전한가?

## 5. 12슬롯을 유연하게 운영하는 방법

유연화는 빈칸을 자동으로 추정해서 통과시키는 것이 아닙니다. 단계마다 필요한 슬롯을 구분하고, 누락 상태와 다음 행동을 기록하는 방식입니다.

| 단계 | 주로 필요한 정보 | 누락됐을 때 처리 |
|---|---|---|
| 후보검색 | indicator, time, source_hint 등 | 가능한 후보만 제안하고 낮은 신뢰도 기록 |
| Hard Guard | unit, frequency, time, region, dimension 등 | 구조 충돌 또는 판단 불가로 HOLD |
| 의미 검증 | indicator, population, dimension, condition | 다중 후보 유지 또는 HUMAN_REVIEW |
| Evidence Cell | table, ITEM, OBJ, PRD | 좌표를 확정할 때까지 HOLD |
| Verdict | value, target_value_role, comparison, calculation | 공식값과 계산이 없으면 판정 불가 |

예를 들어 `source_hint`가 비어 있어도 indicator로 후보검색은 시도할 수 있습니다. 반면 단위나 시점이 불명확한데 최종 MATCH/MISMATCH를 자동 판정해서는 안 됩니다.

## 6. 임베딩 모델과 LLM을 사용하는 위치

임베딩 모델과 LLM은 후보를 더 잘 찾는 보조 수단으로 사용할 수 있습니다.

허용할 수 있는 역할:

- 문자열 검색이 놓친 동의어 후보 추가
- 공식 검색 결과의 top-k 재정렬 제안
- 의미가 비슷한 후보를 HUMAN_REVIEW에 추천
- 낮은 신뢰도 Claim의 오류 유형 분류 보조

허용하면 안 되는 역할:

- Hard Guard 우회
- embedding top-1을 정답 table로 즉시 확정
- 공식 ITEM/OBJ/PRD 좌표 없이 값 조회
- 독립 Gold 없이 정확도 향상을 주장

임베딩은 recall을 높일 수 있지만 표의 단위, 주기, 지역, 좌표, 공식값을 보장하지 않습니다.

## 7. 앞으로의 개선 우선순위

### 1순위: catalog 미연결 245건

- 공식 KOSIS table identity와 metadata를 정규화 catalog에 추가합니다.
- 목표 지표는 `정규화 catalog 후보 보유율`입니다.
- 이 단계에서는 R3 정확도라고 부르지 않습니다.

### 2순위: 후보 전부 거절 170건

- `UNIT_CONFLICT`와 `FREQUENCY_CONFLICT` 대표 유형부터 확인합니다.
- Claim slot, 후보 연결, catalog metadata 중 실제 오류 위치를 구분합니다.
- 같은 frozen dev에서 거절 감소와 새 잘못된 생존 후보를 함께 확인합니다.

### 3순위: 구조 생존 253건

- indicator, population, dimension의 의미 일치를 검증합니다.
- 생존 후보가 여러 개인 216건을 우선 rerank 대상으로 둡니다.
- expected table ID Gold가 생긴 뒤 Hit@1, Hit@3, MRR을 계산합니다.

### 4순위: Evidence Cell과 공식값

- ITEM/OBJ/PRD 좌표를 확정합니다.
- 공식값, 조회 시각, 발표 시점, 기사 당시 값을 기록합니다.
- 결정적인 Python 계산으로 MATCH/MISMATCH 또는 HOLD를 판정합니다.

## 8. 정량평가와 정성평가

### 8.1 지금 계산할 수 있는 정량평가

- 후보 부착 coverage
- 정규화 catalog 조인 coverage
- Hard Guard 구조 생존 비율
- HOLD 사유별 Claim 수
- 후보 수가 1개인 Claim과 여러 개인 Claim 수
- split별 route 분포

이 지표들은 운영 coverage와 구조 준비도이며 정확도가 아닙니다.

### 8.2 독립 Gold가 생긴 뒤 계산할 정량평가

- Claim 분리 Precision/Recall/F1
- 12슬롯 macro accuracy와 whole-claim exact
- R3 table Hit@1, Hit@3, MRR
- Evidence Cell 좌표 exact match
- 공식값 일치율
- R4 Verdict accuracy/F1
- 잘못된 AUTO 비율과 HOLD 안전성

### 8.3 정성평가

- 개선된 대표 Claim
- 악화된 대표 Claim
- 과분리·미분리 사례
- 현재값·증감값 역할 혼동 사례
- 단위·주기·지역 충돌 사례
- 사람이 보면 판단 가능하지만 시스템 정보가 부족한 사례
- 기사 당시 값과 현재 수정값이 달라지는 사례

정성평가는 좋은 예만 제시하지 않고 개선·악화·HOLD 사례를 함께 남겨야 합니다.

## 9. 평가 실험을 진행하는 표준 순서

```text
baseline
  → 가장 큰 병목 하나 선택
  → 한 문장 가설
  → 구현 전 failing test
  → 한 가지 요소만 변경
  → 같은 frozen dev 재평가
  → 개선·악화·HOLD 오류 분석
  → 기준을 바꾸지 않고 locked full 1회 실행
  → 결과와 SHA-256 기록
```

운영 coverage와 Gold 정확도는 반드시 별도로 보고합니다.

독립 expected table ID와 Evidence Cell Gold가 없으므로 이번 R3 정확도 상태는 다음과 같습니다.

```text
NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD
```

## 10. 팀원에게 설명할 문장

> 현재 KOSIS 연결 실패를 모두 Hard Guard의 엄격함으로 보기는 어렵습니다. 668건을 구조적으로 재실행한 결과 245건은 정규화 catalog 미연결, 170건은 단위·주기 등 구조 충돌, 253건은 다음 의미 검증이 가능한 상태로 분리됐습니다. 따라서 Guard를 완화하기보다 catalog coverage와 슬롯·metadata 충돌을 먼저 고치고, 같은 Gold에서 개선 전후를 평가하겠습니다. 구조 생존 후보는 정답 표가 아니므로 Evidence Cell과 공식값 검증 전까지 자동 판정하지 않습니다.

## 11. 확인 체크리스트

- [ ] Claim이 독립적으로 검증 가능한 최소 단위인가?
- [ ] 현재값·증감값·비율을 별도 Claim 또는 역할로 구분했는가?
- [ ] 12슬롯의 missing과 추정값을 구분했는가?
- [ ] 후보 coverage와 table 정확도를 분리했는가?
- [ ] Hard Guard 생존을 정답이라고 부르지 않았는가?
- [ ] 생존 후보가 여러 개인 경우 의미 검증을 거쳤는가?
- [ ] Evidence Cell의 ITEM/OBJ/PRD 좌표를 남겼는가?
- [ ] 공식값, 발표 시점, 조회 시각을 기록했는가?
- [ ] 같은 Gold에서 개선 전후를 비교했는가?
- [ ] 잘못된 AUTO와 안전한 HOLD를 별도로 확인했는가?

## 12. 관련 구현

- 구조 재실행: `overlay/core/r3_hard_guard_readiness.py`
- 실행 도구: `overlay/tools/run_r3_hard_guard_readiness.py`
- 단위 테스트: `overlay/tests/unit/test_r3_hard_guard_readiness.py`
- 원문 제외 실행 테스트: `overlay/tests/unit/test_run_r3_hard_guard_readiness.py`
- 기술 실험 문서: `overlay/docs/reference/22_R3_HARD_GUARD_READINESS.md`
- 설명용 PDF: `output/pdf/CLAFACT_Hard_Guard_KOSIS_연결_방법_20260824.pdf`

## 13. 이번 결과의 한계

- 기사 문장 열을 읽지 않은 부분 구조 평가입니다.
- 동결 R2 Gold에는 독립적인 `target_value_role`이 연결돼 있지 않습니다.
- 독립 expected table ID와 Evidence Cell Gold가 없습니다.
- 구조 생존 후보는 의미 일치와 공식 좌표 검증이 남아 있습니다.
- 따라서 253건을 KOSIS 최종 통과 또는 R3 정답이라고 표현하지 않습니다.

