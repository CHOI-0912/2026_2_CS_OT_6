# 구급차·병원 입지 프로젝트 인수인계

작성 기준: 2026-09-11 08:05 KST (상단 "현재 상태" 블록만 2026-09-11 16:10 갱신)

> **먼저 읽을 것**: 프로젝트 요약과 절대 규칙은 [CLAUDE.md](CLAUDE.md), 폴더·모듈·파이프라인·결과표 전체는 [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)에 있다. 이 문서의 아래 절들은 08:05 시점 상태를 담고 있어 일부가 낡았다.

## 현재 상태 (2026-09-11 16:10 갱신)

- **제출 완료된 최종 산출물은 "수원시(41110) 응급병원 신설 입지" 분석이다.** 보고서는 `보고서.md` / `2518최지완_7번.hwpx`, 결과는 `outputs/suwon_grid_k1/hospital_placement_result.json`(격자 후보 31곳, 10 episode, calibrated 계수, §19 치료 규칙 수정 후 재실행분), 그림은 `outputs/figures/` 5장이다.
- **전주시(52110) 구급차 상시대기 재배치는 "코드·데이터 완성, 결과 stale" 상태다.** 엔진(`ScheduledStandbyPolicy`, SHIFT_CHANGE), 최적화(`ambulance_sim/standby_placement.py`), 입력 생성기, 시뮬레이터가 모두 동작하고 입력(`data/jeonju_real/52110_jeonju/`)도 검증을 통과했다. 다만 산출된 수치는 전부 `docs/decisions_and_tradeoffs_ko.md` §19(치료 결과 최종화, 전원은 병상 없을 때만) **이전** 실행분이므로 **인용하지 말 것**.
- 아래 0-1절·0절의 "현재 목표 = 전주"라는 서술과 `README.md`·`REPORT_GUIDE_KO.md` 상단 배너는 이 반전을 반영하지 못한 낡은 문장이다. 충돌 시 이 블록과 `PROJECT_CONTEXT.md`가 우선한다.
- **이어서 진행한다면 할 일**: (1) 현재 엔진으로 전주 재배치를 충분한 episode 수로 재실행하고 `RESULTS_KO.md` 3절·`REPORT_GUIDE_KO.md` 5-5절을 갱신, (2) 공개 저장소의 Kakao 도로시간·원자료 재배포 약관 확인(LICENSE 없음, 미검증), (3) `data/gyeonggi_real/README.md`의 "후보 4곳 / 1,683쌍 / provisional" 서술을 실제(격자 31곳 / 5,652쌍 / calibrated)로 갱신, (4) 시간대별 수요 배율 24개 중 22개가 보간값인 문제(§12-3) 해소.
- 낡은 실행 결과는 `outputs/archive/`로 옮겨 두었다(`suwon_placement_provisional`, `suwon_placement_smoke`, `jeonju_standby_test`, `jeonju_standby_prelim`, `jeonju_standby_grid_test`, `gyeonggi_real_resource_dashboard.html`). 전부 인용 금지.

## 0-1. 2026-09-11 08:05 대상 도시 전환

- 수원시 → 전주시(전북특별자치도, 시 코드 52110). 결정 기록 14절.
- 처리 루트는 `data/processed/jeonju_20260911/52110/`(수원과 같은 파일 구성), 최적화 입력은 `data/gyeonggi_real/` 대신 `data/municipal_real/52110_jeonju/` 또는 생성기 `--output-root` 지정 폴더.
- 2026-09-11 11:00 기준 완료: 엔진·최적화(`ambulance_sim/standby_placement.py`, `ScheduledStandbyPolicy`, SHIFT_CHANGE), 대기지 파이프라인(`--mode standby`, `--pair-set standby`, `build_municipal_standby_inputs.py`), 시뮬레이터 재배치 모드, 전주 처리 폴더 `data/processed/jeonju_20260911/52110/`(수요 35, 거점 10/구급차 12, 병원 5, 실측 수요 104.3건/일), 격자 대기지 92곳(도로접근 전부 통과), 도로시간 6,780쌍 수집 완료, 최적화 입력 `data/jeonju_real/52110_jeonju/{standby.json,scenario_standby.json}` 생성·검증 통과, 기준안 시뮬레이터 `outputs/jeonju_standby_simulator_baseline.html`. 테스트 198개 통과.
- 진행 중: 2 episode 시험 최적화(`outputs/archive/jeonju_standby_test/`). 이후 중복 도로시간 행 정리 → 입력 재생성 → 예비 실행(episode 수 사용자 미확정, 예비 표시) → 시뮬레이터 `baseline,best` → 문서·보고서 노트 5절.
- 주의: 같은 폴더의 도로시간 수집을 병렬 실행하면 CSV가 깨집니다(결정 기록 18절). 반드시 순차 실행.

## 0. 2026-09-11 전환 요약

- 연구 문제가 "신설 병원 입지"에서 "기존 구급차 상시대기 재배치"로 바뀌었다(`docs/decisions_and_tradeoffs_ko.md` 13절).
- 운영 규칙: 07~24시 자유 배치, 01~06시 원소속 복귀 목표(최하위 우선순위).
- 대기지 후보: 시 내부 1km 격자 전체(인구 > 0, 도로접근 가능)에서 119안전센터 1km 이내 제외 + 기존 안전센터.
- 구현 중(opus 워크플로): `ambulance_sim/standby_placement.py`, `ScheduledStandbyPolicy`, SHIFT_CHANGE 이벤트, `scripts/build_municipal_standby_inputs.py`, 수집기 `--pair-set standby`, 격자 스크립트 `--mode standby`. 입력 계약은 `standby.json` + `scenario_standby.json`.
- 수원 병원 신설용 도로시간(격자 31칸, 5,652쌍, `simulation_ready: true`)은 대기지 경로로 재사용된다.

## 1. 사용자 요구사항 — 절대 변경 금지

- 계산 단위는 경기도 전체 통합이 아니라 **시·군별 독립 계산**이다. 다른 시·군의 환자·병원·구급차·후보지를 섞지 않는다. 31개 합계는 보고용으로만.
- 직선거리나 중심점 거리를 쓰지 않는다. 실제 좌표와 Kakao 방향별 도로 이동시간을 사용한다. 대원거리는 후보지 2km 이격 판정과 도로접근 점검의 "가장 가까운 시설 선택"에만 쓴다.
- 목적함수는 **기대 생존자 수 최대화**다.
- 합성 예제 결과를 실제 분석 결과처럼 표시하면 안 된다(`outputs/synthetic_examples/`에 격리).
- **신설 후보지는 1km 격자 칸**이다: 시·군 내부, 인구 > 0, 인구 상위 30% 칸 제외, 기존 응급의료기관·보건시설에서 2km 이상, Kakao 경로 탐색 가능. 모든 후보는 "계획검토 필요".
- 신설 병원 사양은 지역응급의료기관·응급실 20병상·전부 동일.
- 치료 성공률은 병상 있으면 1.0, 없으면 0.1. 등급·유형 차이 없음.
- 도로 혼잡 시간대 배율은 적용하지 않는다. 계절성·이벤트 날짜는 제외한다.
- episode는 환자 발생 24시간 + 냉각 6시간(발생 마감 후 새 환자 없음, 미종결 환자만 종결까지). 7일 연속은 시간대별 출동 분포가 **실제 공식 통계**일 때만 적용하고, 합성이면 사용자에게 먼저 보고.
- 개발 확인은 2 episode. 최종 episode 수는 사용자와 재확정.
- 환자 유형 4종(cardiac/stroke/trauma/minor)은 유지하되 현실 분류 조사 결과(`docs/patient_types_in_practice_ko.md`)를 보고 조정.
- 서브에이전트는 Fable 금지, 기본 sonnet, 최대 opus. (이 환경에서 sonnet은 반복 정지했고 opus만 동작함.)
- 모든 결정과 trade-off는 `docs/decisions_and_tradeoffs_ko.md`에 기록.

## 2. 현재 데이터 상태

처리 루트: `data/processed/gyeonggi_20260909/`

| 항목 | 상태 |
|---|---:|
| 시·군 | 31개 |
| 행정동 수요 단위 / 좌표 | 604 / 604 (행정복지센터 대표점, 3곳은 공식 주소 교정) |
| 응급의료기관 / HIRA 병상 결합 | 73 / 73 (1행 수동검토 표시) |
| 119 거점 / 구급차 | 232행 / 290대 |
| 후보지 | 수원 격자 31곳(도로접근 불가 2곳 제외), 나머지 30개 시·군은 MOHW 보건시설 332곳(격자로 교체 예정) |
| 도로시간 | 수원만 `simulation` 등급 수집 중(5,652쌍 필수). 나머지 30개는 예비 또는 미수집 |

수원 파일: `41110/candidate_sites.csv`(격자 31), `candidate_sites_public_health_reference.csv`(보건소 4, 참고), `candidate_sites_unroutable.csv`(제외 2), `grid_candidates_manifest.json`(단계별 개수, API 캐시), `road_times.csv`, `road_times_manifest.json`, `road_times_unroutable.csv`(있으면), `readiness.json`.

## 3. 계수 파일

- `data/processed/calibration/model_parameters_provisional.json`: 현재 사용. 성공률 1.0/0.1은 사용자 결정, 나머지는 임시 계수.
- `data/processed/calibration/model_parameters_calibrated.json`: 문헌·공식통계 기반 보정본(opus 에이전트 작성 중). 완성되면 `--parameters`를 이 파일로 바꾸고 준비도를 갱신한다. 근거는 `docs/calibration_sources_ko.md`.
- 시·군별 실제 출동 건수(`daily_calls_by_municipality`) 확보 시도: `docs/demand_data_acquisition_ko.md`.

## 4. 파이프라인 (순서대로)

1. `scripts/prepare_gyeonggi_inputs.py` → 31개 폴더
2. `scripts/geocode_gyeonggi_kakao.py`, `scripts/apply_demand_coordinate_overrides.py`
3. `scripts/build_hospital_capacity_match.py` → `scripts/stage_hospital_capacity.py`
4. `scripts/build_grid_candidates.py` → `scripts/probe_candidate_routability.py` (수원 완료; 나머지 시·군은 미실행)
5. `scripts/collect_kakao_routes.py --pair-set simulation --max-calls N` (재개 가능, 경로 실패는 `road_times_unroutable.csv`에 기록하고 계속)
6. `scripts/refresh_gyeonggi_readiness.py --parameters <계수 파일>`
7. `scripts/build_municipal_placement_inputs.py --horizon-days 1 --cooldown-minutes 360` → `data/gyeonggi_real/<code>_<slug>/`
8. `python -m ambulance_sim --hospital-placement ... --new-hospitals K --episodes N`
9. `scripts/run_placement_sensitivity.py`, `scripts/build_folium_municipal_simulator.py --placement ... --results ...`

명령 예시는 `README.md`에 있다.

## 5. 코드 변경 요약 (2026-09-10 저녁)

- `ambulance_sim/model.py`: 출발지별 최단경로 캐시(1 episode 22.6초 → 0.9초, 결과 동일). `Scenario.arrival_cutoff_minutes` 추가.
- `ambulance_sim/engine.py`: `_dispatch_waiting` 조기 종료 수정, 기본 시나리오 검증 호출·병상 명시, 발생 마감 적용.
- `ambulance_sim/experiment.py`, `hospital_placement.py`: episode<2일 때 CI `null` + `ci95_note`, 시·군 합산 결합 CI, `CandidateHospital.transfer_delay_minutes`.
- `ambulance_sim/__main__.py`: episode<2 경고.
- `ambulance_sim/io.py`: `arrival_cutoff_minutes` 로드·검증·저장.
- `ambulance_sim/kakao_api.py`: `region_codes()`(좌표→행정구역).
- 새 스크립트: `build_municipal_placement_inputs.py`, `build_grid_candidates.py`, `probe_candidate_routability.py`, `run_placement_sensitivity.py`. 재작성: `build_folium_municipal_simulator.py`(placement.json 기반, 시나리오 선택·결과표·이벤트 로그).
- 파이프라인 수정: 준비도 조건부 차단, 수집기 `pair_set` 열·`--max-calls`·오류 처리, 후보 병합 로직, 경로 인자화.
- 테스트 140개 통과(`python -m pytest -q`).

## 6. 결과 파일

- `outputs/archive/suwon_placement_provisional/k1`, `k2`: **옛 후보 정의(보건소 4곳)**, 24시간, 100 episode, 옛 성공률(0.86/0.78). 참고용. 기준안 142.9명, k=1 최선 영통구보건소 +0.795[0.748, 0.843], k=2 최선 영통+장안 +1.214[1.146, 1.282].
- `outputs/suwon_folium_simulator.html`: 위 k1 결과 기반 시뮬레이터(기준안 vs C02). 격자 후보 결과가 나오면 재생성.
- `outputs/synthetic_examples/`: 합성 예제. 실제 결과 아님.

## 7. 다음에 할 일 (우선순위)

1. 수원 도로시간 수집 완료 확인(`road_times_manifest.json` `simulation_ready: true`). 실패 쌍이 있으면 해당 후보 제외 후 재수집.
2. `refresh_gyeonggi_readiness.py` → `build_municipal_placement_inputs.py --municipality-code 41110 --horizon-days 1 --cooldown-minutes 360 --overwrite`.
3. `--new-hospitals 1 --episodes 2`로 파이프라인 확인 → 시뮬레이터 재생성 → 민감도 분석(2 episode).
4. 보정 계수(`model_parameters_calibrated.json`)가 나오면 시간대별 분포가 공식 통계인지 확인해 사용자에게 보고하고, 승인 시 `--parameters` 교체 및 7일 연속 여부 결정.
5. 환자 유형 조사 결과를 반영해 비율·생존함수 조정 여부 결정.
6. 최종 episode 수 확정 후 k=1 전수, k=2는 상위 후보 선별 후 실행.
7. 나머지 30개 시·군: 격자 후보 생성 → 도로접근 점검 → 도로시간 수집(쿼터 확인 후 시·군별 순차) → 생성 → 최적화.
8. `REPORT_GUIDE_KO.md`의 "결과 대기" 항목 갱신.

## 8. 실시간 병상 API (미착수)

국립중앙의료원 `전국 응급의료기관 정보 조회 서비스`(공공데이터포털 15000563)의 공식 요청 파라미터는 `serviceKey`다. 필요 시 사용자에게 이 공식 명칭으로 발급을 요청한다.

## 9. 완료 판정 기준

- 대상 시·군 각각에 검증된 수요 공간자료와 정당하게 보정된 출동률이 있다.
- 기존 병원·119·격자 후보 좌표와 필수 방향별 도로시간이 완전하다.
- 계수 파일 상태가 `calibrated`이고 근거 문서가 있다.
- 최적화가 시·군별 독립으로 실행되고, 민감도 분석으로 최적 후보의 강건성이 확인됐다.
- 결과 화면이 실제 데이터·추정값·임시값을 구분하고, 테스트와 브라우저 확인을 통과했다.

현재는 위 조건 중 일부만 충족했으므로 프로젝트는 **진행 중**이다.
