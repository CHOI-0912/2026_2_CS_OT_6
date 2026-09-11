# CLAUDE.md — 세션 시작 시 먼저 읽는 파일

## 프로젝트 정체

고등학교 연구 수행평가(2026-2학기, 학생 2518 최지완)로 만든 **시·군 단위 이벤트 기반 SMDP 구급 시뮬레이터**(`ambulance_sim/`)다. 실제 공공데이터(행안부 인구, 경기데이터드림 구급활동, HIRA 병상, E-Gen 등급, 소방청 구급차)와 Kakao Mobility 도로 이동시간만으로 시나리오를 만들고, 기대 생존자 수를 목적함수로 삼아 최적 입지를 찾는다. **최종 제출 산출물은 "수원시 응급병원 신설 입지" 분석**(`보고서.md` / `2518최지완_7번.hwpx`, 결과 `outputs/suwon_grid_k1/`)이다. 프로젝트 중간에 "전주시 구급차 상시대기 재배치"로 잠시 전환했다가(결정 §13·§14) 다시 수원 병원 신설로 되돌아와 제출했으므로, **전주 작업은 코드·데이터·파이프라인이 모두 완성된 부수 트랙이지만 산출 수치는 stale**이다(치료 규칙 수정 §19 이전 실행분).

## Start here

1. `PROJECT_CONTEXT.md` — 폴더 트리, 모듈 지도, 데이터 파이프라인, 전체 CLI, 결과표, 한계, 미해결 항목.
2. `보고서.md` — 최종 제출본(수원 병원 신설). **수정 금지**(사용자가 명시적으로 요청할 때만).
3. `RESULTS_KO.md` — 실행 결과 요약표 3개(수원 구후보 / 수원 격자 최종 / 전주 예비). 각 표의 조건이 달라 표끼리 직접 비교 금지.
4. `docs/decisions_and_tradeoffs_ko.md` (§1~§19) — 모든 결정과 trade-off의 **정본**. 충돌이 있으면 이 문서가 이긴다.

보조: `REPORT_GUIDE_KO.md`(방법론 정리), `HANDOFF_REAL_DATA_KO.md`(인수인계), `docs/model_spec_ko.md`, `docs/input_schema_ko.md`, `docs/candidate_sites_ko.md`, `docs/calibration_sources_ko.md`.

## 절대 규칙 (연구 규칙)

- **시·군별 독립 계산.** 다른 시·군의 환자·병원·구급차·후보지를 섞지 않는다. 31개 시·군 합계는 보고용으로만.
- **직선거리·중심점 거리 금지.** 실제 좌표 + Kakao 방향별 도로 이동시간만 사용한다. 대원(great-circle)거리는 후보지 2km 이격 판정과 도로접근 점검의 "가장 가까운 시설 선택"에만 허용.
- **fail-loud, 합성 대체 금지.** 자료가 하나라도 없으면 생성기와 계산은 실패해야 한다. 임의 수치로 빈칸을 채우지 않는다.
- **합성 예제 격리.** 합성 시나리오 결과는 `outputs/synthetic_examples/`에만 두고 실제 결과처럼 표시하지 않는다.
- 목적함수는 **기대 생존자 수 최대화**. 후보지는 1km 격자 칸(시 내부, 인구>0, 인구 상위 30% 제외, 기존 시설 2km 이상, Kakao 경로 탐색 가능).
- 신설 병원은 전부 동일 사양(지역응급의료기관, 응급실 20병상). 치료 성공률은 사용자 결정(1.0/0.1)이 아니라 **문헌 보정 계수 파일의 유형·등급별 값**이 적용된 상태다(§5 ↔ §12 충돌, §12가 최신).
- **치료 결과는 최종**(§19). 병상이 있어 진료를 받았으면 그 결과로 종결한다. 전원은 병상이 없거나 진료 불가할 때만 최대 2회.
- episode는 환자 발생 24시간 + 냉각 6시간. 도로 혼잡 배율·계절성·특정 날짜는 적용하지 않는다.

## 절대 규칙 (작업 방식)

- **서브에이전트는 Fable 금지, 기본 sonnet, 최대 opus.** (이 환경에서 sonnet이 반복 정지한 이력이 있어 무거운 작업은 opus.)
- **모든 결정·trade-off는 `docs/decisions_and_tradeoffs_ko.md`에 새 절로 기록**한다.
- 사용자용 문서·보고서·주석 설명은 **한국어**로 쓴다(식별자·경로는 영어).
- **같은 시·군 폴더의 Kakao 도로시간 수집기를 동시에 두 개 실행하지 않는다.** 반드시 순차(§18, CSV 파손 사고 있었음).
- **`.env` 값을 읽거나 출력하지 않는다.** 키는 `KAKAO_REST_API_KEY`, `GYEONGGI_DATA_API_KEY` 두 개이며 `ambulance_sim/env_file.py`가 주입한다.
- **시뮬레이션 실행과 외부 API 호출은 사용자가 명시적으로 지시할 때만** 한다(장시간·유료 쿼터 소모).
- **GitHub Pages 경로를 깨뜨리지 않는다.** 아래 3개 URL은 `outputs/` 최상위에 그대로 유지:
  `outputs/suwon_folium_simulator.html`, `outputs/jeonju_standby_simulator.html`, `outputs/jeonju_standby_simulator_baseline.html`
  (공개 저장소 https://github.com/CHOI-0912/2026_2_CS_OT_6 , Pages https://choi-0912.github.io/2026_2_CS_OT_6/)

## 최종 결과 재현 경로 (5단계)

```powershell
# 1. 최적화 입력 생성 (placement.json + scenario.json)
python scripts/build_municipal_placement_inputs.py --processed-dir data/processed/gyeonggi_20260909 --parameters data/processed/calibration/model_parameters_calibrated.json --municipality-code 41110 --horizon-days 1 --cooldown-minutes 360 --overwrite

# 2. 신설 1곳 전수탐색 (최종 결과, 약 9분 30초)
python -m ambulance_sim --hospital-placement data/gyeonggi_real/41110_suwon/placement.json --new-hospitals 1 --episodes 10 --seed 42 --json --output-dir outputs/suwon_grid_k1

# 3. 보고서 그림 5장 생성
python scripts/make_report_figures.py --result outputs/suwon_grid_k1/hospital_placement_result.json --processed data/processed/gyeonggi_20260909/41110 --out outputs/figures

# 4. 브라우저 재생 시뮬레이터 (Pages 링크 대상)
python scripts/build_folium_municipal_simulator.py --placement data/gyeonggi_real/41110_suwon/placement.json --processed-dir data/processed/gyeonggi_20260909 --results outputs/suwon_grid_k1/hospital_placement_result.json --scenarios baseline,best --seed 42 --grid-km 1 --output outputs/suwon_folium_simulator.html

# 5. 회귀 검증
python -m pytest -q -p no:cacheprovider tests
```

1~4단계는 Kakao API를 호출하지 않는다(도로시간은 `data/processed/gyeonggi_20260909/41110/road_times.csv`에 이미 수집됨). 사용자 지시 없이 실행하지 말 것.

## 테스트

```powershell
python -m pytest -q -p no:cacheprovider tests   # 199 passed
python -m compileall -q ambulance_sim scripts
```

## 결과·그림·시뮬레이터 위치

| 대상 | 경로 |
|---|---|
| 최종 결과 JSON | `outputs/suwon_grid_k1/hospital_placement_result.json` |
| 보고서 그림 5장 | `outputs/figures/{fig1_candidate_ranking,fig2_candidate_map,fig3_paired_episodes,table1_top10,table2_inputs}.png` |
| 수원 시뮬레이터 | `outputs/suwon_folium_simulator.html` (+ `.summary.json`) |
| 전주 시뮬레이터 | `outputs/jeonju_standby_simulator.html`, `outputs/jeonju_standby_simulator_baseline.html` |
| 합성 검증 예제 | `outputs/synthetic_examples/` |
| 보관(stale) 실행 | `outputs/archive/` |

## 인용 금지 — stale 목록

- `outputs/archive/` 전체: `suwon_placement_provisional/{k1,k2}`(옛 후보 4곳·임시 계수), `suwon_placement_smoke`(스모크), `jeonju_standby_test`(폐기된 안전센터 간 이동 규칙), `jeonju_standby_prelim`(0바이트, 미완료), `jeonju_standby_grid_test`(2 episode 예비), `gyeonggi_real_resource_dashboard.html`(보고서 미인용).
- 전주 재배치 수치 전부(`RESULTS_KO.md` 3절 포함): §19 치료 규칙 수정 **이전** 산출이라 현재 엔진으로 재실행해야 유효하다.
- `data/processed/calibration/model_parameters_provisional.json`: 임시 계수. 최종은 `model_parameters_calibrated.json`.
- `README.md` / `HANDOFF_REAL_DATA_KO.md` / `REPORT_GUIDE_KO.md` 상단의 "현재 목표 = 전주" 서술: 최종 제출본과 어긋난다. `PROJECT_CONTEXT.md`를 우선한다.
