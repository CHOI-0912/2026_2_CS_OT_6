from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ambulance_sim.standby_placement import load_municipal_standby_problem
from scripts.build_municipal_standby_inputs import build_municipality, main
from scripts.collect_kakao_routes import read_rows


CODE = "41110"
DEMAND = [f"{CODE}::demand::4111156000", f"{CODE}::demand::4111156600"]
BASE = f"{CODE}::ambulance_base::001"
HOSPITAL = f"{CODE}::hospital::001"
POSTS = [f"{CODE}::standby::GRID-1KM-{CODE}-001-000", f"{CODE}::standby::GRID-1KM-{CODE}-002-000"]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _parameters() -> dict:
    return {
        "schema_version": 1,
        "status": "provisional",
        "generated_at": "2026-09-11T00:00:00+09:00",
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


def _standby_rows(unroutable: bool = False) -> list[dict[str, object]]:
    return [
        {
            "candidate_id": f"GRID-1KM-{CODE}-00{index}-000",
            "candidate_name": f"경기도 수원시 장안구 파장동 격자 {index}-0",
            "municipality_code": CODE,
            "latitude": latitude,
            "longitude": longitude,
            "candidate_source": "population-weighted 1 km grid standby post; Kakao coord2regioncode membership",
            "land_feasibility_status": "requires_review_standby_post_unverified",
            "routing_status": "unroutable_no_nearby_road" if unroutable and index == 2 else "probe_routable",
            "cell_population_estimate": "333.3",
        }
        for index, (latitude, longitude) in enumerate([(37.258, 126.971), (37.256, 127.069)], start=1)
    ]


def _processed(
    tmp_path: Path,
    *,
    standby_ready: bool = True,
    drop_route: tuple[str, str] | None = None,
    manifest: bool = True,
    unroutable_post: bool = False,
    municipality_name: str = "수원시",
) -> Path:
    processed = tmp_path / "processed"
    folder = processed / CODE
    folder.mkdir(parents=True)
    _write_csv(processed / "municipality_summary.csv", [
        {"municipality_code": CODE, "municipality_name": municipality_name, "population_202608": 1000},
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
    _write_csv(folder / "standby_candidates.csv", _standby_rows(unroutable_post))
    nodes = DEMAND + [BASE, HOSPITAL] + POSTS
    _write_csv(folder / "road_times.csv", [
        {"origin_id": origin, "destination_id": destination, "duration_minutes": 5.0 + index % 7}
        for index, (origin, destination) in enumerate((a, b) for a in nodes for b in nodes if a != b)
        if (origin, destination) != drop_route
    ])
    if manifest:
        (folder / "road_times_standby_manifest.json").write_text(json.dumps({
            "municipality_code": CODE, "pair_set": "standby", "routing_provider": "Kakao Mobility Directions API",
            "routing_profile": "RECOMMEND", "required_pair_count": 16, "completed_pair_count": 16,
            "complete": True, "simulation_ready": False, "standby_ready": standby_ready,
            "updated_at": "2026-09-11T04:15:43+00:00",
        }), encoding="utf-8")
    (tmp_path / "parameters.json").write_text(json.dumps(_parameters(), ensure_ascii=False), encoding="utf-8")
    return processed


def _build(tmp_path: Path, processed: Path, *, slug: str | None = None) -> Path:
    from scripts.build_municipal_standby_inputs import load_parameters

    parameters_path = tmp_path / "parameters.json"
    return build_municipality(
        processed / CODE, read_rows(processed / "municipality_summary.csv"), load_parameters(parameters_path),
        parameters_path, tmp_path / "out", slug=slug, overwrite=False,
    )


def test_generated_inputs_load_through_the_standby_loader(tmp_path: Path) -> None:
    processed = _processed(tmp_path)
    arguments = ["--processed-dir", str(processed), "--parameters", str(tmp_path / "parameters.json"), "--output-root", str(tmp_path / "out")]
    assert main(arguments) == 0
    standby_path = tmp_path / "out" / f"{CODE}_suwon" / "standby.json"
    problem = load_municipal_standby_problem(standby_path)
    scenario = problem.scenario

    assert problem.scenario_path.name == "scenario_standby.json"
    assert [(post.id, post.post_type, post.location) for post in problem.posts] == [
        ("P001", "grid_cell", POSTS[0]), ("P002", "grid_cell", POSTS[1]), ("P003", "existing_base", BASE),
    ]
    assert problem.posts[0].cell_population_estimate == pytest.approx(333.3)
    assert problem.posts[2].cell_population_estimate is None
    assert problem.ambulance_home_bases == {"A01-1": BASE, "A01-2": BASE}
    assert problem.free_hours == (0, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23)
    assert problem.home_hours == (1, 2, 3, 4, 5, 6)
    assert scenario.standby_nodes == (BASE, HOSPITAL, POSTS[0], POSTS[1])
    for ambulance in scenario.ambulances.values():
        assert (ambulance.location, ambulance.home_base, ambulance.assigned_post) == (BASE, BASE, None)
    # 36525 dispatches / 365.25 days * 1000 / 4000 population = 25 calls per day
    assert scenario.villages[DEMAND[0]] == pytest.approx(25 * 0.6 / 24)
    assert scenario.hospitals["H01"].capacity == 13
    assert scenario.network.positions[POSTS[0]] == (126.971, 37.258)
    assert scenario.horizon_minutes == 1440 + 360
    assert problem.provenance["road_time_source"]["extracted_at"] == "2026-09-11T04:15:43+00:00"
    assert problem.provenance["candidate_source"]["existing_base_post_count"] == 1
    assert problem.provenance["generated_by"]["script"] == "scripts/build_municipal_standby_inputs.py"

    raw = json.loads(standby_path.read_text(encoding="utf-8"))
    assert raw["schedule"]["free_hours"][0] == 0 and raw["schedule"]["home_hours"] == [1, 2, 3, 4, 5, 6]
    assert raw["resource_municipality_codes"]["standby_posts"] == {"P001": CODE, "P002": CODE, "P003": CODE}
    assert main(arguments) == 1  # refuses to overwrite without --overwrite
    assert main(arguments + ["--overwrite"]) == 0


def test_missing_contract_route_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, drop_route=(HOSPITAL, POSTS[1]))
    with pytest.raises(ValueError, match="required directional road routes are missing"):
        _build(tmp_path, processed)
    assert not (tmp_path / "out").exists()


def test_missing_demand_to_hospital_route_fails(tmp_path: Path) -> None:
    # Not part of the standby pair set, but every episode transports a patient.
    processed = _processed(tmp_path, drop_route=(DEMAND[0], HOSPITAL))
    with pytest.raises(ValueError, match="required directional road routes are missing"):
        _build(tmp_path, processed)


def test_manifest_that_is_not_standby_ready_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, standby_ready=False)
    with pytest.raises(ValueError, match="not standby_ready"):
        _build(tmp_path, processed)


def test_missing_standby_manifest_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, manifest=False)
    with pytest.raises(ValueError, match="road_times_standby_manifest.json is missing"):
        _build(tmp_path, processed)


def test_unroutable_post_still_listed_fails(tmp_path: Path) -> None:
    processed = _processed(tmp_path, unroutable_post=True)
    with pytest.raises(ValueError, match="probe_candidate_routability"):
        _build(tmp_path, processed)


def test_unknown_municipality_needs_an_explicit_slug(tmp_path: Path) -> None:
    processed = _processed(tmp_path, municipality_name="전주시")
    with pytest.raises(ValueError, match="--slug"):
        _build(tmp_path, processed)
    standby_path = _build(tmp_path, processed, slug="jeonju")
    assert standby_path.parent.name == f"{CODE}_jeonju"
    assert json.loads(standby_path.read_text(encoding="utf-8"))["municipality_name"] == "전주시"
