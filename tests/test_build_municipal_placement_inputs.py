from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ambulance_sim.hospital_placement import load_municipal_placement_problem
from scripts.build_municipal_placement_inputs import build_municipality, load_parameters, main
from scripts.collect_kakao_routes import read_rows


CODE = "41110"
DEMAND = [f"{CODE}::demand::4111156000", f"{CODE}::demand::4111156600"]
BASE = f"{CODE}::ambulance_base::001"
HOSPITAL = f"{CODE}::hospital::001"
CANDIDATES = [f"{CODE}::candidate::MOHW-1", f"{CODE}::candidate::MOHW-2"]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _parameters() -> dict:
    return {
        "schema_version": 1,
        "status": "provisional",
        "generated_at": "2026-09-10T00:00:00+09:00",
        "profiles": [
            {"name": "cardiac", "share": 0.4, "golden_minutes": 8, "decay_rate": 0.045, "scene_minutes": 12, "field_success": 0.0, "basis": "temporary"},
            {"name": "minor", "share": 0.6, "golden_minutes": 60, "decay_rate": 0.004, "scene_minutes": 8, "field_success": 0.08, "basis": "temporary"},
        ],
        "hourly_profile_weights": None,
        "hourly_demand_multipliers": None,
        "hospital": {
            "categories": ["지역응급의료기관"],
            "success_by_category": {"지역응급의료기관": {"cardiac": 0.78, "minor": 0.78}},
            "capabilities_by_category": {"지역응급의료기관": ["cardiac", "minor"]},
            "success_when_unavailable_by_profile": {"cardiac": 0.25, "minor": 0.25},
            "treatment_minutes": 75.0,
            "transfer_delay_minutes": 5.0,
            "basis": "temporary",
        },
        "candidate": {"category_assumption": "지역응급의료기관", "capacity_beds": 20, "treatment_minutes": 75.0, "cost": 1.0, "basis": "temporary"},
        "ambulance": {"restock_minutes": 8.0, "basis": "temporary"},
        "demand": {
            "method": "population_proportional_provincial_dispatches",
            "provincial_annual_dispatches": 36525,
            "year": 2023,
            "provincial_population_reference": "fixture",
            "daily_calls_by_municipality": None,
            "basis": "fixture",
        },
        "sources": [],
    }


def _processed(tmp_path: Path, *, simulation_ready: bool = True, drop_route: tuple[str, str] | None = None) -> Path:
    processed = tmp_path / "processed"
    folder = processed / CODE
    folder.mkdir(parents=True)
    _write_csv(processed / "municipality_summary.csv", [
        {"municipality_code": CODE, "municipality_name": "수원시", "population_202608": 1000},
        {"municipality_code": "41130", "municipality_name": "성남시", "population_202608": 3000},
    ])
    _write_csv(folder / "demand_population.csv", [
        {"administrative_code": "4111156000", "administrative_name": "파장동", "municipality_code": CODE, "population": 600, "latitude": 37.31, "longitude": 126.99, "coordinate_source": "proxy"},
        {"administrative_code": "4111156600", "administrative_name": "율천동", "municipality_code": CODE, "population": 400, "latitude": 37.29, "longitude": 126.97, "coordinate_source": "proxy"},
    ])
    _write_csv(folder / "ambulance_bases.csv", [
        {"base_name": "정자119안전센터", "ambulance_count": 2, "latitude": 37.297, "longitude": 126.995},
    ])
    _write_csv(folder / "existing_hospitals.csv", [{
        "hospital_name": "수원병원", "emergency_category": "지역응급의료기관", "municipality_code": CODE,
        "latitude": 37.292, "longitude": 126.996, "coordinate_source": "official", "emergency_room_beds": 13,
        "capacity_match_review_required": "False", "hira_encrypted_care_symbol": "HIRA-1", "egen_institution_code": "A1",
        "capacity_snapshot_period": "2026-06", "capacity_source": "hira", "capacity_semantics": "static",
        "egen_evaluation_source": "egen", "egen_evaluation_year": "2024",
    }])
    _write_csv(folder / "candidate_sites.csv", [
        {"candidate_id": "MOHW-1", "candidate_name": "권선구보건소", "municipality_code": CODE, "latitude": 37.258, "longitude": 126.971, "candidate_source": "https://www.data.go.kr/data/3072692/fileData.do", "land_feasibility_status": "requires_planning_review"},
        {"candidate_id": "MOHW-2", "candidate_name": "영통구보건소", "municipality_code": CODE, "latitude": 37.256, "longitude": 127.069, "candidate_source": "https://www.data.go.kr/data/3072692/fileData.do", "land_feasibility_status": "requires_planning_review"},
    ])
    nodes = DEMAND + [BASE, HOSPITAL] + CANDIDATES
    routes = [
        {"origin_id": origin, "destination_id": destination, "duration_minutes": 5.0 + index % 7}
        for index, (origin, destination) in enumerate((a, b) for a in nodes for b in nodes if a != b)
        if (origin, destination) != drop_route
    ]
    _write_csv(folder / "road_times.csv", routes)
    (folder / "road_times_manifest.json").write_text(json.dumps({
        "municipality_code": CODE, "pair_set": "simulation", "routing_provider": "Kakao Mobility Directions API",
        "routing_profile": "RECOMMEND", "required_pair_count": 21, "completed_pair_count": 21,
        "complete": True, "simulation_ready": simulation_ready, "updated_at": "2026-09-10T04:15:43+00:00",
    }), encoding="utf-8")
    (tmp_path / "parameters.json").write_text(json.dumps(_parameters(), ensure_ascii=False), encoding="utf-8")
    return processed


def _build(tmp_path: Path, processed: Path) -> Path:
    parameters_path = tmp_path / "parameters.json"
    return build_municipality(
        processed / CODE, read_rows(processed / "municipality_summary.csv"), load_parameters(parameters_path),
        parameters_path, tmp_path / "out", overwrite=False,
    )


def test_generated_inputs_load_through_placement_loader(tmp_path: Path) -> None:
    processed = _processed(tmp_path)
    arguments = ["--processed-dir", str(processed), "--parameters", str(tmp_path / "parameters.json"), "--output-root", str(tmp_path / "out")]
    assert main(arguments) == 0
    placement_path = tmp_path / "out" / f"{CODE}_suwon" / "placement.json"
    problem = load_municipal_placement_problem(placement_path)
    scenario = problem.scenario
    # 36525 dispatches / 365.25 days * 1000 / 4000 population = 25 calls per day
    assert scenario.villages[DEMAND[0]] == pytest.approx(25 * 0.6 / 24)
    assert scenario.villages[DEMAND[1]] == pytest.approx(25 * 0.4 / 24)
    assert set(scenario.ambulances) == {"A01-1", "A01-2"}
    assert scenario.hospitals["H01"].capacity == 13
    assert scenario.hospitals["H01"].success_by_profile == {"cardiac": 0.78, "minor": 0.78}
    assert scenario.standby_nodes == (BASE, HOSPITAL)
    assert scenario.network.positions[HOSPITAL] == (126.996, 37.292)
    assert scenario.hourly_profile_probabilities[23] == {"cardiac": 0.4, "minor": 0.6}
    assert [candidate.source_id for candidate in problem.candidates] == ["MOHW-1", "MOHW-2"]
    assert problem.candidates[0].success_when_available == 0.78
    assert problem.provenance["road_time_source"]["extracted_at"] == "2026-09-10T04:15:43+00:00"
    assert problem.provenance["demand_source"]["daily_calls_model_input"] == pytest.approx(25.0)
    raw = json.loads(placement_path.read_text(encoding="utf-8"))
    assert raw["candidate_hospitals"][0]["land_feasibility_status"] == "requires_planning_review"
    raw_scenario = json.loads((placement_path.parent / "scenario.json").read_text(encoding="utf-8"))
    assert raw_scenario["hospitals"]["H01"]["official_identifiers"]["hira_encrypted_care_symbol"] == "HIRA-1"
    assert main(arguments) == 1  # refuses to overwrite without --overwrite
    assert main(arguments + ["--overwrite"]) == 0


def test_missing_directional_route_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, drop_route=(BASE, DEMAND[0]))
    with pytest.raises(ValueError, match="required directional road routes are missing"):
        _build(tmp_path, processed)
    assert not (tmp_path / "out").exists()


def test_not_simulation_ready_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, simulation_ready=False)
    with pytest.raises(ValueError, match="not simulation_ready"):
        _build(tmp_path, processed)


def test_profile_shares_must_sum_to_one(tmp_path: Path) -> None:
    parameters = _parameters()
    parameters["profiles"][0]["share"] = 0.5
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(parameters, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="shares sum to"):
        load_parameters(path)
