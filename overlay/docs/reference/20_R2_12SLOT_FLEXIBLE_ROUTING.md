# R2 12슬롯 유연 라우팅과 1,542건 전수 재생

## 학습 질문

R2에서 `time`, `frequency`만 부족한 Claim과 값·단위·의미가 부족한 Claim을 같은 `HOLD`로
저장하지 않으면, KOSIS 안전 경계를 약화하지 않고도 다음 개선 대상을 더 정확히 정할 수 있는가?

## 기준선과 실패 증거

- 입력: frozen `r2_12slot_v2` Gold 1,542건
- 기존 상태: `AUTO_OK 849`, `HOLD 550`, `HUMAN_REVIEW 143`
- 수정 전 failing test: 3 failed, 5 passed
- 관찰된 실패: 상대기간 resolver가 기간을 채워도 이전 missing-slot HOLD가 남았고,
  기간·주기만 부족한 Claim도 일반 HOLD에 섞였다.

## 한 가지 변경

필수 슬롯 계약이나 KOSIS Hard Guard를 완화하지 않고 R2 대기열만 분리했다.

1. 상대기간 보완 후 missing-slot 상태를 다시 검사한다.
2. `time`, `frequency`만 부족하면 `ENRICHMENT_REQUIRED`로 저장한다.
3. 값·단위·`target_value_role`·계산이 부족하거나 의미가 충돌하면 기존처럼 `HOLD`한다.
4. `HUMAN_REVIEW`는 자동 해제하지 않는다.
5. R2 manifest에 `r2_enrichment_required.jsonl`과 해당 count/reason을 기록한다.

## 1,542건 전수 결과

| 운영 경로 | 건수 | 허용되는 해석 |
|---|---:|---|
| `R2_SLOT_READY` | 849 | 동결 Gold의 R2 채점 슬롯이 채워짐 |
| `ENRICHMENT_REQUIRED` | 490 | 기존 HOLD 중 기간·주기 문맥 보완 대상 |
| `HOLD` | 60 | 핵심 슬롯 부족 또는 기록되지 않은 의미 HOLD |
| `HUMAN_REVIEW` | 143 | 기존 사람 검토 결정을 보존 |
| 합계 | 1,542 | 고유 Claim ID 1,542 |

490건 중 `gold_time_source=context_required`는 479건이다. 나머지 11건은
`absolute_in_sentence`로 표시됐지만 기간·주기 슬롯이 비어 있어 별도 진단 대상으로 분리했다.

## 후속 실험: absolute-in-sentence 11건

### 초기 가설과 실패 시험

초기 가설은 “문장 안의 연도·월 표현을 읽으면 11건을 결정론적으로 복원할 수 있다”였다.
수정 전에는 기간 형태를 구분하는 모듈이 없어 대표 시험이 collection error로 실패했다.

### 확인된 반례와 단일 변경

11건에는 목표 기간뿐 아니라 비교 기준, 출생 코호트, 역사적 사건 배경, 여러 연도·범위가 섞여 있었다.
따라서 기간을 자동 입력하지 않고 문장의 기간 형태와 다음 조치만 기록하는 privacy-safe triage를 추가했다.

| 하위유형 | 건수 | 다음 조치 |
|---|---:|---|
| `MULTIPLE_OR_RANGE_PERIODS_REVIEW` | 4 | 목표·비교 기간 분리 또는 원자 Claim 재확인 |
| `DECADE_OR_COHORT_PERIOD_REVIEW` | 3 | 연대가 목표 기간인지 코호트·비교 기준인지 확인 |
| `SINGLE_YEAR_TARGET_CONFIRMATION` | 3 | 단일 연도가 목표인지 사건 배경인지 확인 |
| `PARTIAL_MONTH_NEEDS_TARGET_YEAR_REVIEW` | 1 | 목표 월 확인 후 기사 문맥에서 연도 복원 |
| 합계 | 11 | 자동 입력 0건 |

대표 시험은 수정 후 8개가 통과했고, 같은 1,542건 재생에서 운영 경로 수는
`849 / 490 / 60 / 143`으로 변하지 않았다. 자동 기간 복원 가설의 결론은 `NOT_IMPROVED`다.
대신 기간 누락 세부유형 coverage는 `0/11`에서 `11/11`로 늘었으며, 이는 정확도 지표가 아니라
검토 대기열의 설명 가능성 개선이다.

## 평가 경계

이 분포는 Gold 기대 슬롯을 입력으로 재생한 운영 대기열 수치이며 Claim 추출·분리 정확도나
KOSIS 표·셀·Verdict 정확도가 아니다. 동결 Gold에는 `target_value_role`이 없으므로
`R2_SLOT_READY` 849건도 새 실행에서 숫자 역할을 확정하고 Hard Guard를 통과하기 전에는 R3 AUTO로
보내지 않는다. 기사 문장과 1,542행 원장은 공개 Git에 저장하지 않는다.

## 다음 단일 실험

먼저 `SINGLE_YEAR_TARGET_CONFIRMATION` 3건과 partial-month 1건의 목표 기간을 비공개 검토표에서
확인한다. 그 전에는 runtime period resolver를 넓히지 않는다. 이후 `context_required` 479건을 기사 ID별로
묶고, 원문 전체 대신 기간 후보와 근거 span/hash만 R1에서 전달해 `time`, `frequency`를 한 번 복원한다.
