from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ambulance_sim.hospital_placement import (
    CandidateHospital,
    MunicipalPlacementProblem,
    optimize_hospital_placement,
    validate_municipal_placement_problem,
)
from ambulance_sim.model import Ambulance, Hospital, PatientProfile, RoadNetwork, Scenario


CODE = "41110"


def _problem() -> MunicipalPlacementProblem:
    nodes = [
        f"{CODE}::station",
        f"{CODE}::demand",
        f"{CODE}::existing",
        f"{CODE}::candidate_near",
        f"{CODE}::candidate_far",
    ]
    edges = {node: {} for node in nodes}
    for left in nodes:
        for right in nodes:
            if left != right:
                edges[left][right] = 20.0
    edges[f"{CODE}::station"][f"{CODE}::demand"] = 1.0
    edges[f"{CODE}::demand"][f"{CODE}::station"] = 1.0
    edges[f"{CODE}::demand"][f"{CODE}::existing"] = 40.0
    edges[f"{CODE}::existing"][f"{CODE}::demand"] = 40.0
    edges[f"{CODE}::demand"][f"{CODE}::candidate_near"] = 1.0
    edges[f"{CODE}::candidate_near"][f"{CODE}::demand"] = 1.0
    edges[f"{CODE}::demand"][f"{CODE}::candidate_far"] = 18.0
    edges[f"{CODE}::candidate_far"][f"{CODE}::demand"] = 18.0
    scenario = Scenario(
        network=RoadNetwork(edges=edges, positions={node: (127.0 + i / 100, 37.0) for i, node in enumerate(nodes)}),
        villages={f"{CODE}::demand": 4.0},
        hospitals={
            "H_REAL": Hospital(
                id="H_REAL", location=f"{CODE}::existing", capabilities={"critical"},
                success_when_available=1.0, capacity=100,
            )
        },
        ambulances={"A_REAL": Ambulance(id="A_REAL", location=f"{CODE}::station", restock_minutes=0)},
        profiles=(PatientProfile("critical", golden_minutes=0, decay_rate=0.1, scene_minutes=0),),
        standby_nodes=(f"{CODE}::station",),
        horizon_minutes=60,
        max_transfers=0,
    )
    candidates = (
        CandidateHospital(
            id="C_NEAR", name="near", municipality_code=CODE, source_id="site-near",
            candidate_type="new_build", location=f"{CODE}::candidate_near",
            capabilities=frozenset({"critical"}), capacity=100, success_when_available=1.0,
        ),
        CandidateHospital(
            id="C_FAR", name="far", municipality_code=CODE, source_id="site-far",
            candidate_type="new_build", location=f"{CODE}::candidate_far",
            capabilities=frozenset({"critical"}), capacity=100, success_when_available=1.0,
        ),
    )
    return MunicipalPlacementProblem(
        municipality_code=CODE,
        municipality_name="수원시",
        scenario_path=Path("fixture.json"),
        scenario=scenario,
        candidates=candidates,
        provenance={
            "hospital_source": "official-fixture",
            "demand_source": "dispatch-fixture",
            "candidate_source": "planning-fixture",
            "road_time_source": {
                "provider": "routing-fixture", "extracted_at": "2026-09-08T00:00:00+09:00",
                "routing_profile": "car", "unit": "minutes",
            },
        },
        resource_municipality_codes={
            "demand": {f"{CODE}::demand": CODE},
            "hospitals": {"H_REAL": CODE},
            "ambulances": {"A_REAL": CODE},
        },
    )


def test_local_problem_is_valid_and_near_candidate_wins_exact_search() -> None:
    problem = _problem()
    validate_municipal_placement_problem(problem)
    result = optimize_hospital_placement(problem, new_hospitals=1, episodes=20, seed=10)
    assert result["cross_municipality_resources_allowed"] is False
    assert result["best"]["candidate_ids"] == ["C_NEAR"]
    assert result["best"]["incremental_expected_saved_mean"] > 0
    assert [row["seed"] for row in result["best"]["episodes"]] == list(range(10, 30))
    assert "ci95_low" in result["best"]["incremental_expected_saved"]


@pytest.mark.parametrize("resource", ["demand", "hospital", "ambulance", "candidate"])
def test_cross_municipality_resources_are_rejected(resource: str) -> None:
    problem = _problem()
    if resource == "demand":
        problem.scenario.villages = {"11680::seoul-demand": 1.0}
    elif resource == "hospital":
        problem.scenario.hospitals["H_REAL"].location = "11680::seoul-hospital"
    elif resource == "ambulance":
        problem.scenario.ambulances["A_REAL"].location = "11680::seoul-station"
    else:
        problem = replace(problem, candidates=(replace(problem.candidates[0], municipality_code="11680"),))
    with pytest.raises(ValueError, match="cross-municipality|municipality code"):
        validate_municipal_placement_problem(problem)


def test_authoritative_municipality_code_mismatch_is_rejected() -> None:
    problem = _problem()
    problem.resource_municipality_codes["hospitals"]["H_REAL"] = "11680"
    with pytest.raises(ValueError, match="municipality code mismatch"):
        validate_municipal_placement_problem(problem)


def test_missing_route_provenance_is_rejected() -> None:
    problem = _problem()
    problem.provenance["road_time_source"] = {"provider": "unknown"}
    with pytest.raises(ValueError, match="road_time_source"):
        validate_municipal_placement_problem(problem)


def test_municipality_with_zero_existing_emergency_hospitals_is_allowed() -> None:
    problem = _problem()
    problem.scenario.hospitals = {}
    problem.resource_municipality_codes["hospitals"] = {}
    validate_municipal_placement_problem(problem)
    result = optimize_hospital_placement(problem, new_hospitals=1, episodes=2, seed=3)
    assert result["baseline_existing_only"]["mean"] == 0
    assert result["best"]["candidate_ids"] == ["C_NEAR"]


def test_new_build_cannot_duplicate_existing_hospital_location() -> None:
    problem = _problem()
    duplicate = replace(problem.candidates[0], location=f"{CODE}::existing")
    with pytest.raises(ValueError, match="duplicates an existing hospital"):
        validate_municipal_placement_problem(replace(problem, candidates=(duplicate,)))
