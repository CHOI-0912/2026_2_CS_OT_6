# PROJECT_CONTEXT.md — 프로젝트 전체 맥락

작성 기준: 2026-09-11. 짧은 요약과 절대 규칙은 `CLAUDE.md`, 결정의 정본은 `docs/decisions_and_tradeoffs_ko.md`에 있다. 이 문서는 "무엇이 어디에 있고, 어떻게 연결되며, 무엇을 믿으면 되는가"를 한 번에 설명한다.

---

## 1. 한 문단 요약

경기도 수원시(41110)를 1km 격자로 나누고, 실제 공공데이터와 Kakao Mobility 도로 이동시간만으로 만든 이벤트 기반 SMDP 시뮬레이터에서 **응급병원을 1곳 신설할 때 하루 기대 생존자 수가 가장 많이 늘어나는 위치**를 찾는 고등학교 연구 프로젝트다(2026-2학기 수행평가, 2518 최지완). 중간에 "전주시 기존 구급차 상시대기 재배치"로 문제를 전환했다가(§13·§14) 다시 수원 병원 신설로 되돌아와 제출했다. 전주 트랙은 엔진·최적화·파이프라인·시뮬레이터가 모두 완성되어 있으나, **산출 수치는 §19(치료 결과 최종화) 이전 실행분이라 stale**이다.

---

## 2. 폴더 트리 (KEEP / STALE)

```text
.
├─ CLAUDE.md                     KEEP  세션 시작 시 자동 로드되는 요약·규칙
├─ PROJECT_CONTEXT.md            KEEP  이 문서
├─ 보고서.md                     KEEP  최종 제출 보고서(수원 병원 신설). 수정 금지
├─ 2518최지완_7번.hwpx           KEEP  제출용 한글 파일(보고서.md와 같은 내용)
├─ RESULTS_KO.md                 KEEP  실행 결과 요약표 3개(조건이 달라 표끼리 비교 금지)
├─ REPORT_GUIDE_KO.md            KEEP(부분 STALE)  방법론 정리. 상단 "전주 전환" 서술과 5-5절 "결과 대기"가 낡음
├─ README.md                     KEEP(부분 STALE)  상단 배너가 "현재 주 파이프라인 = 전주"라고 선언. 명령 예시·후보지 정의·인증키 안내는 유효
├─ HANDOFF_REAL_DATA_KO.md       KEEP  인수인계. 상단 "현재 상태" 블록은 갱신됨
├─ pyproject.toml                KEEP
│
├─ ambulance_sim/                KEEP  시뮬레이터 패키지 (§3 모듈 지도)
│  └─ legacy/                    KEEP  광역 합산·중심점 모형. --legacy-overview 없이는 실행 차단
├─ scripts/                      KEEP  데이터 파이프라인 스크립트 23개 (§6 CLI)
├─ tests/                        KEEP  26개 파일, 199개 테스트
├─ examples/synthetic_scenario.json  KEEP  합성 예제 입력(실제 결과 아님)
├─ legacy/README.md              KEEP  레거시 모형 보관 안내(스스로 "사용 금지" 명시)
│
├─ docs/                         KEEP  근거 문서 (§11 타임라인 참조)
│  ├─ decisions_and_tradeoffs_ko.md   KEEP  §1~§19. 결정의 정본
│  ├─ model_spec_ko.md                KEEP  SMDP 상태·행동·생존함수 수식
│  ├─ input_schema_ko.md              KEEP  scenario.json 스키마
│  ├─ hospital_placement_input_ko.md  KEEP  placement.json 절대 원칙
│  ├─ candidate_sites_ko.md           KEEP  1km 격자 후보지 정의(병원용·대기지용)
│  ├─ calibration_sources_ko.md       KEEP  보정 계수의 문헌·통계 출처(최대 문서)
│  ├─ demand_data_acquisition_ko.md   KEEP  시·군별 실측 출동건수 확보 기록
│  ├─ patient_types_in_practice_ko.md KEEP  환자 유형 실제 분류 조사(파라미터 미반영)
│  ├─ sensitivity_analysis_ko.md      KEEP  민감도·seed 스윕 사용법
│  ├─ simulator_ko.md                 KEEP  Folium 재생 시뮬레이터 사용법
│  ├─ jeonju_data_ko.md               KEEP  전주 실데이터 확보 기록(전주 트랙 전용)
│  └─ data_sources_ko.md              STALE 2026-09-07 초기 로드맵 구상. 실제 채택 출처는 REPORT_GUIDE_KO.md §1-1 표
│
├─ data/
│  ├─ raw/                       KEEP  1차 원자료 8개 폴더(재수집 고비용). §5 참조
│  ├─ processed/
│  │  ├─ gyeonggi_20260909/41110/          KEEP  수원 staging. 최종 보고서가 직접 의존
│  │  ├─ gyeonggi_20260909/<나머지 30개>/  STALE(미완성)  도로시간 미수집, 격자 후보 미생성. 후속 확장용 골격
│  │  ├─ jeonju_20260911/52110/            KEEP  전주 staging(수요 35, 거점 10/구급차 12, 병원 5, 격자 92, 도로시간 6,780쌍)
│  │  └─ calibration/                      §5 참조. calibrated=KEEP, provisional=STALE
│  ├─ gyeonggi_real/41110_suwon/  KEEP  최종 최적화 입력(placement.json + scenario.json)
│  │  └─ README.md                STALE  "후보 4곳 / 도로시간 1,683쌍 / provisional 계수" 서술. 실제는 격자 31곳 / 5,652쌍 / calibrated
│  └─ jeonju_real/52110_jeonju/   KEEP  전주 입력(standby.json, scenario_standby.json, standby_grid_only.json)
│
└─ outputs/
   ├─ suwon_grid_k1/             KEEP  ★ 최종 결과. 보고서 §4의 모든 수치 출처
   ├─ figures/*.png              KEEP  ★ 보고서 그림 5장
   ├─ suwon_folium_simulator.html(+.summary.json)            KEEP ★ GitHub Pages 링크
   ├─ jeonju_standby_simulator.html(+.summary.json)          KEEP ★ GitHub Pages 링크
   ├─ jeonju_standby_simulator_baseline.html(+.summary.json) KEEP ★ GitHub Pages 링크
   ├─ synthetic_examples/        KEEP  합성 검증 결과(실제 결과와 혼용 금지)
   ├─ logs/                      (.gitignore) 수집 로그 잔재
   └─ archive/                   STALE  아래 6개는 인용 금지
      ├─ suwon_placement_provisional/{k1,k2}/  옛 후보(보건소 4곳)·임시 계수·100 episode
      ├─ suwon_placement_smoke/                파이프라인 확인용 2~3 episode 스모크
      ├─ jeonju_standby_test/                  폐기된 규칙(안전센터 간 이동 허용) 2 episode
      ├─ jeonju_standby_prelim/                0바이트, 미완료 실행 잔재
      ├─ jeonju_standby_grid_test/             전주 예비 2 episode(RESULTS_KO.md 3절 근거)
      └─ gyeonggi_real_resource_dashboard.html 경기 전역 자원 지도(보고서 미인용)
```

---

## 3. 모듈 지도 (`ambulance_sim/`)

### 핵심 엔진
- **`model.py`** — 도메인 데이터클래스. `RoadNetwork`(다익스트라 캐시 포함 방향 그래프, 시간대별 배율 `time_multipliers`), `PatientProfile`(골든타임 이후 지수 감쇠 `survival()`), `Patient`, `Hospital`(병상 점유·`treatment_success()`), `Ambulance`, `Scenario`(마을별 수요율, 시간대별 수요/유형 확률, `arrival_cutoff_minutes`), `Event`/`EventKind`.
- **`engine.py`** — `class Simulation`. 힙 기반 이벤트 루프. 초기 가용성 / `SHIFT_CHANGE` / 포아송 도착 이벤트를 예약하고, 배차(`_dispatch_waiting`) → 현장 → 이송(`_transport`) → 병원 도착(`_arrive_hospital`) → 재정비·재배치(`_begin_restock` → `_restock_complete` → `choose_standby_location` → `_reposition_complete`)를 처리한다. `summary()`가 통계 dict를 반환. `build_default_scenario()`는 문서·테스트용 합성 예시.
- **`policy.py`** — `GreedySurvivalPolicy`(기본, 재귀적 전원가치 `_route_value` 포함), `NoRepositionPolicy`, `NearestHospitalPolicy`(투명 베이스라인), `ScheduledStandbyPolicy`(자유/귀소 시간대 스케줄 기반 대기소 정책), `FixedPlacementPolicy`(구급차별 고정 대기위치).
- **`io.py`** — JSON ↔ `Scenario`. `scenario_from_dict` / `scenario_to_dict` / `load_scenario` / `save_scenario`, 필드별 검증 `validate_scenario`(`ScenarioValidationError`가 JSON 경로를 함께 보고).
- **`__main__.py`** — CLI 진입점. `__init__.py`는 공개 API 재노출.

### 최적화
- **`hospital_placement.py`** — ★ 최종 보고서의 산출 모듈. `load_municipal_placement_problem`(모든 노드가 `{code}::` 접두사인지, provenance가 있는지, 실좌표·실도로시간이 있는지 검증), `optimize_hospital_placement`(`itertools.combinations` 완전탐색, 짝seed로 baseline 대비 증분 기대생존 계산, `math.comb > max_combinations`면 즉시 ValueError), `optimize_municipality_directory`(시·군별 독립 최적화 후 합산).
- **`standby_placement.py`** — 전주 트랙. `load_municipal_standby_problem` / `validate_municipal_standby_problem`, `_coverage_score`(수요 근접도 사전점수로 `--shortlist` 축소), `optimize_standby_placement`(**그리디 전진탐색, 전역 최적 비보장** — 결과 dict의 `search_note`에 명시), `evaluate_assignment`.

### 지원
- **`experiment.py`** — `run_experiment`(공통 seed 짝비교 정책 비교), `evaluate_standby_candidates`(구급차 1대의 영구 대기위치 후보 비교), `write_experiment_outputs`(JSON+CSV).
- **`initialization.py`** — `randomized_initial_locations`. 배경 구급차 초기위치만 가중 무작위화(대상 차량은 고정). `experiment` 한 곳에서만 사용.
- **`env_file.py`** — `load_env_file()`. `.env`의 KEY=VALUE를 환경에 주입(기존 값은 덮지 않음). 키 값은 절대 출력하지 않는다.
- **`kakao_api.py`** — `KakaoApiClient`(주소·키워드 지오코딩, 행정동 코드, 자동차 경로시간). **외부 API 호출 모듈 — 지시 없이 실행 금지.**
- **`visualize.py`** — `write_visualization()`. 자기완결형 SVG+재생 컨트롤 HTML(`--visualize`). Folium 시뮬레이터(`scripts/build_folium_municipal_simulator.py`)와는 별개다.
- **`calibration.py`** — `allocate_hourly_demand`, `normalize_profile_counts`. **고아 모듈**: 패키지·스크립트 어디에서도 import되지 않고 `tests/test_calibration.py`만 사용한다. 실제 보정은 `scripts/prepare_municipal_dispatch_rates.py` 등이 자체 로직으로 수행. 삭제·이동은 사용자 확인 후.
- **`legacy/gyeonggi_centroid.py`, `legacy/national_aggregate.py`** — 인구비례 배분 추정·중심점 기반 개요 시나리오. `--national --legacy-overview` 조합으로만 도달 가능하고 그 외에는 `parser.error`로 차단된다. 죽은 코드가 아니라 **의도적으로 격리된 참고 코드**.

---

## 4. 데이터 파이프라인 (텍스트 다이어그램)

```text
[원자료 data/raw/]
  주민등록 인구 · 응급의료기관 현황 · HIRA 병상 · E-Gen 등급 · 소방청 구급차 · 경기데이터드림 구급활동
        │
        ├─ prepare_gyeonggi_inputs.py  (전국판: prepare_municipal_inputs.py)
        │     └─> data/processed/<region>_<date>/<code>/
        │            demand_population.csv / existing_hospitals.csv / ambulance_bases.csv
        │
        ├─ geocode_gyeonggi_kakao.py  →  좌표 확보  [Kakao Local]
        │     └─ apply_demand_coordinate_overrides.py  (모호 주소 수동 교정)
        │
        ├─ build_hospital_capacity_match.py → stage_hospital_capacity.py
        │     └─> existing_hospitals.csv 에 병상 수·등급 결합
        │
        ├─ prepare_public_health_candidates.py / prepare_mohw_public_health_facilities.py
        │     └─> 보건시설 표 (격자의 "기존 시설 2km 이격" 판정 입력으로만 소비)
        │
        ├─ build_grid_candidates.py  --mode hospital | standby   [Kakao Local]
        │     └─> candidate_sites.csv (수원 격자 31곳) / standby_candidates.csv (전주 92곳)
        │           조건: 시 내부 · 인구>0 · 인구 상위 30% 제외 · 기존 시설 2km 이상
        │
        ├─ probe_candidate_routability.py  [Kakao Directions]
        │     └─> candidate_sites_unroutable.csv (수원 2곳 제외 → 최종 31곳)
        │
        ├─ collect_kakao_routes.py --pair-set simulation|operations|standby   [Kakao Directions]
        │     └─> road_times.csv (수원 5,652쌍) + road_times_manifest.json(simulation_ready)
        │           ※ 같은 폴더에 동시 실행 금지 — 반드시 순차 (§18)
        │
        ├─ prepare_municipal_dispatch_rates.py
        │     └─> data/processed/calibration/municipal_daily_calls_2025.csv (수원 210.4건/일)
        │
        └─ refresh_gyeonggi_readiness.py  →  readiness.json (blocking / advisory)
                │
                ▼
  build_municipal_placement_inputs.py (병원 신설)  |  build_municipal_standby_inputs.py (대기지 재배치)
        + data/processed/calibration/model_parameters_calibrated.json
                │                                             │
                ▼                                             ▼
  data/gyeonggi_real/41110_suwon/                    data/jeonju_real/52110_jeonju/
    {placement.json, scenario.json}                    {standby.json, scenario_standby.json}
                │                                             │
                ▼                                             ▼
  ambulance_sim.hospital_placement                   ambulance_sim.standby_placement
    load_municipal_placement_problem (엄격 검증)       load_municipal_standby_problem
    → 후보 조합 완전탐색                                → 그리디 전진탐색
    → 조합마다 scenario 복제 → Simulation × N seed      → 배정마다 Simulation × N seed
    → 짝비교 통계 _estimate(mean / sd / 95% CI)
                │
                ├─> outputs/suwon_grid_k1/hospital_placement_result.json
                ├─> make_report_figures.py             → outputs/figures/*.png
                └─> build_folium_municipal_simulator.py → outputs/*_simulator.html
```

시나리오 로딩 세부: `load_scenario` → `scenario_from_dict`(필드 파싱) → `validate_scenario`(노드 참조·좌표·확률·용량 교차검증) → `Scenario` → `Simulation(scenario, policy, seed).run()` → `summary()`.

---

## 5. 파라미터 파일과 데이터 상태

### `data/processed/calibration/model_parameters_calibrated.json` — KEEP(최종 사용)
- `status: "calibrated"`, `schema_version: 1`, 생성 2026-09-11 10:25 KST.
- `profiles[4]`: 이름 / 구성비 `share` / `golden_minutes` / `decay_rate` / `scene_minutes` / 현장 성공률. 값은 cardiac 12.17%(골든 6분, 감쇠 0.0496), stroke 8.2%(0분, 0.0035), trauma 0.66%(50분, 0.0058), minor 79.0%(60분, 0.003). 현장 처치 12/8/10/7분.
- `hourly_demand_multipliers[24]`: 09시 최대 1.378, 04시 최소 0.529. **공식 앵커 2점만 실측이고 22개 값은 보간**이라 성격상 합성이다(§12-3).
- `hospital.success_by_category`: 등급×유형별 치료 성공률(지역응급의료센터 기준 cardiac 0.283 / stroke 0.464 / trauma 0.434 / minor 0.98, 권역 ×1.10, 지역기관 ×0.75). `success_when_unavailable_by_profile`은 병상이 없을 때 값(cardiac 0.141 / stroke 0.232 / trauma 0.217 / minor 0.882).
- `hospital.treatment_minutes = 204.0` (NEDIS 중증 재실시간). 구급차 재정비 16.7분, 현장 종결률 0.
- 근거 전체: `docs/calibration_sources_ko.md`.

### `model_parameters_provisional.json` — STALE
임시 계수(성공률 0.86/0.78, 병상 없음 0.25, 유형 15/20/15/50). `outputs/archive/suwon_placement_provisional`·`suwon_placement_smoke`만 이 파일 기반이다. 새 실행에 쓰지 말 것.

### 그 외 calibration 자료
| 파일 | 상태 |
|---|---|
| `municipal_daily_calls_2025.csv`(+manifest) | KEEP. 수원 210.4건/일의 1차 출처 |
| `municipal_daily_calls_jeonju_2024.csv`(+manifest) | KEEP. 전주 104.3건/일 |
| `nfa_gyeonggi_monthly_seasonality_2020_2023.csv`(+manifest) | KEEP이나 **미적용**(§6, 후속 과제) |
| `suwon_patient_category_shares_2025.csv`(+manifest) | KEEP. 조사 참고자료(§8), 파라미터 미반영 |

### `data/raw/` 원자료
`public_sources_20260908_203053KST/`(인구·응급의료기관·119센터·구급차 전국/경기 + `_failed_downloads/`), `official_hospital_capacity_20260910/`(E-Gen 평가 PDF/CSV, HIRA 병원현황 — 63MB 추출본과 zip은 `.gitignore`로 추적 제외), `official_public_health_facilities_*`·`official_public_health_standard_*`(보건시설), `municipal_dispatch_counts_20260910/`(경기 실측 출동 + 소방청 통계연보 PDF), `nfa_headquarters_ambulance_activity_20260910/`(계절성용, 미적용), `jeonju_sources_20260911/`(전주 전용). 전부 KEEP — 재수집 비용이 크다.

### `data/processed/gyeonggi_20260909/41110/` (수원 staging)
`demand_population.csv`(44개 행정동), `existing_hospitals.csv`(응급의료기관 7곳), `ambulance_bases.csv`(119안전센터 11곳 / 구급차 19대), `candidate_sites.csv`(격자 31곳), `candidate_sites_public_health_reference.csv`(보건소 4곳, 참고), `candidate_sites_unroutable.csv`(제외 2곳), `grid_candidates_manifest.json`, `road_times.csv`(5,652쌍) + `road_times_manifest.json`, `readiness.json`.

---

## 6. CLI 전체 (예시 포함)

### `python -m ambulance_sim` — 상호배타적 5가지 모드
```powershell
# (1) 단일 시뮬레이션
python -m ambulance_sim --scenario examples/synthetic_scenario.json --policy greedy --episodes 10 --seed 42

# (2) 정책 3종 짝비교 (greedy / no-reposition / nearest)
python -m ambulance_sim --scenario examples/synthetic_scenario.json --compare --episodes 10 --seed 42

# (3) 신설 병원 배치 최적화  ★ 최종 보고서 경로
python -m ambulance_sim --hospital-placement data/gyeonggi_real/41110_suwon/placement.json --new-hospitals 1 --episodes 10 --seed 42 --json --output-dir outputs/suwon_grid_k1
# 여러 시·군 일괄 (각각 독립 계산)
python -m ambulance_sim --municipality-root data/gyeonggi_real --new-hospitals 1 --episodes 100 --seed 42 --json --output-dir outputs/gyeonggi_local

# (4) 구급차 대기소 재배치 최적화 (전주)
python -m ambulance_sim --standby-placement data/jeonju_real/52110_jeonju/standby.json --episodes 2 --seed 42 --movable 3 --shortlist 15 --json --output-dir outputs/jeonju_standby_rerun

# (5) 구급차 1대의 영구 대기위치 후보 비교
python -m ambulance_sim --scenario <scenario.json> --placement-ambulance <ambulance_id> --episodes 10 --seed 42

# [LEGACY] --legacy-overview 없이는 parser.error로 차단되며 위 5개 모드와 조합 불가
python -m ambulance_sim --national --detail municipal --legacy-overview
```

### 파이프라인 스크립트
```powershell
# staging 생성 (경기 / 전국판)
python scripts/prepare_gyeonggi_inputs.py --output-root data/processed/gyeonggi_20260909
python scripts/prepare_municipal_inputs.py --province 전북특별자치도 --municipality 전주시 --municipality-code 52110 --output-root data/processed/jeonju_20260911

# 지오코딩 [Kakao]
python scripts/geocode_gyeonggi_kakao.py --processed-dir data/processed/gyeonggi_20260909
python scripts/apply_demand_coordinate_overrides.py --processed-dir data/processed/gyeonggi_20260909

# 병상·등급 결합
python scripts/build_hospital_capacity_match.py
python scripts/stage_hospital_capacity.py --processed-dir data/processed/gyeonggi_20260909

# 격자 후보 + 도로접근 [Kakao]
python scripts/build_grid_candidates.py --processed-dir data/processed/gyeonggi_20260909 --municipality-code 41110
python scripts/build_grid_candidates.py --mode standby --processed-dir data/processed/jeonju_20260911 --municipality-code 52110 --requests-per-second 20
python scripts/probe_candidate_routability.py --processed-dir data/processed/gyeonggi_20260909 --municipality-code 41110

# 도로시간 수집 [Kakao] — 같은 폴더 동시 실행 금지, 반드시 순차 (§18)
python scripts/collect_kakao_routes.py --processed-dir data/processed/gyeonggi_20260909 --municipality-code 41110 --pair-set simulation --requests-per-second 5 --max-calls 6000

# 준비도 갱신
python scripts/refresh_gyeonggi_readiness.py --processed-dir data/processed/gyeonggi_20260909 --parameters data/processed/calibration/model_parameters_calibrated.json

# 수요 실측치
python scripts/prepare_municipal_dispatch_rates.py

# 최적화 입력 생성
python scripts/build_municipal_placement_inputs.py --processed-dir data/processed/gyeonggi_20260909 --parameters data/processed/calibration/model_parameters_calibrated.json --municipality-code 41110 --horizon-days 1 --cooldown-minutes 360 --overwrite
python scripts/build_municipal_standby_inputs.py --processed-dir data/processed/jeonju_20260911 --parameters data/processed/calibration/model_parameters_calibrated.json --municipality-code 52110 --slug jeonju --output-root data/jeonju_real --horizon-days 1 --cooldown-minutes 360 --overwrite

# 보고서 그림 (세 인자 모두 기본값이 최종 경로라 인자 없이도 동작)
python scripts/make_report_figures.py --result outputs/suwon_grid_k1/hospital_placement_result.json --processed data/processed/gyeonggi_20260909/41110 --out outputs/figures

# 브라우저 재생 시뮬레이터 (--placement 또는 --standby 중 하나)
python scripts/build_folium_municipal_simulator.py --placement data/gyeonggi_real/41110_suwon/placement.json --processed-dir data/processed/gyeonggi_20260909 --results outputs/suwon_grid_k1/hospital_placement_result.json --scenarios baseline,best --seed 42 --grid-km 1 --output outputs/suwon_folium_simulator.html

# 민감도·seed 스윕
python scripts/run_placement_sensitivity.py --placement data/gyeonggi_real/41110_suwon/placement.json --new-hospitals 1 --episodes 2 --seed 42 --output-dir outputs/sensitivity/41110_suwon --seed-sweep 3

# 경기 전역 자원 대시보드
python scripts/build_real_resource_dashboard.py
```

### 테스트
```powershell
python -m pytest -q -p no:cacheprovider tests   # 199 passed
python -m compileall -q ambulance_sim scripts
```

---

## 7. 모델 규칙 (구현된 그대로)

1. **분석 단위**: 시·군 하나. 타 시·군 자원은 검증 단계에서 차단(`{code}::` 노드 접두사 강제).
2. **환자 발생**: 행정동 행정복지센터 대표점에서 시간당 포아송. 시·군 실측 일일 출동건수를 행정동 인구 비율로 배분하고 `hourly_demand_multipliers`를 곱한다.
3. **환자 유형**: cardiac / stroke / trauma / minor 4종. 골든타임까지 생존확률 1, 이후 매 분 지수 감쇠(`PatientProfile.survival()`).
4. **배차**: 가장 가까운 유휴 구급차(도로시간 기준).
5. **병원 선택**: `GreedySurvivalPolicy`가 기대 생존확률이 가장 높은 병원을 고른다(전원 가치 재귀 계산 포함).
6. **치료 결과는 최종 (§19)**: `_arrive_hospital`에서 `treated = (환자 유형이 병원 capability에 있음) and reserve_bed() 성공`. `treated`이면 성공분은 saved, 실패분은 그대로 lost로 종결하고 **다시 전원하지 않는다**. 전원은 병상이 없거나 진료 불가일 때만 `max_transfers`(2회)까지 허용된다.
7. **시간 구조**: `arrival_horizon`은 `arrival_cutoff_minutes`(없으면 `horizon_minutes`)까지만 신규 도착을 예약하지만, 이벤트 루프는 `horizon_minutes`까지 계속 돈다 → 마감 후 6시간 냉각 동안 이미 발생한 환자의 이송·전원·재정비만 진행되고, 그래도 안 끝나면 손실 처리된다.
8. **재배치**: 재정비 완료 후 정책의 `choose_standby_location`으로 이동. `ScheduledStandbyPolicy`는 `free_hours`/`home_hours`가 24시간을 겹침 없이 분할해야 하며, `SHIFT_CHANGE` 이벤트에서 **IDLE 차량만** 새 구간 목적지로 옮긴다(출동·이송·재정비 중 차량은 건드리지 않음).
9. **적용하지 않는 것**: 도로 혼잡 시간대 배율, 계절성, 이벤트성 날짜, 시·군 경계 밖 자원.
10. **후보지**: 1km 격자 칸 중심. 시 내부 · 인구>0 · 인구 상위 30% 제외(NIMBY) · 기존 응급의료기관·보건시설에서 2km 이상(대원거리는 이 판정에만 허용) · Kakao 경로 탐색 가능. 수원은 154칸 중 31곳. 모든 후보의 `land_feasibility_status`는 "계획검토 필요".
11. **신설 병원 사양**: 전부 지역응급의료기관·응급실 20병상으로 동일. 위치 효과만 비교하기 위한 통제다.

---

## 8. 결과표

### 8-1. 최종 — 수원시 병원 신설 (보고서 §4 / `outputs/suwon_grid_k1/`)
조건: 격자 후보 31곳, 24h 발생 + 6h 냉각, calibrated 계수, 실측 수요 210.4건/일, **10 episode**, seed 42, 소요 9분 29초.

| 항목 | 값 |
|---|---|
| 기준안(기존 응급의료기관 7곳) 하루 기대 생존자 | **177.2명** (95% CI 170.9 ~ 183.5) |
| 1위 후보 | 권선구 권선동 격자 2-8, 증분 **+0.021명/일** (0.013 ~ 0.029) |
| 2위 | 권선구 장지동 1-6, +0.013 |
| 3위 | 권선구 대황교동 0-6, +0.013 |
| 신뢰구간 하한 > 0 | 9곳 (22곳은 0 포함, **음수 후보 없음**) |
| 효과가 확인된 9곳의 분포 | 8곳이 권선구 남부(권선동·장지동·대황교동·세류동·곡반정동)에 집중 |

유형별 생존률 — 증분 0.021명은 **전부 심정지·심혈관에서 발생**한다.

| 유형 | 기준안 → 1위 후보 신설 시 | 비고 |
|---|---|---|
| 심정지·심혈관 | 7.4% → **7.5%** | 하루 약 23명. 증분 전부 여기서 나옴 |
| 뇌졸중 | 45.6% → 45.6% | 불변 |
| 중증외상 | 49.4% → 49.4% | 불변 |
| 경증 | 98.5% → 98.5% | 불변 |

해석: 효과가 확인된 후보가 권선구 남부에 몰린 이유는 기존 7곳이 팔달·장안·영통 쪽에 있어 이 지역의 도로 이동시간이 길기 때문이다. 반대로 기존 병원이 가까운 영통·장안 북부는 병원을 추가해도 환자만 분산되고 생존자는 늘지 않는다. 절대 크기는 연간 약 8명, 기준안의 0.01% 수준이다.

### 8-2. STALE — 수원시 옛 후보(보건소 4곳) `outputs/archive/suwon_placement_provisional/`
조건: 보건소 후보 4곳, 24시간(냉각 없음), 100 episode, provisional 계수(성공률 0.86/0.78, 병상 없음 0.25), 인구비례 추정 수요 199.5건/일. **인용 금지.**

| 항목 | 값 |
|---|---|
| 기준안 | 142.91명 (140.61 ~ 145.22) |
| k=1 최선 | 영통구보건소 +0.795 (0.748 ~ 0.843) |
| 2~4위 | 장안구보건소 +0.419 / 팔달구보건소 +0.305 / 권선구보건소 +0.093 |
| k=2 최선 | 영통 + 장안 +1.214 (1.146 ~ 1.282) |

### 8-3. STALE — 전주시 구급차 재배치 `outputs/archive/jeonju_standby_grid_test/`
조건: **예비 2 episode**, 이동 3대, 격자 칸만 후보, §19 치료 규칙 수정 **이전** 실행. **절대값 인용 금지, 재실행 필요.**

| 항목 | 값 |
|---|---|
| 기준안(전원 원소속 대기) | 87.28명 (환자 약 104명) |
| 3대 이동 후 | 87.52명, 증분 +0.240 (95% CI 0.015 ~ 0.465) |
| 이동 1 | 평화119안전센터 차량 → 덕진구 인후동2가 격자 5-8 (칸 인구 ≈4,900), +0.084 |
| 이동 2 | 조촌119안전센터 차량 → 덕진구 덕진동1가 격자 6-6 (≈3,900), +0.069 |
| 이동 3 | 아중119안전센터 차량 → 덕진구 송천동1가 격자 7-6 (≈10,700), +0.087 |

세 이동 모두 덕진구 방향이며 완산구 차량은 이동 이득이 없었다. 안전센터 간 이동을 허용한 이전 시험(+0.176, `outputs/archive/jeonju_standby_test/`)은 사용자 규칙(새 격자로만 이동) 위반이라 폐기했다. 기준안 시뮬레이터(`outputs/jeonju_standby_simulator_baseline.html`)의 seed 42 하루 값은 환자 91명·기대 생존 79.48·손실 11.52이다.

> 세 표는 후보 정의·계수·episode 수가 모두 다르므로 **표끼리 직접 비교하지 않는다.**

---

## 9. 알려진 한계

1. **수요 위치가 대표점**: 환자 발생 위치가 행정복지센터 좌표이며 실제 신고 지점이 아니다.
2. **도로시간 1회 스냅샷**: 2026-09-10 수집값 1회. 같은 구간을 20분 뒤 재조회하면 수초 차이가 난다. 구급차 우선통행 미반영, 혼잡 배율 미적용.
3. **시간대 배율의 22/24가 보간값** (§12-3). 공식 앵커는 09시 최대·04시 최소 2점뿐이라 성격상 합성이다. 7일 연속 실행을 아직 켜지 않은 이유이기도 하다.
4. **수요 정의 불일치**: 수요가 "출동 건수" 기준이라 실제 이송 환자보다 약 1.78배 많다(2023 경기 이송/출동 = 56.3%).
5. **환자 유형 비율이 전국 통계 대체**: calibrated 파일의 trauma 0.66%는 잔차로 계산된 값이라 수원 실측 매핑(12.5%)과 크게 다르다(§8·§12-2).
6. **치료 성공률이 §5 사용자 결정과 다름**: 사용자 결정은 1.0/0.1이지만 실제 적용된 것은 문헌 보정 계수의 유형·등급별 값이다(§12-1, 사용자 확인이 필요한 항목으로 기록되어 있음).
7. **격자 칸의 부지 미검증**: 토지 소유·용도지역·면적을 확인하지 않았다. 전 후보가 "계획검토 필요".
8. **10 episode는 통계적으로 부족**: 하위 후보의 순위는 바뀔 수 있다. 상위 후보의 방향성만 신뢰한다.
9. **구급차 재배치는 탐욕 해**: `optimize_standby_placement`는 전역 최적을 보장하지 않는다(`search_note`에 명시).
10. **시·군 경계 밖 자원 미포함**: 인접 시·군의 병원·구급차가 실제로는 개입하지만 모델에는 없다.
11. **절대 효과의 크기**: 증분이 기준안의 0.01% 수준이다. 경증 79%와 그 완만한 감쇠가 총계를 입지에 둔감하게 만들기 때문이며, 그래서 유형별 분리 보고가 필수다.

---

## 10. 미해결 항목 (Open items)

1. **전주 재배치 재실행** — 현재 전주 수치는 전부 §19 치료 규칙 수정 이전 산출이다. `data/jeonju_real/52110_jeonju/standby.json`과 현재 엔진으로 episode 수를 확정해 재실행하고, `RESULTS_KO.md` 3절과 `REPORT_GUIDE_KO.md` 5-5절을 갱신해야 한다.
2. **공개 저장소 라이선스 확인 — 미검증** — 저장소가 공개(https://github.com/CHOI-0912/2026_2_CS_OT_6)인데 **Kakao API 응답으로 만든 도로시간 행렬(`road_times.csv`)의 재배포 약관을 확인하지 못했다.** 원자료(HIRA·E-Gen·경기데이터드림·소방청)의 재배포 조건도 개별 확인이 필요하다. 저장소에 LICENSE 파일이 없다.
3. **시간대별 수요 배율의 실측화** — 24개 중 22개가 보간값. 실제 시간대별 출동 분포를 확보하면 §12-3의 합성 성격이 해소되고 7일 연속 실행이 가능해진다.
4. **`ambulance_sim/calibration.py` 배선 여부 결정** — 어느 파이프라인에서도 호출되지 않는 고아 모듈. 사용처를 만들지, legacy로 옮길지, 주석만 달지 사용자 확정 필요(테스트가 있으므로 삭제 금지).
5. **경기도 나머지 30개 시·군** — 도로시간 미수집으로 `simulation_ready`가 아니다. Kakao 쿼터 때문에 미완이며, 후보지도 보건시설 목록 상태로 남아 있다.
6. **계절성 미적용** — 자료(`nfa_gyeonggi_monthly_seasonality_2020_2023.csv`)는 있으나 §6 결정으로 제외. 후속 과제.
7. **`data/gyeonggi_real/README.md` 갱신** — "후보 4곳 / 1,683쌍 / provisional" 서술이 실제(격자 31곳 / 5,652쌍 / calibrated)와 다르다.
8. **`README.md` 상단 배너 갱신** — "현재 주 파이프라인 = 전주"라는 선언이 최종 제출본과 어긋난다.

---

## 11. 결정 타임라인 (`docs/decisions_and_tradeoffs_ko.md`)

| § | 날짜 | 내용 |
|---|---|---|
| 1 | 이전 세션 | 분석 단위 = 시·군 독립 계산 |
| 2 | 이전 세션 | 목적함수 = 기대 생존자 수 최대화 |
| 3 | 09-10 | 후보지 = 1km 격자, 인구 상위 30% 제외, 기존 시설 2km 이상 |
| 4 | 09-10 | 신설 병원 사양 = 지역응급의료기관, 응급실 20병상, 전부 동일 |
| 5 | 09-10 | 치료 성공률 = 병상 있으면 1.0, 없으면 0.1 (→ §12에서 보정 파일 값으로 대체됨) |
| 6 | 09-10 | 시간 구조 = 24h 발생 + 6h 냉각, 혼잡 배율·계절성·이벤트 날짜 제외 |
| 7 | 09-10 | episode = 개발 2회, 최종은 별도 확정 (최종 실행은 10회) |
| 8 | 09-10 | 환자 유형 4종 유지 + 현실 분류 조사 병행 |
| 9 | 이전 세션 | 도로시간 출처 = Kakao Mobility 자동차 길찾기 |
| 10 | 09-10 23:15 | 수요 = 시·군별 2025 실측 출동 건수로 전환 (10-1: Kakao 쿼터 제약) |
| 11 | 09-10 | 도로시간 캐시 구현 |
| 12 | 09-10 23:25 | 문헌 보정 계수 채택, 사용자 결정과 충돌 4건 명시 |
| 13 | 09-11 00:05 | 연구 문제 전환: 병원 신설 → 구급차 상시대기 재배치 |
| 14 | 09-11 08:05 | 대상 도시 전환: 수원 → 전주 |
| 15 | 09-11 08:10 | Kakao 쿼터 재확인, 자율 진행 지시 |
| 16 | 09-11 | Kakao 호출 속도 상향 |
| 17 | 09-11 10:30 | 전주 입력 확정, 격자 범위 결정 |
| 18 | 09-11 11:00 | **도로시간 동시 수집 사고** → 같은 폴더 병렬 실행 금지, 순차 규칙 |
| 19 | 09-11 15:20 | **치료 결과 최종화 / 전원은 병상 없을 때만** (사용자 지적). 15:0x 결과 폐기 후 재실행 |
| — | 09-11 15:58 | `outputs/suwon_grid_k1/` 최종 실행 + `outputs/figures/` 생성 |
| — | 09-11 16:03 | `보고서.md` / `2518최지완_7번.hwpx` 확정·제출 |

**중간 단계 주의**: §13에서 "수원시 구급차 재배치"로 전환했다가 §14에서 곧바로 전주로 옮겼기 때문에, "수원 구급차 재배치"는 실행 결과가 어디에도 없는 서류상 중간 단계다.

---

## 12. 노드 ID 규약 (glossary)

시나리오 그래프의 모든 노드 id는 **`{시군코드}::{종류}::{식별자}`** 형태이며, `load_municipal_placement_problem` / `load_municipal_standby_problem`이 접두사가 해당 시·군 코드와 일치하는지 검증해 타 시·군 자원 혼입을 차단한다.

| 구성요소 | 의미 | 예 |
|---|---|---|
| `41110` / `52110` | 시·군 법정코드. 수원시 41110, 전주시 52110 | — |
| `village` / `demand` | 행정동 수요 단위(행정복지센터 대표점) | `41110::village::...` |
| `hospital` | 기존 응급의료기관 | `41110::hospital::...` |
| `candidate` | 신설 병원 후보 격자 칸 | `41110::candidate::...` |
| `base` | 119안전센터. 구급차의 `home_base` | `52110::base::...` |
| `post` | 상시대기 후보 위치(격자 칸 또는 기존 안전센터) | `52110::post::...` |
| 격자 라벨 `N-M` | 시·군 격자의 (열-행) 좌표. 보고서의 "권선동 격자 2-8" 등 | — |

- `RoadNetwork`의 간선은 **방향별**(A→B와 B→A가 다름)이며 단위는 분이다.
- `road_times_manifest.json`의 `simulation_ready: true`는 수요지·후보지까지 포함한 모든 방향 쌍이 실제 API로 수집됐을 때만 기록된다. 경로를 못 찾은 쌍은 `road_times_unroutable.csv`로 분리된다.
- `readiness.json`의 `blocking_inputs`는 생성 차단 사유, `advisory_inputs`는 권고 사항이다.
