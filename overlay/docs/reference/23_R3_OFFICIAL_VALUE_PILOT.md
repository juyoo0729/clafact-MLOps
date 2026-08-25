# R3/R4 KOSIS 공식값 조회 파일럿

## 1. 학습 질문과 범위

이 파일럿의 질문은 다음 두 가지다.

1. R3 후보 표를 늘리면 Evidence Cell 좌표와 공식값 조회까지 자동으로 이어지는가?
2. 이미 등록된 Evidence Cell은 현재 KOSIS metadata와 값 조회 계약을 통과하는가?

구간은 B(R3 통계표 탐색)와 C(R4 근거 좌표)의 경계다. 고정된 `dev` 입력만 사용했고,
기사 원문은 읽지 않았다. 값이 비슷하다는 이유로 통계표를 선택하지 않으며, 항목·분류·기간·단위를
하나로 확정하지 못하면 값 API를 호출하지 않고 `HOLD`한다.

공식값 조회 성공은 연결 시험 결과이지 정확도가 아니다. 독립된 R3 table Gold, R4 Evidence Cell
Gold, 기사 작성 당시 값 Gold가 없으므로 표 선택과 Verdict 정확도는 계속 `NOT_EVALUABLE`이다.

## 2. 가설과 TDD

가설은 "공식 metadata로 Evidence Cell을 먼저 확정하고, 확정된 좌표만 값 API에 전달하면
근거 없는 AUTO를 늘리지 않고 KOSIS 공식값 연결 상태를 측정할 수 있다"이다.

구현 전에 다음 기대 동작을 테스트로 작성했고 대상 모듈이 없어 실패하는 것을 확인했다.

- 신규 후보는 현재 catalog 밖의 고정 `dev` 후보만 선택한다.
- 항목·분류·기간이 하나로 확정된 경우에만 값 조회 좌표를 만든다.
- 등록 좌표도 현재 공식 metadata에 존재하는 코드인지 다시 확인한다.
- 공식 응답이 정확히 한 행이고 단위가 맞을 때만 값을 저장한다.
- 기사 원문과 API 키 값은 산출물에 기록하지 않는다.
- KOSIS 기간 표기의 점·하이픈 등 구분자를 제거해 값 조회 형식으로 정규화한다.

최소 변경은 기간 정규화 함수가 숫자 이외의 문자를 제거하도록 한 것이다. 첫 등록 좌표 실행에서
metadata의 월별 기간 `2026.07`을 값 API에 그대로 보내 2건 모두 실패했고, 같은 입력을
`202607`로 정규화한 뒤 두 건 모두 공식값을 받았다.

## 3. 고정 dev 실행 결과

| 실행 | 대상 | metadata | 좌표 확정 | 공식값 조회 | 해석 |
|---|---:|---:|---:|---:|---|
| 신규 후보 v1 | 20 | 20 | 0 | 0 | 신규 표 수만 늘려서는 좌표가 확정되지 않음 |
| 신규 18 + 구조 Guard 대조 2 | 20 | 20 | 0 | 0 | Guard 단일 생존도 Evidence Cell 확정과 같지 않음 |
| 등록 좌표 v1 | 2 | 2 | 2 | 0 | 월별 기간 표현 차이로 값 응답 계약 실패 |
| 등록 좌표 v2 | 2 | 2 | 2 | 2 | 기간 정규화 후 현재 공식값 조회 성공 |

신규 후보 20건의 안전한 `HOLD` 원인은 다음과 같다.

| reason code | 건수 |
|---|---:|
| `EVIDENCE_DIMENSION_UNRESOLVED` | 4 |
| `EVIDENCE_ITEM_AMBIGUOUS` | 7 |
| `EVIDENCE_ITEM_UNRESOLVED` | 5 |
| `TIME_NOT_AVAILABLE` | 4 |

결론은 KOSIS API 연결 자체보다 **Claim 의미와 Evidence Cell 좌표 확정**이 현재 병목이라는
것이다. 같은 indicator 이름만으로 표를 넓히면 관련 없는 후보가 함께 늘 수 있다. 다음 개선은
`indicator` 단독 키가 아니라 대상·값의 역할·단위·주기·지역을 합친 복합 signature와 검토된
좌표 registry를 사용해야 한다.

## 4. 구현 계약

`core/r3_official_value_pilot.py`는 다음 순서를 강제한다.

```text
frozen dev candidate
  -> official ITM/PRD metadata
  -> exact item + dimensions + period + unit
  -> one Evidence Cell
  -> official value request
  -> value snapshot only
```

어느 단계에서든 하나로 확정되지 않으면 `HOLD`하고 이후 호출을 중단한다. 공식값은 판정 근거의
한 요소일 뿐이며, 기사 수치와 비교해 자동으로 표를 고르거나 Verdict를 만들지 않는다.

`tools/run_r3_official_value_pilot.py`는 실시간 호출을 기본 차단한다. 사람이 명시적으로
`--allow-live-kosis`를 준 실행만 허용하고, 호출 전 환경의 `KOSIS_API_KEY` 존재 여부만 확인한다.
키 값은 manifest와 결과 파일에 기록하지 않는다.

실행 결과는 새 output directory에 다음 파일로 보존한다.

- `pilot_results.csv`: Claim 원문을 제외한 상태·reason code·좌표 결과
- `metadata_snapshots.jsonl`: 공식 metadata 원시 snapshot, 로컬 전용
- `value_snapshots.jsonl`: 공식값 원시 snapshot, 로컬 전용
- `summary.json`: count-only 외부 보고용 결과
- `manifest.json`: 입력·출력 SHA-256과 키 비기록 상태

원시 KOSIS 응답과 1,542건 원장은 공개 Git에 커밋하지 않는다.

## 5. 평가 경계와 다음 작업

이번에 확인된 지표는 다음뿐이다.

- 신규 후보 metadata 수집: 20/20
- 신규 후보 Evidence Cell 확정: 0/20
- 등록 Evidence Cell 현재 metadata 검증: 2/2
- 등록 Evidence Cell 현재 공식값 조회: 수정 전 0/2, 수정 후 2/2

다음 항목은 수치화하지 않는다.

- 올바른 KOSIS 통계표 선택 정확도: 독립 table Gold 없음
- 좌표 exact 정확도: 같은 Claim ID에 연결된 독립 Evidence Cell Gold 없음
- 기사 수치 참·거짓 정확도: 독립 공식값·Verdict Gold와 기사 당시 snapshot 없음

다음 실험은 1,542건 전체 API 호출이 아니다. 신규 후보 HOLD 20건을 네 reason code로 나누고,
각 유형의 대표 Claim에 검토된 좌표를 연결한 뒤 같은 `dev`에서 좌표 확정률을 재측정한다. 좌표
확정률이 개선된 경우에만 해당 좌표의 공식값을 제한적으로 조회한다.

## 6. 공식 계약 근거

- [KOSIS 통계자료 개발가이드](https://kosis.kr/openapi/devGuide/devGuide_0201List.do)
- [KOSIS 공유서비스 소개](https://kosis.kr/openapi/introduce/introduce_01List.do)

KOSIS 값 조회는 통계표, 항목, 주기, 기간과 분류 코드를 명시하는 계약이다. 따라서 12-slot을
무조건 느슨하게 만드는 대신, Claim 분리 단계에서는 모르는 정보를 `UNKNOWN`으로 보존하고
Evidence Cell 단계에서 공식 코드로 확인된 값만 채우는 방식이 안전하다.
