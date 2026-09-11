# 국내 공개데이터 연동 로드맵

조사 기준일은 2026-09-07이다. 1단계는 공개 집계자료와 논문 기반 계수로 시뮬레이터를 보정하고, 2단계에서 환자 단위 비식별 원자료를 신청하는 구성을 권장한다.

## 권장 결합 순서

1. [소방청 본부별 구급활동정보](https://www.data.go.kr/data/15080046/fileData.do?recommendDataYn=Y)의 시도·월별 출동량으로 기본 수요율을 만든다.
2. [행정안전부 주민등록 인구통계](https://jumin.mois.go.kr/index.jsp) 또는 [KOSIS Open API](https://kosis.kr/serviceInfo/openAPIGuide.do)의 읍면동 인구·65세 이상 인구로 공간 수요를 배분한다.
3. [생활안전지도 소방시설 API](https://www.safemap.go.kr/opna/data/dataViewRenew.do?objtId=144)와 [소방청 구급차 보유 현황](https://www.data.go.kr/data/15113282/fileData.do?recommendDataYn=Y)으로 대기 후보지와 실제 운용 차량 수를 설정한다.
4. [국립중앙의료원 전국 응급의료기관 정보 API](https://www.data.go.kr/dataset/15000563/openapi.do)와 [실시간 병상정보](https://www.safetydata.go.kr/disaster-data/view?dataSn=227)로 병원 위치, 진료 역량, 병상 가용성을 설정한다.
5. [ITS 국가교통정보센터](https://www.its.go.kr/opendata/intro)의 표준노드·링크와 링크별 통행시간으로 시간대별 최단 이동시간을 계산한다. 정밀 연구에서는 [KTDB](https://ktdb.go.kr/) 자료 신청을 검토한다.
6. [소방청 119구급서비스 통계연보](https://www.nfa.go.kr/nfa/releaseinformation/statisticalinformation/main/?pageidx=4)로 시간대·요일·환자군·처치시간 분포를 보정한다.

## `Scenario` 매핑

| 외부 자료 | 현재 모델 필드 | 비고 |
|---|---|---|
| 읍면동별 추정 출동률 | `Scenario.villages` | 현재는 시간당 고정 Poisson 비율 |
| 표준노드·링크 통행시간 | `RoadNetwork.edges` | 분 단위 가중치, 현재는 정적 |
| 기관 위치·진료 가능 질환 | `Hospital.location`, `capabilities` | 좌표를 최근접 도로 노드에 매칭 |
| 가용병상·수용 가능 여부 | `Hospital.available` | 실시간 자료의 관측 시각도 보존 필요 |
| 운용 구급차 수·센터 위치 | `Scenario.ambulances` | 교대·정비 결원은 별도 모형 필요 |
| 환자군별 시간 효과 | `PatientProfile` | 임상 보정 전 값은 실험용 가정 |

공간 수요의 초기 추정식 예시는 다음과 같다.

```text
lambda(v, t) = 시도·월 기본수요
             * 읍면동 인구 가중치
             * 고령인구 보정
             * 시간대·요일 보정
```

고령인구에 단순 비례시키면 농촌 수요가 과대 추정될 수 있으므로 소방서별 실제 출동량으로 재보정해야 한다.

코드의 `ambulance_sim.calibration.allocate_hourly_demand()`는 월간 총출동량을 인구·고령비율·시간대 가중치로 나누면서, 24시간·월 전체 기대 발생량이 원래 총출동량과 정확히 일치하도록 정규화한다. `normalize_profile_counts()`는 통계연보의 환자 유형별 건수를 범주확률로 변환한다.

```python
from ambulance_sim.calibration import allocate_hourly_demand, normalize_profile_counts

hourly_rates = allocate_hourly_demand(
    monthly_calls=310,
    days_in_month=31,
    population={"마을A": 1200, "마을B": 800},
    elderly_share={"마을A": 0.22, "마을B": 0.38},
    elderly_effect=1.0,
    hourly_multipliers={8: 1.4, 18: 1.5},
)
profile_probability = normalize_profile_counts({"cardiac": 20, "stroke": 25, "trauma": 15, "minor": 40})
```

## 생존함수 보정 자료

- 심정지: [한국 도시지역 OHCA 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC5052837/), [한국 NEDIS 기반 등록 연구](https://pmc.ncbi.nlm.nih.gov/articles/PMC4278034/)
- 뇌졸중: [한국 Stroke Statistics](https://pmc.ncbi.nlm.nih.gov/articles/PMC3779666/), [2013–2023 급성 뇌졸중 질 평가 연구](https://pubmed.ncbi.nlm.nih.gov/42374978/)
- 중증외상: [한국 EMS 외상등록자료 연구](https://pubmed.ncbi.nlm.nih.gov/28489503/)

심정지는 ROSC와 퇴원 생존을 구분해야 한다. 뇌졸중은 재관류 가능 여부와 병원 도착 후 지연을 포함해야 한다. 외상은 중증도와 처치 때문에 생기는 역인과가 있어 모든 환자에 단순 지수감소를 일괄 적용하면 안 된다.

## 주요 제약

- 전국 공개 119 자료는 대체로 월·시도·소방서 단위 집계이며 환자별 좌표와 상세 시각은 공개되지 않는다.
- 실제 119 구급활동일지, NEDIS, KTDB 환자 단위 자료에는 연구계획, 비식별화, IRB 또는 기관 협약이 필요할 수 있다.
- 실시간 병상 값은 기관별 전송 시각이 달라 완전한 동시 스냅샷이 아니다.
- 공개 API는 인증키, 호출 제한, 좌표계, 공공누리 유형을 수집 단계에서 다시 확인해야 한다.
- 현재 코드의 합성 생존계수와 병원 성공률은 임상 의사결정용이 아니며 민감도 분석용 초기값이다.
