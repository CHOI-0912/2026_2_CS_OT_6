# 시·군별 실제 병원 입지 입력 형식

## 절대 원칙

- 폴더 하나가 시·군 하나입니다.
- 시·군 사이 도로 연결, 수요, 병원, 구급차 공유는 금지합니다.
- 실제 기존 병원은 항상 활성 상태이고, `candidate_hospitals` 중 선택된 신설 후보만 추가됩니다. 공식 목록상 기존 응급의료기관이 0곳인 시·군은 빈 기준안을 그대로 사용합니다.
- 모든 위치는 실제 좌표와 라우팅 서비스에서 계산한 분 단위 도로시간을 가져야 합니다.
- 중심점·Haversine·임의 이동시간 fallback은 허용하지 않습니다.

## `placement.json`

```json
{
  "municipality_code": "41110",
  "municipality_name": "수원시",
  "scenario": "scenario.json",
  "provenance": {
    "hospital_source": "원본 파일명·URL·기준일",
    "demand_source": "119 원시자료 또는 검증된 수요모형의 버전",
    "candidate_source": "도시계획 후보지 원본·선정 기준·기준일",
    "road_time_source": {
      "provider": "라우팅 제공자",
      "extracted_at": "2026-09-08T00:00:00+09:00",
      "routing_profile": "emergency_vehicle_or_car",
      "unit": "minutes"
    }
  },
  "resource_municipality_codes": {
    "demand": {"41110::demand_001": "41110"},
    "hospitals": {"H_actual_001": "41110"},
    "ambulances": {"A_actual_001": "41110"}
  },
  "candidate_hospitals": [
    {
      "id": "C_001",
      "source_id": "planning_site_001",
      "candidate_type": "new_build",
      "name": "후보지 001",
      "municipality_code": "41110",
      "location": "41110::candidate_001",
      "capabilities": ["cardiac", "stroke", "trauma"],
      "capacity": 20,
      "success_when_available": 0.9,
      "success_when_unavailable": 0.0,
      "treatment_minutes": 45,
      "cost": 1
    }
  ]
}
```

위 값은 **형식 설명용**이며 분석값으로 사용하면 안 됩니다.

## `scenario.json`

기존 `docs/input_schema_ko.md` 형식을 사용하되 다음 제약이 추가됩니다.

- 모든 도로 노드와 시설 위치명은 `시군코드::`로 시작합니다.
- `network.positions`에 모든 수요지·기존 병원·후보지·구급차 위치의 실제 경위도를 기록합니다.
- `network.edges`의 가중치는 라우팅 결과인 이동시간(분)입니다.
- 모든 구급차·대기지→모든 수요지, 모든 수요지→모든 기존 병원·후보지, 병원 간 전원 및 병원→대기지의 방향별 경로가 존재해야 합니다.
- 기존 병원은 공식 시설 식별자를 보존하고 실제 진료역량·병상 가정의 출처를 기록합니다.

## 아직 필요한 실자료

저장소에는 현재 완전한 31개 시·군 실자료 묶음이 없습니다. 공식 응급의료기관 전체 원본, 세부 119 수요/차량 자료, 도시계획상 가능한 신설 후보지, 차량용 도로시간 행렬이 모두 준비되어야 실제 결과를 낼 수 있습니다. 누락 상태에서 프로그램은 가짜 결과를 만들지 않고 실패합니다.

## 생성기

`placement.json`과 `scenario.json`은 손으로 작성하지 않고, 정제된 시·군 폴더와 모형 계수 파일에서 생성합니다.

```bash
python scripts/build_municipal_placement_inputs.py \
  --processed-dir data/processed/gyeonggi_20260909 \
  --parameters data/processed/calibration/model_parameters_provisional.json \
  --output-root data/gyeonggi_real \
  --municipality-code 41110
```

- `--municipality-code`는 여러 번 지정할 수 있고, 생략하면 `road_times_manifest.json`의 `simulation_ready`가 `true`인 폴더를 모두 처리합니다.
- 결과물은 `<output-root>/<시군코드>_<ASCII 약칭>/scenario.json`과 같은 폴더의 `placement.json`으로 저장되며, 기존 파일을 덮어쓰려면 `--overwrite`를 붙여야 합니다.
- 생성기는 파일을 쓴 직후에 `load_municipal_placement_problem()`으로 다시 읽어서 계약을 검증하므로, 통과하지 못한 조합은 종료 코드 1과 함께 실패합니다.
- 수요는 `daily_calls × 행정동 인구 비율 ÷ 24`로 계산한 시간당 발생률입니다. `daily_calls`는 계수 파일의 `demand.daily_calls_by_municipality`에 관측값이 있으면 그 값을 쓰고, 없으면 `연간 도 전체 출동건수 ÷ 365.25 × 시·군 인구 ÷ 도 전체 인구`로 추정합니다.
- `network.positions`에는 WGS84 경위도를 `[경도, 위도]` 순서로 기록하며, 이 순서는 `placement.json`의 `provenance.coordinate_order`에도 함께 남습니다.

### 실패 조건

다음 상황에서 생성기는 합성값으로 대체하지 않고 즉시 실패합니다.

- `road_times_manifest.json`이 없거나 `simulation_ready`가 `true`가 아닙니다.
- `road_times.csv`에 필요한 방향별 경로가 하나라도 빠져 있습니다. 필요한 경로 집합은 `scripts/collect_kakao_routes.py`의 `required_pairs()`와 동일합니다.
- 수요지·구급대·기존 병원·후보지 가운데 검증된 경위도가 없는 행이 있습니다.
- 기존 병원의 `emergency_category`가 계수 파일의 `hospital.categories`에 없습니다.
- 기존 병원 행의 `capacity_match_review_required`가 `true`이거나, `emergency_room_beds`가 1보다 작습니다.
- 수요·병원·후보지 행의 `municipality_code`가 폴더 이름과 다릅니다.
- 계수 파일이 스키마를 위반합니다. 예를 들어 환자 유형별 `share`의 합이 1.0에서 1e-6 이상 벗어나거나, `hourly_demand_multipliers`의 평균이 1.0이 아니거나, `candidate.category_assumption`이 `hospital.categories`에 없는 경우입니다.
- 후보지에 적용할 유형별 성공확률이 서로 달라서, 후보지 하나에 단일 `success_when_available` 값을 부여할 수 없습니다.
