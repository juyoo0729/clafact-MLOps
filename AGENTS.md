# CLAFACT MLOps Agent Guide

이 지침은 이 저장소 전체에 적용된다. 이 저장소를 다루는 에이전트는 작업 전에 이 문서와
`overlay/docs/reference/19_BOOTCAMP_EDUCATIONAL_DIRECTION.md`를 먼저 읽는다.

## 1. 프로젝트의 현재 목적

CLAFACT의 1차 목표는 실시간 뉴스 서비스를 확장하거나 무인 운영하는 것이 아니다.

이 저장소의 목적은 부트캠프 교육 과정에서 다음 문제 해결 흐름을 재현 가능한 증거로 남기는 것이다.

```text
baseline -> bottleneck -> hypothesis -> failing test -> one change
         -> fixed-data evaluation -> error analysis -> reflection
```

서비스 화면, RSS 수집, FlyHermes 배포, E2E 데모는 학습 결과를 보여주는 보조 수단이다.
기사 처리량, AUTO/HOLD 비율, API 성공률은 모델 정확도가 아니다.

교육 방향의 근거는 2026-08-22 내부 Notion의 팩트체크 파이프라인 멘토링 세션이다.
내부 Notion URL은 공개 Git 산출물에 복사하지 않는다.

## 2. 교육용 A/B/C 구간

| 구간 | CLAFACT 단계 | 핵심 학습 질문 |
|---|---|---|
| A | R1-R2 | 검증 가능한 Claim과 12-slot IR을 얼마나 안정적으로 만드는가? |
| B | R3 | Hard Guard 이후 올바른 KOSIS 통계표를 얼마나 잘 찾는가? |
| C | R4 | 정확한 Evidence 좌표·공식값·계산으로 재현 가능한 Verdict를 만드는가? |

현재 첫 학습 우선순위는 A구간의 저장된 R2 dev prediction과 frozen Gold 오류 분석이다.
A/B/C를 동시에 바꾸지 않는다.

## 3. 필수 실험 계약

모든 개선 작업은 다음 순서를 지킨다.

1. 고정 입력, Gold, split, scorer, baseline을 먼저 기록한다.
2. Gold와 prediction의 ID/hash 조인 상태를 확인한다.
3. 오류 유형 하나와 한 문장 가설만 선택한다.
4. 기대 동작을 표현하는 failing test를 먼저 작성하고 실제 실패를 확인한다.
5. prompt, rule, normalizer, postprocess 중 한 요소만 최소 수정한다.
6. 관련 단위 테스트와 동일한 고정 평가를 다시 실행한다.
7. 결과를 `IMPROVED`, `NOT_IMPROVED`, `NOT_EVALUABLE` 중 하나로 기록한다.
8. 실패한 시도와 퇴행도 삭제하지 않고 새 ID의 불변 산출물로 남긴다.

개선 중에는 frozen dev만 사용한다. locked test는 최종 후보 확인 전까지 실행하지 않는다.
LLM 비결정론 비교가 별도로 승인되면 동일 설정을 3-5회 반복하고 평균과 표준편차를 함께 기록한다.

## 4. 평가 경계

- 운영 지표와 Gold 성능 지표를 서로 다른 namespace로 유지한다.
- Gold와 동일 ID/hash로 조인된 행에서만 정확도 계열 지표를 계산한다.
- R1 `UNCERTAIN`을 `FALSE`로 바꾸지 않는다.
- R2는 frozen Gold와 저장 prediction이 요구된 계약으로 조인될 때만 12-slot macro accuracy와
  whole-claim exact를 보고한다.
- R3는 Gold table ID와 ranked candidates가 모두 있는 경우에만 Hit@1, Hit@3, MRR을 보고한다.
- R4 Gold20과 prediction의 claim cohort가 다르거나 조인이 0이면
  `NOT_EVALUABLE_DIFFERENT_CLAIM_COHORT`이며 Route/Verdict/table/coordinate 점수를 만들지 않는다.
- conditional replay를 raw article-to-Verdict end-to-end accuracy라고 부르지 않는다.
- Gold, prediction, threshold를 억지로 변환해 점수를 만들지 않는다.

## 5. 안전·자동화 경계

- 평가 코드는 저장된 manifest, prediction, Gold만 읽는다.
- 평가 중 RSS, KOSIS API, LLM/provider API를 새로 호출하지 않는다.
- `HOLD`와 `NOT_EVALUABLE`은 안전하고 유효한 결과다.
- 실패/HOLD/PARTIAL을 자동 재시도하거나 자동학습으로 전환하지 않는다.
- RSS 승인, 새 출처, provider 호출, 코드 변경, Gold 변경은 사람의 별도 승인이 필요하다.
- API 키 값, 기사 원문·제목·URL, raw provider/KOSIS 응답을 읽거나 외부 요약에 기록하지 않는다.
- 기존 run/evaluation artifact를 덮어쓰지 않는다. 새 ID와 새 output directory를 사용한다.
- 외부 보고는 count, status, reason code, metric, artifact path, SHA-256 상태만 포함한다.

## 6. 저장소 수정 범위

- 사용자가 범위를 넓히지 않는 한 `clafact-MLOps` Git만 수정한다.
- 형제 저장소 `clafact-auto`와 `clafact`의 Git 파일을 수정·커밋·푸시하지 않는다.
- 테스트용 임시 복사본은 사용할 수 있지만 실제 형제 저장소의 사용자 변경을 덮어쓰지 않는다.
- dirty worktree의 기존 변경은 사용자 소유로 간주하고 보존한다.
- `overlay/` 파일을 추가·수정하면 `deployment_sha256.json`의 path, bytes, SHA-256,
  executable 기대값을 함께 갱신하고 전 항목을 다시 검증한다.
- Git 브랜치는 `codex/` 접두사를 사용한다.

## 7. 현재 오전 11시 자동화

- automation ID: `daily-clafact-news-gold-evaluation`
- name: `daily-clafact-bootcamp-learning-lab`
- schedule: 매일 오전 11시
- current section: A(R1-R2)
- inputs: frozen R2 dev Gold와 이미 저장된 prediction
- network: RSS/KOSIS/LLM 호출 금지
- behavior: 가장 큰 유효 오류 하나, 학습 질문, 제안 failing test, 다음 단일 가설만 작성
- terminal status: `EXPERIMENT_NOT_RUN`; 사람 승인 없이 코드를 고치거나 새 prediction을 만들지 않음
- duplicate policy: 입력 fingerprint가 같으면 `LEARNING_INPUT_UNCHANGED`이고 새 산출물을 만들지 않음

## 8. 우선순위와 보류 항목

지금 우선순위:

1. 구간별 baseline과 오류 원인 확정
2. failing test가 있는 단일 개선
3. 동일 계약 재평가와 회고
4. 발표용 초기값 -> 시행착오 -> 개선값 증거 정리
5. 필요할 때만 제한된 E2E 데모

현재 보류:

- 새 RSS 확대와 실시간 기사 처리량 증대
- KOSIS QPM 대응용 여러 API 키 자동 전환
- 무중단 서비스, 자동 재시도, 자동학습
- UI 장식과 배포 플랫폼 최적화

위 운영 과제는 핵심 품질 목표와 학습 증거가 확보된 뒤 별도 승인으로 다룬다.

## 9. 주요 문서

- `README.md`: 저장소의 교육 중심 개요
- `overlay/docs/reference/19_BOOTCAMP_EDUCATIONAL_DIRECTION.md`: 교육 목표, 이틀 사이클, 완료 정의
- `overlay/docs/reference/18_AUTONOMOUS_MLOPS_LOOP.md`: 상세 MLOps 실행·평가 경계
- `overlay/config/hermes/clafact_bootcamp_learning_lab_prompt.md`: FlyHermes 오프라인 학습 점검 프롬프트
- `overlay/config/hermes/clafact_autonomous_mlops_loop_master_prompt.md`: bounded controller 공통 경계
- `deployment_sha256.json`: FlyHermes 배포 overlay 무결성 계약

## 10. 완료 보고 형식

에이전트는 작업 완료 시 최소한 다음을 보고한다.

1. 선택한 A/B/C 구간과 학습 질문
2. 수정 파일 목록
3. failing test와 최소 변경 내용
4. baseline과 새 결과의 비교 가능한 지표
5. Gold join coverage와 `NOT_EVALUABLE` 사유
6. 테스트 결과와 기존 실패/새 실패 구분
7. 새 산출물 절대 경로와 SHA-256 검증 상태
8. 운영 coverage와 Gold 정확도를 분리했는지 확인
9. 아직 수치화할 수 없는 항목과 이유

완성 기준은 서비스 기능 수가 아니라, 다른 학습자가 같은 입력과 같은 scorer로 개선 과정을
재현하고 설명할 수 있는가이다.
