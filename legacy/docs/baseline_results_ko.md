# 합성 시나리오 기준 실험 결과

## 실행 조건

```powershell
python -m ambulance_sim --scenario examples/synthetic_scenario.json --compare --episodes 100 --seed 1000 --output-dir outputs/synthetic_examples/baseline_100
```

- episode: 24시간 × 100회
- 정책마다 동일한 seed 1000~1099 사용
- episode당 평균 환자 수: 46.84명
- 입력: `examples/synthetic_scenario.json`의 합성 수요·임상 계수
- 신뢰구간: episode 표본표준편차를 이용한 평균의 95% 정규근사 구간

## 결과

| 정책 | 하루 기대 생존자 | 95% CI | 환자당 기대 생존 | 평균 응답시간(분) | 평균 episode별 p90 응답시간(분) | 도달률 |
|---|---:|---:|---:|---:|---:|---:|
| greedy | 16.290 | 15.821–16.758 | 0.352 | 57.65 | 131.12 | 0.855 |
| no-reposition | 15.183 | 14.706–15.661 | 0.328 | 61.25 | 121.75 | 0.897 |
| nearest | 15.296 | 14.792–15.799 | 0.330 | 61.29 | 123.05 | 0.899 |

동일 수요를 사용한 paired difference에서:

- `greedy - no-reposition`: 하루 기대 생존자 **+1.106명**(반대 방향으로 저장된 95% CI -1.280~-0.933), 약 7.3% 증가
- `greedy - nearest`: 하루 기대 생존자 **+0.994명**(반대 방향으로 저장된 95% CI -1.174~-0.814), 약 6.5% 증가

두 구간 모두 0을 포함하지 않으므로 이 합성 설정에서는 greedy 정책의 기대 생존량이 높다.

## 해석

목적함수가 응답률이나 최대 응답시간이 아니라 기대 생존량이므로 `greedy`가 모든 운영지표에서 우월하지는 않다. 실제로 이 실행에서는 greedy의 도달률이 낮고 episode별 p90 응답시간 평균이 더 컸지만, 도달 환자의 평균 응답시간과 병원 경로 선택에서 얻은 생존 기여가 커 최종 reward가 높았다. 이는 단일 평균 응답시간만으로 정책을 평가하면 목적함수와 다른 결론이 나올 수 있음을 보여준다.

다만 현재 재배치 중인 구급차는 새 출동에 사용할 수 없고, 합성 수요·성공률을 사용한다. 실제 배치 결론으로 해석해서는 안 되며 다음 검증이 필요하다.

1. 지역별 실제 119 수요와 ITS 통행시간으로 입력 교체
2. NMC/NEDIS 자료로 환자 유형별 병원 성공률 보정
3. 재배치 중 우회 출동 허용 여부를 반영한 정책 비교
4. 병상·수요·생존함수 계수의 민감도 분석
5. 학습/보정 기간과 다른 기간·지역에서 외부 검증

원자료는 `outputs/synthetic_examples/baseline_100/episode_results.csv`, 전체 통계와 episode 기록은 `outputs/synthetic_examples/baseline_100/experiment_summary.json`에 있다.
