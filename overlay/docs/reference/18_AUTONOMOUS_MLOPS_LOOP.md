# CLAFACT-AUTO: 자동 수집·검증·점수 개선 반복 구조

## 목표

CLAFACT의 자동화 목표는 다음 두 반복을 연결하는 것이다.

```text
승인 RSS -> 기사 입력 -> R1 -> R2 -> R3 -> R4/KOSIS 대조 -> 운영 결과
                                               |
                                               v
                                   HOLD 사유와 커버리지 개선 후보

고정 Gold(dev) -> R2/R3/R4 채점 -> 점수/오류 큐 -> 한 가지 개선 -> 재실행
```

위 두 화살표는 연결되지만 같은 점수는 아니다. 새 기사는 독립 정답이 없으므로
`MATCH`, `MISMATCH`, `UNDETERMINED`, `HOLD` 건수를 만들 수는 있어도 이것을
정확도라고 부르면 안 된다. 정확도는 미리 고정하고 독립 검토한 Gold에서만 계산한다.

## 1. 운영 반복: 새 기사를 안전하게 처리한다

### 입력 승인 경계

RSS config의 `enabled: true`는 사람이 승인한 출처라는 뜻이다. 수집기는 RSS 전문과
발행일이 모두 있는 기록만 `R1_READY_RSS_FULL_TEXT`로 넘긴다. 요약만 있거나 발행일이
없으면 원문을 억지로 추측하지 않고 HOLD한다.

새 출처 추가, RSS 활성화, 웹 크롤링, robots/paywall 우회, 정기 실행 등록은 이 도구의
범위 밖이며 별도 승인 사항이다.

### 한 번 실행하는 명령

```powershell
.\.venv\Scripts\python.exe tools\run_clafact_operational_cycle.py `
  --rss-config config\rss_sources.approved.local.json `
  --state-root data\mlops_operational_state `
  --through r4 --max-records 5 `
  --use-configured-extractor `
  --use-kosis-live-discovery `
  --use-kosis-api
```

`rss_sources.approved.local.json`은 예시 이름이다. 현재 config 중 어떤 출처도 이 명령으로
자동 활성화되지 않는다. `enabled: true`인 승인 출처가 없으면 수집기만 실행해
`NO_R1_READY` count-only 기록을 남기고 R1~R4는 실행하지 않는다.

### 결과의 의미

`data/mlops_operational_state/operational_cycles/<rss_run_id>.json`에는 RSS 집계와
R1~R4의 상태·건수·HOLD reason count만 저장된다. 기사 제목·URL·본문·API 키·KOSIS 원시 응답은
이 요약에 없다.

R3은 후보 표를 고르고 Hard Guard를 통과시키는 단계다. R4에서만 Evidence Cell, 검증된 공식값,
결정론 계산, 최종 Verdict가 이어진다. 어느 단계든 근거가 부족하면 HOLD가 정답이다.

## 2. 평가 반복: 점수로 한 가지씩 개선한다

R2에는 고정 `dev` 270건과 잠긴 `test` 169건이 있다. 개선 중에는 dev만 사용한다.

1. score board의 기준선과 error queue를 확인한다.
2. 가장 큰 오류 유형 중 **하나**만 선택한다. 예: comparison alias.
3. prompt, 규칙, 후처리 중 한 요소만 수정한다.
4. 새 experiment ID로 dev 270건을 실행하고 같은 scorer로 채점한다.
5. 응답률 95% 이상이고 12-slot macro accuracy가 같은 계약에서 재현되면 다음 오류를 선택한다.
6. test는 후보가 안정된 뒤 한 번만 최종 확인한다.

`tools/build_r2_experiment_tracking.py --report <new_report> --predictions <new_predictions>`는
새 실험을 저장된 dev 기준선들과 함께 score board에 넣는다. 새 실험 하나만 보이게 기준선을
덮어쓰지 않는다.

R3/R4는 현재 독립 Gold·기사시점 공식 Snapshot·Evidence coordinate 정답이 충분하지 않으므로,
우선 `HOLD reason`, Evidence 좌표 확정률, 공식값 조회 성공률, as-of 차단률을 운영 지표로 쓴다.
독립 Gold가 준비된 범위에서만 단계별 Hit@k, exact coordinate, Verdict precision/recall을 추가한다.

### 운영 실행과 Gold 평가를 연결하는 별도 CLI

운영 실행이 끝난 뒤 `tools/run_mlops_gold_evaluation.py`를 선택적으로 실행할 수 있다. 이 CLI는
저장된 run/stage manifest, prediction, Gold만 읽으며 RSS·KOSIS·LLM API를 새로 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe tools\run_mlops_gold_evaluation.py `
  --run-manifest <run_manifest.json> `
  --r1-gold <R1_Gold30.csv> `
  --r2-predictions <saved_dev_predictions.jsonl> `
  --r3-predictions <saved_r3_predictions.jsonl> `
  --r4-predictions <saved_r4_predictions.jsonl> `
  --evaluation-id <new_evaluation_id>
```

R3/R4 prediction 파일이 없는 실패·부분 실행에서는 해당 인자를 생략한다. 평가기는 Gold를 다른
prediction으로 바꾸지 않고 각각 `R3_PREDICTION_ARTIFACT_MISSING`,
`R4_PREDICTION_ARTIFACT_MISSING`으로 기록하며 그 단계의 점수를 만들지 않는다.

저장된 pipeline run에 자동으로 연결할 때는 아래 wrapper가 post-run 설정 원본을 새 파일로 복제하고,
실제로 존재하는 R3/R4 산출물만 찾아 정확히 한 번 오프라인 평가한다. 설정 파일, controller run ID,
evaluation ID가 이미 있으면 중단한다.

```sh
/opt/data/clafact_state/venvs/clafact-auto/bin/python \
  tools/run_linked_post_run_evaluation.py \
  --template /opt/data/clafact_state/config/mlops_automation.post_run_full_20260820.json \
  --run-manifest /opt/data/clafact_state/runs/<pipeline_run_id>/run_manifest.json \
  --evaluation-id <new_evaluation_id> \
  --config-output /opt/data/clafact_state/config/daily/<new_config>.json \
  --controller-run-id <new_controller_run_id>
```

같은 evaluation ID의 디렉터리가 이미 있으면 중단하며 기존 산출물을 덮어쓰지 않는다. 새 디렉터리에는
다음 파일이 생긴다.

- `evaluation_summary.json`: `operational_metrics`와 `gold_evaluation_metrics`를 별도 필드로 보존
- `join_results.jsonl`: Gold ID/hash와 prediction의 행 단위 조인 상태 및 reason code
- `review_queue_status.jsonl`: `sentence_hash`가 Gold에 있으면 `EXCLUDE_GOLD_LABELED`로 표시
- `sha256_manifest.json`: 입력과 출력의 SHA-256, 크기, 파일 이름
- `summary.txt`: 기사 내용·URL·비밀값이 없는 count-only 요약

R1의 `UNCERTAIN`은 `FALSE`로 바꾸지 않는다. R2는 선택한 frozen Gold split과 prediction이 완전히
조인될 때만 12-slot macro accuracy와 whole-claim exact를 낸다. R3는 Gold table ID와 저장된 ranked
candidate가 모두 있는 행만 평가 가능하며 그 외 행을 reason code로 남긴다. R4는 Gold20과 prediction의
claim ID cohort가 완전히 같을 때만 정확도 계열 지표를 낸다. 조인이 0건이면
`NOT_EVALUABLE_DIFFERENT_CLAIM_COHORT`이고 Route/Verdict/table/coordinate 점수는 생성하지 않는다.

## 3. 단일-mode 반자동 controller

`tools/run_mlops_automation.py`는 한 번의 호출에서 다음 네 mode 중 정확히 하나만 실행한다.

```text
operational_cycle
post_run_gold_evaluation
gold_replay_evaluation
r2_dev_experiment
```

먼저 `config/mlops_automation.example.json`을 별도 local config로 복사하고, 실행할 mode 하나에만
`enabled: true`를 지정한다. 예시 config는 모든 mode가 비활성이고 scheduling도 비활성이므로 그대로는
실행되지 않는다.

```powershell
.\.venv\Scripts\python.exe tools\run_mlops_automation.py `
  --mode post_run_gold_evaluation `
  --config config\mlops_automation.local.json `
  --controller-run-id <new_controller_run_id> `
  --validate-only
```

`--validate-only`를 제거하면 선택한 mode를 한 번 실행한다. controller는 shell command 문자열을 만들지 않고
기존 Python 도구를 argument list로 호출한다. child stdout/stderr는 외부 summary로 전달하지 않는다. 실패,
HOLD, PARTIAL이면 즉시 종료하며 재시도하거나 다음 mode로 넘어가지 않는다.

운영 mode에서 API 사용 flag를 켜는 경우 controller 최상위 `environment_file`에 FlyHermes 로컬 환경
파일의 절대 경로를 지정한다. controller는 필요한 `OPENAI_API_KEY`, `KOSIS_API_KEY`의 존재만 확인해
자식 프로세스 환경에 전달하며 값은 summary/config/manifest에 기록하지 않는다. 오프라인 Gold mode는
이 키들을 자식 환경에서 제거한다. 운영 자식이 non-zero로 끝나도 유효한 count-only cycle JSON이 있으면
단계별 status/count/reason과 SHA-256을 보존하되, 평가 점수로 바꾸거나 자동 재시도하지 않는다.

controller 자체 기록은 새 `data/mlops_automation_runs/<controller_run_id>/` 아래에만 생성된다.

```text
controller_summary.json
external_summary.json
sha256_manifest.json
```

외부용 `external_summary.json`에는 mode, status, stage count, metric value, reason code, artifact hash와
scope만 남긴다. 기사 원문·제목·URL, feed 이름, raw provider/KOSIS 응답, API key, 로컬 절대 경로는
기록하지 않는다. 실제 scheduler 등록 기능은 controller에 없으며, 세 번의 수동 cycle 검토와 별도 승인
전에는 scheduling 후보로도 승격하지 않는다.

### 저장 산출물 기반 실시간 스냅샷

`tools/run_live_evaluation_snapshot.py`는 최신 운영 cycle과 저장된 Gold 평가를 한 번 읽어 새 스냅샷을
만든다. 네트워크나 모델 API를 호출하지 않으며, 최신 `pipeline_run_id`와 동일한 post-run Gold 평가만
운영 결과 옆에 표시한다. 연결되지 않은 과거 평가 수치는 복사하지 않고
`POST_RUN_EVALUATION_NOT_LINKED_TO_LATEST_RUN`으로 남긴다. Gold replay는 원시 기사 end-to-end 정확도가
아닌 `CONDITIONAL_REPLAY_NOT_END_TO_END` 구역에 별도로 표시한다.

```sh
/opt/data/clafact_state/venvs/clafact-auto/bin/python \
  tools/run_live_evaluation_snapshot.py \
  --state-root /opt/data/clafact_state \
  --snapshot-id <new_snapshot_id>
```

산출물은 `/opt/data/clafact_state/live_evaluation_snapshots/<snapshot_id>/`에 생성된다. 같은 ID가 있으면
중단하고 덮어쓰지 않는다. `live_evaluation_snapshot.json`, `summary.txt`, `sha256_manifest.json`만 만들며
RSS 원문·기사 URL·비밀값·로컬 경로를 복사하지 않는다. 이 명령은 on-demand 조회이므로 scheduler나
무한 반복을 등록하지 않는다.

## 4. Hermes 역할과 사람 역할

| 주체 | 할 일 | 하지 않는 일 |
|---|---|---|
| Local CLAFACT | RSS 수집, R1~R4, KOSIS 조회, Python 계산, manifest 저장 | LLM 공식값 생성 |
| Hermes | quality gate, bounded Mission 실행, count-only 결과 전달 | RSS 승인, 비밀 읽기/출력, Windows 역방향 접근, Verdict 생성 |
| 사람 | 출처 승인, Shadow run 검토, 개선 가설 한 건 승인, 승격 결정 | 매 기사에 임의 KOSIS ID를 강제 입력 |

Hermes에는 `config/hermes/clafact_autonomous_mlops_loop_master_prompt.md`를 먼저 넣고,
`operational_cycle`, `post_run_gold_evaluation`, `gold_replay_evaluation`, `r2_dev_experiment` 중 하나만
고르게 한다. 처음에는 스케줄을 등록하지 않고, 수동 bounded cycle 세 번을 검토한 뒤에만 별도 승인으로
일정화를 검토한다.

## 5. 승격 기준

1. Quality Gate와 persistence preflight PASS
2. 승인된 한 RSS 출처로 최대 5건 cycle 세 번 수동 검토
3. 기사 본문/URL/비밀이 Hermes 보고에 없음을 확인
4. R2 dev 개선이 고정 계약에서 재현
5. 독립 test와 R3/R4 Gold는 마지막 확인에만 사용
6. 그 뒤에도 모든 RSS 출처 및 스케줄은 명시적으로 승인한 것만 활성화
