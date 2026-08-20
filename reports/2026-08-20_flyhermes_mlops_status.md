# FlyHermes MLOps 상태 — 2026-08-20

## 오늘 확인한 결과

- 원격 수정본 반영 확인: `.env.example`과 운영체제 간 경로 호환 처리가 존재함
- 원격 전체 테스트: `735 passed`, 실패 0건, 14.11초
- FlyHermes 사전검사: `PASS`
  - Python 실행 환경
  - 작업 폴더
  - 영구 상태 저장소
  - 재시작 후 상태 유지
  - 필요한 비밀키의 존재 여부
  - KOSIS HTTPS 연결
- MLOps 품질 게이트: `PASS`
- 생성된 품질 보고서: `20260820T040305Z.json`

비밀키는 값이 아니라 존재 여부만 확인했습니다.

## 오늘의 제한 RSS 실행

- 실행 결과 파일: `20260820T041338Z.json`
- 전체 상태: `PIPELINE_HOLD`
- RSS: 신규 10건, R1 준비 10건, HOLD 0건, 오류 피드 0건
- R1: 입력 10건, 처리 5건, 후보 9건, HOLD 1건, 대기 5건
- R1 사유:
  - `R1_BATCH_LIMIT_REACHED`: 5건
  - `R1_NUMERIC_CANDIDATE_NOT_FOUND`: 1건
- R2: 입력 9건, 처리 5건, R3 전달 0건, HOLD 5건, 대기 4건
- R2 사유:
  - `R2_STRUCTURED_EXTRACTOR_FAILED`: 5건
  - `R2_BATCH_LIMIT_REACHED`: 4건
- R3/R4: 전달받은 입력이 없어 실행 보류

설정 존재 여부만 점검한 결과, 구조화 제공자 변수가 없어서 기본값 `hcx`가 선택됐지만 HCX 키는 없었습니다. OpenAI 키는 존재했습니다. 키 값은 읽거나 기록하지 않았습니다.

이번 실행 후 공급자 설정을 변경하거나 재실행하지 않았습니다.

## 이전 제한 실행 상태 — 2026-08-19

- 전체 상태: `PIPELINE_HOLD`
- R1: 입력 10건, 처리 5건, 후보 6건, 보류 대기 5건
- R2: 입력 6건, 처리 5건, HOLD 5건, 대기 1건, R3 전달 0건
- R2 사유:
  - `R2_CLAIM_PARSE_NOT_AUTO_OK`: 4건
  - `CLAIM_SPLIT_CONTEXT_UNRESOLVED`: 1건
  - `R2_BATCH_LIMIT_REACHED`: 1건
- R3/R4: 전달받은 입력이 없어 실행 보류

이 결과는 운영 커버리지와 HOLD 현황이며 정확도 점수가 아닙니다.

## R2 보류 항목 점검

보류 5건에서 비어 있던 슬롯 수는 다음과 같습니다.

- `time`: 4건
- `dimension`: 4건
- `frequency`: 4건
- `indicator`: 3건
- `population`: 3건
- `region`: 3건
- `value`: 1건
- `unit`: 1건
- `target_value_role`: 1건

## 다음 작업

1. FlyHermes 비밀 설정에 `CLAFACT_CLAIM_PROVIDER=openai`를 명시합니다.
2. 설정 반영을 확인한 뒤 다음 승인된 제한 RSS 사이클을 한 번 실행합니다.
3. R2가 정상 호출된 후에도 구조화 HOLD가 남으면, `time`과 `indicator` 추출 개선안 한 가지를 고정된 Gold 개발 세트에서 기존 기준선과 비교합니다.

자동으로 값을 채우지 않고 불확실하면 `HOLD`를 유지합니다. 새 RSS의 운영 개수는 정확도 점수로 사용하지 않습니다.
