# 경기도 31개 시·군 모델 입력

`municipality_summary.csv`는 경기도 상세 모드가 사용하는 31개 계산 구역이다.

- 경기도 2024년 공식 출동 총계: 799,302건
- 경기도 2024년 12월 주민등록 인구 합계 검산값: 13,694,685명
- `population_weight`: 시·군별 수요 배분용 잠정 인구 가중치
- `estimated_dispatches_2024`: 공식 경기도 총계를 가중치 비례로 나눈 추정값
- 31개 `estimated_dispatches_2024` 합계: 정확히 799,302건
- 좌표: 시청·군청 인근 대표 중심점으로 도로 노드 실측값이 아님

중요: 시·군별 출동량 자체는 관측값이 아니다. 공식 구급활동 원자료를 확보하면 `estimated_dispatches_2024`를 발생 시·군별 집계값으로 교체해야 한다. 차량도 경기·경기북부 합계 290대가 확인되지만, 소방서 관할과 시·군 경계가 일치하지 않아 현재 상세 모드는 시·군당 최소 1대인 대표 차량을 사용한다.

출처:

- 행정안전부 주민등록 인구통계: https://jumin.mois.go.kr/ageStatMonth.do
- 소방청 본부별 구급활동정보: https://www.data.go.kr/data/15080046/fileData.do
- 소방청 전국 소방서·119안전센터별 구급차 정보: https://www.data.go.kr/data/15113282/fileData.do
- 경기도 119 구조·구급 공개목록: https://www.gg.go.kr/opendata/openDataList.do
