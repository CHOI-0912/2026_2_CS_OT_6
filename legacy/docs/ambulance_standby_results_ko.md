# 상시 대기 위치 후보 실험

## 연구 질문

후보 구급차 `A1`의 위치만 고정하고 나머지 구급차의 초기 위치를 확률적으로 표본화했을 때, 하루 전체 기대 생존자 수를 가장 크게 만드는 상시 대기 위치를 찾는다.

## 실행 조건

```powershell
python -m ambulance_sim `
  --scenario examples/synthetic_scenario.json `
  --placement-ambulance A1 `
  --episodes 100 --seed 2000 `
  --candidate "Station North" `
  --candidate "Station South" `
  --candidate "Central ER" `
  --candidate "Regional ER" `
  --background-location "Station North=0.5" `
  --background-location "Central ER=0.3" `
  --background-location "Regional ER=0.2" `
  --output-dir outputs/synthetic_examples/placement_100
```

- 후보차량: `A1`—각 후보에서 시작하고 매 재정비 후 같은 후보로 복귀
- 배경차량: 후보차량을 제외하고 0.50/0.30/0.20 위치분포에서 episode별 표본
- 공정 비교: 네 후보 모두 같은 episode seed와 같은 배경차량 위치 표본 사용
- 목표 지표: 24시간 기대 생존자 수

## 결과

| 후보 위치 | 하루 기대 생존자 | 95% CI |
|---|---:|---:|
| Station North | 14.757 | 14.334–15.181 |
| **Station South** | **15.198** | **14.778–15.618** |
| Central ER | 14.479 | 14.041–14.918 |
| Regional ER | 14.766 | 14.358–15.174 |

동일 episode끼리 비교한 `Station South - 다른 후보`의 paired difference:

| 비교 | 기대 생존자 차이 | 95% CI |
|---|---:|---:|
| South − North | +0.441 | +0.323–+0.559 |
| South − Central ER | +0.719 | +0.596–+0.842 |
| South − Regional ER | +0.432 | +0.327–+0.537 |

이 합성 시나리오에서는 `Station South`가 네 후보 중 최적이며, North 대비 하루 기대 생존자 수가 약 3.0% 높았다. 세 paired 구간 모두 0보다 크다.

이는 실제 지역의 최적 위치라는 결론이 아니다. 결과는 합성 도로·수요·병원 성공률에만 해당하며, 실제 자료로 JSON을 교체한 뒤 같은 명령을 다시 실행해야 한다. 특히 배경차량 위치분포 0.50/0.30/0.20은 조사자료로 추정하기 전의 실험 가정이다.

전체 episode 결과는 `outputs/synthetic_examples/placement_100/episode_results.csv`, 통계와 설정은 `outputs/synthetic_examples/placement_100/experiment_summary.json`에 있다.
