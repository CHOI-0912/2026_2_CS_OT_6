# 시·군 독립 구급차 상시대기 재배치 SMDP

**2026-09-11 전환**: 연구 문제가 신설 병원 입지에서 **기존 구급차의 상시대기 위치 재배치**로, 대상 도시가 수원시에서 **전주시(52110)**로 바뀌었습니다. 병원 신설 파이프라인(아래 절들)은 보관용으로 유지됩니다. 전환 배경과 규칙은 [docs/decisions_and_tradeoffs_ko.md](docs/decisions_and_tradeoffs_ko.md) 13~18절을 보십시오.

## 구급차 상시대기 재배치 (현재 주 파이프라인, 전주시)

규칙: 07~24시(00~01시 포함) 자유 배치, 01~06시 원소속 복귀 목표(최하위 우선순위). 대기지 후보 = 시 내부 1km 격자 칸(인구 > 0, 도로접근 가능) 중 119안전센터 1km 이내 제외 + 기존 안전센터. 목적함수는 기대 생존자 최대화, 탐색은 짝seed 탐욕 순차 배정입니다(`docs/model_spec_ko.md` 재배치 절).

```powershell
# 0. 전주 원자료 → 처리 폴더 (전국 파일에서 추출, Kakao Local 지오코딩)
python scripts/prepare_municipal_inputs.py --province 전북특별자치도 --municipality 전주시 --municipality-code 52110 --output-root data/processed/jeonju_20260911

# 1. 격자 대기지 후보 + 도로접근 점검 (Kakao Local 20/s, Directions 10/s)
python scripts/build_grid_candidates.py --mode standby --processed-dir data/processed/jeonju_20260911 --municipality-code 52110 --requests-per-second 20
python scripts/probe_candidate_routability.py --processed-dir data/processed/jeonju_20260911 --municipality-code 52110 --candidates-file standby_candidates.csv --requests-per-second 10

# 2. 도로시간 수집 — 반드시 순차 실행 (같은 CSV에 동시 쓰기 금지)
python scripts/collect_kakao_routes.py --processed-dir data/processed/jeonju_20260911 --municipality-code 52110 --pair-set operations --requests-per-second 10
python scripts/collect_kakao_routes.py --processed-dir data/processed/jeonju_20260911 --municipality-code 52110 --pair-set standby --requests-per-second 10

# 3. 최적화 입력 생성 (보정 계수 파일, 24h 발생 + 6h 냉각)
python scripts/build_municipal_standby_inputs.py --processed-dir data/processed/jeonju_20260911 --parameters data/processed/calibration/model_parameters_calibrated.json --municipality-code 52110 --slug jeonju --output-root data/jeonju_real --horizon-days 1 --cooldown-minutes 360 --overwrite

# 4. 탐욕 재배치 최적화 (개발 확인: 2 episode, 이동 3대, 후보 사전선별 15)
python -m ambulance_sim --standby-placement data/jeonju_real/52110_jeonju/standby.json --episodes 2 --seed 42 --movable 3 --shortlist 15 --json --output-dir outputs/jeonju_standby_rerun

# 5. 시뮬레이터 (기준안 vs 최적 배정)
python scripts/build_folium_municipal_simulator.py --standby data/jeonju_real/52110_jeonju/standby.json --processed-dir data/processed/jeonju_20260911 --results outputs/jeonju_standby_rerun/standby_placement_result.json --scenarios baseline,best --seed 42 --output outputs/jeonju_standby_simulator.html
```

전주 자료 출처와 수량은 [docs/jeonju_data_ko.md](docs/jeonju_data_ko.md), 대기지 후보 정의는 [docs/candidate_sites_ko.md](docs/candidate_sites_ko.md)의 "구급차 대기지 후보" 절, 시뮬레이터 사용법은 [docs/simulator_ko.md](docs/simulator_ko.md)의 "구급차 재배치 모드" 절을 보십시오.

이 프로젝트의 실사용 분석 단위는 **개별 시·군**입니다. 각 시·군은 자기 행정구역 안의 실제 수요지, 119 자원, 기존 병원, 신설 후보지와 도로 이동시간만 사용해 별도로 최적화합니다. 서울이나 인접 시·군의 수요·병원·구급차는 경기도 시·군의 후보 평가에 들어갈 수 없습니다.

문서 안내:

- 연구 규칙·데이터·전처리·시뮬레이션 제작법 정리: [REPORT_GUIDE_KO.md](REPORT_GUIDE_KO.md)
- 결정 사항과 trade-off 기록: [docs/decisions_and_tradeoffs_ko.md](docs/decisions_and_tradeoffs_ko.md)
- 인수인계(진행 상태): [HANDOFF_REAL_DATA_KO.md](HANDOFF_REAL_DATA_KO.md)
- 모델 명세: [docs/model_spec_ko.md](docs/model_spec_ko.md), 입력 형식: [docs/input_schema_ko.md](docs/input_schema_ko.md), [docs/hospital_placement_input_ko.md](docs/hospital_placement_input_ko.md)
- 후보지 정의: [docs/candidate_sites_ko.md](docs/candidate_sites_ko.md), 계수 근거: [docs/calibration_sources_ko.md](docs/calibration_sources_ko.md), 민감도 분석: [docs/sensitivity_analysis_ko.md](docs/sensitivity_analysis_ko.md), 시뮬레이터: [docs/simulator_ko.md](docs/simulator_ko.md)

## 계산 구조

각 시·군 `m`에 대해 다음 문제를 독립적으로 풉니다.

```text
기존 병원 E_m: 실제 존재하는 기관은 항상 운영하며, 0곳인 지역은 빈 기준안 유지
신설 후보 C_m: 1km 격자 칸 중 조건을 만족하는 칸(아래 "후보지 정의"). 선택된 후보만 운영
수요·구급차·도로: 모두 m 내부 자료만 허용
목적: 기존 병원만 있을 때보다 기대 생존자 수가 가장 많이 증가하는 후보 조합 선택
```

31개 시·군 결과의 합계는 모든 지역 계산이 끝난 뒤 보고용으로만 만듭니다. 경기도 전체를 하나의 네트워크로 합쳐 최적화하지 않습니다.

## 후보지 정의

신설 후보는 시·군 위에 깐 1km 격자 칸 중심이며 다음 조건을 모두 만족해야 합니다(`scripts/build_grid_candidates.py`).

1. Kakao 좌표→행정구역 조회로 해당 시·군 안에 있음
2. 칸 추정 인구가 0보다 큼(행정동 인구를 칸 수로 균등 배분한 추정치)
3. 칸 인구가 시·군 내 상위 30%에 들지 않음
4. 기존 응급의료기관·보건시설에서 2km 이상 떨어짐(대원거리, 부지 적격성 판정에만 사용)
5. Kakao 길찾기가 경로를 찾을 수 있음(`scripts/probe_candidate_routability.py`)

모든 후보의 `land_feasibility_status`는 "계획검토 필요"입니다. 토지 소유, 용도지역, 면적은 검증되지 않았습니다.

## 실제 데이터 입력

시·군 하나는 다음처럼 구성되며 생성기가 만듭니다.

```text
data/gyeonggi_real/
  41110_suwon/
    placement.json
    scenario.json
```

```powershell
python scripts/build_municipal_placement_inputs.py `
  --processed-dir data/processed/gyeonggi_20260909 `
  --parameters data/processed/calibration/model_parameters_provisional.json `
  --municipality-code 41110 --horizon-days 1 --cooldown-minutes 360 --overwrite
```

`scenario.json`에는 실제 좌표와 분 단위 도로시간 그래프, 행정동 수요율, 기존 병원, 지역 구급차가, `placement.json`에는 시·군 코드, 신설 후보지, 각 자료의 출처가 들어갑니다. 모든 계수는 `--parameters` 파일 한 곳에서 읽습니다. 자료가 하나라도 없으면 생성과 계산은 실패합니다. 중심점 거리, 직선거리, 임의 수치로 대체하지 않습니다.

전처리 결과는 `data/processed/gyeonggi_20260909`에 있으며(31개 시·군, 병원 73, 수요지 604, 구급차 290대), 도로시간 행렬은 수원(41110)만 갖추어져 있습니다. 시·군별 준비도는 각 폴더의 `readiness.json`(`blocking_inputs`는 차단, `advisory_inputs`는 권고)에서 확인합니다.

## 필요한 공식 인증키

검색하거나 발급 화면에서 확인해야 할 **공식 명칭**은 아래 두 가지입니다. 괄호 안 영문은 이 프로젝트에서만 쓰는 로컬 환경변수명이며, 키 발급 사이트의 상품명이나 키 이름이 아닙니다.

1. **Kakao Developers `REST API 키`** (`KAKAO_REST_API_KEY`)
   - 확인 경로: Kakao Developers → 내 애플리케이션 → 해당 앱 → 앱 → 플랫폼 키 → REST API 키
   - JavaScript 키, 네이티브 앱 키, Admin 키가 아니라 **REST API 키**를 사용합니다.
   - 공식 문서: [카카오모빌리티 길찾기 API 시작하기](https://developers.kakaomobility.com/guide/navi-api/start), [Kakao Developers 앱 키](https://developers.kakao.com/docs/ko/app-setting/app)
2. **경기데이터드림 `Open API 인증키`** (`GYEONGGI_DATA_API_KEY`)
   - 발급 경로: 경기데이터드림 로그인 → 마이페이지 → 인증키 발급. API 요청의 공식 파라미터 이름은 `KEY`입니다.

프로젝트 루트의 `.env`에 두 줄로 넣습니다. `.env`는 공유 제외 대상이며 `.env.example`에 빈 형식만 보존합니다. 스크립트는 키 값을 출력하지 않습니다.

```dotenv
KAKAO_REST_API_KEY=발급받은_REST_API_키
GYEONGGI_DATA_API_KEY=발급받은_Open_API_인증키
```

## 데이터 준비 순서

```powershell
# 1. 격자 후보 생성 (Kakao 좌표→행정구역 조회 사용) 및 도로접근 점검
python scripts/build_grid_candidates.py --processed-dir data/processed/gyeonggi_20260909 --municipality-code 41110
python scripts/probe_candidate_routability.py --processed-dir data/processed/gyeonggi_20260909 --municipality-code 41110

# 2. 시·군 내부 방향별 도로시간 수집 (재개 가능, 쿼터 가드)
python scripts/collect_kakao_routes.py --processed-dir data/processed/gyeonggi_20260909 `
  --municipality-code 41110 --pair-set simulation --requests-per-second 5 --max-calls 6000

# 3. 준비도 갱신
python scripts/refresh_gyeonggi_readiness.py --processed-dir data/processed/gyeonggi_20260909 `
  --parameters data/processed/calibration/model_parameters_provisional.json
```

`simulation` 모드는 수요지·후보지까지 포함한 모든 방향별 경로가 실제 API로 수집됐을 때만 `road_times_manifest.json`에 `simulation_ready: true`를 기록합니다. 경로를 찾을 수 없는 쌍은 `road_times_unroutable.csv`에 기록됩니다.

## 실행

한 시·군에서 신설 병원 1곳을 전수조사합니다(개발 확인용 2 episode).

```powershell
python -m ambulance_sim `
  --hospital-placement data/gyeonggi_real/41110_suwon/placement.json `
  --new-hospitals 1 --episodes 2 --seed 42 --json `
  --output-dir outputs/suwon_placement_test
```

준비된 모든 시·군을 각각 독립적으로 계산합니다.

```powershell
python -m ambulance_sim --municipality-root data/gyeonggi_real `
  --new-hospitals 1 --episodes 100 --seed 42 --json --output-dir outputs/gyeonggi_local
```

출력에는 기존 병원만 운영한 기준안, 후보 조합별 결과, 동일 seed를 사용한 기대 생존자 증가량, 평균·표준편차·95% 신뢰구간이 포함됩니다. episode가 2개 미만이면 신뢰구간은 `null`이고 경고가 출력됩니다.

## 민감도 분석

```powershell
python scripts/run_placement_sensitivity.py --placement data/gyeonggi_real/41110_suwon/placement.json `
  --new-hospitals 1 --episodes 2 --seed 42 --output-dir outputs/sensitivity/41110_suwon --seed-sweep 3
```

## 시·군 시뮬레이터

최적화와 같은 입력 파일을 읽어 기준안과 후보 추가 시나리오를 브라우저에서 재생합니다.

```powershell
python scripts/build_folium_municipal_simulator.py `
  --placement data/gyeonggi_real/41110_suwon/placement.json `
  --processed-dir data/processed/gyeonggi_20260909 `
  --results outputs/suwon_placement_test/hospital_placement_result.json `
  --scenarios baseline,best --seed 42 --grid-km 1 `
  --output outputs/suwon_folium_simulator.html
```

## 결과 폴더

- `outputs/suwon_*`, `outputs/gyeonggi_real_*`: 실제 데이터 기반 산출물
- `outputs/synthetic_examples/`: 합성 시나리오 검증용 결과. 실제 입지 결론에 사용 금지
- `legacy/`: 과거 전국 합산·중심점 모형. CLI에서 `--legacy-overview`를 명시해야만 실행

## 개발 확인

```powershell
python -m pytest -q
```
