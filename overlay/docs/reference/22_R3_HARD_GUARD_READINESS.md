# R3 Hard Guard 구조 준비도 실험

## 1. 선택 구간과 질문

- 구간: **B(R3)**
- 학습 질문: 12슬롯과 공식 KOSIS 후보가 연결된 뒤, 현재 Hard Guard를 완화하지 않고 어느 Claim이 다음 의미 검증 단계로 갈 수 있는가?
- 결과 판정: **MEASURED - 구조 준비도와 HOLD 원인을 분리함**
- 정확도 판정: **NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD**

Hard Guard 생존은 정답 표 선택, Evidence Cell 확정, 공식값 확인, Verdict 정확도를 뜻하지 않는다.

## 2. Baseline과 병목

앞선 후보검색 실험은 R3 대상 668건 모두에 최대 5개의 공식 KOSIS 후보 identity를 붙였다. 그러나 후보 부착 coverage 100%만으로는 KOSIS 연결 성공을 주장할 수 없다.

이번 실험은 후보를 현재 정규화 catalog와 조인한 뒤 다음 구조 조건만 검사한다.

- unit 일치 가능성
- frequency 일치 가능성
- region granularity
- sex/age dimension 요구
- 요청 period의 제공 가능성
- 필수 metadata 완전성

기사 문장과 `target_value_role`은 동결 R2 Gold에 독립적으로 연결되지 않았으므로 읽거나 추정하지 않는다.

## 3. 한 문장 가설

> 기존 Hard Guard를 그대로 재사용하고 거절 코드를 Claim별로 보존하면, 규칙을 약화하지 않고 catalog 확장 문제와 12슬롯/metadata 충돌 문제를 분리할 수 있다.

## 4. Failing test와 한 가지 변경

구현 전에 구조 재실행 모듈이 없어서 `ModuleNotFoundError`가 발생하는 테스트를 먼저 확인했다. 이후 한 가지 변경으로 읽기 전용 구조 준비도 replay를 추가했다.

계약은 다음과 같다.

1. 원장의 기사 문장 열은 아예 읽지 않는다.
2. Claim ID로 12슬롯과 후보를 조인한다.
3. 정규화 catalog에 있는 후보에만 기존 `apply_hard_guard`를 실행한다.
4. 후보가 하나라도 생존해도 table을 자동 선택하지 않는다.
5. 모든 결과는 `HOLD_NO_TABLE_SELECTED` 경계를 유지한다.
6. 결과와 입력의 경로, 바이트, SHA-256을 새 불변 output directory에 기록한다.

## 5. 고정 dev 결과

| 지표 | 건수 | 비율 |
|---|---:|---:|
| 대상 Claim | 120 | 100.0% |
| 정규화 catalog 후보 보유 | 80 | 66.7% |
| 구조 Guard 생존 후보 보유 | 46 | 38.3% |
| 정규화 후보 전부 거절 | 34 | 28.3% |
| 정규화 catalog 후보 없음 | 40 | 33.3% |
| table 자동 선택 | 0 | 0.0% |

정규화 후보가 있는 Claim만 분모로 보면 구조 생존 비율은 46/80, 57.5%다. 이 값은 구조 준비도이지 정확도가 아니다.

## 6. 잠금 후 1,542건 최종 실행

동일한 코드와 기준으로 전체 원장을 재실행했다. 전체 1,542건 중 이번 후보검색/Guard 대상은 668건이다.

| 지표 | 건수 | 대상 668건 기준 |
|---|---:|---:|
| 12슬롯 조인 성공 | 668 | 100.0% |
| 정규화 catalog 후보 보유 | 423 | 63.3% |
| 구조 Guard 생존 후보 보유 | 253 | 37.9% |
| 정규화 후보 전부 거절 | 170 | 25.4% |
| 정규화 catalog 후보 없음 | 245 | 36.7% |
| table 자동 선택 | 0 | 0.0% |

정규화 후보 423건만 분모로 보면 253건, 59.8%가 구조 Guard 이후 다음 단계 후보를 남겼다. 그러나 그중 생존 후보가 정확히 1개인 Claim은 37건뿐이며, 216건은 2개 이상이다. 따라서 top-1 자동 확정은 허용하지 않는다.

## 7. 오류 분석

170개 `HOLD_ALL_NORMALIZED_CANDIDATES_REJECTED` Claim에서 거절 코드가 한 번 이상 나타난 Claim 수는 다음과 같다. 한 Claim에 여러 코드가 함께 나타날 수 있어 합계는 170보다 크다.

| 거절 코드 | 해당 Claim 수 | 해석 |
|---|---:|---|
| `UNIT_CONFLICT` | 123 | Claim 단위와 후보 metadata 단위가 맞지 않음 |
| `FREQUENCY_CONFLICT` | 104 | 월/분기/연 등 주기가 맞지 않음 |
| `TIME_NOT_AVAILABLE` | 31 | 요청 시점이 제공 period에 없음 |
| `REGION_GRANULARITY_CONFLICT` | 19 | 전국/시도/시군구 수준이 맞지 않음 |
| `AGE_DIMENSION_REQUIRED` | 7 | 연령 조건을 담는 차원이 필요함 |
| `SEX_DIMENSION_REQUIRED` | 3 | 성별 조건을 담는 차원이 필요함 |
| `METADATA_INCOMPLETE` | 2 | Guard 판단에 필요한 공식 metadata가 부족함 |

후보 단위 발생 횟수는 `FREQUENCY_CONFLICT` 546회, `UNIT_CONFLICT` 454회, `TIME_NOT_AVAILABLE` 93회, `REGION_GRANULARITY_CONFLICT` 58회, `AGE_DIMENSION_REQUIRED` 17회, `SEX_DIMENSION_REQUIRED` 6회, `METADATA_INCOMPLETE` 8회다. Claim 수와 후보 수를 혼동하지 않는다.

## 8. 개선 우선순위

1. **245건 catalog coverage**: 공식 table metadata를 정규화 catalog에 더 연결한다. Guard를 낮출 문제가 아니다.
2. **170건 충돌 교정**: unit/frequency를 중심으로 Claim slot과 공식 metadata 중 어느 쪽이 잘못 연결됐는지 대표 Claim 묶음으로 확인한다.
3. **253건 의미 검증**: 구조 생존 후보에 대해 indicator 의미, population, dimension, period 좌표를 검증한다.
4. **Evidence Cell**: ITEM/OBJ/PRD 좌표와 공식값을 고정한 뒤 계산과 Verdict로 이동한다.
5. **독립 Gold 평가**: expected table ID와 Evidence Cell Gold가 생긴 뒤 Hit@k, MRR, R3 정확도를 계산한다.

12슬롯을 유연하게 운영한다는 것은 missing 정보를 자동 정답으로 채우는 것이 아니다. 후보검색에 필요한 정보와 최종 판정에 반드시 필요한 정보를 구분하고, 모호하면 `HOLD`를 유지하는 방식이다.

## 9. KOSIS 연결 흐름

```text
atomic Claim
  -> 12 slots (missing 허용 + 상태 기록)
  -> 공식 KOSIS 후보 top-k
  -> 정규화 catalog metadata
  -> Hard Guard                         [이번 구현]
  -> 의미 일치 검증
  -> Evidence Cell (ITEM/OBJ/PRD)
  -> 공식값 + 결정적 계산
  -> Verdict
```

임베딩이나 LLM은 후보 recall과 rerank 보조로 사용할 수 있다. 하지만 이 모델들이 Hard Guard를 우회하거나, 생존 후보를 곧바로 정답 표로 확정해서는 안 된다.

## 10. 재현 계약과 안전 경계

`tools/run_r3_hard_guard_readiness.py`는 저장된 원장, 후보 CSV, 정규화 catalog, period snapshot만 읽는다. 실행 중 KOSIS API, RSS, LLM/provider API를 호출하지 않는다.

- 기사 문장 열: `EXCLUDED_NOT_READ`
- `target_value_role`: `NOT_GRANTED`
- Guard 범위: `PARTIAL_NO_ARTICLE_CONTEXT_NO_TARGET_VALUE_ROLE`
- table 선택: 항상 0
- 다음 필수 단계: `SEMANTIC_MATCH -> EVIDENCE_CELL -> OFFICIAL_VALUE`
- 정확도: `NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD`

관련 테스트:

- `tests/unit/test_r3_hard_guard_readiness.py`
- `tests/unit/test_run_r3_hard_guard_readiness.py`

