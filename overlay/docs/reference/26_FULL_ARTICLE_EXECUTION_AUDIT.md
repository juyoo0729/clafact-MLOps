# 전체 기사·Claim·KOSIS 실행 감사 원장

## 1. 목적

최종 통과 건수만 저장하지 않고 다음 질문에 Claim 단위로 답할 수 있는 감사 원장을 만든다.

1. 어떤 기사 본문과 작성일을 사용했는가?
2. 어떤 Claim과 앞뒤 문맥을 평가했는가?
3. 어떤 분류와 12-slot 상태로 실행했는가?
4. 어떤 KOSIS 표·항목·기간·분류 코드를 조회했는가?
5. 실시간 조회인지 cache 재사용인지, 응답은 성공인지 오류인지?
6. 어디에서 중단됐고 왜 `HOLD`됐는가?

`OFFICIAL_VALUE_LINKED`는 공식값 연결 성공을 의미한다. Claim이 사실이라는 정확도 증명은
아니다. 독립 Evidence Cell Gold와 Verdict Gold가 없으면 정확도는 `NOT_EVALUABLE`로
유지한다.

## 2. 전체 기사 범위

1,542 Claim은 고유 기사 499개에 연결된다. `Labeling/source_articles_G001-G200`은 사람이
선별한 200개 묶음이므로 전체 범위가 아니다.

전체 기사 원본은 `kosis/articles_2025.csv`에서 Claim의 `기사번호` 499개를 선택했다.
이 파일에서는 1,542개 Claim 원문이 기사 본문에 모두 정확히 존재했다. 정제본은 일부 Claim
문장을 제거했으므로 문맥 감사 기준 입력으로 사용하지 않았다.

전체 본문은 `articles_499_full.csv`에 보존한다. Excel은 셀 길이 제한과 파일 크기를 고려해
본문 해시·길이·미리보기와 Claim 앞뒤 500자 문맥을 제공한다.

## 3. 산출물

| 파일 | 내용 |
|---|---|
| `CLAFACT_499기사_1542Claim_전수실행감사원장.xlsx` | 팀 검토용 통합 원장 |
| `articles_499_full.csv` | 기사 499개의 전체 본문과 SHA-256 |
| `claim_audit_1542.csv` | Claim별 문맥·분류·시도·좌표·공식값·중단 이유 |
| `kosis_query_audit.csv` | metadata/value 요청 조건과 응답 감사 기록 |
| `summary.json` | count-only 실행 결과 |
| `manifest.json` | 입력·출력 파일 크기와 SHA-256 |

Excel 시트는 다음과 같다.

- `요약`: 기사·Claim·KOSIS 조회·공식값 연결·HOLD 집계
- `전체 기사`: 기사 499개와 본문 출처·해시·Claim 수
- `Claim 실행원장`: 1,542 Claim의 전체 처리 상태
- `KOSIS 조회기록`: metadata 466건과 공식값 좌표 94건
- `사유 집계`: 성공·실패 reason code별 Claim 수
- `실행정보`: 입력 파일 해시와 용어 정의

## 4. 2026-08-25 기준 결과

| 항목 | 건수 |
|---|---:|
| 전체 기사 | 499 |
| 전체 Claim | 1,542 |
| 기사 본문 정확 연결 Claim | 1,542 |
| KOSIS metadata 기록 | 466 |
| KOSIS 공식값 좌표 기록 | 94 |
| 조회 응답 성공 | 557 |
| 조회 HOLD | 3 |
| 공식값 연결 Claim | 85 |
| 최종 HOLD Claim | 1,457 |

KOSIS 조회 성공 557건은 metadata 466건과 값 응답 91건의 합계다. 값 조회 HOLD 3건은
KOSIS 오류코드 30에 해당하며, 해당 좌표를 공유한 5개 Claim은
`KOSIS_VALUE_INVALID_RESPONSE`로 기록했다.

## 5. 성공·실패 기록 규칙

모든 KOSIS 기록은 다음 정보를 저장한다.

- 조회 종류: `METADATA` 또는 `VALUE`
- 기관·통계표·항목·기간·분류 코드
- 조회 시각
- `LIVE_KOSIS` 또는 `LOCAL_CACHE_REUSED`
- 성공/HOLD 상태와 오류코드
- 응답 행 수와 SHA-256
- 동일 좌표를 공유하는 Claim 번호

Claim은 마지막 상태와 함께 중단 단계를 저장한다.

- `CONCEPT`: 의미 표준 또는 registry 미준비
- `REGISTRY`: 후보 없음 또는 복수 좌표
- `EVIDENCE_CELL`: 항목·기간·분류·단위 미확정
- `KOSIS_VALUE`: 공식값 응답 오류
- `OFFICIAL_VALUE`: 공식값 연결 성공

API 키 값은 입력 파일·Excel·CSV·JSON·manifest 어디에도 기록하지 않는다.

## 6. 생성 명령

```powershell
$env:PYTHONPATH='C:\AI_study\pre365\work\clafact-MLOps\overlay'
python -m tools.build_full_article_audit_ledger `
  --articles-csv C:\AI_study\pre365\kosis\articles_2025.csv `
  --claim-ledger-csv C:\AI_study\pre365\CLAFACT_1542_통합진행원장.csv `
  --subtype-csv C:\AI_study\pre365\outputs\20260825_tab_subtype_reclass\clafact_1542_tab_subtypes_v1_20260825.csv `
  --claim-values-csv <claim_value_results.csv> `
  --metadata-snapshots-jsonl <metadata_snapshots.jsonl> `
  --value-snapshots-jsonl <value_snapshots.jsonl> `
  --replay-summary-json <summary.json> `
  --output-dir <새로운_빈_출력폴더>
```

Excel에서 수식을 재계산해 저장한 뒤 manifest 출력 해시를 갱신한다.

```powershell
python -m tools.build_full_article_audit_ledger `
  --output-dir <출력폴더> `
  --refresh-manifest-only
```

같은 출력폴더를 덮어쓰지 않는다. 코드·입력·설정이 달라진 실행은 새 폴더에 생성하고
manifest 해시로 비교한다.

## 7. 다음 실행

이 산출물은 전체 기사 본문을 Claim에 연결한 감사 기준선이다. 아직 499개 기사에서 Claim을
다시 추출하거나 12-slot을 재구성하지 않았다. 다음 실행에서는 다음 순서를 사용한다.

1. 문맥 보완·복수 Claim 분리 대상에 기사 본문과 작성일 전달
2. 원문에 존재하는 값만 자식 Claim의 `target_value_role`로 확정
3. 보완된 Claim을 평가 유형으로 다시 배정
4. KOSIS 후보·metadata·값 조회를 새 실행 원장에 기록
5. 동일 Claim ID의 개선 전후 상태와 reason code 비교

