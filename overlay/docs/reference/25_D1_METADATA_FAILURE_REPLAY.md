# D1 KOSIS 구조정보 실패 동일 코호트 재실행

## 결론

D1 전체를 반복 실행하지 않는다. 기준 실행의 `실행이력.csv`에서
`failure_cause=KOSIS_METADATA_UNAVAILABLE`인 Claim만 고정하고, 같은 Claim ID에
한해 수정 전후를 비교한다. 12슬롯이나 Hard Guard 기준은 낮추지 않는다.

## 기준선과 증거 경계

사용자 보고 기준선은 전체 146건, 엄격 성공 8건, 실패 138건이며 구조정보
조회 실패가 52건이다. 다만 이 수치는 현재 저장소에서 원본 실행이력으로
재검증되지 않았다. 로컬에 남아 있는 실행 원장은 158건 snapshot 실행뿐이고,
원본 재분류 규칙으로 복원되는 기본형도 147건이어서 보고된 146건과 1건 차이가
있다. 따라서 같은 52건의 개선 효과는 현재 `NOT_EVALUABLE`이다.

필요한 기준 파일은 146행의 실제 `실행이력.csv`다. 최소한 다음 열이 있어야
한다.

- `claim_id`
- `failed_stage`
- `failure_cause`
- `api_call_count`
- `tbl_id`, `item_id`, `period`, `dimension_coords`
- `response_hash`, `official_value`, `final_verdict`

## 확인된 공통 코드 원인

기준 배치 러너의 `live` 모드는 공식값 조회 API만 연결하고, R3 호출에는
`live_search=None`, `kosis_api_key=None`, `metadata_fetcher=None`을 전달했다.
따라서 로컬 후보의 구조정보가 불완전하면 live 모드에서도 공식 표 검색과
ITM/PRD metadata 보완을 시도할 수 없었다.

이번 변경은 live 모드에만 다음 세 의존성을 전달한다.

1. `KosisLiveCatalogSearch`: 공식 KOSIS 표 식별자 후보 검색
2. `KOSIS_API_KEY`: 구조정보 조회 권한 전달(값은 기록하지 않음)
3. `get_meta`: 공식 ITM/PRD 구조정보 조회

snapshot 모드는 세 값 모두 `None`을 유지한다. 후보 선택, Hard Guard,
Evidence Cell, 공식값 판정 규칙은 변경하지 않았다.
Claim별 `api_call_count`에는 R3 표 검색·ITM/PRD 조회 시도와 R4 공식값 조회
시도를 모두 합산한다.

## 동일 52건 고정 방법

실행 원장이 준비되면 다음 명령으로 정확히 52건만 새 배치로 만든다. 개수,
Claim ID 중복, 원본 배치 누락, 입력 SHA-256이 자동 검증된다.

```powershell
python tools/build_execution_failure_cohort.py `
  --history <146건_실행이력.csv> `
  --source-batch <D1_146_원본배치.json> `
  --failure-cause KOSIS_METADATA_UNAVAILABLE `
  --expected-history-count 146 `
  --expected-cohort-count 52 `
  --output <D1_metadata_failure_52_v1.json>
```

그다음 새 출력 폴더에 같은 52건만 실행한다.

```powershell
python tools/run_execution_batch.py `
  --mode live `
  --batch <D1_metadata_failure_52_v1.json> `
  --output-dir <D1_metadata_failure_52_replay_v2>
```

## 평가 규칙

- 1차 지표: `KOSIS_METADATA_UNAVAILABLE` 52건 중 구조정보 조회를 통과한 수
- 2차 지표: 공식 좌표 확정 수와 공식값 확보 수
- 안전 지표: 새 잘못된 AUTO 0건, snapshot 네트워크 호출 0건
- 정확도: 같은 Claim의 독립 Gold가 없으면 `NOT_EVALUABLE`
- 신규 Claim 일반화: 별도 고정 평가셋 실행 전까지 `NOT_EVALUABLE`

코드 테스트 통과는 연결 구현의 회귀 방지 증거일 뿐, 52건 성능 개선 증거는
아니다. 개선 여부는 실제 146건 실행이력에서 고정한 동일 52건을 재실행한 뒤에만
`IMPROVED` 또는 `NOT_IMPROVED`로 판정한다.
