# 합성 예제 결과 (실제 분석 결과 아님)

이 폴더의 결과는 모두 `examples/synthetic_scenario.json` 또는 내장 합성 시나리오로 만든 코드 검증용 산출물입니다. 경기도 실제 데이터와 무관하며, 병원 입지나 구급차 배치 결론으로 인용하면 안 됩니다.

| 폴더/파일 | 실험 내용 | 생성일 |
|---|---|---|
| `baseline_100/` | 합성 시나리오에서 정책 3종(greedy / no-reposition / nearest) 비교, 100 episode, base seed 1000 | 2026-09-08 |
| `placement_100/` | 합성 시나리오에서 구급차 A1 상시대기 위치 후보 4곳 비교, 100 episode, base seed 2000 | 2026-09-08 |
| `simulation.html` | 합성 시나리오 단일 실행 재생 화면 | 2026-09-08 |

`baseline_100`과 `placement_100`은 서로 다른 실험이며 seed도 다르므로 "배치 전/후" 비교로 읽으면 안 됩니다. 설명 문서는 `legacy/docs/baseline_results_ko.md`, `legacy/docs/ambulance_standby_results_ko.md`에 있습니다.

실제 데이터 기반 산출물은 상위 `outputs/` 폴더의 `suwon_*`, `gyeonggi_real_*` 파일입니다.
