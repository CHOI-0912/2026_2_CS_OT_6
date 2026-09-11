# 전북특별자치도 전주시 실제자료 전처리 결과

전국 단위 공식 원본을 전주시(52110) 하나로 분리한 staging 자료입니다. 아직 도로시간과 대기지점 후보가 없으므로 시뮬레이션 결과가 아닙니다.

폴더 구조와 열 이름은 `data/processed/gyeonggi_20260909` 와 완전히 같으므로 하위 스크립트를 수정하지 않고 그대로 쓸 수 있습니다.

## 검산

- 시·군: 1개 (전주시, 완산구 52111 · 덕진구 52113)
- 2026년 8월 주민등록인구: 618,908명
- 행정동 수요 지점: 35개 (완산구 19, 덕진구 16)
- 응급의료기관: 5개 (권역 1, 지역센터 3, 지역기관 1)
- 구급차 원본 행: 10개 (전주완산소방서 5, 전주덕진소방서 5)
- 운용 구급차: 12대
- 미분류 구급차 행: 0개
- 좌표 확보: 수요 35/35, 구급차 10/10, 병원 5/5
- 합성 fallback: 사용하지 않음

## 폴더 구성

`52110` 폴더에 다음 파일이 있습니다.

- `existing_hospitals.csv`: 응급의료기관 명단·종별·평가등급·주소·좌표·병상
- `demand_population.csv`: 행정동 코드와 주민등록인구, 행정복지센터 대표점 좌표
- `ambulance_bases.csv`: 119안전센터별 구급차 대수와 좌표
- `candidate_sites.csv`: 실제 대기지점 후보 입력 대기용 빈 표
- `readiness.json`: 미완료 입력

도로시간·후보지가 없는 칸은 임의값으로 채우지 않고 `pending` 으로 표시했습니다.

## 자료 출처

`data/raw/jeonju_sources_20260911/source_manifest.json` 에 재사용한 전국 원본의 sha256 과 추출 결과를 기록했습니다. 자세한 설명은 `docs/jeonju_data_ko.md` 에 있습니다.

경기도와 달리 전북특별자치도는 시·도 자체 응급의료기관 CSV 를 개방하지 않습니다. 그래서 응급의료기관 명단은 중앙응급의료센터 2024년도 평가 전국 PDF 에서 읽고, 주소·좌표·병상은 심사평가원 2026년 6월 자료에서 결합했습니다. 두 출처가 독립적으로 전주시 응급의료기관을 5곳으로 제시해 서로 일치합니다.

## 구급수요

`data/processed/calibration/municipal_daily_calls_jeonju_2024.csv` 에 2024년 관측 출동건수 38,183건(일평균 104.3251건)을 기록했습니다. 인구비례 추정이 아니라 소방청 통계연보의 전주완산·전주덕진소방서 안전센터 9개소 합산값입니다.

## 재생성 명령

```powershell
python scripts/prepare_municipal_inputs.py `
  --province 전북특별자치도 `
  --municipality 전주시 `
  --municipality-code 52110 `
  --nationwide-dir data/raw/public_sources_20260908_203053KST `
  --capacity-dir data/raw/official_hospital_capacity_20260910 `
  --output-root data/processed/jeonju_20260911 `
  --egen-extract-out data/raw/jeonju_sources_20260911/egen_2024_evaluation_jeonbuk.csv

python scripts/geocode_municipal_kakao.py `
  --processed-dir data/processed/jeonju_20260911 `
  --province-keyword 전북 `
  --municipality-code 52110 `
  --include-demand-representatives `
  --requests-per-second 20

python scripts/refresh_gyeonggi_readiness.py `
  --processed-dir data/processed/jeonju_20260911

python data/raw/jeonju_sources_20260911/extract_jeonju_dispatch_counts.py
```
