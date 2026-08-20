# FlyHermes MLOps 상태 — 2026-08-20

## 오늘 확인한 결과

- 원격 수정본 반영 확인: `.env.example`과 운영체제 간 경로 호환 처리가 존재함
- 원격 전체 테스트: `746 passed`, 실패 0건
- FlyHermes 사전검사: `PASS`
  - Python 실행 환경
  - 작업 폴더
  - 영구 상태 저장소
  - 재시작 후 상태 유지
  - 필요한 비밀키의 존재 여부
  - KOSIS HTTPS 연결
- MLOps 품질 게이트: `PASS`
- 최종 품질 보고서: `20260820T044944Z.json`

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

## 실패 방지 보완 및 검증

- 품질 게이트가 선택된 구조화 제공자와 해당 비밀키의 조합을 확인하도록 보완함
- 잘못된 조합 재현 결과: `HOLD / CLAIM_PROVIDER_SECRET_MISSING`
- 지원하지 않는 제공자 설정: `HOLD / CLAIM_PROVIDER_UNSUPPORTED`
- FlyHermes 제공자를 `openai`로 명시한 뒤 전체 품질 게이트 재실행: `PASS`
- 최종 회귀 테스트: `746 passed`, 실패 0건
- 기존 R1 후보를 사용한 R2 제한 검증: 입력 9건 중 1건 처리, `R3_READY` 1건, 구조화 HOLD 0건
- 남은 8건은 검증 범위를 1건으로 제한해 `R2_BATCH_LIMIT_REACHED`로 대기
- R2 실행 제공자: `openai`; 실제 추출기: `OpenAIFunctionClaimExtractor`

R2 제한 검증 결과는 `HOLD`이지만, 이는 실패가 아니라 미처리 8건을 안전하게 남긴 상태입니다. 새 RSS는 다시 수집하지 않았고 키 값도 읽거나 기록하지 않았습니다.

## 반자동 개선 구조

- 운영 실패 분류: 설정·키·테스트·제공자 장애는 학습하지 않고 `REPAIR_REQUIRED`로 분리
- 개선 후보 생성: 고정 R2 dev Gold의 text-free 오류 요약에서 슬롯 한 종류만 선택
- 사람 승인: 한 실험의 provider·model·prompt version·변경 범위·사유 코드와 기준선 점수를 불변 기록
- dev 평가: 응답률 95% 이상, 전체 12슬롯 macro accuracy 개선, parse-status macro F1 비하락을 확인
- 평가를 통과해도 `PROMOTION_REVIEW_REQUIRED`에서 중단
- 잠긴 test, 코드 변경, 모델 승격, 배포는 별도 승인 없이는 실행하지 않음

FlyHermes에서 새 구조의 단위 및 CLI 테스트 13건이 통과했고, 전체 품질 게이트는 `746 passed`, 실패 0건으로 `PASS`했습니다.

기존 운영 실패 요약은 `REPAIR_REQUIRED`로 분류됐으며 `automatic_training_allowed=false`가 기록됐습니다. 제공자 설정 문제는 이미 복구하고 전체 품질 게이트를 통과했습니다.

고정 R2 dev Gold 오류 요약에서 최초 개선 후보 `r2-dev-20260820-indicator-001`을 만들었습니다. 대상 슬롯은 `indicator`, 관측 오류는 227건이며 현재 상태는 `AWAITING_HUMAN_APPROVAL`입니다. 아직 실험·코드 변경·test·승격은 실행하지 않았습니다.

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

1. 다음 수집 가능 시각 이후 승인된 제한 RSS 사이클을 한 번 실행합니다.
2. R2의 `R2_STRUCTURED_EXTRACTOR_FAILED` 재발 여부를 사유 코드와 건수로 확인합니다.
3. 실제 구조화 HOLD가 남으면, `time`과 `indicator` 추출 개선안 한 가지를 고정된 Gold 개발 세트에서 기존 기준선과 비교합니다.

자동으로 값을 채우지 않고 불확실하면 `HOLD`를 유지합니다. 새 RSS의 운영 개수는 정확도 점수로 사용하지 않습니다.
