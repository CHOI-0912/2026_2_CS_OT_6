from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ambulance_sim.__main__ import main
from ambulance_sim.io import (
    ScenarioValidationError,
    load_scenario,
    scenario_from_dict,
    scenario_to_dict,
)
from ambulance_sim.model import Ambulance
from ambulance_sim.standby_placement import (
    evaluate_assignment,
    load_municipal_standby_problem,
    optimize_standby_placement,
)


CODE = "52110"
DEMAND_NEAR = f"{CODE}::demand::A"
DEMAND_FAR = f"{CODE}::demand::B"
BASE_ONE = f"{CODE}::ambulance_base::001"
BASE_TWO = f"{CODE}::ambulance_base::002"
HOSPITAL = f"{CODE}::hospital::001"
POST_NEAR = f"{CODE}::standby::G001"
POST_FAR = f"{CODE}::standby::G002"
NODES = (DEMAND_NEAR, DEMAND_FAR, BASE_ONE, BASE_TWO, HOSPITAL, POST_NEAR, POST_FAR)


def _scenario() -> dict[str, Any]:
    """Both stations sit next to DEMAND_NEAR; only POST_FAR covers DEMAND_FAR."""
    edges = {source: {target: 30.0 for target in NODES if target != source} for source in NODES}

    def link(left: str, right: str, minutes: float) -> None:
        edges[left][right] = minutes
        edges[right][left] = minutes

    for node in NODES:
        if node != HOSPITAL:
            link(node, HOSPITAL, 2.0)
    link(BASE_ONE, DEMAND_NEAR, 1.0)
    link(BASE_TWO, DEMAND_NEAR, 1.0)
    link(POST_NEAR, DEMAND_NEAR, 1.0)
    link(POST_FAR, DEMAND_FAR, 1.0)
    return {
        "network": {
            "edges": edges,
            "positions": {node: [127.10 + index / 100, 35.82] for index, node in enumerate(NODES)},
        },
        "villages": {DEMAND_NEAR: 0.5, DEMAND_FAR: 1.0},
        "hospitals": {
            "H001": {
                "location": HOSPITAL,
                "capabilities": ["critical"],
                "success_when_available": 1.0,
                "capacity": 100,
                "treatment_minutes": 0.0,
            }
        },
        "ambulances": {
            "A001": {"location": BASE_ONE, "home_base": BASE_ONE, "assigned_post": None, "restock_minutes": 0.0},
            "A002": {"location": BASE_TWO, "home_base": BASE_TWO, "assigned_post": None, "restock_minutes": 0.0},
        },
        "profiles": [{"name": "critical", "golden_minutes": 0.0, "decay_rate": 0.1, "scene_minutes": 0.0}],
        "standby_nodes": [BASE_ONE, BASE_TWO, HOSPITAL, POST_NEAR, POST_FAR],
        "horizon_minutes": 1440,
        "arrival_cutoff_minutes": 1200,
        "max_transfers": 0,
    }


def _manifest() -> dict[str, Any]:
    return {
        "municipality_code": CODE,
        "municipality_name": "전주시",
        "scenario": "scenario_standby.json",
        "provenance": {
            "hospital_source": "hospital-fixture",
            "demand_source": "dispatch-fixture",
            "candidate_source": "grid-fixture",
            "road_time_source": {
                "provider": "routing-fixture",
                "extracted_at": "2026-09-11T00:00:00+09:00",
                "routing_profile": "car",
                "unit": "minutes",
            },
            "model_parameters": "calibration-fixture",
            "generated_by": "tests/test_standby_placement.py",
        },
        "resource_municipality_codes": {
            "demand": {DEMAND_NEAR: CODE, DEMAND_FAR: CODE},
            "hospitals": {"H001": CODE},
            "ambulances": {"A001": CODE, "A002": CODE},
        },
        "ambulance_home_bases": {"A001": BASE_ONE, "A002": BASE_TWO},
        "standby_posts": [
            {
                "id": "P001", "source_id": "GRID-0001", "name": "격자 후보 1",
                "post_type": "grid_cell", "municipality_code": CODE,
                "location": POST_NEAR, "cell_population_estimate": 1200.0,
            },
            {
                "id": "P002", "source_id": "GRID-0002", "name": "격자 후보 2",
                "post_type": "grid_cell", "municipality_code": CODE,
                "location": POST_FAR, "cell_population_estimate": 3400.0,
            },
        ],
        "schedule": {
            "free_hours": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0],
            "home_hours": [1, 2, 3, 4, 5, 6],
        },
    }


def _write(tmp_path: Path, *, scenario: dict[str, Any] | None = None, manifest: dict[str, Any] | None = None) -> Path:
    scenario_path = tmp_path / "scenario_standby.json"
    scenario_path.write_text(json.dumps(scenario or _scenario(), ensure_ascii=False), encoding="utf-8")
    manifest_path = tmp_path / "standby.json"
    manifest_path.write_text(json.dumps(manifest or _manifest(), ensure_ascii=False), encoding="utf-8")
    return manifest_path


def test_strict_standby_fixture_loads(tmp_path: Path) -> None:
    problem = load_municipal_standby_problem(_write(tmp_path))
    assert problem.municipality_code == CODE
    assert [post.id for post in problem.posts] == ["P001", "P002"]
    assert problem.ambulance_home_bases == {"A001": BASE_ONE, "A002": BASE_TWO}
    assert problem.home_hours == (1, 2, 3, 4, 5, 6)
    assert problem.scenario.ambulances["A002"].home_base == BASE_TWO


def test_home_base_and_assigned_post_survive_a_json_round_trip(tmp_path: Path) -> None:
    _write(tmp_path)
    scenario = load_scenario(tmp_path / "scenario_standby.json")
    scenario.ambulances["A001"].assigned_post = POST_FAR
    clone = scenario_from_dict(scenario_to_dict(scenario))
    assert clone.ambulances["A001"].home_base == BASE_ONE
    assert clone.ambulances["A001"].assigned_post == POST_FAR
    assert clone.ambulances["A002"].assigned_post is None
    # An ambulance with no explicit home station belongs to where it starts.
    assert Ambulance("A003", BASE_TWO).home_base == BASE_TWO


@pytest.mark.parametrize("key", ["home_base", "assigned_post"])
def test_home_base_and_assigned_post_must_be_graph_nodes(tmp_path: Path, key: str) -> None:
    scenario = _scenario()
    scenario["ambulances"]["A001"][key] = f"{CODE}::standby::MISSING"
    with pytest.raises(ScenarioValidationError, match=key):
        load_municipal_standby_problem(_write(tmp_path, scenario=scenario))


def test_missing_provenance_field_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    del manifest["provenance"]["model_parameters"]
    with pytest.raises(ValueError, match="missing provenance fields"):
        load_municipal_standby_problem(_write(tmp_path, manifest=manifest))


def test_road_time_source_must_report_minutes(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["provenance"]["road_time_source"]["unit"] = "seconds"
    with pytest.raises(ValueError, match="road_time_source"):
        load_municipal_standby_problem(_write(tmp_path, manifest=manifest))


def test_cross_municipality_post_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["standby_posts"][0]["municipality_code"] = "11680"
    with pytest.raises(ValueError, match="cross-municipality"):
        load_municipal_standby_problem(_write(tmp_path, manifest=manifest))


def test_home_station_must_be_an_ambulance_base_node(tmp_path: Path) -> None:
    scenario, manifest = _scenario(), _manifest()
    scenario["ambulances"]["A001"]["home_base"] = POST_NEAR
    manifest["ambulance_home_bases"]["A001"] = POST_NEAR
    with pytest.raises(ValueError, match="ambulance_base node"):
        load_municipal_standby_problem(_write(tmp_path, scenario=scenario, manifest=manifest))


def test_registry_must_name_every_local_resource(tmp_path: Path) -> None:
    manifest = _manifest()
    del manifest["resource_municipality_codes"]["ambulances"]["A002"]
    with pytest.raises(ValueError, match="resource_municipality_codes.ambulances"):
        load_municipal_standby_problem(_write(tmp_path, manifest=manifest))


def test_missing_directional_route_from_a_post_is_rejected(tmp_path: Path) -> None:
    scenario = _scenario()
    # The post keeps its inbound routes but can no longer reach any demand point.
    scenario["network"]["edges"][POST_FAR] = {}
    with pytest.raises(ValueError, match="missing measured directional road route"):
        load_municipal_standby_problem(_write(tmp_path, scenario=scenario))


def test_post_must_appear_in_scenario_standby_nodes(tmp_path: Path) -> None:
    scenario = _scenario()
    scenario["standby_nodes"] = [BASE_ONE, BASE_TWO, HOSPITAL, POST_NEAR]
    with pytest.raises(ValueError, match="missing from scenario.standby_nodes"):
        load_municipal_standby_problem(_write(tmp_path, scenario=scenario))


def test_greedy_search_moves_an_ambulance_to_the_uncovered_demand(tmp_path: Path) -> None:
    problem = load_municipal_standby_problem(_write(tmp_path))
    result = optimize_standby_placement(problem, episodes=2, seed=11)
    assert result["steps"], "a strictly improving move must be recorded"
    assert result["steps"][0]["post_id"] == "P002"
    assert result["best"]["incremental_expected_saved"]["mean"] >= 0
    assert result["best"]["moved_ambulances"]
    assert "P002" in result["best"]["assignment"].values()
    assert len(result["baseline_home_bases"]["values"]) == 2
    assert result["schedule"]["home_hours"] == [1, 2, 3, 4, 5, 6]
    assert result["episodes"] == 2


def test_shortlist_keeps_the_highest_coverage_post(tmp_path: Path) -> None:
    problem = load_municipal_standby_problem(_write(tmp_path))
    result = optimize_standby_placement(problem, episodes=2, seed=11, shortlist=1)
    assert result["shortlisted_posts"] == ["P002"]


def test_movable_zero_keeps_every_ambulance_at_home(tmp_path: Path) -> None:
    problem = load_municipal_standby_problem(_write(tmp_path))
    result = optimize_standby_placement(problem, episodes=2, seed=11, movable=0)
    assert result["steps"] == []
    assert result["best"]["assignment"] == {}
    assert result["best"]["incremental_expected_saved"]["mean"] == 0


def test_evaluate_assignment_rejects_unknown_ids(tmp_path: Path) -> None:
    problem = load_municipal_standby_problem(_write(tmp_path))
    estimate = evaluate_assignment(problem, {"A002": "P002"}, 2, 11)
    assert estimate["mean"] > 0
    assert len(estimate["values"]) == 2
    with pytest.raises(ValueError, match="unknown standby post ids"):
        evaluate_assignment(problem, {"A002": "P999"}, 1, 11)
    with pytest.raises(ValueError, match="unknown ambulance ids"):
        evaluate_assignment(problem, {"A999": "P002"}, 1, 11)


def test_cli_writes_standby_placement_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest_path = _write(tmp_path)
    output_dir = tmp_path / "out"
    code = main([
        "--standby-placement", str(manifest_path), "--episodes", "2",
        "--seed", "11", "--shortlist", "1", "--output-dir", str(output_dir),
    ])
    assert code == 0
    result = json.loads((output_dir / "standby_placement_result.json").read_text(encoding="utf-8"))
    assert result["municipality_code"] == CODE
    assert result["search"].startswith("greedy forward selection")
    assert "increment_vs_home_bases" in capsys.readouterr().out


def test_cli_rejects_mixing_standby_with_other_modes(tmp_path: Path) -> None:
    manifest_path = _write(tmp_path)
    with pytest.raises(SystemExit):
        main(["--standby-placement", str(manifest_path), "--compare", "--episodes", "2"])
    with pytest.raises(SystemExit):
        main(["--movable", "1", "--episodes", "2"])
