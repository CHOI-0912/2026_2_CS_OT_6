"""Nationwide 17-province overview scenario built from official 2024 totals.

This is intentionally an aggregate planning model.  Official province-level
dispatch totals and resident-registration population are retained, while each
province is represented by one demand node, one station and one aggregate
hospital.  Facility-level and road-level calibration belongs in a later
detailed scenario.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..io import validate_scenario
from ..model import Ambulance, Hospital, PatientProfile, RoadNetwork, Scenario


@dataclass(frozen=True)
class ProvinceDatum:
    code: str
    name: str
    population: int
    elderly_65plus: int
    dispatches_2024: int
    longitude: float
    latitude: float
    response_minutes: float
    transport_minutes: float


# Dispatches: National Fire Agency, 2024 ambulance activity annual report.
# Population: Ministry of the Interior and Safety, December 2024 resident
# registration table.  Coordinates and travel times are overview-model inputs,
# not facility-level observations.
PROVINCES: tuple[ProvinceDatum, ...] = (
    ProvinceDatum("seoul", "서울", 9_331_828, 1_813_648, 557_213, 126.98, 37.57, 6.5, 9.0),
    ProvinceDatum("busan", "부산", 3_266_598, 780_576, 190_032, 129.08, 35.18, 7.0, 10.0),
    ProvinceDatum("daegu", "대구", 2_363_629, 493_256, 144_894, 128.60, 35.87, 7.0, 10.0),
    ProvinceDatum("incheon", "인천", 3_021_010, 533_369, 184_881, 126.71, 37.46, 7.5, 11.0),
    ProvinceDatum("gwangju", "광주", 1_408_422, 246_980, 73_061, 126.85, 35.16, 7.0, 10.0),
    ProvinceDatum("daejeon", "대전", 1_439_157, 259_245, 80_657, 127.38, 36.35, 7.0, 10.0),
    ProvinceDatum("ulsan", "울산", 1_098_049, 188_702, 50_723, 129.31, 35.54, 7.5, 11.0),
    ProvinceDatum("sejong", "세종", 390_685, 45_301, 18_332, 127.29, 36.48, 7.0, 10.0),
    ProvinceDatum("gyeonggi", "경기", 13_694_685, 2_269_603, 799_302, 127.20, 37.41, 8.0, 12.0),
    ProvinceDatum("gangwon", "강원", 1_517_766, 384_970, 132_888, 128.30, 37.82, 10.0, 16.0),
    ProvinceDatum("chungbuk", "충북", 1_591_177, 349_187, 119_306, 127.70, 36.80, 8.5, 13.0),
    ProvinceDatum("chungnam", "충남", 2_136_574, 475_648, 186_078, 126.80, 36.52, 9.0, 14.0),
    ProvinceDatum("jeonbuk", "전북", 1_738_690, 439_263, 151_304, 127.11, 35.82, 9.0, 14.0),
    ProvinceDatum("jeonnam", "전남", 1_788_819, 486_492, 161_028, 126.46, 34.82, 10.0, 16.0),
    ProvinceDatum("gyeongbuk", "경북", 2_531_384, 659_227, 191_691, 128.89, 36.49, 10.0, 16.0),
    ProvinceDatum("gyeongnam", "경남", 3_228_380, 704_330, 225_283, 128.24, 35.24, 9.0, 14.0),
    ProvinceDatum("jeju", "제주", 670_368, 126_985, 57_538, 126.53, 33.50, 10.0, 16.0),
)

OFFICIAL_DISPATCH_TOTAL_2024 = 3_324_211
OFFICIAL_POPULATION_TOTAL_2024 = 51_217_221
OFFICIAL_ELDERLY_TOTAL_2024 = 10_256_782


def _select_provinces(region: str | None) -> tuple[ProvinceDatum, ...]:
    if region is None:
        return PROVINCES
    normalized = region.strip().lower()
    selected = tuple(row for row in PROVINCES if normalized in {row.code.lower(), row.name.lower()})
    if not selected:
        available = ", ".join(row.name for row in PROVINCES)
        raise ValueError(f"unknown region {region!r}; choose one of: {available}")
    return selected


def _allocate_fleet(total: int, provinces: tuple[ProvinceDatum, ...] = PROVINCES) -> dict[str, int]:
    """Allocate representative units by dispatch share, with one per province."""
    if total < len(provinces):
        raise ValueError(f"representative fleet must be at least {len(provinces)}")
    allocation = {province.code: 1 for province in provinces}
    remaining = total - len(provinces)
    if remaining == 0:
        return allocation
    quotas = {
        province.code: remaining * province.dispatches_2024 / sum(row.dispatches_2024 for row in provinces)
        for province in provinces
    }
    used = 0
    for code, quota in quotas.items():
        whole = math.floor(quota)
        allocation[code] += whole
        used += whole
    for code in sorted(quotas, key=lambda item: quotas[item] - math.floor(quotas[item]), reverse=True)[: remaining - used]:
        allocation[code] += 1
    return allocation


def build_national_overview_scenario(
    *,
    hours: float = 24.0,
    demand_scale: float = 0.01,
    representative_ambulances: int = 34,
    region: str | None = None,
) -> Scenario:
    """Create a computationally tractable nationwide aggregate scenario.

    ``demand_scale=0.01`` simulates a one-percent paired sample of the official
    annual call rate.  Result counts therefore describe that sample and must be
    divided by the scale only for a rough national-rate projection.
    """
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("hours must be positive")
    if not math.isfinite(demand_scale) or not 0 < demand_scale <= 1:
        raise ValueError("national demand scale must be in (0, 1]")

    provinces = _select_provinces(region)
    fleet = _allocate_fleet(representative_ambulances, provinces)
    edges: dict[str, dict[str, float]] = {}
    positions: dict[str, tuple[float, float]] = {}
    villages: dict[str, float] = {}
    hospitals: dict[str, Hospital] = {}
    ambulances: dict[str, Ambulance] = {}
    standby_nodes: list[str] = []

    for province in provinces:
        demand = f"{province.name} 수요"
        station = f"{province.name} 119"
        hospital_node = f"{province.name} 응급의료"
        hospital_id = f"H-{province.code}"
        # Disconnected province components enforce regional dispatch in this
        # overview model without pretending that a centroid link is a road.
        edges[demand] = {station: province.response_minutes, hospital_node: province.transport_minutes}
        edges[station] = {demand: province.response_minutes, hospital_node: province.transport_minutes * 0.7}
        edges[hospital_node] = {demand: province.transport_minutes, station: province.transport_minutes * 0.7}

        if len(provinces) == 1:
            # A regional playback should occupy the canvas instead of retaining
            # the tiny nationwide centroid offsets.
            positions[demand] = (48.0, 48.0)
            positions[station] = (20.0, 68.0)
            positions[hospital_node] = (78.0, 32.0)
        else:
            x = (province.longitude - 125.5) / 4.0 * 100.0
            y = (province.latitude - 33.0) / 5.2 * 100.0
            positions[demand] = (x, y)
            positions[station] = (x - 1.1, y + 1.2)
            positions[hospital_node] = (x + 1.1, y - 1.2)
        villages[demand] = province.dispatches_2024 / (365.0 * 24.0) * demand_scale
        standby_nodes.append(station)

        province_share = province.dispatches_2024 / OFFICIAL_DISPATCH_TOTAL_2024
        hospitals[hospital_id] = Hospital(
            id=hospital_id,
            location=hospital_node,
            capabilities={"심정지", "뇌혈관", "심혈관", "외상", "기타"},
            success_when_available=0.78,
            success_when_unavailable=0.25,
            capacity=max(1, round(12 * province_share * len(PROVINCES))),
            treatment_minutes=45.0,
            transfer_delay_minutes=8.0,
            success_by_profile={"심정지": 0.48, "뇌혈관": 0.76, "심혈관": 0.82, "외상": 0.80, "기타": 0.93},
        )
        for unit in range(1, fleet[province.code] + 1):
            ident = f"A-{province.code}-{unit:02d}"
            ambulances[ident] = Ambulance(id=ident, location=station, restock_minutes=10.0)

    profiles = (
        PatientProfile("심정지", golden_minutes=4, decay_rate=0.090, scene_minutes=12, field_success=0.08),
        PatientProfile("뇌혈관", golden_minutes=20, decay_rate=0.025, scene_minutes=10, field_success=0.01),
        PatientProfile("심혈관", golden_minutes=15, decay_rate=0.030, scene_minutes=10, field_success=0.02),
        PatientProfile("외상", golden_minutes=10, decay_rate=0.035, scene_minutes=14, field_success=0.04),
        PatientProfile("기타", golden_minutes=45, decay_rate=0.008, scene_minutes=9, field_success=0.15),
    )
    # Generic national profile mix; replace with NEDIS/119 microdata when available.
    profile_mix = {"심정지": 0.08, "뇌혈관": 0.12, "심혈관": 0.13, "외상": 0.17, "기타": 0.50}
    # Relative hourly pattern normalized to a 24-hour mean of one.
    raw_pattern = (0.45, 0.38, 0.34, 0.32, 0.34, 0.46, 0.70, 0.95, 1.12, 1.20, 1.24, 1.25,
                   1.22, 1.18, 1.16, 1.18, 1.27, 1.38, 1.45, 1.38, 1.20, 0.98, 0.76, 0.58)
    normalizer = 24.0 / sum(raw_pattern)
    hourly_rates = {
        hour: {node: rate * raw_pattern[hour] * normalizer for node, rate in villages.items()}
        for hour in range(24)
    }
    hourly_profiles = {hour: dict(profile_mix) for hour in range(24)}
    scenario = Scenario(
        network=RoadNetwork(edges=edges, positions=positions, time_multipliers={7: 1.18, 8: 1.25, 17: 1.22, 18: 1.28}),
        villages=villages,
        hospitals=hospitals,
        ambulances=ambulances,
        profiles=profiles,
        standby_nodes=tuple(standby_nodes),
        horizon_minutes=hours * 60.0,
        max_transfers=1,
        hourly_village_rates=hourly_rates,
        hourly_profile_probabilities=hourly_profiles,
    )
    validate_scenario(scenario, source="build_national_overview_scenario")
    return scenario


def national_data_summary(region: str | None = None) -> dict[str, int | str]:
    provinces = _select_provinces(region)
    return {
        "region": provinces[0].name if len(provinces) == 1 else "전국",
        "provinces": len(provinces),
        "population_2024": sum(row.population for row in provinces),
        "elderly_65plus_2024": sum(row.elderly_65plus for row in provinces),
        "dispatches_2024": sum(row.dispatches_2024 for row in provinces),
    }


__all__ = [
    "PROVINCES",
    "OFFICIAL_DISPATCH_TOTAL_2024",
    "OFFICIAL_POPULATION_TOTAL_2024",
    "OFFICIAL_ELDERLY_TOTAL_2024",
    "build_national_overview_scenario",
    "national_data_summary",
]
