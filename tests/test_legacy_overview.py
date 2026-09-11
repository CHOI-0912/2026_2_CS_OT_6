from __future__ import annotations

import math
import csv
from pathlib import Path

import pytest

from ambulance_sim.engine import Simulation
from ambulance_sim.legacy.gyeonggi_centroid import (
    GYEONGGI_OFFICIAL_DISPATCHES_2024,
    MUNICIPALITIES,
    build_gyeonggi_municipal_scenario,
    estimated_municipal_dispatches,
)
from ambulance_sim.legacy.national_aggregate import (
    OFFICIAL_DISPATCH_TOTAL_2024,
    OFFICIAL_ELDERLY_TOTAL_2024,
    OFFICIAL_POPULATION_TOTAL_2024,
    PROVINCES,
    build_national_overview_scenario,
    national_data_summary,
)


def test_official_national_totals_are_preserved() -> None:
    assert len(PROVINCES) == 17
    assert national_data_summary() == {
        "region": "전국",
        "provinces": 17,
        "population_2024": OFFICIAL_POPULATION_TOTAL_2024,
        "elderly_65plus_2024": OFFICIAL_ELDERLY_TOTAL_2024,
        "dispatches_2024": OFFICIAL_DISPATCH_TOTAL_2024,
    }


def test_national_scenario_has_all_regions_and_scaled_rate() -> None:
    scale = 0.01
    scenario = build_national_overview_scenario(demand_scale=scale, representative_ambulances=34)
    assert len(scenario.villages) == 17
    assert len(scenario.hospitals) == 17
    assert len(scenario.ambulances) == 34
    annualized = sum(scenario.villages.values()) * 24 * 365
    assert annualized == pytest.approx(OFFICIAL_DISPATCH_TOTAL_2024 * scale)
    assert all(len(component) == 2 for component in scenario.network.edges.values())


def test_disconnected_regions_prevent_cross_province_dispatch() -> None:
    scenario = build_national_overview_scenario(hours=1, demand_scale=0.001, representative_ambulances=17)
    seoul_station = "서울 119"
    jeju_demand = "제주 수요"
    assert math.isinf(scenario.network.travel_time(seoul_station, jeju_demand))


def test_each_region_selects_its_reachable_hospital() -> None:
    scenario = build_national_overview_scenario(hours=1, demand_scale=0.001, representative_ambulances=17)
    simulation = Simulation(scenario, seed=1)
    profile = scenario.profiles[-1]
    from ambulance_sim.model import Patient
    patient = Patient("test", "제주 수요", profile, 0.0)
    selected = simulation.policy.choose_hospital(simulation, patient, patient.location, 0.0)
    assert selected is not None
    assert selected.location == "제주 응급의료"


def test_small_national_episode_conserves_probability_mass() -> None:
    scenario = build_national_overview_scenario(hours=24, demand_scale=0.001, representative_ambulances=17)
    result = Simulation(scenario, seed=7).run()
    assert result["expected_saved"] + result["expected_lost"] == pytest.approx(result["seeded_patients"])


def test_invalid_national_scale_and_fleet_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_national_overview_scenario(demand_scale=0)
    with pytest.raises(ValueError):
        build_national_overview_scenario(representative_ambulances=16)


def test_single_region_scenario_is_cropped_and_preserves_official_total() -> None:
    scenario = build_national_overview_scenario(region="경기", representative_ambulances=6)
    assert set(scenario.villages) == {"경기 수요"}
    assert len(scenario.hospitals) == 1
    assert len(scenario.ambulances) == 6
    assert scenario.network.positions["경기 119"] == (20.0, 68.0)
    assert national_data_summary("gyeonggi")["dispatches_2024"] == 799_302


def test_gyeonggi_calculation_is_really_split_into_31_municipalities() -> None:
    scenario = build_gyeonggi_municipal_scenario(representative_ambulances=31)
    assert len(MUNICIPALITIES) == 31
    assert len(scenario.villages) == 31
    assert len(scenario.hospitals) == 31
    assert len(scenario.ambulances) == 31
    assert sum(estimated_municipal_dispatches().values()) == GYEONGGI_OFFICIAL_DISPATCHES_2024
    annualized = sum(scenario.villages.values()) * 365 * 24
    assert annualized == pytest.approx(GYEONGGI_OFFICIAL_DISPATCHES_2024 * 0.01)


def test_every_gyeonggi_station_can_reach_every_municipal_demand() -> None:
    scenario = build_gyeonggi_municipal_scenario(hours=1, demand_scale=0.001)
    stations = [f"{row.name} 119" for row in MUNICIPALITIES]
    demands = [f"{row.name} 수요" for row in MUNICIPALITIES]
    assert all(math.isfinite(scenario.network.travel_time(station, demand)) for station in stations for demand in demands)


def test_gyeonggi_csv_has_31_complete_rows_and_matching_estimates() -> None:
    path = Path(__file__).parents[1] / "legacy" / "data" / "gyeonggi_centroid_2024" / "municipality_summary.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 31
    assert all(all(value not in (None, "") for value in row.values()) for row in rows)
    assert sum(int(row["estimated_dispatches_2024"]) for row in rows) == GYEONGGI_OFFICIAL_DISPATCHES_2024
