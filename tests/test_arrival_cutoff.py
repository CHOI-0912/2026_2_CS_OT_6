"""Arrivals stop at the cut-off while the episode keeps running through the cool-down."""

from __future__ import annotations

from ambulance_sim.engine import Simulation, build_default_scenario
from ambulance_sim.io import ScenarioValidationError, scenario_from_dict, scenario_to_dict, validate_scenario
from ambulance_sim.model import EventKind
import pytest


def _scenario(horizon: float, cutoff: float | None):
    scenario = build_default_scenario()
    scenario.horizon_minutes = horizon
    scenario.arrival_cutoff_minutes = cutoff
    return scenario


def test_no_patient_arrives_after_the_cutoff_but_events_continue() -> None:
    simulation = Simulation(_scenario(1440 + 360, 1440), seed=7, trace=True)
    simulation.run()
    arrivals = [frame for frame in simulation.trace if frame["event"] == EventKind.PATIENT_ARRIVAL.value]
    assert arrivals
    assert max(frame["time"] for frame in arrivals) < 1440
    later = [frame for frame in simulation.trace if frame["time"] > 1440 and frame["event"] != "horizon_end"]
    assert later, "restock/transport events must keep running after the arrival cut-off"


def test_cutoff_equal_to_horizon_matches_the_old_behaviour() -> None:
    plain = Simulation(_scenario(1440, None), seed=3).run()
    explicit = Simulation(_scenario(1440, 1440), seed=3).run()
    assert plain["expected_saved"] == explicit["expected_saved"]
    assert plain["seeded_patients"] == explicit["seeded_patients"]


def test_cutoff_round_trips_through_json_and_is_validated() -> None:
    scenario = _scenario(1800, 1440)
    loaded = scenario_from_dict(scenario_to_dict(scenario), source="memory")
    assert loaded.arrival_cutoff_minutes == 1440
    assert loaded.horizon_minutes == 1800
    assert scenario_to_dict(_scenario(1440, None))["arrival_cutoff_minutes"] is None
    with pytest.raises(ScenarioValidationError):
        validate_scenario(_scenario(1440, 2000))
