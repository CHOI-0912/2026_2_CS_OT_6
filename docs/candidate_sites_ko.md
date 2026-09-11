# 신설 응급병원 후보지 정의

## 후보지 개념

신설 후보지는 지금 아무 의료·보건 시설도 존재하지 않는 지점이어야 합니다. 이전 단계에서는 보건복지부 공공보건기관 자료에 등재된 보건소 4곳을 후보지로 사용했지만, 이미 시설이 운영되고 있는 자리는 "신설"이라는 개념에 부합하지 않으므로 후보지 목록에서 제외했습니다. 보건소 4곳의 원본 행은 삭제하지 않고 `candidate_sites_public_health_reference.csv`로 옮겨서 기존 보건시설 참조 자료로 보존하며, 2 km 이격 판정의 기준 시설로 계속 사용합니다.

새 후보지는 `scripts/build_grid_candidates.py`가 생성하는 1 km 격자의 중심점 중에서 다음 세 가지 조건을 모두 충족한 격자입니다.

1. 격자 중심점이 해당 시·군의 행정동 안에 있습니다.
2. 격자의 추정 인구가 시·군 내부 격자 중에서 상위 30%에 들지 않습니다.
3. 격자 중심점이 기존 응급의료기관과 기존 보건시설 전체로부터 2 km 이상 떨어져 있습니다.

## 격자 구성

격자는 `scripts/build_folium_municipal_simulator.py`의 `add_grid` 함수와 동일한 방식으로 배치합니다. 즉 `demand_population.csv`에 기록된 수요 대표점의 경위도 범위를 구하고, 사방으로 격자 한 칸만큼 여유를 더한 사각형을 1 km 간격으로 분할합니다. 위도 간격은 `1 km / 111.32`이고, 경도 간격은 `1 km / (111.32 × cos(중심위도))`입니다. 각 격자에는 `GRID-1KM-{시군코드}-{행:03d}-{열:03d}` 형식의 식별자를 부여하고, 후보지 좌표로는 격자의 중심점을 사용합니다.

## 필터 1: 행정 구역 소속 판정

격자 중심점의 소속 행정동은 Kakao Local API의 좌표 대 행정구역 변환 기능(`coord2regioncode`)으로 확인합니다. 응답 문서 중 `region_type`이 `H`인 문서에서 10자리 행정동 코드를 읽고, `region_type`이 `B`인 문서에서 법정동 주소를 읽습니다.

행정동 코드의 앞 5자리는 시·군 코드와 항상 일치하지는 않습니다. 수원시처럼 자치구를 두고 있는 특례시는 행정동 코드가 자치구 코드로 시작하기 때문에, 수원시(41110)의 행정동 코드는 41111(장안구), 41113(권선구), 41115(팔달구), 41117(영통구)로 시작합니다. 그래서 소속 판정은 시·군 코드를 직접 비교하는 방식이 아니라, `demand_population.csv`에 실제로 적재된 행정동 코드의 앞 5자리 집합과 비교하는 방식으로 수행합니다. 이 집합에 속하지만 10자리 코드가 `demand_population.csv`에 없는 경우에는 인구를 추정하지 않고 해당 코드를 출력하면서 실행을 중단합니다.

API 응답은 `grid_candidates_manifest.json`의 `region_code_cache` 항목에 좌표별로 저장하며, 호출 한 건마다 파일에 기록합니다. 따라서 실행이 중단되어도 같은 명령을 다시 실행하면 이미 조회한 좌표는 다시 호출하지 않습니다. 호출 속도는 `--requests-per-second` 옵션으로 제어하며 기본값은 초당 5회입니다.

## 필터 2: 인구 상위 30% 격자 제외 (NIMBY 고려)

인구가 가장 밀집한 지역에 응급병원을 신설하면 이송 시간 측면에서는 유리하지만, 주민의 반대(NIMBY 현상) 때문에 실제로 부지를 확보하기 어렵습니다. 그래서 추정 인구를 내림차순으로 정렬한 다음 상위 `ceil(0.30 × 격자 수)` 순위에 해당하는 격자를 제외합니다. 절단 순위에 놓인 인구값과 동일한 값을 가진 격자가 여러 개라면 그 격자를 모두 제외하고, 제외된 격자 수가 순위 기준보다 늘어났다는 사실을 표준 출력과 명세 파일에 함께 기록합니다.

격자 인구는 다음과 같이 추정합니다.

```
격자 인구 = (행정동의 주민등록인구) / (그 행정동에 속한 시·군 내부 격자 수)
```

이 값은 행정동 내부에서 인구가 균등하게 분포한다고 가정한 **추정치**이며, 실제 인구 분포는 아닙니다. 격자 단위 인구 통계(예: 통계청 100 m 격자 인구)를 확보하면 이 가정을 대체해야 합니다. 같은 행정동에 속한 격자는 추정 인구가 서로 같기 때문에 절단 순위에서 동일값이 자주 발생하며, 위에서 설명한 동일값 처리 규칙이 이런 상황에 적용됩니다. 추정치라는 사실은 CSV의 `cell_population_estimate` 열 이름과 명세 파일의 `population_allocation_note` 항목에도 명시했습니다.

## 필터 3: 기존 시설로부터 2 km 이격

이미 응급의료기관이나 보건시설이 있는 곳 옆에 병원을 신설하면 진료 권역이 중복되어 접근성 개선 효과가 작아집니다. 그래서 격자 중심점에서 기존 응급의료기관 7곳(`existing_hospitals.csv`)과 기존 보건시설 4곳(`candidate_sites_public_health_reference.csv`)까지의 거리를 모두 계산하고, 가장 가까운 시설까지의 거리가 2 km 미만인 격자는 제외합니다. 가장 가까운 시설까지의 거리는 `nearest_facility_km` 열에 기록합니다.

이 거리 계산에는 대권거리(Haversine 공식)를 사용합니다. **대권거리는 부지 적격성을 판정하는 규칙으로만 사용하며, 이동시간이나 도로거리로는 절대 사용하지 않습니다.** 시뮬레이션의 모든 이동시간은 Kakao Mobility Directions API가 산출한 실제 도로시간(`road_times.csv`)만 사용하며, 이 원칙은 `README.md`와 `docs/hospital_placement_input_ko.md`에 기재된 실데이터 정책과 동일합니다. 같은 문장을 `grid_candidates_manifest.json`의 `haversine_note` 항목에도 기록해 두었습니다.

## 출력 파일

- `candidate_sites.csv`: 격자 후보지 목록입니다. 기존 13개 열(`candidate_id`부터 `routing_status`까지)의 순서와 이름을 그대로 유지하고, 그 뒤에 `cell_population_estimate`, `administrative_code`, `nearest_facility_km`, `grid_row`, `grid_col` 열을 추가했습니다. 이 파일을 읽는 `scripts/build_municipal_placement_inputs.py`와 `scripts/collect_kakao_routes.py`는 `csv.DictReader`로 열 이름을 지정해서 읽기 때문에, 뒤에 추가된 열은 무시하고 기존과 동일하게 동작합니다.
- `candidate_sites_public_health_reference.csv`: 기존 보건소 4곳의 행을 원래 13개 열 형식으로 보존한 참조 파일입니다.
- `grid_candidates_manifest.json`: 실행 매개변수, 격자 형상, 단계별 잔존 격자 수, 인구 상위 제외 절단값, 행정동 조회 캐시, 생성 시각, 대권거리 사용 범위에 관한 문구를 기록합니다.

`land_feasibility_status` 값은 `requires_planning_review_vacant_site_unverified`입니다. 격자 중심점은 도시계획상 건축 가능 여부를 확인하지 않은 지점이므로, 후보지가 실제로 확보 가능한 부지라고 주장하려면 별도의 계획 검토가 필요합니다.

## 실행 방법

```bash
python scripts/build_grid_candidates.py \
  --processed-dir data/processed/gyeonggi_20260909 \
  --municipality-code 41110
```

`--dry-run` 옵션을 붙이면 API를 호출하지 않고 격자의 행과 열 개수만 출력하므로, 호출량을 미리 확인할 수 있습니다. 이미 한 번 생성한 시·군을 다시 생성하려면 `--overwrite` 옵션이 필요하며, 이 옵션이 없으면 참조 파일을 덮어쓰지 않고 실행을 중단합니다.

2026년 9월 10일에 수원시(41110)를 대상으로 실행한 결과는 다음과 같습니다. 전체 격자 154개 중에서 시·군 내부 격자가 108개였고, 인구가 0명인 격자는 없었으며, 인구 상위 30% 제외 후 74개가 남았고, 2 km 이격 조건까지 적용해서 최종 후보지 33곳을 선정했습니다. 절단 순위 33번째의 추정 인구가 11,224.7명이었고 같은 값을 가진 격자가 있어서 실제로는 34개 격자를 제외했습니다.

## 임계값을 변경하는 방법

- 격자 크기는 `--grid-km`으로 변경합니다. 기본값은 1.0이고, 값을 바꾸면 격자 식별자와 `candidate_source` 문구에 기록되는 크기도 함께 바뀝니다.
- 인구 상위 제외 비율은 `--exclude-top-population-share`로 변경합니다. 기본값은 0.30이며 0 이상 1 미만의 값을 받습니다. 0을 지정하면 인구 조건을 적용하지 않습니다.
- 기존 시설과의 최소 이격 거리는 `--min-distance-km`로 변경합니다. 기본값은 2.0입니다.

임계값을 변경하면 후보지 집합이 바뀌므로 `grid_candidates_manifest.json`의 `parameters` 항목에 새 값이 기록되며, 논문이나 보고서에 결과를 기재할 때에는 이 항목의 값을 함께 제시해야 합니다.

## 후보지 변경 이후에 필요한 작업

후보지가 바뀌면 기존 `road_times.csv`와 `road_times_manifest.json`은 더 이상 시뮬레이션 요구 조건을 충족하지 못합니다. `scripts/build_grid_candidates.py`는 이 두 파일을 수정하지 않고, 재수집이 필요하다는 사실과 필요한 방향성 경로 쌍의 개수만 출력합니다. 경로 쌍의 개수는 `scripts/collect_kakao_routes.py`의 `required_pairs` 정의와 동일한 다음 식으로 계산합니다.

```
(거점 + 시설) × 수요지 + 수요지 × 시설 + 시설 × (시설 + 거점 - 1),  시설 = 기존 병원 + 후보지
```

수원시의 경우 수요지 44곳, 거점 11곳, 기존 병원 7곳, 후보지 33곳이므로 방향성 경로 6,004개가 필요합니다. 다음 명령으로 재수집하며, 이미 수집한 쌍은 다시 호출하지 않습니다.

```bash
python scripts/collect_kakao_routes.py \
  --processed-dir data/processed/gyeonggi_20260909 \
  --municipality-code 41110 --pair-set simulation
```

# 구급차 대기지 후보

## 대기지 후보의 개념

2026년 9월 11일에 연구 문제가 "응급병원 신설 입지 선정"에서 "기존 구급차의 상시대기 위치 재배치"로 바뀌었습니다(`docs/decisions_and_tradeoffs_ko.md` 13절). 이 문제에서는 구급차를 새로 늘리지 않고, 이미 배치되어 있는 구급차가 낮 시간대에 어느 지점에서 대기할 것인지만 결정합니다. 따라서 대기지 후보는 건물을 지을 수 있는 부지가 아니라, 구급차 한 대가 대기할 수 있는 지점입니다.

대기지 후보는 `scripts/build_grid_candidates.py --mode standby`가 생성하며, 다음 세 가지 조건을 모두 충족한 1 km 격자 칸입니다.

1. 격자 중심점이 해당 시·군의 행정동 안에 있습니다.
2. 격자의 추정 인구가 0명보다 많습니다.
3. 격자 중심점이 기존 119안전센터 전체로부터 `--min-distance-km` 이상(기본값 1.0 km) 떨어져 있습니다.

격자 구성 방식, 행정동 소속 판정 방식, 인구 추정 방식은 앞에서 설명한 신설 병원 후보지와 완전히 동일합니다. Kakao Local API의 좌표 대 행정구역 변환 결과는 `grid_candidates_manifest.json`의 `region_code_cache` 항목을 그대로 공유하기 때문에, 같은 시·군에서 병원 후보지를 이미 생성했다면 행정동 조회를 다시 호출하지 않습니다.

## 신설 병원 후보지와 다른 점

- **인구 상위 30% 제외 규칙을 적용하지 않습니다.** 이 규칙은 병원 신설에 따르는 주민 반대를 고려한 조치였습니다. 구급차 대기지는 건축물을 세우는 사업이 아니고, 오히려 수요가 밀집한 지역에 가까울수록 대응 시간이 짧아지기 때문에 인구가 많은 격자를 제외할 이유가 없습니다.
- **이격 거리를 측정하는 기준 시설이 기존 119안전센터뿐입니다.** 응급의료기관이나 보건소가 가까이 있는지는 대기지 적격성과 관계가 없으므로 판정에 사용하지 않습니다. 따라서 `nearest_facility_km` 열에 기록되는 값은 가장 가까운 119안전센터까지의 대권거리입니다. 이 거리도 부지 적격성 판정에만 사용하며, 이동시간으로는 절대 사용하지 않습니다.
- **기존 119안전센터도 대기지입니다.** 안전센터 자체는 `standby_candidates.csv`에 기록하지 않고, `scripts/build_municipal_standby_inputs.py`가 `standby.json`을 생성할 때 `post_type`이 `existing_base`인 대기지로 추가합니다. 그래야 "구급차를 지금 위치에 그대로 둔다"라는 선택지가 탐색 범위 안에 항상 포함됩니다.

## 출력 파일

- `standby_candidates.csv`: 대기지 후보 목록입니다. 열 구성은 `candidate_sites.csv`와 동일합니다. `land_feasibility_status` 값은 `requires_review_standby_post_unverified`이며, 격자 중심점에 구급차가 실제로 주차하고 대기할 수 있는지는 검증되지 않았다는 사실을 뜻합니다. `candidate_source` 값은 `population-weighted 1 km grid standby post; Kakao coord2regioncode membership`입니다.
- `standby_candidates_manifest.json`: 실행 매개변수, 격자 형상, 단계별 잔존 격자 수, 이격 판정의 기준 시설 수, 대권거리 사용 범위에 관한 문구를 기록합니다. `--mode standby`는 `candidate_sites.csv`와 `candidate_sites_public_health_reference.csv`를 읽지도 않고 수정하지도 않으므로, 병원 신설 분석의 결과물은 그대로 보존됩니다.

## 도로 접근성 점검

격자 중심점 근처에 도로가 없으면 Kakao Mobility가 경로를 산출하지 못합니다. `scripts/probe_candidate_routability.py`에 `--candidates-file standby_candidates.csv`를 지정하면 대기지 후보를 점검하고, 경로 탐색에 실패한 행은 `standby_candidates_unroutable.csv`로 옮깁니다. 다른 파일을 사용하려면 `--unroutable-file`로 지정합니다.

이미 `road_times.csv`에 `<시군코드>::candidate::<후보지 ID>` 노드로 수집된 도로시간이 있다면, 그 격자 칸은 도로 접근이 가능하다는 사실이 실측으로 증명된 상태입니다. 이런 후보는 API를 다시 호출하지 않고 `routing_status`를 `probe_routable`로 기록합니다.

## 수집해야 하는 도로시간

대기지 P마다 다음 네 가지 방향의 경로가 필요합니다. 기존 119안전센터도 대기지이므로 안전센터에 대해서도 같은 경로가 필요합니다.

1. P에서 모든 수요지로 가는 경로
2. 모든 병원에서 P로 오는 경로
3. P에서 모든 안전센터로 가는 경로
4. 모든 안전센터에서 P로 오는 경로

필요한 방향성 경로의 개수는 `scripts/build_grid_candidates.py`의 `standby_pair_count` 함수와 동일한 다음 식으로 계산합니다.

```
대기지 × (수요지 + 병원 + 2 × 안전센터) + 안전센터 × (수요지 + 병원) + 안전센터 × (안전센터 - 1)
```

여기에 더해서 환자를 이송하는 구간(수요지 → 병원), 병원 사이의 전원 구간, 안전센터에서 수요지로 출동하는 구간이 모든 episode에 필요합니다. 이 경로들은 `--pair-set operations`가 수집하는 집합과 같기 때문에, `scripts/build_municipal_standby_inputs.py`는 두 집합을 합친 목록을 검사하고 하나라도 빠져 있으면 실행을 중단합니다.

같은 격자 칸을 병원 후보지로 이미 수집해 두었다면, `--pair-set standby`는 그 행을 복사해서 대기지 노드 식별자로 다시 기록하고 `reused_from` 열에 원본 경로를 남깁니다. 복사한 경로는 API를 호출하지 않으므로 호출량이 줄어듭니다. `reused_from` 열이 없는 기존 `road_times.csv`는 `pair_set` 열을 추가할 때와 같은 방식으로 한 번 다시 기록하며, 기존 행의 값은 빈 문자열이 됩니다. 수집 결과는 `road_times_standby_manifest.json`에 기록하고, 필요한 경로를 모두 수집하면 `standby_ready` 값이 `true`가 됩니다. 병원 신설 분석이 사용하는 `road_times_manifest.json`은 이 과정에서 수정하지 않습니다.

## 실행 방법

아래 명령에서 `<처리 루트>`는 `data/processed/<지역>_<날짜>` 형식의 처리 루트이고, `<시군코드>`는 그 아래에 있는 시·군 폴더 이름입니다. 호출 속도는 Kakao Local API가 초당 50회 이하를 권장하고 Directions API에는 공식적인 초당 제한이 없다는 사실을 반영해서 지정합니다.

```bash
# 1. 대기지 후보 생성 (coord2regioncode 호출)
python scripts/build_grid_candidates.py --mode standby \
  --processed-dir <처리 루트> --municipality-code <시군코드> \
  --requests-per-second 20

# 2. 도로 접근성 점검
python scripts/probe_candidate_routability.py \
  --processed-dir <처리 루트> --municipality-code <시군코드> \
  --candidates-file standby_candidates.csv

# 3. 운영 경로 수집 (수요지 → 병원, 병원 → 수요지, 병원 사이 전원, 안전센터 관련 경로)
python scripts/collect_kakao_routes.py \
  --processed-dir <처리 루트> --municipality-code <시군코드> \
  --pair-set operations --requests-per-second 10 --max-calls <일일 한도>

# 4. 대기지 경로 수집
python scripts/collect_kakao_routes.py \
  --processed-dir <처리 루트> --municipality-code <시군코드> \
  --pair-set standby --requests-per-second 10 --max-calls <일일 한도>

# 5. 입력 파일 생성
python scripts/build_municipal_standby_inputs.py \
  --processed-dir <처리 루트> --parameters <계수 파일> \
  --municipality-code <시군코드> --slug <ascii 폴더 이름> \
  --horizon-days 1 --cooldown-minutes 360
```

3번과 4번 명령은 `--max-calls`로 지정한 호출 수에 도달하면 중단되고, 같은 명령을 다시 실행하면 이미 수집한 경로를 건너뛰고 이어서 수집합니다. 5번 명령은 `<output-root>/<시군코드>_<slug>/` 폴더에 `standby.json`과 `scenario_standby.json`을 기록합니다. `--slug`는 경기도 31개 시·군처럼 내장 표에 이름이 있는 경우에는 생략할 수 있고, 그 밖의 시·군에서는 반드시 지정해야 합니다.

`standby.json`에는 대기지 목록(`standby_posts`), 구급차마다 소속 안전센터를 지정하는 `ambulance_home_bases`, 자유 배치 시간대와 원소속 복귀 시간대를 지정하는 `schedule`, 그리고 자료 출처(`provenance`)가 기록됩니다. `scenario_standby.json`의 구급차는 `home_base`에 소속 안전센터를 기록하고 `assigned_post`는 `null`로 시작하며, 대기지를 결정하는 작업은 최적화 단계에 맡깁니다. 생성기는 두 파일을 기록한 다음 `ambulance_sim.io.load_scenario`로 다시 읽어서 계약을 확인하고, 필요한 경로가 빠져 있거나 `road_times_standby_manifest.json`이 준비되지 않았다면 실행을 중단합니다.
