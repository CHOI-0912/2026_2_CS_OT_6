# Legacy: 실제 병원 입지 분석에 사용 금지

이 디렉터리는 이전 단계의 광역 합산·중심점·대표 시설 모형을 기록 목적으로 보관합니다.

- `data/national_aggregate_2024`: 17개 시·도 합산 자료
- `data/gyeonggi_centroid_2024`: 경기도 31개 시·군 중심점 추정 자료
- `examples`: 대표 병원과 중심점으로 만든 과거 시나리오
- `outputs`: 위 시나리오의 과거 HTML 출력
- `docs`: 합성 기준선과 구급차 대기지 배치 결과
- `ambulance_sim/legacy`: 과거 overview 생성 코드

이 자료는 실제 병원 수·실제 병원 위치·실제 도로시간을 반영한 병원 입지 최적화가 아닙니다. 현행 CLI는 `--legacy-overview`를 명시해야만 이 코드를 실행합니다.
