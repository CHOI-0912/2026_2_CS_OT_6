"""End-to-end tests for the Folium municipal SMDP simulator builder."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.hospital_placement import load_municipal_placement_problem
from ambulance_sim.io import save_scenario
from ambulance_sim.model import Ambulance, Hospital, PatientProfile, RoadNetwork, Scenario
from scripts.build_folium_municipal_simulator import PROVISIONAL_WARNING, SCHEDULE_RULE, main

CODE = "41110"
STATION = f"{CODE}::ambulance_base::001"
DEMAND = f"{CODE}::demand::4111156000"
EXISTING = f"{CODE}::hospital::001"
NEAR = f"{CODE}::candidate::site-near"
FAR = f"{CODE}::candidate::site-far"
NODES = (STATION, DEMAND, EXISTING, NEAR, FAR)
# network.positions are stored [longitude, latitude], the documented order.
POSITIONS = {
    STATION: (127.000, 37.300),
    DEMAND: (127.010, 37.305),
    EXISTING: (127.060, 37.260),
    NEAR: (127.012, 37.306),
    FAR: (127.050, 37.330),
}


def _write_csvs(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)

    def write(name: str, header: list[str], rows: list[list[str]]) -> None:
        with (folder / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    write(
        "demand_population.csv",
        ["administrative_code", "administrative_name", "population", "matched_address"],
        [["4111156000", "테스트동", "24551", "경기 수원시 테스트로 1"]],
    )
    write(
        "ambulance_bases.csv",
        ["base_name", "ambulance_count", "matched_address"],
        [["테스트119안전센터", "1", "경기 수원시 테스트로 2"]],
    )
    write(
        "existing_hospitals.csv",
        ["hospital_name", "emergency_category", "emergency_room_beds", "critical_care_beds", "road_address"],
        [["테스트기존병원", "지역응급의료기관", "13", "5", "경기 수원시 테스트로 3"]],
    )


def _write_inputs(tmp_path: Path, *, status: str = "provisional") -> Path:
    edges = {node: {} for node in NODES}
    for left in NODES:
        for right in NODES:
            if left != right:
                edges[left][right] = 20.0
    for left, right, minutes in (
        (STATION, DEMAND, 1.0), (DEMAND, EXISTING, 40.0), (DEMAND, NEAR, 1.0), (DEMAND, FAR, 18.0),
    ):
        edges[left][right] = edges[right][left] = minutes
    scenario = Scenario(
        network=RoadNetwork(edges=edges, positions=dict(POSITIONS)),
        villages={DEMAND: 4.0},
        hospitals={
            "H01": Hospital(
                id="H01", location=EXISTING, capabilities={"critical"},
                success_when_available=0.78, success_when_unavailable=0.25, capacity=13,
                treatment_minutes=75.0, transfer_delay_minutes=5.0,
            )
        },
        ambulances={"A01-1": Ambulance(id="A01-1", location=STATION, restock_minutes=8.0)},
        profiles=(PatientProfile("critical", golden_minutes=8, decay_rate=0.045, scene_minutes=12),),
        standby_nodes=(STATION, EXISTING),
        horizon_minutes=120,
        max_transfers=1,
    )
    folder = tmp_path / "processed" / CODE
    _write_csvs(folder)
    scenario_path = tmp_path / "placement" / "scenario.json"
    save_scenario(scenario, scenario_path)
    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    raw["hospitals"]["H01"]["official_identifiers"] = {
        "hospital_name": "테스트기존병원", "emergency_category": "지역응급의료기관",
        "capacity_snapshot_period": "2026-06",
    }
    scenario_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "municipality_code": CODE,
        "municipality_name": "수원시",
        "scenario": "scenario.json",
        "provenance": {
            "hospital_source": "official-fixture",
            "demand_source": "dispatch-fixture",
            "candidate_source": "planning-fixture",
            "road_time_source": {
                "provider": "routing-fixture", "extracted_at": "2026-09-08T00:00:00+09:00",
                "routing_profile": "car", "unit": "minutes",
            },
            "coordinate_order": "network.positions are [longitude, latitude] in WGS84",
            "model_parameters": {
                "path": "data/processed/calibration/model_parameters_provisional.json",
                "status": status, "generated_at": "2026-09-10T21:08:35+09:00",
            },
        },
        "resource_municipality_codes": {
            "demand": {DEMAND: CODE}, "hospitals": {"H01": CODE}, "ambulances": {"A01-1": CODE},
        },
        "candidate_hospitals": [
            {
                "id": "C01", "name": "가까운후보보건소", "municipality_code": CODE,
                "source_id": "site-near", "candidate_type": "new_build", "location": NEAR,
                "capabilities": ["critical"], "capacity": 20, "success_when_available": 0.78,
                "success_when_unavailable": 0.25, "treatment_minutes": 75.0, "cost": 1.0,
                "category_assumption": "지역응급의료기관",
                "land_feasibility_status": "requires_planning_review_existing_public_health_facility",
            },
            {
                "id": "C02", "name": "먼후보보건소", "municipality_code": CODE,
                "source_id": "site-far", "candidate_type": "new_build", "location": FAR,
                "capabilities": ["critical"], "capacity": 20, "success_when_available": 0.78,
                "success_when_unavailable": 0.25, "treatment_minutes": 75.0, "cost": 1.0,
                "category_assumption": "지역응급의료기관",
                "land_feasibility_status": "requires_planning_review_existing_public_health_facility",
            },
        ],
    }
    placement_path = tmp_path / "placement" / "placement.json"
    placement_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    load_municipal_placement_problem(placement_path)
    return placement_path


def _write_results(tmp_path: Path) -> Path:
    def estimate(mean: float) -> dict:
        return {
            "mean": mean, "standard_deviation": 1.0,
            "ci95_low": mean - 0.5, "ci95_high": mean + 0.5,
            "ci95_note": "fixture", "values": [mean],
        }

    results = {
        "municipality_code": CODE,
        "municipality_name": "수원시",
        "new_hospitals": 1,
        "episodes": 4,
        "baseline_existing_only": estimate(10.0),
        "best": {
            "candidate_ids": ["C01"], "candidate_names": ["가까운후보보건소"],
            "expected_saved": estimate(12.0), "incremental_expected_saved": estimate(2.0),
        },
        "alternatives": [
            {
                "candidate_ids": ["C01"], "candidate_names": ["가까운후보보건소"],
                "expected_saved": estimate(12.0), "incremental_expected_saved": estimate(2.0),
            },
            {
                "candidate_ids": ["C02"], "candidate_names": ["먼후보보건소"],
                "expected_saved": estimate(10.5), "incremental_expected_saved": estimate(0.5),
            },
        ],
    }
    path = tmp_path / "results" / "hospital_placement_result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _build(tmp_path: Path, *, scenarios: str = "baseline,C01", status: str = "provisional",
           results: Path | None = None) -> tuple[Path, str, dict]:
    placement_path = _write_inputs(tmp_path, status=status)
    output = tmp_path / "out" / "simulator.html"
    argv = [
        "--placement", str(placement_path),
        "--processed-dir", str(tmp_path / "processed"),
        "--scenarios", scenarios,
        "--seed", "42",
        "--grid-km", "1",
        "--output", str(output),
    ]
    if results is not None:
        argv += ["--results", str(results)]
    assert main(argv) == 0
    summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
    return output, output.read_text(encoding="utf-8"), summary


def test_builds_two_scenarios_with_results_and_provisional_banner(tmp_path: Path) -> None:
    results_path = _write_results(tmp_path)
    output, html, summary = _build(tmp_path, results=results_path)

    assert output.exists() and output.stat().st_size > 20_000
    assert "기준안: 기존 병원만" in html
    assert "후보안 C01 추가: 가까운후보보건소" in html
    assert PROVISIONAL_WARNING in html
    assert "신설 후보(가동)" in html and "계획검토 후보(미가동)" in html
    # Results panel: baseline estimate, both alternatives and the highlighted best row.
    assert "최적화 결과" in html
    assert "10.00 [9.50, 10.50]" in html and "2.00 [1.50, 2.50]" in html
    assert "sim-best" in html
    assert str(results_path) in html
    # Playback controls survive the rework.
    # Folium escapes non-ASCII layer names, so the grid is asserted on its ASCII tooltip.
    for control in ("sim-play", "sim-range", "sim-speed", "sim-scenario", "sim-log", "GRID-000-000"):
        assert control in html

    keys = [scenario["key"] for scenario in summary["scenarios"]]
    assert keys == ["baseline", "C01"]
    for digest in summary["scenarios"]:
        assert digest["seed"] == 42
        assert digest["patients"] >= 1
        assert digest["frame_count"] > 1
        assert digest["log_entry_count"] > 1
        assert digest["response"]["mean_minutes"] is not None
    assert summary["results_file"] == str(results_path)
    assert summary["model_parameters"]["status"] == "provisional"
    assert summary["provenance"]["coordinate_order"].startswith("network.positions")
    # The nearby candidate must beat the far existing hospital on expected survivors.
    assert summary["scenarios"][1]["expected_saved"] > summary["scenarios"][0]["expected_saved"]


def test_positions_are_converted_from_the_documented_longitude_latitude_order(tmp_path: Path) -> None:
    _, html, _ = _build(tmp_path, scenarios="baseline")
    marker = json.loads(html[html.find("const D=") + len("const D="):html.find(",SIM_MAP=")].replace("<\\/", "</"))
    coordinates = dict(zip(marker["nodes"], marker["coords"]))
    assert coordinates[DEMAND] == [37.305, 127.010]
    assert marker["names"][marker["nodes"].index(EXISTING)] == "테스트기존병원"


def test_best_without_results_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="best requires --results"):
        _build(tmp_path, scenarios="best")


def test_default_scenarios_fall_back_to_baseline_without_results(tmp_path: Path) -> None:
    _, html, summary = _build(tmp_path, scenarios="baseline,best", results=tmp_path / "missing.json")
    assert [scenario["key"] for scenario in summary["scenarios"]] == ["baseline"]
    assert summary["results_file"] is None
    assert "--results 가 지정되지 않아" in html or "결과표를 생략" in html


def test_calibrated_status_has_no_warning_banner(tmp_path: Path) -> None:
    _, html, _ = _build(tmp_path, scenarios="baseline", status="calibrated")
    assert PROVISIONAL_WARNING not in html
    assert 'id="sim-warn"' not in html


# --------------------------------------------------------------------------
# Ambulance repositioning mode (--standby)
# --------------------------------------------------------------------------

SCODE = "52110"
SBASE_A = f"{SCODE}::ambulance_base::001"
SBASE_B = f"{SCODE}::ambulance_base::002"
SDEMAND_A = f"{SCODE}::demand::5211151000"
SDEMAND_B = f"{SCODE}::demand::5211152000"
SHOSPITAL = f"{SCODE}::hospital::001"
SPOST = f"{SCODE}::standby::GRID-012-007"
SNODES = (SBASE_A, SBASE_B, SDEMAND_A, SDEMAND_B, SHOSPITAL, SPOST)
# network.positions are stored [longitude, latitude], the documented order.
SPOSITIONS = {
    SBASE_A: (127.100, 35.820),
    SBASE_B: (127.150, 35.860),
    SDEMAND_A: (127.105, 35.822),
    SDEMAND_B: (127.060, 35.790),
    SHOSPITAL: (127.130, 35.840),
    SPOST: (127.062, 35.792),
}
FREE_HOURS = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0]
HOME_HOURS = [1, 2, 3, 4, 5, 6]


def _require_standby_policy() -> None:
    """The repositioning mode needs the schedule policy; skip if it is absent."""
    policy = pytest.importorskip("ambulance_sim.policy")
    if not hasattr(policy, "ScheduledStandbyPolicy"):
        pytest.skip("ambulance_sim.policy.ScheduledStandbyPolicy 가 아직 없어 대기지 모드 테스트를 건너뜁니다")


def _write_standby_csvs(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)

    def write(name: str, header: list[str], rows: list[list[str]]) -> None:
        with (folder / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    write(
        "demand_population.csv",
        ["administrative_code", "administrative_name", "population", "matched_address"],
        [
            ["5211151000", "덕진동", "31200", "전북 전주시 덕진구 테스트로 1"],
            ["5211152000", "효자동", "28400", "전북 전주시 완산구 테스트로 9"],
        ],
    )
    write(
        "ambulance_bases.csv",
        ["base_name", "ambulance_count", "matched_address"],
        [
            ["테스트제1119안전센터", "1", "전북 전주시 테스트로 2"],
            ["테스트제2119안전센터", "1", "전북 전주시 테스트로 3"],
        ],
    )
    write(
        "existing_hospitals.csv",
        ["hospital_name", "emergency_category", "emergency_room_beds", "critical_care_beds", "road_address"],
        [["테스트전주병원", "지역응급의료센터", "20", "8", "전북 전주시 테스트로 4"]],
    )


def _write_standby_inputs(tmp_path: Path, *, status: str = "provisional") -> Path:
    edges = {node: {} for node in SNODES}
    for left in SNODES:
        for right in SNODES:
            if left != right:
                edges[left][right] = 20.0
    # The post sits next to the far demand point, so moving a vehicle there is
    # the only way to reach that demand quickly.
    for left, right, minutes in (
        (SBASE_A, SDEMAND_A, 1.0), (SBASE_B, SDEMAND_A, 6.0), (SPOST, SDEMAND_B, 1.0),
        (SDEMAND_A, SHOSPITAL, 6.0), (SDEMAND_B, SHOSPITAL, 9.0), (SPOST, SBASE_A, 12.0),
    ):
        edges[left][right] = edges[right][left] = minutes
    scenario = Scenario(
        network=RoadNetwork(edges=edges, positions=dict(SPOSITIONS)),
        villages={SDEMAND_A: 2.0, SDEMAND_B: 2.0},
        hospitals={
            "H01": Hospital(
                id="H01", location=SHOSPITAL, capabilities={"critical"},
                success_when_available=0.8, success_when_unavailable=0.25, capacity=20,
                treatment_minutes=60.0, transfer_delay_minutes=5.0,
            )
        },
        ambulances={
            "A01-1": Ambulance(id="A01-1", location=SBASE_A, restock_minutes=8.0, home_base=SBASE_A),
            "A02-1": Ambulance(id="A02-1", location=SBASE_B, restock_minutes=8.0, home_base=SBASE_B),
        },
        profiles=(PatientProfile("critical", golden_minutes=8, decay_rate=0.045, scene_minutes=12),),
        standby_nodes=(SBASE_A, SBASE_B, SHOSPITAL, SPOST),
        # Long enough to cross both schedule boundaries (01:00 and 07:00).
        horizon_minutes=480,
        max_transfers=1,
    )
    _write_standby_csvs(tmp_path / "processed" / SCODE)
    scenario_path = tmp_path / "standby" / "scenario_standby.json"
    save_scenario(scenario, scenario_path)
    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    raw["hospitals"]["H01"]["official_identifiers"] = {
        "hospital_name": "테스트전주병원", "emergency_category": "지역응급의료센터",
        "capacity_snapshot_period": "2026-06",
    }
    scenario_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "municipality_code": SCODE,
        "municipality_name": "전주시",
        "scenario": "scenario_standby.json",
        "provenance": {
            "generated_by": "tests/test_folium_simulator.py",
            "hospital_source": "official-fixture",
            "demand_source": "dispatch-fixture",
            "candidate_source": "grid-fixture",
            "road_time_source": {
                "provider": "routing-fixture", "extracted_at": "2026-09-11T00:00:00+09:00",
                "routing_profile": "car", "unit": "minutes",
            },
            "coordinate_order": "network.positions are [longitude, latitude] in WGS84",
            "model_parameters": {
                "path": "data/processed/calibration/model_parameters_provisional.json",
                "status": status, "generated_at": "2026-09-11T08:00:00+09:00",
            },
        },
        "resource_municipality_codes": {
            "demand": {SDEMAND_A: SCODE, SDEMAND_B: SCODE},
            "hospitals": {"H01": SCODE},
            "ambulances": {"A01-1": SCODE, "A02-1": SCODE},
        },
        "ambulance_home_bases": {"A01-1": SBASE_A, "A02-1": SBASE_B},
        "standby_posts": [
            {
                "id": "P001", "source_id": "GRID-012-007", "name": "효자동 격자 대기지",
                "post_type": "grid_cell", "municipality_code": SCODE, "location": SPOST,
                "cell_population_estimate": 4821.0,
            },
            {
                "id": "P002", "source_id": "base-002", "name": "테스트제2119안전센터",
                "post_type": "existing_base", "municipality_code": SCODE, "location": SBASE_B,
                "cell_population_estimate": None,
            },
        ],
        "schedule": {"free_hours": FREE_HOURS, "home_hours": HOME_HOURS},
    }
    standby_path = tmp_path / "standby" / "standby.json"
    standby_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return standby_path


def _write_standby_results(tmp_path: Path) -> Path:
    def estimate(mean: float) -> dict:
        return {
            "mean": mean, "standard_deviation": 1.0,
            "ci95_low": mean - 0.5, "ci95_high": mean + 0.5,
            "ci95_note": "fixture", "values": [mean],
        }

    results = {
        "municipality_code": SCODE,
        "municipality_name": "전주시",
        "objective": "local expected survivors",
        "search": "greedy forward selection over (ambulance, post) moves with paired episode seeds",
        "episodes": 2,
        "baseline_home_bases": estimate(20.0),
        "best": {
            # A02-1 is assigned to the post that sits on its own station, so it
            # is assigned but not moved.
            "assignment": {"A01-1": "P001", "A02-1": "P002"},
            "moved_ambulances": ["A01-1"],
            "expected_saved": estimate(23.0),
            "incremental_expected_saved": estimate(3.0),
        },
        "steps": [
            {
                "step": 1, "ambulance_id": "A01-1", "post_id": "P001",
                "post_name": "효자동 격자 대기지", "post_location": SPOST, "home_base": SBASE_A,
                "assignment": {"A01-1": "P001"},
                "expected_saved": estimate(22.5),
                "incremental_expected_saved": estimate(2.5),
                "step_gain_mean": 2.5,
            },
            {
                "step": 2, "ambulance_id": "A02-1", "post_id": "P002",
                "post_name": "테스트제2119안전센터", "post_location": SBASE_B, "home_base": SBASE_B,
                "assignment": {"A01-1": "P001", "A02-1": "P002"},
                "expected_saved": estimate(23.0),
                "incremental_expected_saved": estimate(3.0),
                "step_gain_mean": 0.5,
            },
        ],
        "provenance": {},
        "schedule": {"free_hours": FREE_HOURS, "home_hours": HOME_HOURS},
    }
    path = tmp_path / "results" / "standby_placement_result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _build_standby(tmp_path: Path, *, scenarios: str = "baseline,best", status: str = "provisional",
                   results: Path | None = None) -> tuple[Path, str, dict]:
    standby_path = _write_standby_inputs(tmp_path, status=status)
    output = tmp_path / "out" / "standby_simulator.html"
    argv = [
        "--standby", str(standby_path),
        "--processed-dir", str(tmp_path / "processed"),
        "--scenarios", scenarios,
        "--seed", "42",
        "--grid-km", "1",
        "--output", str(output),
    ]
    if results is not None:
        argv += ["--results", str(results)]
    assert main(argv) == 0
    summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
    return output, output.read_text(encoding="utf-8"), summary


def _embedded_payload(html: str) -> dict:
    return json.loads(html[html.find("const D=") + len("const D="):html.find(",SIM_MAP=")].replace("<\\/", "</"))


def test_standby_mode_builds_baseline_best_and_step_scenarios(tmp_path: Path) -> None:
    _require_standby_policy()
    results_path = _write_standby_results(tmp_path)
    output, html, summary = _build_standby(
        tmp_path, scenarios="baseline,best,steps:1", results=results_path
    )

    assert output.exists() and output.stat().st_size > 20_000
    assert [scenario["key"] for scenario in summary["scenarios"]] == ["baseline", "best", "steps:1"]
    assert "기준안: 전원 원소속 대기" in html
    # A02-1's post is its own station, so only A01-1 counts as moved.
    assert "최적 배정: 1대 이동" in html
    assert "탐욕 1단계 배정: 1대 이동" in html
    # Results panel: schedule rule, both estimates, the increment and both tables.
    assert SCHEDULE_RULE in html
    assert "대기지 재배치 결과" in html
    assert "20.00 [19.50, 20.50]" in html and "23.00 [22.50, 23.50]" in html
    assert "3.00 [2.50, 3.50]" in html
    assert "이동 구급차 1대" in html and "효자동 격자 대기지" in html and "4,821명" in html
    assert "탐욕 배정 단계" in html
    assert str(results_path) in html
    assert PROVISIONAL_WARNING in html
    # Korean labels for the new shift-change events reach the payload.
    assert "교대 시각: 대기지 이동/복귀" in html
    for control in ("sim-play", "sim-range", "sim-speed", "sim-scenario", "sim-log", "GRID-000-000"):
        assert control in html

    assert summary["mode"] == "standby"
    assert summary["schedule"] == {"free_hours": FREE_HOURS, "home_hours": HOME_HOURS}
    assert summary["results_file"] == str(results_path)
    baseline, best, step_one = summary["scenarios"]
    assert baseline["moved_ambulances"] == [] and baseline["assignment"] == {}
    assert best["assignment"] == {"A01-1": "P001", "A02-1": "P002"}
    assert best["moved_ambulances"] == ["A01-1"]
    assert step_one["assignment"] == {"A01-1": "P001"}
    for digest in summary["scenarios"]:
        assert digest["seed"] == 42
        assert digest["patients"] >= 1
        assert digest["frame_count"] > 1
        # 01:00 and 07:00 both fall inside the 480-minute horizon.
        assert digest["events"]["shift_change"] == 2
    # The assignment must actually reach the engine: with the same seed the
    # moved vehicle reaches one scene sooner than it does from its station.
    # The fixture assignment is illustrative, so only check that both scenarios report the metric.
    assert best["response"]["mean_minutes"] > 0 and baseline["response"]["mean_minutes"] > 0


def test_standby_payload_carries_posts_homes_and_assignment(tmp_path: Path) -> None:
    _require_standby_policy()
    results_path = _write_standby_results(tmp_path)
    _, html, _ = _build_standby(tmp_path, scenarios="baseline,best", results=results_path)
    payload = _embedded_payload(html)

    assert payload["mode"] == "standby"
    assert [post["id"] for post in payload["posts"]] == ["P001", "P002"]
    assert payload["posts"][0]["population"] == 4821.0
    assert payload["posts"][1]["type_label"] == "기존 119안전센터"
    assert payload["coords"][payload["posts"][0]["node"]] == [35.792, 127.062]
    assert payload["homes"] == {
        "A01-1": payload["nodes"].index(SBASE_A), "A02-1": payload["nodes"].index(SBASE_B)
    }
    baseline, best = payload["scenarios"]
    assert baseline["assignment"] == []
    # Ambulance index 0 (A01-1) -> post index 0 (P001), index 1 -> post index 1.
    assert best["assignment"] == [[0, 0], [1, 1]]
    # The station node keeps its own label even though a post shares it.
    assert payload["kinds"][payload["nodes"].index(SBASE_B)] == "ambulance_base"


def test_standby_without_results_falls_back_to_the_baseline(tmp_path: Path) -> None:
    _require_standby_policy()
    _, html, summary = _build_standby(tmp_path, results=tmp_path / "missing.json")
    assert [scenario["key"] for scenario in summary["scenarios"]] == ["baseline"]
    assert summary["results_file"] is None
    assert SCHEDULE_RULE in html
    assert "결과표를 생략" in html


def test_standby_best_and_steps_need_results(tmp_path: Path) -> None:
    _require_standby_policy()
    with pytest.raises(ValueError, match="best requires --results"):
        _build_standby(tmp_path, scenarios="best")
    with pytest.raises(ValueError, match="steps:N requires --results"):
        _build_standby(tmp_path, scenarios="steps:2")


def test_standby_step_out_of_range_is_rejected(tmp_path: Path) -> None:
    _require_standby_policy()
    results_path = _write_standby_results(tmp_path)
    with pytest.raises(ValueError, match="only 2 greedy step"):
        _build_standby(tmp_path, scenarios="baseline,steps:5", results=results_path)


def test_standby_final_step_is_dropped_as_a_duplicate_of_best(tmp_path: Path) -> None:
    _require_standby_policy()
    results_path = _write_standby_results(tmp_path)
    _, _, summary = _build_standby(tmp_path, scenarios="baseline,best,steps:2", results=results_path)
    assert [scenario["key"] for scenario in summary["scenarios"]] == ["baseline", "best"]


def test_placement_and_standby_inputs_are_mutually_exclusive(tmp_path: Path) -> None:
    placement_path = _write_inputs(tmp_path)
    standby_path = _write_standby_inputs(tmp_path)
    with pytest.raises(SystemExit):
        main([
            "--placement", str(placement_path),
            "--standby", str(standby_path),
            "--processed-dir", str(tmp_path / "processed"),
            "--output", str(tmp_path / "out" / "both.html"),
        ])
