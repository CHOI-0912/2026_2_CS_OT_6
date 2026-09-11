from __future__ import annotations

import pytest

from ambulance_sim.engine import Simulation
from ambulance_sim.model import Ambulance, AmbulanceStatus, PatientProfile, RoadNetwork, Scenario
from ambulance_sim.policy import GreedySurvivalPolicy, ScheduledStandbyPolicy


FREE_HOURS = (7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0)
HOME_HOURS = (1, 2, 3, 4, 5, 6)
BASE_ONE, BASE_TWO, POST = "base-1", "base-2", "post"


def _policy() -> ScheduledStandbyPolicy:
    return ScheduledStandbyPolicy(FREE_HOURS, HOME_HOURS)


def _scenario() -> Scenario:
    """A demand-free graph, so only the schedule creates events."""
    edges = {
        BASE_ONE: {POST: 10.0, BASE_TWO: 10.0},
        BASE_TWO: {POST: 10.0, BASE_ONE: 10.0},
        POST: {BASE_ONE: 10.0, BASE_TWO: 10.0},
    }
    return Scenario(
        network=RoadNetwork(edges),
        villages={},
        hospitals={},
        ambulances={
            "IDLE": Ambulance("IDLE", POST, home_base=BASE_ONE, assigned_post=POST),
            "BUSY": Ambulance("BUSY", BASE_TWO, home_base=BASE_TWO, assigned_post=POST),
        },
        profiles=(PatientProfile("critical", golden_minutes=0, decay_rate=0.1, scene_minutes=0),),
        standby_nodes=(BASE_ONE, BASE_TWO, POST),
        horizon_minutes=720,
    )


def test_schedule_hours_are_the_two_window_boundaries() -> None:
    assert _policy().schedule_hours() == (1, 7)


def test_overlapping_or_incomplete_windows_are_rejected() -> None:
    with pytest.raises(ValueError, match="overlap"):
        ScheduledStandbyPolicy((1, 2, 3), (3, 4))
    with pytest.raises(ValueError, match="every hour"):
        ScheduledStandbyPolicy((7, 8), (1, 2))


def test_home_window_returns_the_vehicle_to_its_own_station() -> None:
    sim = Simulation(_scenario(), policy=_policy())
    ambulance = sim.scenario.ambulances["IDLE"]
    sim.now = 3 * 60.0
    assert _policy().at_home_hour(sim.now) is True
    assert _policy().choose_standby_location(sim, ambulance) == BASE_ONE


def test_free_window_uses_the_assigned_post_and_falls_back_to_home() -> None:
    sim = Simulation(_scenario(), policy=_policy())
    ambulance = sim.scenario.ambulances["IDLE"]
    sim.now = 9 * 60.0
    assert _policy().choose_standby_location(sim, ambulance) == POST
    ambulance.assigned_post = None
    assert _policy().choose_standby_location(sim, ambulance) == BASE_ONE


def test_shift_change_repositions_idle_vehicles_and_leaves_busy_ones_alone() -> None:
    scenario = _scenario()
    # A vehicle committed to a patient must never be recalled by the schedule.
    scenario.ambulances["BUSY"].status = AmbulanceStatus.TO_SCENE
    simulation = Simulation(scenario, policy=_policy(), trace=True)
    simulation.run()

    assert simulation.event_counts["shift_change"] == 2
    assert simulation.event_counts["reposition_complete"] == 2
    shift_times = [frame["time"] for frame in simulation.trace if frame["event"] == "shift_change"]
    assert shift_times == [60.0, 420.0]

    # 01:00 sends the idle vehicle home, 07:00 sends it back to its post.
    night = next(frame for frame in simulation.trace if frame["time"] == 60.0)
    assert night["ambulances"]["IDLE"]["status"] == "repositioning"
    assert night["ambulances"]["IDLE"]["destination"] == BASE_ONE
    day = next(frame for frame in simulation.trace if frame["time"] == 420.0)
    assert day["ambulances"]["IDLE"]["destination"] == POST

    final = simulation.trace[-1]["ambulances"]
    assert final["IDLE"]["location"] == POST
    assert final["IDLE"]["status"] == "idle"
    assert final["BUSY"]["location"] == BASE_TWO
    assert final["BUSY"]["status"] == "to_scene"


def test_policy_without_a_schedule_creates_no_shift_events() -> None:
    simulation = Simulation(_scenario(), policy=GreedySurvivalPolicy())
    simulation.run()
    assert "shift_change" not in simulation.event_counts
