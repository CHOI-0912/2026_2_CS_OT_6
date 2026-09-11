# 시·군별 입지 결과 민감도 분석

`scripts/run_placement_sensitivity.py`는 시·군 하나의 `placement.json`을 읽어서, 입력 가정을 하나씩 바꾸었을 때 최적 신설 후보 조합이 그대로 유지되는지를 확인하는 도구입니다. 기존 `optimize_hospital_placement` 완전 탐색을 그대로 재사용하며, 실제 입력이 없으면 기존 로더와 동일하게 즉시 실패합니다. 합성 입력이나 대체값은 만들지 않습니다.

## 실행 방법

```powershell
python scripts/run_placement_sensitivity.py `
  --placement data/gyeonggi_real/41110_suwon/placement.json `
  --new-hospitals 1 --episodes 30 --seed 42 `
  --output-dir outputs/sensitivity/41110_suwon `
  --sweep daily_calls_scale=0.8,1.2 --sweep treatment_minutes=45,120 `
  --seed-sweep 5
```

| 인자 | 의미 |
| --- | --- |
| `--placement` | 시·군 하나의 `placement.json` 경로입니다. `docs/hospital_placement_input_ko.md` 형식을 따라야 합니다. |
| `--new-hospitals` | 활성화할 신설 후보 수 K입니다. 완전 탐색이므로 후보 조합 수가 10,000개를 넘으면 실패합니다. |
| `--episodes` | 조합 하나를 평가할 때 반복하는 에피소드 수 N입니다. |
| `--seed` | 기준 실행 시드 S입니다. 모든 요인 스윕도 같은 시드를 사용합니다. 기본값은 42입니다. |
| `--output-dir` | `sensitivity_summary.json`과 `sensitivity_table.csv`를 저장할 폴더입니다. |
| `--sweep NAME=V1,V2,...` | 바꿀 요인과 값 목록입니다. 여러 번 지정할 수 있으며, 한 번도 지정하지 않으면 아래 기본 스윕을 모두 실행합니다. |
| `--seed-sweep M` | 입력을 바꾸지 않은 기준 문제를 시드 `S+1000`, `S+2000`, ..., `S+1000·M`으로 M번 다시 풉니다. 기본값은 0입니다. |

실행 시간은 대략 `(1 + 스윕 값 개수 + M) × (조합 수 + 1) × N` 회의 시뮬레이션에 비례합니다. 실제 시·군 자료로 실행하기 전에 작은 `--episodes` 값으로 먼저 소요 시간을 확인하기를 권장합니다.

## 요인 스윕의 의미

요인 스윕은 기준 문제에서 요인 하나만 바꾸고 나머지 입력은 모두 고정한 뒤, 기준 실행과 같은 시드로 완전 탐색을 다시 수행합니다. 시드가 같기 때문에 기준 실행과 짝지어(paired) 비교할 수 있고, 값이 달라졌다면 난수가 아니라 요인 변화가 원인입니다.

| 스윕 이름 | 바꾸는 값 | 기본값 목록 |
| --- | --- | --- |
| `daily_calls_scale` | 모든 수요지의 시간당 출동 발생률(`villages`)과 시간대별 발생률(`hourly_village_rates`)에 배율을 곱합니다. 수요가 20% 적거나 많을 때를 뜻합니다. | 0.8, 1.2 |
| `treatment_minutes` | 기존 병원과 모든 신설 후보의 병상 점유 시간(분)을 같은 값으로 바꿉니다. 병상 회전이 느릴수록 수용 능력 제약이 강해집니다. | 45, 120 |
| `restock_minutes` | 모든 구급차의 재정비 시간(분)을 같은 값으로 바꿉니다. | 5, 15 |
| `success_when_unavailable` | 기존 병원과 모든 신설 후보가 병상이 없을 때의 치료 성공 확률을 같은 값으로 바꿉니다. 값은 0과 1 사이로 잘라냅니다. | 0.0, 0.5 |
| `decay_rate_scale` | 모든 환자 유형의 생존 확률 감쇠율(`decay_rate`)에 배율을 곱합니다. 값이 클수록 이송 지연이 생존에 더 큰 손실을 줍니다. | 0.5, 2.0 |
| `candidate_capacity` | 모든 신설 후보의 병상 수를 같은 정수로 바꿉니다. 기존 병원은 바꾸지 않습니다. | 기본 스윕에 포함되지 않습니다. |
| `transfer_delay_minutes` | 기존 병원과 신설 후보의 전원 준비 지연(분)을 같은 값으로 바꿉니다. | 기본 스윕에 포함되지 않습니다. |

`success_when_unavailable`은 `success_when_unavailable` 필드만 바꾸므로, 시나리오에 `unavailable_success_by_profile`이 따로 지정된 병원은 해당 환자 유형에 대해 그 값을 계속 사용합니다.

스윕으로 바뀐 문제는 실행 전에 `validate_municipal_placement_problem`을 다시 통과해야 합니다. 예를 들어 `candidate_capacity=0`이나 음수 배율은 시뮬레이션을 시작하지 않고 오류로 종료합니다.

## 출력 파일

### `sensitivity_summary.json`

| 키 | 내용 |
| --- | --- |
| `baseline` | 입력을 바꾸지 않은 기준 실행 결과입니다. `best_candidate_ids`가 이후 모든 비교의 기준 조합입니다. |
| `sweeps` | 스윕 값 하나당 행 하나입니다. `sweep`, `value`, `seed`, `best_candidate_ids`, `incremental_mean`, `ci95`, `rank_of_baseline_best`를 담습니다. |
| `seed_sweep` | `--seed-sweep`으로 추가한 시드별 결과입니다. 구조는 `sweeps`와 같고 `sweep`과 `value`는 `null`입니다. |
| `robustness` | 아래에서 설명하는 강건성 요약입니다. |

행마다 기록되는 값의 의미는 다음과 같습니다.

- `best_candidate_ids`: 그 실행에서 증분 기대 생존자 수가 가장 컸던 후보 조합입니다.
- `incremental_mean`: 그 조합을 추가했을 때 기존 병원만 있는 경우와 비교한 기대 생존자 수 증가분의 에피소드 평균입니다.
- `ci95`: 증분 평균의 95% 신뢰구간 `[하한, 상한]`입니다. `optimize_hospital_placement`가 계산한 값(평균 ± 1.96 × 표본표준편차 / √N)을 그대로 가져옵니다.
- `rank_of_baseline_best`: 기준 실행의 최적 조합이 그 실행의 순위표에서 몇 위였는지를 뜻합니다. 1이면 여전히 최적이고, 2 이상이면 다른 조합에 밀렸습니다.

### `sensitivity_table.csv`

기준 실행, 스윕, 시드 순으로 실행 하나가 행 하나입니다. `best_candidate_ids`는 후보 id를 `|`로 이어 붙였고, 신뢰구간은 `ci95_low`, `ci95_high` 두 열로 나뉩니다. 보고서 표나 그래프를 만들 때 이 파일을 사용하면 됩니다.

## 강건성 읽는 법

`robustness` 블록은 다음 값을 담습니다.

- `runs`: 기준 실행을 제외한 검증 실행 수(스윕 값 개수 + M)입니다.
- `baseline_best_stays_best`: 그중 기준 최적 조합이 그대로 1위였던 실행 수입니다.
- `fraction`: 위 두 값의 비율입니다. 1.0이면 어떤 가정을 바꾸어도 결론이 유지되었고, 값이 낮을수록 결론이 입력 가정에 민감합니다.
- `ever_best_combinations`: 기준 실행을 포함하여 한 번이라도 1위였던 조합의 목록입니다. 항목이 하나뿐이면 결론이 견고하고, 여러 개이면 어느 실행에서 어느 조합으로 바뀌었는지를 `sweeps`와 `seed_sweep`의 `rank_of_baseline_best`로 추적해야 합니다.

해석할 때는 다음 두 가지를 함께 확인합니다.

1. 1위가 바뀐 실행에서 두 조합의 `ci95`가 크게 겹친다면, 그 요인 값에서는 두 후보의 차이가 통계적으로 구분되지 않는 것이므로 `--episodes`를 늘려 다시 확인합니다.
2. 시드 스윕에서만 1위가 바뀐다면 요인 민감도가 아니라 난수 변동이 원인입니다. 이 경우에도 `--episodes`를 늘리는 것이 우선입니다.

표준 출력에는 같은 내용이 한국어 표로 요약되어 나오며, 마지막 두 줄이 강건성 비율과 1위 경험 조합 목록입니다.
