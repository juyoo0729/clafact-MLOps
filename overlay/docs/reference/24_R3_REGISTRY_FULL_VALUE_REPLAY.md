# R3 Registry 보강과 1,542건 KOSIS 공식값 전체 조회

## 1. 실행 목적

이 실행은 1,542개 Claim 전부를 다음 경로로 순회했다.

```text
Claim ID 조인
  -> Semantic Concept
  -> 복합 registry signature
  -> 공식 검색 후보 + 로컬 catalog + 등록 Evidence Cell
  -> KOSIS ITM/PRD metadata
  -> 유일한 Evidence Cell
  -> KOSIS 공식값
```

여기서 "전체 조회"는 1,542건을 모두 처리 대상으로 삼았다는 뜻이다. 좌표가 확정되지 않은
Claim에 임의의 표·항목·지역·기간을 넣어 값을 만드는 뜻이 아니다. 좌표가 하나로 확정된 Claim만
값 API를 호출하고 나머지는 reason code와 함께 `HOLD`했다.

공식값은 현재 공개본에서 Claim 기간을 조회한 값이다. 기사 작성 당시 값의 수정 전 snapshot은
확인하지 않았으며, 공식값 유사도로 통계표나 Verdict를 선택하지 않았다.

## 2. Registry 보강 규칙

Registry key는 다음 정보를 결합한다.

- Semantic `standard_key`
- 단위와 주기
- 지역과 모집단
- dimension과 condition
- 계산 유형

후보는 세 출처를 합치되 동일 표를 중복 제거한다.

1. 사람이 등록한 Evidence Cell profile
2. 기존 KOSIS 공식 검색 후보
3. 350개 로컬 catalog의 항목·지표명 lexical 후보

로컬 catalog 후보는 주기와 단위가 충돌하면 lexical 점수 계산 전에 제거한다. 등록 profile도
alias만 맞는다고 사용하지 않고 `calculation_types`와 `comparison_types` 계약을 통과해야 한다.
등록 좌표가 현재 metadata에 존재하면 우선하고, 등록 좌표가 없는 경우 공식 metadata에서 정확한
좌표가 하나만 준비됐을 때만 provisional registry로 사용한다. 둘 이상이면
`MULTIPLE_OFFICIAL_COORDINATES_READY`로 중단한다.

## 3. TDD와 수정 이력

구현 전에 다음 계약을 테스트로 작성해 대상 모듈이 없어 실패하는 것을 확인했다.

- 복합 signature에 의미·단위·주기·지역·모집단·계산 유형이 포함된다.
- catalog 후보는 주기와 단위를 먼저 통과해야 한다.
- 등록 좌표 provenance가 일반 후보보다 우선한다.
- provisional 좌표가 복수이면 자동 선택하지 않는다.
- 동일 Evidence Cell은 값 API를 한 번만 호출한다.
- 1,542 원장과 Concept 원장은 Claim ID로 전수 조인한다.
- 기사 문장과 API 키 값은 결과에 기록하지 않는다.
- cached official response는 동일 셀 재실행에 재사용한다.
- offline cache-only 실행은 API 키가 없어도 외부 호출 없이 끝나며, cache miss를 `HOLD`로 남긴다.

첫 전체 실행에서 KOSIS 연간 응답의 `PRD_SE=A`를 내부 연간 코드 `Y`와 다르다고 판단하는
문제가 발견됐다. 기대 동작 테스트를 추가한 뒤 `A`와 `Y`를 같은 연간 주기로 정규화했다.

두 번째 실행에서는 official response 연결이 늘었지만 등록 profile의 계산 유형을 강제하지 않아
직접값 profile이 다른 계산 Claim에 사용될 수 있는 위험을 발견했다. 최종 v3에서는 alias와 함께
등록 profile의 계산·비교 계약을 강제했다. 연결 수가 줄더라도 의미가 다른 값을 연결하지 않는
최종 결과를 채택했다.

## 4. 2026-08-25 전체 1,542건 재현 결과

| 항목 | 결과 |
|---|---:|
| 전체 입력 Claim | 1,542 |
| Concept ID 조인 | 1,542 |
| 복합 registry signature | 1,018 |
| 후보 고유 KOSIS 표 | 233 |
| ITM/PRD metadata snapshot | 466 |
| 좌표 확정 Claim | 202 |
| 고유 Evidence Cell | 94 |
| 공식값 연결 Claim | 85 |
| 공식값 미연결/HOLD Claim | 1,457 |

Registry signature 상태는 다음과 같다.

| Registry 상태 | signature 수 |
|---|---:|
| `REGISTERED_COORDINATE_VALIDATED` | 25 |
| `PROVISIONAL_UNIQUE_METADATA_REGISTRY` | 122 |
| `HOLD_REGISTRY_UNRESOLVED` | 871 |

좌표 처리 결과는 다음과 같다.

| 좌표 상태 | Claim 수 |
|---|---:|
| `REGISTERED_COORDINATE_READY` | 50 |
| `PROVISIONAL_UNIQUE_METADATA_COORDINATE_READY` | 152 |
| `HOLD_COORDINATE_AMBIGUOUS` | 257 |
| `HOLD_COORDINATE_UNRESOLVED` | 1,083 |

주요 최종 HOLD 원인은 다음과 같다.

| reason code | Claim 수 |
|---|---:|
| `MULTIPLE_OFFICIAL_COORDINATES_READY` | 257 |
| `EVIDENCE_ITEM_UNRESOLVED` | 235 |
| `EVIDENCE_DIMENSION_UNRESOLVED` | 223 |
| `EVIDENCE_PERIOD_UNRESOLVED` | 192 |
| `NO_REGISTRY_CANDIDATE` | 194 |
| `CONCEPT_REGISTRY_NOT_READY` | 135 |
| `UNIT_CONFLICT` | 122 |
| `EVIDENCE_ITEM_AMBIGUOUS` | 81 |
| `TIME_NOT_AVAILABLE` | 13 |
| `KOSIS_VALUE_INVALID_RESPONSE` | 5 |

## 5. 호출량과 재현성

최초 전체 실행은 고유 표 233개의 ITM/PRD metadata를 표 단위로 중복 제거했다. 이전 snapshot
44개를 재사용하고 422회 실시간 metadata 호출을 수행했다. 좌표가 준비된 동일 Evidence Cell을
묶어 공식값 호출 결과를 전체 Claim에 재사용했다.

2026-08-25 재현 실행은 두 단계로 수행했다.

1. `--offline-cache-only`: metadata 466개와 공식값 91개를 재사용하고, 공식값 cache miss
   3개를 외부 호출 없이 `KOSIS_VALUE_CACHE_MISSING`으로 기록했다.
2. `--allow-live-kosis`: 같은 cache를 재사용하여 metadata 실호출은 0회, 공식값 실호출은
   누락된 고유 좌표 3회만 수행했다.

세 좌표는 KOSIS 오류코드 30(해당 조건에 조회 데이터 없음)을 반환했다. 이 좌표를 공유한
5개 Claim은 `KOSIS_VALUE_INVALID_RESPONSE`로 유지했고, 임의의 다른 표나 기간으로 대체하지
않았다. 특히 `LOCAL_CATALOG` 후보가 일반적인 `취업자 수` Claim을 산재보상 취업자 비율표 또는
학교 졸업자수 표에 연결한 사례였으므로, 공식값 85건보다 통과 수를 부풀리지 않는 것이 맞다.

### 실행 모드 선택

- 네트워크 없이 재현: `--offline-cache-only`
- cache miss만 실제 KOSIS 조회: `--allow-live-kosis`
- 두 모드는 동시에 사용할 수 없으며 반드시 하나를 지정한다.
- live 모드에서만 `KOSIS_API_KEY`가 필요하다. 키 값은 manifest·JSONL·Excel에 기록하지 않는다.

산출물은 다음을 포함한다.

- `CLAFACT_1542_공식값_조회결과.xlsx`: 요약, Registry, Claim 공식값, HOLD 사유
- `claim_value_results.csv`: 기사 원문을 제외한 1,542 Claim 결과
- `registry.csv`: 1,016개 복합 signature 상태
- `metadata_snapshots.jsonl`: 로컬 전용 공식 metadata 응답
- `value_snapshots.jsonl`: 로컬 전용 공식값 응답
- `summary.json`, `manifest.json`: count-only 결과와 SHA-256

원시 KOSIS 응답과 전체 값 원장은 공개 Git에 커밋하지 않는다.

## 6. 해석과 다음 우선순위

85/1,542는 정확도가 아니라 공식값 연결 coverage다. 독립 table Gold, Evidence Cell Gold,
기사시점 값 Gold와 Verdict Gold가 없으므로 R3/R4 정확도는
`NOT_EVALUABLE_NO_INDEPENDENT_R3_R4_GOLD`다.

다음 registry 보강 우선순위는 자동 기준 완화가 아니다. Claim 수가 많은 HOLD 묶음부터 사람이
대표 좌표를 검토하는 것이다.

1. 복수 좌표 257건: 표 정의·대상·계산 방식으로 한 표를 검토
2. item 미확정 236건: Claim indicator와 KOSIS item alias registry 보강
3. dimension 미확정 225건: 지역·성별·연령·산업 member code 보강
4. period 미확정 198건: 문맥에서 기간을 복구한 뒤 동일 Claim ID로 재실행
5. 후보 없음 182건: 공식 검색과 embedding은 후보 생성에만 사용하고 자동 확정 금지

## 7. 공식 계약 근거

- [KOSIS 통계자료 개발가이드](https://kosis.kr/openapi/devGuide/devGuide_0201List.do)
- [KOSIS 공유서비스 소개](https://kosis.kr/openapi/introduce/introduce_01List.do)

KOSIS 값 조회에는 통계표·항목·주기·기간·분류 코드가 필요하다. 따라서 12-slot 입력은 결측을
허용할 수 있지만, Evidence Cell 출력은 공식 코드가 하나로 확정될 때만 통과시켜야 한다.
