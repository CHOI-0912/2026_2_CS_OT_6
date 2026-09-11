from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ambulance_sim.hospital_placement import load_municipal_placement_problem
from ambulance_sim.io import save_scenario
from ambulance_sim.model import Ambulance, Hospital, PatientProfile, RoadNetwork, Scenario
from scripts.run_placement_sensitivity import DEFAULT_SWEEPS, SWEEP_NAMES, apply_sweep, main, parse_sweeps


CODE = "41110"
CANDIDATE_IDS = {"C_NEAR", "C_FAR"}


def _write_problem(root: Path) -> Path:
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
        hourly_village_rates={3: {f"{CODE}::demand": 6.0}},
    )
    save_scenario(scenario, root / "scenario.json")
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
        },
        "resource_municipality_codes": {
            "demand": {f"{CODE}::demand": CODE},
            "hospitals": {"H_REAL": CODE},
            "ambulances": {"A_REAL": CODE},
        },
        "candidate_hospitals": [
            {
                "id": "C_NEAR", "name": "near", "municipality_code": CODE, "source_id": "site-near",
                "candidate_type": "new_build", "location": f"{CODE}::candidate_near",
                "capabilities": ["critical"], "capacity": 100, "success_when_available": 1.0,
            },
            {
                "id": "C_FAR", "name": "far", "municipality_code": CODE, "source_id": "site-far",
                "candidate_type": "new_build", "location": f"{CODE}::candidate_far",
                "capabilities": ["critical"], "capacity": 100, "success_when_available": 1.0,
            },
        ],
    }
    placement = root / "placement.json"
    placement.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return placement


def test_main_writes_summary_and_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    placement = _write_problem(tmp_path)
    output_dir = tmp_path / "out"
    assert main([
        "--placement", str(placement), "--new-hospitals", "1", "--episodes", "2", "--seed", "3",
        "--output-dir", str(output_dir),
        "--sweep", "daily_calls_scale=0.5,1.5", "--sweep", "restock_minutes=5",
        "--seed-sweep", "2",
    ]) == 0
    summary = json.loads((output_dir / "sensitivity_summary.json").read_text(encoding="utf-8"))
    assert summary["baseline"]["best_candidate_ids"] == ["C_NEAR"]
    assert summary["baseline"]["rank_of_baseline_best"] == 1
    assert [(row["sweep"], row["value"]) for row in summary["sweeps"]] == [
        ("daily_calls_scale", 0.5), ("daily_calls_scale", 1.5), ("restock_minutes", 5.0),
    ]
    assert [row["seed"] for row in summary["seed_sweep"]] == [1003, 2003]
    for row in summary["sweeps"] + summary["seed_sweep"]:
        assert set(row["best_candidate_ids"]) <= CANDIDATE_IDS
        assert row["ci95"][0] <= row["incremental_mean"] <= row["ci95"][1]
        assert 1 <= row["rank_of_baseline_best"] <= 2
    robustness = summary["robustness"]
    assert robustness["runs"] == 5
    assert 0 <= robustness["fraction"] <= 1
    assert all(set(ids) <= CANDIDATE_IDS for ids in robustness["ever_best_combinations"])
    with (output_dir / "sensitivity_table.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["run_type"] for row in rows] == ["baseline", "sweep", "sweep", "sweep", "seed", "seed"]
    assert rows[0]["best_candidate_ids"] == "C_NEAR"
    assert "강건성" in capsys.readouterr().out


def test_default_sweeps_cover_every_default_factor() -> None:
    assert parse_sweeps(None) == DEFAULT_SWEEPS
    assert set(DEFAULT_SWEEPS) < set(SWEEP_NAMES)
    with pytest.raises(ValueError, match="--sweep expects"):
        parse_sweeps(["not_a_factor=1"])


@pytest.mark.parametrize("sweep", SWEEP_NAMES)
def test_apply_sweep_changes_only_its_factor(tmp_path: Path, sweep: str) -> None:
    problem = load_municipal_placement_problem(_write_problem(tmp_path))
    value = {"daily_calls_scale": 0.5, "decay_rate_scale": 2.0, "success_when_unavailable": 1.5}.get(sweep, 7.0)
    modified = apply_sweep(problem, sweep, value)
    scenario, original = modified.scenario, problem.scenario
    hospital = scenario.hospitals["H_REAL"]
    if sweep == "daily_calls_scale":
        assert scenario.villages == {f"{CODE}::demand": 2.0}
        assert scenario.hourly_village_rates == {3: {f"{CODE}::demand": 3.0}}
    elif sweep == "treatment_minutes":
        assert hospital.treatment_minutes == 7.0
        assert all(candidate.treatment_minutes == 7.0 for candidate in modified.candidates)
    elif sweep == "restock_minutes":
        assert scenario.ambulances["A_REAL"].restock_minutes == 7.0
    elif sweep == "success_when_unavailable":
        assert hospital.success_when_unavailable == 1.0
        assert all(candidate.success_when_unavailable == 1.0 for candidate in modified.candidates)
    elif sweep == "decay_rate_scale":
        assert scenario.profiles[0].decay_rate == pytest.approx(0.2)
    elif sweep == "candidate_capacity":
        assert all(candidate.capacity == 7 for candidate in modified.candidates)
        assert hospital.capacity == 100
    else:
        assert hospital.transfer_delay_minutes == 7.0
        assert all(candidate.transfer_delay_minutes == 7.0 for candidate in modified.candidates)
    # Untouched inputs and the original problem stay as loaded.
    assert scenario.network.edges == original.network.edges
    assert original.villages == {f"{CODE}::demand": 4.0}
    assert original.hospitals["H_REAL"].treatment_minutes == 0.0
    assert [candidate.id for candidate in modified.candidates] == ["C_NEAR", "C_FAR"]
