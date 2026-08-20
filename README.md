# clafact-MLOps

ClaFact의 FlyHermes MLOps 작업물을 안전하게 교환하기 위한 저장소입니다.

## 이 저장소에 올리는 것

- MLOps 실행 코드와 설정 예시
- 비밀값이 제거된 운영 문서
- 개수와 상태 코드만 담은 품질 게이트 보고서
- 독립된 Gold 개발 세트로 검증한 실험 결과

## 이 저장소에 올리지 않는 것

- `.env`, API 키, 인증 토큰
- 기사 원문·제목·URL과 RSS 원본
- KOSIS·LLM 제공자의 원본 응답
- 실행 로그, 백업, 가상환경, 캐시
- 로컬 또는 서버의 개인 경로가 들어간 파일

운영 데이터의 `MATCH`, `MISMATCH`, `HOLD` 개수는 정확도 점수가 아닙니다. 정확도는 독립적으로 라벨링된 Gold 데이터에 연결된 경우에만 기록합니다.

## 현재 상태

- [2026-08-20 FlyHermes MLOps 상태](reports/2026-08-20_flyhermes_mlops_status.md)

변경은 로컬에서 검토한 뒤 별도 브랜치와 Pull Request로 교환합니다.

구조화 제공자는 `.env.example`처럼 명시적으로 선택합니다. 실제 키 값은 이 저장소에 저장하지 않습니다.

## 실패 사전 차단

`core/`, `tools/`, `tests/`의 파일은 FlyHermes에 배포된 `clafact-auto`의 같은 경로에 선택적으로 반영하는 MLOps 안전 오버레이입니다.

품질 게이트는 선택된 구조화 제공자와 비밀키가 일치하는지 RSS 실행 전에 확인합니다.

- 지원 제공자: `openai`, `hcx`
- 제공자에 맞는 키가 없으면: `CLAIM_PROVIDER_SECRET_MISSING`
- 지원하지 않는 제공자이면: `CLAIM_PROVIDER_UNSUPPORTED`

두 경우 모두 회귀 테스트와 RSS 실행 전에 `HOLD`로 멈춥니다. 키 값은 보고서에 포함하지 않습니다.

## 반자동 개선

`core/semiauto_improvement.py`와 `tools/run_semiauto_improvement.py`는 다음 흐름을 강제합니다.

`실패 분류 → Gold 확인 → 개선 후보 1건 → 사람 승인 → dev 평가 → 사람 승격 검토`

- 설정·키·테스트 장애는 학습하지 않고 먼저 복구합니다.
- 새 RSS 결과는 정답 데이터로 사용하지 않습니다.
- 고정 R2 dev Gold의 text-free 오류 요약만 개선 후보 생성에 사용합니다.
- 잠긴 test, 코드 변경, 모델 승격, 배포는 자동 실행하지 않습니다.
- 상태 파일은 불변으로 저장해 승인·평가 기준이 나중에 바뀌지 않게 합니다.

자세한 실행 순서는 [반자동 개선 구조](docs/SEMIAUTO_IMPROVEMENT.md)를 확인합니다.
