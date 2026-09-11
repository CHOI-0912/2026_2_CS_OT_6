"""Regression tests for the audit fixes in the simulator package."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import math
from pathlib import Path

import pytest

from ambulance_sim import engine as engine_module
from ambulance_sim import experiment, hospital_placement
from ambulance_sim.__main__ import main
from ambulance_sim.engine import Simulation, build_default_scenario
from ambulance_sim.experiment import run_experiment
from ambulance_sim.hospital_placement import (
    CandidateHospital,
    load_municipal_placement_problem,
    optimize_municipality_directory,
)
from ambulance_sim.io import save_scenario, validate_scenario
from ambulance_sim.legacy.gyeonggi_centroid import build_gyeonggi_municipal_scenario
from ambulance_sim.legacy.national_aggregate import build_national_overview_scenario
from ambulance_sim.model import (
    Ambulance, AmbulanceStatus, EventKind, Hospital, Patient, PatientProfile, PatientStatus, RoadNetwork, Scenario,
)
from ambulance_sim.policy import GreedySurvivalPolicy


# --- 1. _dispatch_waiting skips only the unreachable (patient, ambulance) pair ---

class FirstIdleAmbulancePolicy(GreedySurvivalPolicy):
    """Custom policy that ignores reachability: first idle ambulance in scenario order."""

    def choose_ambulance(self, sim, patient):
        return next((a for a in sim.scenario.ambulances.values() if a.status is AmbulanceStatus.IDLE), None)


def _two_component_simulation(policy) -> tuple[Simulation, Ambulance]:
    # s1-v1 and s2-v2 are disconnected; the only ambulance sits in the s1 component.
    profile = PatientProfile("x", golden_minutes=1000, decay_rate=0, scene_minutes=1)
    network = RoadNetwork({"s1": {"v1": 3}, "v1": {"s1": 3}, "s2": {"v2": 3}, "v2": {"s2": 3}})
    ambulance = Ambulance("A1", "s1")
    scenario = Scenario(
        network=network, villages={}, hospitals={}, ambulances={"A1": ambulance},
        profiles=(profile,), standby_nodes=("s1",), horizon_minutes=60,
    )
    sim = Simulation(scenario, policy=policy, seed=0)
    # Equal mass and queue order [P1, P2]: P1 (unreachable) is examined first.
    sim.patients["P1"] = Patient("P1", "v2", profile, 0, last_survival_time=0)
    sim.patients["P2"] = Patient("P2", "v1", profile, 0, last_survival_time=0)
    sim.waiting = ["P1", "P2"]
    return sim, ambulance


def test_unreachable_policy_choice_does_not_block_other_waiting_patients() -> None:
    sim, ambulance = _two_component_simulation(FirstIdleAmbulancePolicy())
    sim._dispatch_waiting()
    assert ambulance.status is AmbulanceStatus.TO_SCENE
    assert ambulance.patient_id == "P2"
    assert sim.patients["P2"].status is PatientStatus.ASSIGNED
    assert sim.patients["P1"].status is PatientStatus.WAITING
    assert sim.patients["P1"].assigned_ambulance is None
    assert sim.waiting == ["P1"]
    assert [event.kind for event in sim.events] == [EventKind.ARRIVE_SCENE]


def test_patient_without_reachable_ambulance_is_deferred_not_terminal() -> None:
    # The default policy returns None for P1; the trigger must still serve P2.
    sim, ambulance = _two_component_simulation(GreedySurvivalPolicy())
    sim._dispatch_waiting()
    assert ambulance.patient_id == "P2"
    assert sim.waiting == ["P1"]
    assert sim.patients["P1"].status is PatientStatus.WAITING


def test_dispatch_terminates_when_every_choice_is_unreachable() -> None:
    sim, ambulance = _two_component_simulation(FirstIdleAmbulancePolicy())
    sim.patients["P2"].location = "v2"
    sim._dispatch_waiting()
    assert ambulance.status is AmbulanceStatus.IDLE
    assert sim.waiting == ["P1", "P2"]
    assert sim.events == []


# --- 2. scenario builders validate their output ---

def test_built_in_scenarios_pass_validation() -> None:
    validate_scenario(build_default_scenario())
    validate_scenario(build_gyeonggi_municipal_scenario(hours=1))
    validate_scenario(build_national_overview_scenario(hours=1))


def test_default_builder_calls_validate_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(scenario, *, source="scenario"):
        raise ValueError(f"validated {source}")

    monkeypatch.setattr(engine_module, "validate_scenario", boom)
    with pytest.raises(ValueError, match="validated build_default_scenario"):
        build_default_scenario()


# --- 3. demo hospitals have binding capacity ---

def test_demo_hospitals_have_capacity_that_binds() -> None:
    scenario = build_default_scenario()
    for hospital in scenario.hospitals.values():
        assert hospital.capacity is not None and hospital.capacity >= 1
        assert 45 <= hospital.treatment_minutes <= 75
    sim = Simulation(build_default_scenario(hours=24), seed=7, trace=True)
    sim.run()
    assert any(
        state["occupied"] >= state["capacity"]
        for frame in sim.trace for state in frame["hospitals"].values()
    )


# --- 4. single-episode estimates report null confidence intervals ---

def test_experiment_estimate_is_null_below_two_episodes() -> None:
    single = experiment._estimate([1.5]).as_dict()
    assert single["mean"] == 1.5
    assert single["sample_size"] == 1
    assert single["standard_deviation"] is None
    assert single["ci95_low"] is None and single["ci95_high"] is None
    assert "normal approximation" in single["ci95_note"]
    json.dumps(single)
    pair = experiment._estimate([1.0, 3.0]).as_dict()
    assert pair["mean"] == 2.0
    assert pair["standard_deviation"] == pytest.approx(math.sqrt(2), abs=1e-6)
    assert pair["ci95_low"] == pytest.approx(2.0 - 1.96, abs=1e-6)
    assert pair["ci95_high"] == pytest.approx(2.0 + 1.96, abs=1e-6)


def test_placement_estimate_is_null_below_two_episodes() -> None:
    single = hospital_placement._estimate([2.0])
    assert single["mean"] == 2.0
    assert single["standard_deviation"] is None
    assert single["ci95_low"] is None and single["ci95_high"] is None
    assert "normal approximation" in single["ci95_note"]
    pair = hospital_placement._estimate([1.0, 3.0])
    assert pair["ci95_low"] == pytest.approx(2.0 - 1.96)
    assert pair["ci95_high"] == pytest.approx(2.0 + 1.96)


def test_single_episode_experiment_serializes_with_null_ci() -> None:
    result = run_experiment(
        lambda: build_default_scenario(hours=1, ambulances=1), {"greedy": GreedySurvivalPolicy}, episodes=1,
    )
    saved = result["estimates"]["greedy"]["expected_saved"]
    assert saved["ci95_low"] is None
    assert json.loads(json.dumps(result))["estimates"]["greedy"]["expected_saved"]["ci95_high"] is None


def test_cli_compact_output_handles_null_ci_and_warns() -> None:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--compare", "--episodes", "1", "--hours", "1", "--ambulances", "1", "--seed", "1"])
    assert code == 0
    assert "95% CI n/a" in out.getvalue()
    assert "at least 2 episodes" in err.getvalue()
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--compare", "--episodes", "2", "--hours", "1", "--ambulances", "1", "--seed", "1"])
    assert code == 0
    assert "95% CI n/a" not in out.getvalue()
    assert err.getvalue() == ""


# --- 5. directory aggregate carries combined uncertainty ---

def _write_municipality(root: Path, code: str, name: str, demand_rate: float) -> None:
    nodes = [f"{code}::station", f"{code}::demand", f"{code}::existing", f"{code}::cand_a", f"{code}::cand_b"]
    edges = {left: {right: 20.0 for right in nodes if right != left} for left in nodes}
    edges[f"{code}::station"][f"{code}::demand"] = edges[f"{code}::demand"][f"{code}::station"] = 1.0
    edges[f"{code}::demand"][f"{code}::existing"] = edges[f"{code}::existing"][f"{code}::demand"] = 40.0
    edges[f"{code}::demand"][f"{code}::cand_a"] = edges[f"{code}::cand_a"][f"{code}::demand"] = 1.0
    edges[f"{code}::demand"][f"{code}::cand_b"] = edges[f"{code}::cand_b"][f"{code}::demand"] = 18.0
    scenario = Scenario(
        network=RoadNetwork(edges=edges, positions={node: (127.0 + i / 100, 37.0) for i, node in enumerate(nodes)}),
        villages={f"{code}::demand": demand_rate},
        hospitals={f"H{code}": Hospital(f"H{code}", f"{code}::existing", {"critical"}, 1.0, capacity=100)},
        ambulances={f"A{code}": Ambulance(f"A{code}", f"{code}::station", restock_minutes=0)},
        profiles=(PatientProfile("critical", golden_minutes=0, decay_rate=0.1, scene_minutes=0),),
        standby_nodes=(f"{code}::station",),
        horizon_minutes=60,
        max_transfers=0,
    )
    folder = root / code
    folder.mkdir()
    save_scenario(scenario, folder / "scenario.json")
    manifest = {
        "municipality_code": code,
        "municipality_name": name,
        "scenario": "scenario.json",
        "candidate_hospitals": [
            {
                "id": f"C{code}_{suffix}", "name": f"{name} {suffix}", "municipality_code": code,
                "source_id": f"site-{suffix}", "candidate_type": "new_build", "location": f"{code}::cand_{suffix}",
                "capabilities": ["critical"], "capacity": 100, "success_when_available": 1.0,
            }
            for suffix in ("a", "b")
        ],
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
            "demand": {f"{code}::demand": code},
            "hospitals": {f"H{code}": code},
            "ambulances": {f"A{code}": code},
        },
    }
    (folder / "placement.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_directory_aggregate_combines_independent_variances(tmp_path: Path) -> None:
    _write_municipality(tmp_path, "41110", "수원시", 4.0)
    _write_municipality(tmp_path, "41130", "성남시", 3.0)
    episodes = 4
    result = optimize_municipality_directory(tmp_path, new_hospitals=1, episodes=episodes, seed=5)
    assert result["municipality_count"] == 2
    bests = [row["best"]["incremental_expected_saved"] for row in result["municipalities"]]
    assert all(len(best["values"]) == episodes for best in bests)
    aggregate = result["aggregate_after_optimization"]
    expected_mean = sum(best["mean"] for best in bests)
    expected_sd = math.sqrt(sum(best["standard_deviation"] ** 2 for best in bests))
    half_width = 1.96 * math.sqrt(sum(best["standard_deviation"] ** 2 / episodes for best in bests))
    assert aggregate["incremental_expected_saved_mean"] == pytest.approx(expected_mean)
    assert aggregate["incremental_expected_saved_standard_deviation"] == pytest.approx(expected_sd)
    assert aggregate["incremental_expected_saved_ci95_low"] == pytest.approx(expected_mean - half_width)
    assert aggregate["incremental_expected_saved_ci95_high"] == pytest.approx(expected_mean + half_width)
    assert "sqrt(sum(sd_i^2))" in aggregate["aggregation_note"]
    json.dumps(result)


def test_directory_aggregate_ci_is_null_for_single_episode(tmp_path: Path) -> None:
    _write_municipality(tmp_path, "41110", "수원시", 4.0)
    _write_municipality(tmp_path, "41130", "성남시", 3.0)
    result = optimize_municipality_directory(tmp_path, new_hospitals=1, episodes=1, seed=5)
    aggregate = result["aggregate_after_optimization"]
    assert aggregate["incremental_expected_saved_ci95_low"] is None
    assert aggregate["incremental_expected_saved_ci95_high"] is None
    assert aggregate["incremental_expected_saved_standard_deviation"] is None
    for row in result["municipalities"]:
        assert row["best"]["incremental_expected_saved"]["ci95_low"] is None


# --- 6. placement CLI warns below two episodes ---

def test_placement_cli_warns_below_two_episodes(tmp_path: Path) -> None:
    _write_municipality(tmp_path, "41110", "수원시", 4.0)
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--hospital-placement", str(tmp_path / "41110" / "placement.json"), "--new-hospitals", "1", "--episodes", "1"])
    assert code == 0
    assert "at least 2 episodes" in err.getvalue()
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["--municipality-root", str(tmp_path), "--new-hospitals", "1", "--episodes", "2"])
    assert code == 0
    assert err.getvalue() == ""


# --- 7. CandidateHospital carries a transfer delay ---

def _load_candidate_with(tmp_path: Path, **overrides: object) -> CandidateHospital:
    _write_municipality(tmp_path, "41110", "수원시", 4.0)
    manifest_path = tmp_path / "41110" / "placement.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_hospitals"] = manifest["candidate_hospitals"][:1]
    manifest["candidate_hospitals"][0].update(overrides)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return load_municipal_placement_problem(manifest_path).candidates[0]


def test_candidate_transfer_delay_defaults_to_five_minutes(tmp_path: Path) -> None:
    # A manifest written before this field existed must keep working.
    candidate = _load_candidate_with(tmp_path)
    assert candidate.transfer_delay_minutes == 5.0
    assert candidate.activate().transfer_delay_minutes == 5.0


def test_candidate_transfer_delay_is_read_and_activated(tmp_path: Path) -> None:
    candidate = _load_candidate_with(tmp_path, transfer_delay_minutes=12.5)
    assert candidate.transfer_delay_minutes == 12.5
    assert candidate.activate().transfer_delay_minutes == 12.5


def test_negative_candidate_transfer_delay_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid capacity, success probability, or duration"):
        _load_candidate_with(tmp_path, transfer_delay_minutes=-1.0)
