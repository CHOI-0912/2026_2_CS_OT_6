# 실제 입력 디렉터리

이곳에는 시·군별 실제 입력만 둡니다. 합성값이나 시·군 중심점으로 빈칸을 채우지 마십시오. 폴더 구조와 필수 필드는 [../../docs/hospital_placement_input_ko.md](../../docs/hospital_placement_input_ko.md)를 따릅니다.

## 생성되어 있는 시·군

- `41110_suwon/` (수원시): 44개 행정동 수요지, 11개 구급대(구급차 19대), 기존 응급의료기관 7곳, 신설 후보지 4곳, Kakao Mobility 방향별 도로시간 1,683쌍으로 구성되어 있으며 `load_municipal_placement_problem()` 검증을 통과합니다.

수원시는 `road_times_manifest.json`의 `simulation_ready`가 `true`인 유일한 시·군입니다. 나머지 30개 시·군은 `simulation` 도로시간 행렬이 아직 수집되지 않았으므로 생성기가 의도적으로 건너뜁니다.

## 생성 방법

정제된 시·군 폴더(`data/processed/gyeonggi_20260909/<시군코드>/`)와 모형 계수 파일에서 생성기가 자동으로 만듭니다. 이 폴더의 파일은 직접 편집하지 말고, 원본 자료나 계수를 고친 다음 생성기를 다시 실행하십시오.

```bash
python scripts/build_municipal_placement_inputs.py \
  --processed-dir data/processed/gyeonggi_20260909 \
  --parameters data/processed/calibration/model_parameters_provisional.json \
  --output-root data/gyeonggi_real \
  --municipality-code 41110 --overwrite
```

`41110_suwon/`은 위 명령으로 만들었고, 수요·병원·후보지·도로시간의 출처와 계수 파일 경로는 `placement.json`의 `provenance`에 기록되어 있습니다.

## 계수 상태 경고

현재 적용된 계수 파일은 `data/processed/calibration/model_parameters_provisional.json`이며 `status`가 `"provisional"`입니다. 환자 유형 구성비, 생존 감쇠율, 병원 치료 성공확률, 후보지 병상 수는 모두 임시값이므로, 이 입력으로 계산한 기대 생존자 수의 절대값은 발표용 결과로 사용하면 안 됩니다.

수요 역시 관측된 시·군 단위 119 출동 기록이 아니라, 2023년 경기도 본부 출동건수 총계를 인구 비율로 배분한 추정값입니다. 수요지 좌표는 행정복지센터 대표점이며 실제 신고 지점이 아닙니다.

## 실행 예시

```bash
python -m ambulance_sim --hospital-placement data/gyeonggi_real/41110_suwon/placement.json \
  --new-hospitals 1 --episodes 3 --seed 42 --json --output-dir outputs/suwon_placement_smoke_rerun
```

## 공식 병원 자료 원본

- 공공데이터포털 경기도 응급의료기관 현황: https://www.data.go.kr/dataset/15011313/fileData.do
- 경기데이터드림 응급의료기관 현황: https://data.gg.go.kr/portal/data/service/selectServicePage.do?infId=MB714IBPDSE5OPNIMW0V27143432&infSeq=1

경기데이터드림 OpenAPI 전체 호출은 인증키가 필요하며, 키가 없으면 sample 5건만 반환됩니다. sample을 전체 자료로 사용하면 안 됩니다.
