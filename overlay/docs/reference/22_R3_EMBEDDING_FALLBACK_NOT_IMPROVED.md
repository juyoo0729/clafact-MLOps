# R3 임베딩 fallback 실험 — NOT_IMPROVED

## 1. 선택 구간과 질문

- 구간: **B(R3 후보검색)**
- 질문: 문자열 점수가 낮은 KOSIS 후보만 다국어 문장 임베딩으로 재정렬하면 후보 순서가 개선되는가?
- 결과: **NOT_IMPROVED_PROXY**
- 정확도: **NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD**

이 실험은 앞선 결정적 문자열 후보검색을 대체하지 않는다. 전체 668건 중 문자열 점수가 낮았던 16건만 임베딩 fallback 대상으로 분리했다.

## 2. 고정 입력과 가설

튜닝 입력은 frozen dev의 낮은 문자열 점수 3건이다. 원문·기사 제목·URL은 모델에 전달하지 않았고 다음 정보만 사용했다.

- indicator
- unit
- frequency
- calculation
- 이미 확보된 공식 KOSIS table ID와 table name 최대 5개

한 문장 가설:

> 다국어 문장 임베딩이 한국어 지표와 KOSIS 표 제목의 의미 유사도를 보완하면, 기존 LLM 후보 제안과의 top-1 일관성이 문자열 순위보다 높아질 것이다.

## 3. Failing test와 한 가지 변경

구현 전에 다음 계약 테스트가 `ModuleNotFoundError`로 실패하는 것을 확인했다.

1. `LOW_LEXICAL_CONFIDENCE`만 임베딩 모델을 호출한다.
2. `LEXICAL_READY`는 모델을 호출하지 않는다.
3. cosine similarity로 기존 공식 후보만 재정렬한다.
4. 후보를 재정렬해도 `HOLD_NO_TABLE_SELECTED`를 유지한다.
5. 모델 ID와 revision을 manifest에 고정한다.

변경 요소는 선택적 embedding reranker 하나다. 기존 12슬롯, 문자열 점수, KOSIS 검색, Hard Guard, Evidence Cell 코드는 변경하지 않았다.

## 4. 모델과 실행 경계

- model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- revision: `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`
- local package: `sentence-transformers==5.7.0`
- model card: <https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2>

모델은 임시 로컬 환경에서만 실행했다. 모델 파일, cache, 가상환경, 인증정보는 Git에 포함하지 않았다. 실행 도구는 lazy import를 사용하므로 기본 CLAFACT 실행환경에 새 대형 의존성을 강제하지 않는다.

## 5. frozen dev 결과

| 항목 | 문자열 baseline | 임베딩 fallback |
|---|---:|---:|
| 낮은 점수 Claim | 3 | 3 |
| 처리 완료 | 3 | 3 |
| top-1 순서 변경 | - | 2 |
| 기존 LLM 제안과 비교 가능 | 1 | 1 |
| top-1 제안 overlap | 0 | 0 |
| top-3 제안 overlap | 1 | 1 |

임베딩이 후보 순서를 바꾸기는 했지만 고정된 proxy를 개선하지 못했다. 기존 LLM 제안은 Gold가 아니고 비교 행도 1건뿐이므로, 이 결과를 정확도나 일반화 성능으로 해석할 수 없다.

## 6. 오류 분석

dev의 낮은 점수 지표는 같은 지표 이름 아래 다음 의미가 섞일 수 있었다.

- 설비투자
- 총자본형성
- 건설업 투자액
- 주택·건설 물량

표 제목 임베딩만으로는 기사 값이 수준인지 증감률인지, 단위와 주기가 무엇인지, 어떤 모집단·지역·조건인지 확정할 수 없다. 일부 경우 임베딩이 더 자연스러운 표 제목을 위로 올렸지만, 공식 ITEM/OBJ/PRD와 전체 12슬롯 없이 올바른 표라고 승인할 수 없다.

## 7. 전체 1,542건을 실행하지 않은 이유

frozen dev proxy가 개선되지 않았으므로 locked test와 전체 1,542건 임베딩 replay는 실행하지 않았다. dev 결과를 본 뒤 threshold나 prompt를 조정해 test까지 반복하는 방식은 평가 누수가 된다.

실패한 시도도 삭제하지 않고 다음 산출물로 보존했다.

- `r3_embedding_fallback_v1`
- 결과: `NOT_IMPROVED_PROXY`
- locked replay: `NOT_RUN_UNTIL_DEV_PROXY_IMPROVES_AND_R3_TABLE_GOLD_EXISTS`

## 8. 다음 우선순위

임베딩 모델을 더 크게 바꾸기 전에 다음 입력 계약을 먼저 완성한다.

1. 기사 원문이 없는 12슬롯 전용 replay를 만든다.
2. `region`, `population`, `dimension`, `comparison`, `condition`, `target_value_role`을 포함한다.
3. top-5 후보의 공식 ITEM/OBJ/PRD 메타데이터를 결합한다.
4. Hard Guard reject code를 전수 집계한다.
5. 독립적인 expected KOSIS table ID를 가진 작은 R3 dev Gold를 만든 뒤 lexical/embedding Hit@1, Hit@3, MRR을 비교한다.

현재 근거로는 Python 규칙이나 Hard Guard를 느슨하게 하는 것보다, Guard 입력과 R3 table Gold를 연결하는 것이 우선이다.

## 9. 관련 코드와 테스트

- `core/r3_embedding_fallback.py`
- `tools/run_r3_embedding_fallback.py`
- `tests/unit/test_r3_embedding_fallback.py`
- `tests/unit/test_run_r3_embedding_fallback.py`

임베딩 모델은 공식 후보의 순서를 제안할 뿐이다. 최종 경로는 계속 다음과 같다.

```text
후보검색 -> 공식 메타데이터 -> Hard Guard -> Evidence Cell
        -> 공식값 -> 결정적 계산 -> Verdict 또는 HOLD
```
