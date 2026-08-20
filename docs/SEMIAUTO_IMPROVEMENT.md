# ClaFact 반자동 개선 구조

이 구조는 실패를 모델의 정답으로 간주하지 않습니다. 운영 실패와 Gold 평가를 분리하고, 사람의 승인이 있어야 개발 실험으로 넘어갑니다.

```text
품질 게이트·운영 결과
        ↓
실패 분류 ── 설정/테스트 문제 → 복구 후 품질 게이트 재실행
        └── 품질 문제 후보 → Gold 확인
                              ↓
고정 dev Gold 오류 요약 → 개선 후보 1건 → 사람 승인 → dev 실험·채점
                                                    ↓
                                  승격 검토 필요 / 승격 불가
```

어떤 단계도 코드 수정, 모델 학습, 잠긴 test 실행, 배포를 자동 승인하지 않습니다.

## 1. 실패 분류

품질 게이트 보고서 또는 운영 사이클 요약에서 상태 코드와 건수만 읽습니다.

```bash
python tools/run_semiauto_improvement.py triage \
  --input "$CLAFACT_STATE_ROOT/operational_cycles/<cycle-id>.json" \
  --state-root "$CLAFACT_STATE_ROOT"
```

- 키·제공자·테스트·실행기 실패: `REPAIR_REQUIRED`
- 운영 품질 신호: `GOLD_REVIEW_REQUIRED`
- 배치 제한이나 수집 간격 보호: `NO_ACTION`
- 알려지지 않은 사유: `HUMAN_TRIAGE_REQUIRED`

`REPAIR_REQUIRED`는 학습 대상이 아닙니다. 원인을 복구하고 품질 게이트를 다시 실행합니다.

## 2. 개선 후보 한 건 생성

고정 R2 dev Gold에서 만든 text-free `error_summary.json`만 사용합니다. 가장 많은 불일치 슬롯 한 종류만 선택합니다.

```bash
python tools/run_semiauto_improvement.py propose \
  --error-summary data/model_benchmarks/r2_12slot_v2/experiment_tracking_v1/error_summary.json \
  --state-root "$CLAFACT_STATE_ROOT"
```

결과는 `AWAITING_HUMAN_APPROVAL`이며 코드나 프롬프트를 바꾸지 않습니다.

## 3. 사람 승인

아래 명령 실행 자체가 한 가지 변경에 대한 명시적 승인입니다. `change-reason-code`에는 기사 문장 대신 짧은 상태 코드만 사용합니다.

```bash
python tools/run_semiauto_improvement.py approve \
  --proposal "$CLAFACT_STATE_ROOT/semiauto_improvements/<proposal-id>/proposal.json" \
  --scoreboard data/model_benchmarks/r2_12slot_v2/experiment_tracking_v1/scoreboard.json \
  --experiment-id <experiment-id> \
  --provider openai \
  --model <model-id> \
  --prompt-version r2_benchmark_prompt_v2 \
  --change-scope prompt \
  --change-reason-code <SAFE_REASON_CODE> \
  --baseline-source <comparable-dev-report-name>
```

승인은 `dev` 실험만 허용합니다. 잠긴 `test`, 코드 변경, 배포 권한은 포함하지 않습니다.

## 4. dev 평가와 승격 검토

승인된 설정으로 별도 R2 dev 실험과 기존 scorer를 실행한 뒤 후보 보고서를 비교합니다.

```bash
python tools/run_semiauto_improvement.py evaluate \
  --approval "$CLAFACT_STATE_ROOT/semiauto_improvements/<proposal-id>/approval.json" \
  --candidate-report data/model_benchmarks/r2_12slot_v2/runs/<experiment-id>_report.json
```

다음 조건을 모두 만족해야 `PROMOTION_REVIEW_REQUIRED`가 됩니다.

- 응답률 95% 이상
- 전체 12슬롯 macro accuracy가 승인 시점 기준선보다 높음
- parse-status macro F1이 기준선보다 낮아지지 않음
- provider, model, prompt version이 승인 기록과 일치함
- 고정 `dev` 보고서이며 비교 가능한 단일 모델 실행임

`PROMOTION_REVIEW_REQUIRED`는 자동 승격이 아닙니다. 사람이 결과를 검토한 뒤 test 실행과 배포를 각각 별도로 승인해야 합니다.

## 저장 및 공개 안전성

- 상태 파일은 한 번 기록하면 덮어쓰지 않습니다.
- 같은 내용을 다시 실행하는 것은 허용하지만 다른 내용으로 교체할 수 없습니다.
- 출력에는 상태, 사유 코드, 슬롯명, 집계 수치만 포함합니다.
- 기사 원문·제목·URL, Gold 정답, 제공자 원문, KOSIS 원문, 비밀값은 저장하거나 출력하지 않습니다.
