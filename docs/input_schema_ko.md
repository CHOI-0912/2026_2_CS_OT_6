# 로컬 시나리오 입력 형식

시뮬레이터는 실제 공공데이터를 직접 읽지 않고, 연구자가 전처리한 **UTF-8 JSON 한 파일**을 `ambulance_sim.io.load_scenario()`로 읽습니다. 한 파일을 그대로 Git에 보관할 수 있어 실험 조건과 결과를 재현하기 쉽습니다.

```python
from ambulance_sim.engine import Simulation
from ambulance_sim.io import load_scenario

scenario = load_scenario("examples/synthetic_scenario.json")
result = Simulation(scenario, seed=42).run()
```

## 최상위 구조

| 필드 | 형식 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `schema_version` | 정수 | 없음(무시) | 문서·도구용 버전. 현재 `1` |
| `network` | 객체 | `{}` | 도로 그래프. `edges`를 감싼 형태를 권장. 선택적으로 `time_multipliers` 포함 |
| `villages` | 객체 | `{}` | `마을 노드명: 시간당 평균 환자 발생률` |
| `hospitals` | 객체/배열 | `{}` | 병원 ID별 기록 또는 `id`가 있는 배열 |
| `ambulances` | 객체/배열 | `{}` | 구급차 ID별 기록 또는 `id`가 있는 배열 |
| `profiles` | 배열/객체 | `[]` | 환자 유형. 객체 형태에서는 키가 `name`이 됨 |
| `standby_nodes` | 문자열 배열 | 병원 위치 목록 | 재배치 후보 노드 |
| `horizon_minutes` | 숫자 | `1440` | 에피소드 길이(분). 이 시각에 아직 미종결인 환자는 남은 생존량이 손실로 기록됨 |
| `arrival_cutoff_minutes` | 숫자 또는 `null` | `null`(= `horizon_minutes`) | 새 환자 발생을 멈추는 시각(분). 이후에는 발생 없이 이미 있는 환자만 종결까지 진행. 하루 24시간 수요 + 6시간 여유면 `1440` / `1800` |
| `max_transfers` | 정수 | `2` | 환자별 병원 재이송 허용 횟수 |
| `hourly_village_rates` | 객체 | `{}` | 시간대별 마을 도착률(시간당). 키는 `0`~`23` |
| `hourly_profile_probabilities` | 객체 | `{}` | 시간대별 환자 유형 가중치. 키는 `0`~`23` |

누락된 선택 필드는 현재 모델의 안전한 기본값으로 채워집니다. 앞으로 모델에 필드가 추가되어도 이 로더는 알 수 없는 필드를 무시하므로 기존 파일을 그대로 사용할 수 있습니다. 단, 오탈자도 무시될 수 있으니 실험 전에 JSON을 검토해야 합니다. 잘못된 타입, 음수 시간, 확률 범위 밖 값은 `ScenarioValidationError`와 함께 정확한 JSON 경로를 표시합니다.

## 세부 필드

### 도로

```json
"network": {
  "edges": {
    "Station": {"Village": 8},
    "Village": {"Station": 8}
  }
}
```

간선 값은 분 단위 이동시간이며 0 이상이어야 합니다. `RoadNetwork`는 방향 그래프 형식으로 받으므로 양방향 도로는 두 방향을 모두 기록합니다. 이동시간은 직선거리가 아니라 전처리한 도로망의 시간 가중치입니다.

시간대별 도로 혼잡 배율은 다음처럼 기록합니다. 생략한 시간은 `1.0`이며, `0`보다 커야 합니다. JSON 키는 문자열이어도 로드 시 정수 시간으로 변환됩니다.

```json
"time_multipliers": {"0": 1.0, "8": 1.4, "18": 1.6}
```

시각화에서 사용할 노드 위치는 실제 경위도 또는 0~100 범위의 상대좌표를 `[x, y]`로 지정합니다. 생략하면 시각화 도구가 원형 배치를 생성합니다.

```json
"positions": {"Station": [15, 30], "Village": [55, 45], "Hospital": [82, 70]}
```

### 병원

```json
"hospitals": {
  "central": {
    "location": "Central ER",
    "capabilities": ["cardiac", "stroke"],
    "success_when_available": 0.86,
    "success_when_unavailable": 0.45,
    "available": true,
    "restock_capable": true,
    "transfer_delay_minutes": 5
  }
}
```

`location`과 `capabilities`는 병원 기록에 맞춰야 합니다. 성공률은 0~1의 기대 치료 성공률이며, `available`이 false일 때 두 번째 성공률을 사용합니다. `success_when_unavailable`, `available`, `restock_capable`, `transfer_delay_minutes`의 기본값은 각각 `0`, `true`, `true`, `5`입니다. `capacity`는 가용 병상 수(생략 또는 `null`이면 무제한), `occupied`는 현재 점유 수(기본 `0`), `treatment_minutes`는 병상 점유 시간(기본 `0`)입니다. `occupied`는 `capacity`를 넘을 수 없습니다.

환자 유형별 성공률이 있으면 `success_by_profile`과 `unavailable_success_by_profile`에 `"환자유형": 0~1` 값을 기록합니다. 이 값이 공통 `success_when_available`·`success_when_unavailable`보다 우선하며, 모든 키는 `capabilities`에도 있어야 합니다.

### 구급차

```json
"ambulances": {
  "A1": {
    "location": "Station North",
    "status": "idle",
    "patient_id": null,
    "restock_minutes": 8
  }
}
```

초기 입력의 `status`는 `idle` 또는 `unavailable`을 사용합니다. 기존 차량이 다른 업무로 점유된 상태라면 `unavailable`과 `initial_available_after`(episode 시작 후 다시 가용해지는 분)를 함께 지정합니다. 생략하면 `idle`, `patient_id`는 `null`, `restock_minutes`는 `8`입니다. `to_scene`, `on_scene`, `to_hospital`, `restocking`, `repositioning`은 시뮬레이션 중 엔진이 만드는 상태이며, 활성 환자 없이 초기 JSON에 직접 지정할 수 없습니다.

### 환자 유형

```json
"profiles": [
  {
    "name": "cardiac",
    "golden_minutes": 8,
    "decay_rate": 0.045,
    "scene_minutes": 12,
    "field_success": 0.0
  }
]
```

`golden_minutes`, `decay_rate`, `scene_minutes`는 0 이상입니다. 골든타임 이후 생존량은 현재 모델의 지수 감소식으로 계산합니다. `field_success`는 현장 처치로 종결되는 기대 비율(0~1)이며 기본값은 `0`입니다. 마을 발생률이 양수이면 하나 이상의 프로파일이 필요합니다.

## 실제 데이터로 교체할 때

1. 도로망을 노드 ID 기준으로 만들고 양방향 간선을 두 줄씩 작성합니다.
2. 읍면동·마을별 119 신고량을 `villages`의 시간당 평균값으로 집계합니다. 시간대별 수요를 쓰려면 후속 입력 계층에서 episode별로 JSON을 생성하거나 엔진을 확장합니다.
3. 병원 위치, 진료 가능 유형, 병상/가용성 추정치를 `hospitals`에 매핑합니다.
4. 구급차의 실험 시작 위치를 `ambulances`에 넣습니다.
5. `load_scenario()`가 실패하면 오류 메시지의 경로를 따라 원본 전처리 결과를 수정합니다.

합성 전체 예제는 [`examples/synthetic_scenario.json`](../examples/synthetic_scenario.json)입니다. 저장할 때는 `save_scenario(scenario, path)`를 사용하면 같은 형식으로 다시 내보낼 수 있습니다.

## 시간대별 수요와 환자 유형

`hourly_village_rates`를 지정하면 해당 시간대의 `villages` 기본값을 덮어씁니다. 배열이 아니라 시간 키를 가진 객체입니다.

```json
"hourly_village_rates": {
  "7": {"Village A": 1.2},
  "18": {"Village A": 2.4, "Village B": 1.8}
},
"hourly_profile_probabilities": {
  "18": {"cardiac": 0.5, "stroke": 0.3, "trauma": 0.2}
}
```

환자 유형 값은 합이 정확히 1일 필요 없는 가중치이며, 모두 0 이상이어야 합니다. 해당 시간대의 가중치 합이 0이면 기본 프로파일 균등 추출로 돌아갑니다.
