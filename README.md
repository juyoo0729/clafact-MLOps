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
