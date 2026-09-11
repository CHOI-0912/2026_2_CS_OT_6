# 경기도 31개 시·군 실제자료 전처리 결과

공식 원본을 시·군별로 분리한 staging 자료입니다. 아직 도로시간과 실제 구급수요율이 없으므로 시뮬레이션 결과가 아닙니다.

## 검산

- 시·군: 31개
- 2026년 8월 주민등록인구 합계: 13,773,918명
- 읍면동 수요 지점: 604개
- 응급의료기관: 73개
- 구급차 원본 행: 232개
- 운용 구급차: 290대
- 미분류 구급차 행: 0개
- 합성 fallback: 사용하지 않음

공식 응급의료기관이 0곳인 시·군은 동두천시, 과천시, 의왕시, 하남시, 양주시, 가평군입니다. 이 지역의 `existing_hospitals.csv`는 헤더만 존재하며, 빈 기준안을 그대로 보존합니다.

## 폴더 구성

각 5자리 시·군 코드 폴더에는 다음 파일이 있습니다.

- `existing_hospitals.csv`: 실제 병원명·유형·주소·좌표
- `demand_population.csv`: 읍면동 코드와 주민등록인구
- `ambulance_bases.csv`: 구급차 운영 거점과 대수
- `candidate_sites.csv`: 실제 후보지 입력 대기용 빈 표
- `readiness.json`: 지역별 미완료 입력

도로시간·좌표·구급수요·후보지가 없는 칸은 임의값으로 채우지 않고 `pending`으로 표시했습니다.

재생성 명령:

```powershell
python scripts/prepare_gyeonggi_inputs.py `
  --raw-dir data/raw/public_sources_20260908_203053KST `
  --output-dir data/processed/gyeonggi_20260909
```
