"""Gyeonggi municipal planning scenario.

The event calculation is performed separately for all 31 municipalities.
Municipal dispatch demand is currently an explicitly labelled estimate: the
official province total is allocated using provisional population weights.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..io import validate_scenario
from ..model import Ambulance, Hospital, PatientProfile, RoadNetwork, Scenario


GYEONGGI_OFFICIAL_DISPATCHES_2024 = 799_302
GYEONGGI_OFFICIAL_POPULATION_2024 = 13_694_685


@dataclass(frozen=True)
class MunicipalityDatum:
    code: str
    name: str
    population_weight: int
    longitude: float
    latitude: float


# Population weights are provisional planning inputs derived from the 2024
# resident-registration distribution. They are normalized before allocation;
# they must not be presented as official municipal dispatch totals.
MUNICIPALITIES: tuple[MunicipalityDatum, ...] = (
    MunicipalityDatum("suwon", "수원시", 1_197_000, 127.029, 37.264),
    MunicipalityDatum("seongnam", "성남시", 920_000, 127.126, 37.420),
    MunicipalityDatum("goyang", "고양시", 1_075_000, 126.832, 37.658),
    MunicipalityDatum("yongin", "용인시", 1_110_000, 127.178, 37.241),
    MunicipalityDatum("bucheon", "부천시", 773_000, 126.766, 37.504),
    MunicipalityDatum("ansan", "안산시", 621_000, 126.831, 37.322),
    MunicipalityDatum("anyang", "안양시", 550_000, 126.956, 37.394),
    MunicipalityDatum("namyangju", "남양주시", 733_000, 127.217, 37.636),
    MunicipalityDatum("hwaseong", "화성시", 1_010_000, 126.831, 37.199),
    MunicipalityDatum("pyeongtaek", "평택시", 594_000, 127.113, 36.992),
    MunicipalityDatum("uijeongbu", "의정부시", 464_000, 127.034, 37.738),
    MunicipalityDatum("siheung", "시흥시", 519_000, 126.803, 37.380),
    MunicipalityDatum("paju", "파주시", 511_000, 126.780, 37.760),
    MunicipalityDatum("gwangmyeong", "광명시", 279_000, 126.864, 37.478),
    MunicipalityDatum("gimpo", "김포시", 487_000, 126.715, 37.616),
    MunicipalityDatum("gunpo", "군포시", 258_000, 126.935, 37.361),
    MunicipalityDatum("gwangju", "광주시", 414_000, 127.255, 37.430),
    MunicipalityDatum("icheon", "이천시", 223_000, 127.435, 37.272),
    MunicipalityDatum("yangju", "양주시", 286_000, 127.045, 37.785),
    MunicipalityDatum("osan", "오산시", 240_000, 127.077, 37.150),
    MunicipalityDatum("guri", "구리시", 188_000, 127.130, 37.594),
    MunicipalityDatum("anseong", "안성시", 192_000, 127.279, 37.008),
    MunicipalityDatum("pocheon", "포천시", 143_000, 127.200, 37.894),
    MunicipalityDatum("uiwang", "의왕시", 158_000, 126.969, 37.345),
    MunicipalityDatum("hanam", "하남시", 330_000, 127.215, 37.540),
    MunicipalityDatum("yeoju", "여주시", 114_000, 127.637, 37.298),
    MunicipalityDatum("yangpyeong", "양평군", 126_000, 127.488, 37.491),
    MunicipalityDatum("gwacheon", "과천시", 86_000, 126.987, 37.430),
    MunicipalityDatum("gapyeong", "가평군", 62_000, 127.510, 37.832),
    MunicipalityDatum("yeoncheon", "연천군", 41_000, 127.075, 38.096),
    MunicipalityDatum("dongducheon", "동두천시", 88_000, 127.061, 37.904),
)


def _largest_remainder(total: int, weights: dict[str, float], *, minimum: int = 0) -> dict[str, int]:
    if total < minimum * len(weights):
        raise ValueError(f"total must be at least {minimum * len(weights)}")
    remaining = total - minimum * len(weights)
    denominator = sum(weights.values())
    quotas = {key: remaining * value / denominator for key, value in weights.items()}
    result = {key: minimum + math.floor(quota) for key, quota in quotas.items()}
    leftover = total - sum(result.values())
    for key in sorted(quotas, key=lambda item: (quotas[item] - math.floor(quotas[item]), item), reverse=True)[:leftover]:
        result[key] += 1
    return result


def estimated_municipal_dispatches() -> dict[str, int]:
    """Allocate the official province total; outputs are estimates, not observations."""
    return _largest_remainder(
        GYEONGGI_OFFICIAL_DISPATCHES_2024,
        {row.code: float(row.population_weight) for row in MUNICIPALITIES},
    )


def _haversine_km(a: MunicipalityDatum, b: MunicipalityDatum) -> float:
    radius = 6371.0
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = math.radians(b.latitude - a.latitude)
    dl = math.radians(b.longitude - a.longitude)
    value = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def build_gyeonggi_municipal_scenario(
    *, hours: float = 24.0, demand_scale: float = 0.01, representative_ambulances: int = 31,
) -> Scenario:
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("hours must be positive")
    if not math.isfinite(demand_scale) or not 0 < demand_scale <= 1:
        raise ValueError("municipal demand scale must be in (0, 1]")
    fleet = _largest_remainder(
        representative_ambulances,
        {row.code: float(row.population_weight) for row in MUNICIPALITIES},
        minimum=1,
    )
    dispatches = estimated_municipal_dispatches()
    edges: dict[str, dict[str, float]] = {}
    positions: dict[str, tuple[float, float]] = {}
    villages: dict[str, float] = {}
    hospitals: dict[str, Hospital] = {}
    ambulances: dict[str, Ambulance] = {}
    standby: list[str] = []

    for row in MUNICIPALITIES:
        demand, station, hospital_node = f"{row.name} 수요", f"{row.name} 119", f"{row.name} 대표응급"
        urban = row.population_weight >= 300_000
        response, transport = (7.0, 11.0) if urban else (9.5, 15.0)
        edges[demand] = {station: response, hospital_node: transport}
        edges[station] = {demand: response, hospital_node: transport * 0.7}
        edges[hospital_node] = {demand: transport, station: transport * 0.7}
        x = 5.0 + (row.longitude - 126.65) / (127.68 - 126.65) * 90.0
        y = 5.0 + (row.latitude - 36.95) / (38.15 - 36.95) * 90.0
        positions[demand] = (x, y)
        positions[station] = (x - 0.75, y + 0.75)
        positions[hospital_node] = (x + 0.75, y - 0.75)
        villages[demand] = dispatches[row.code] / (365.0 * 24.0) * demand_scale
        standby.append(station)
        hospitals[f"H-{row.code}"] = Hospital(
            id=f"H-{row.code}", location=hospital_node,
            capabilities={"심정지", "뇌혈관", "심혈관", "외상", "기타"},
            success_when_available=0.78, success_when_unavailable=0.25,
            capacity=max(1, round(dispatches[row.code] / 25_000)), treatment_minutes=45.0,
            transfer_delay_minutes=8.0,
            success_by_profile={"심정지": 0.48, "뇌혈관": 0.76, "심혈관": 0.82, "외상": 0.80, "기타": 0.93},
        )
        for unit in range(1, fleet[row.code] + 1):
            ident = f"A-{row.code}-{unit:02d}"
            ambulances[ident] = Ambulance(ident, station, restock_minutes=10.0)

    # Connect each municipality to its four nearest neighbors.  These are
    # centroid-distance planning links, not turn-by-turn road observations.
    for row in MUNICIPALITIES:
        nearest = sorted((other for other in MUNICIPALITIES if other != row), key=lambda other: _haversine_km(row, other))[:4]
        source = f"{row.name} 119"
        for other in nearest:
            target = f"{other.name} 119"
            minutes = max(8.0, _haversine_km(row, other) * 1.35 / 50.0 * 60.0)
            edges[source][target] = round(minutes, 2)
            edges[target][source] = round(minutes, 2)

    profiles = (
        PatientProfile("심정지", 4, 0.090, 12, 0.08),
        PatientProfile("뇌혈관", 20, 0.025, 10, 0.01),
        PatientProfile("심혈관", 15, 0.030, 10, 0.02),
        PatientProfile("외상", 10, 0.035, 14, 0.04),
        PatientProfile("기타", 45, 0.008, 9, 0.15),
    )
    mix = {"심정지": 0.08, "뇌혈관": 0.12, "심혈관": 0.13, "외상": 0.17, "기타": 0.50}
    raw = (0.45, 0.38, 0.34, 0.32, 0.34, 0.46, 0.70, 0.95, 1.12, 1.20, 1.24, 1.25,
           1.22, 1.18, 1.16, 1.18, 1.27, 1.38, 1.45, 1.38, 1.20, 0.98, 0.76, 0.58)
    normalizer = 24.0 / sum(raw)
    hourly_rates = {hour: {node: rate * raw[hour] * normalizer for node, rate in villages.items()} for hour in range(24)}
    scenario = Scenario(
        network=RoadNetwork(edges, {7: 1.18, 8: 1.25, 17: 1.22, 18: 1.28}, positions),
        villages=villages, hospitals=hospitals, ambulances=ambulances, profiles=profiles,
        standby_nodes=tuple(standby), horizon_minutes=hours * 60.0, max_transfers=1,
        hourly_village_rates=hourly_rates,
        hourly_profile_probabilities={hour: dict(mix) for hour in range(24)},
    )
    validate_scenario(scenario, source="build_gyeonggi_municipal_scenario")
    return scenario


def gyeonggi_municipal_summary() -> dict[str, int | str]:
    return {
        "region": "경기",
        "detail": "municipal",
        "municipalities": len(MUNICIPALITIES),
        "official_population_2024": GYEONGGI_OFFICIAL_POPULATION_2024,
        "official_dispatches_2024": GYEONGGI_OFFICIAL_DISPATCHES_2024,
        "municipal_demand_basis": "population-proportional estimate",
    }


__all__ = ["MUNICIPALITIES", "build_gyeonggi_municipal_scenario", "estimated_municipal_dispatches", "gyeonggi_municipal_summary"]
