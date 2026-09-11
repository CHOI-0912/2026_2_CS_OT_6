# 전국 17개 시·도 입력자료

`province_summary.csv`는 전국 개요 시뮬레이션의 공식 기준량이다.

- 인구·65세 이상 인구: 행정안전부 주민등록 인구, 2024년 12월
- 구급 출동: 소방청 2024년 구급활동 통계. 경남 값은 경남본부와 창원본부를 합산했다.
- 검산: 17개 시·도, 인구 51,217,221명, 65세 이상 10,256,782명, 출동 3,324,211건

출동 건수는 실제 연간 총량이다. 시뮬레이터 기본 전국 실행은 계산·시각화 부담을 줄이기 위해 이 발생률의 1%를 표본 추출한다. 대표 병원 1개, 대표 119 거점 1개, 권역 내 이동시간, 환자 유형 비율과 생존·치료 계수는 아직 모형 가정이다. 따라서 현재 결과는 전국 운영 구조를 시험하는 개요 모델이며 실제 배치 권고가 아니다.

출처:

- 소방청, 2024년도 구급활동 통계: https://www.nfa.go.kr/nfa/releaseinformation/statisticalinformation/main/?boardId=bbs_0000000000000019&cntId=71&mode=view
- 소방청 본부별 월별 구급활동정보: https://www.data.go.kr/data/15080046/fileData.do
- 행정안전부 주민등록 인구통계: https://jumin.mois.go.kr/etcStatOldAge.do
- 국립중앙의료원 전국 응급의료기관 정보(향후 상세화): https://www.data.go.kr/dataset/15000563/openapi.do
