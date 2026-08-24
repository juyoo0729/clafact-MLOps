# R3 KOSIS 후보검색 개선 실험

## 1. 선택 구간과 질문

- 구간: **B(R3)**
- 학습 질문: 의미 표준에 등록되지 않은 Claim에 대해 Hard Guard를 낮추지 않고도 공식 KOSIS 후보 표를 더 안정적으로 연결할 수 있는가?
- 결과 판정: **IMPROVED — 후보 부착 coverage만 개선**
- 정확도 판정: **NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD**

후보 표를 붙였다는 사실은 정답 표, Evidence Cell, 공식값, 계산, Verdict가 맞다는 뜻이 아니다.

## 2. Baseline과 병목

고정된 1,542건 R2 replay에서 R3 진입 Claim은 849건이었다. 전부 HOLD였으며 가장 큰 이유는 다음과 같았다.

| R3 HOLD 사유 | 건수 |
|---|---:|
| `CONCEPT_UNREGISTERED` | 662 |
| `AMBIGUOUS_TABLE_SCORE_MARGIN` | 113 |
| `NO_HARD_GUARD_CANDIDATE` | 51 |
| `EVIDENCE_ITEM_UNRESOLVED` | 17 |
| `LOW_SEMANTIC_SCORE` | 6 |

이번 실험 대상은 `CONCEPT_UNREGISTERED`와 `LOW_SEMANTIC_SCORE`뿐이다. 기존 Hybrid 후보 경로는 전체 대상 668건 중 563건, frozen dev 대상 120건 중 97건에 후보를 붙였다.

이 수치는 후보검색 운영 coverage다. 독립적인 R3 정답 table ID가 없으므로 Hit@1, Hit@3, MRR은 계산하지 않았다.

## 3. 한 문장 가설

> 공식 KOSIS 검색 순위를 결정적 문자열 유사도와 결합해 최대 5개 후보만 남기면, Hard Guard를 완화하지 않고 후보 부착 coverage를 높일 수 있다.

## 4. Failing test와 한 가지 변경

먼저 다음 계약을 테스트로 작성했고 구현 전 `ModuleNotFoundError` 실패를 확인했다.

1. 공식 검색 순위와 문자열 점수를 함께 기록한다.
2. 중복 table ID를 제거하고 최대 5개만 남긴다.
3. 후보를 붙여도 `HOLD_NO_TABLE_SELECTED`를 유지한다.
4. 후보 연결 coverage와 R3 정확도를 별도로 기록한다.
5. 문자열 점수가 낮은 경우 임베딩 후속 검토 대상으로 분리한다.

변경 요소는 후보검색 normalizer 하나다.

- KOSIS 공식 검색 순위: 기존 검색 결과의 순서를 결정적 prior로 사용
- 문자열 점수: 정규화 문자열, 문자 2-gram, `SequenceMatcher`를 사용
- 변화율 표현: 증가율·감소율·상승률·하락률·등락률·증감률을 후보검색 안에서만 `변동률`로 정규화
- 최종 점수: 문자열 0.7 + 공식 검색 순위 0.3
- 상위 5개 후보만 다음 메타데이터 단계로 전달
- 점수 0.6 미만은 후보를 보존하되 `EMBEDDING_TOP_K_REVIEW`로 분리

이 정규화는 Semantic Standard 등록이나 table 선택이 아니다.

## 5. 고정 dev 결과

| 지표 | Baseline | 변경 후 |
|---|---:|---:|
| 대상 Claim | 120 | 120 |
| 공식 후보 부착 | 97 (80.8%) | 120 (100.0%) |
| 문자열 단계 준비 | - | 117 (97.5%) |
| 임베딩 후속 검토 | - | 3 (2.5%) |

기존에 후보가 있던 97건과 비교했을 때, 새 top-5 안에 기존 후보가 하나 이상 포함된 Claim은 93건(95.9%)이었다. top-1 동일은 20건뿐이었으므로 문자열 결과를 자동 정답 표로 선택하지 않는 현재 경계가 필요하다.

## 6. 잠금 후 1,542건 최종 실행

dev에서 방법과 기준을 확정한 뒤 조정 없이 전체 replay를 한 번 실행했다.

| 지표 | Baseline | 변경 후 |
|---|---:|---:|
| 전체 입력 | 1,542 | 1,542 |
| 이번 후보검색 대상 | 668 | 668 |
| 공식 후보 부착 | 563 (84.3%) | 668 (100.0%) |
| 문자열 단계 준비 | - | 652 (97.6%) |
| 임베딩 후속 검토 | - | 16 (2.4%) |
| table 자동 선택 | 0 | 0 |

`table 자동 선택 0`은 실패가 아니라 의도한 안전 경계다. 동결 R2 Gold에는 `target_value_role`과 독립적인 정답 table/Evidence Cell이 없기 때문이다.

## 7. KOSIS 연결 파이프라인

```text
R2 atomic Claim + 12 slots + target_value_role
  -> 확정된 Concept/alias exact match
  -> 공식 검색순위 + 결정적 문자열 top-k   [이번 구현]
  -> 낮은 점수만 embedding top-k          [다음 별도 실험]
  -> 필요한 경우 LLM rerank               [후보 제안만]
  -> 공식 ITEM/OBJ/PRD metadata
  -> Hard Guard
  -> Evidence Cell
  -> 공식값 + 결정적 계산
  -> Verdict
```

임베딩과 LLM은 recall을 높이는 후보 제안 단계에만 둔다. 이 모델들이 table top-1을 바로 확정하거나 Hard Guard를 우회해서는 안 된다.

## 8. 오류 분석과 다음 작업

- 전체 668건 모두 공식 후보 identity가 생겼다.
- 652건은 결정적 문자열 단계에서 메타데이터 조회로 넘길 준비가 됐다.
- 16건, 4개 고유 indicator는 문자열 점수가 낮아 임베딩 top-k 실험 대상으로 남겼다.
- 기존에 수집된 공식 메타데이터와 읽기 전용 교차 점검을 하면 668건 중 650건은 top-5 안에 메타데이터가 준비된 후보가 하나 이상 있었다.
- 다음 실험은 16건만 대상으로 embedding recall을 비교해야 한다. 동일한 frozen dev와 독립적인 expected table ID가 생기기 전까지는 정확도라고 부르지 않는다.

Hard Guard를 낮추는 방식은 시도하지 않는다. 현재 가장 큰 병목은 엄격한 Guard가 아니라 의미 표준과 후보검색 coverage였기 때문이다.

## 9. 재현 명령 계약

`tools/run_r3_kosis_candidate_retrieval.py`는 저장된 replay CSV, 저장된 공식 KOSIS table identity JSONL, 선택적인 baseline route CSV만 읽는다. 평가 중 RSS, KOSIS API, LLM/provider API를 호출하지 않으며 새 output directory에 CSV, summary, manifest와 SHA-256을 기록한다.

관련 테스트:

- `tests/unit/test_r3_kosis_candidate_retrieval.py`
- `tests/unit/test_run_r3_kosis_candidate_retrieval.py`

전체 overlay 회귀시험은 61 passed, 3 failed였다. 3건은 변경 전과 동일한 기존 fixture/source 경계 실패이며 이번 변경으로 생긴 새 실패는 0건이다.
