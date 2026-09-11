"""Strict, municipality-local hospital placement optimization.

Unlike the legacy overview models, this module never creates representative
hospitals or centroid travel times.  It consumes an explicit local Scenario
whose graph already contains measured road travel times, existing hospitals,
demand points and candidate-site nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from .engine import Simulation
from .io import load_scenario, scenario_from_dict, scenario_to_dict
from .model import Hospital, Scenario
from .policy import GreedySurvivalPolicy


@dataclass(frozen=True)
class CandidateHospital:
    id: str
    name: str
    municipality_code: str
    source_id: str
    candidate_type: str
    location: str
    capabilities: frozenset[str]
    capacity: int
    success_when_available: float
    success_when_unavailable: float = 0.0
    treatment_minutes: float = 45.0
    transfer_delay_minutes: float = 5.0
    cost: float = 0.0

    def activate(self) -> Hospital:
        return Hospital(
            id=self.id,
            location=self.location,
            capabilities=set(self.capabilities),
            success_when_available=self.success_when_available,
            success_when_unavailable=self.success_when_unavailable,
            capacity=self.capacity,
            treatment_minutes=self.treatment_minutes,
            transfer_delay_minutes=self.transfer_delay_minutes,
        )


@dataclass(frozen=True)
class MunicipalPlacementProblem:
    municipality_code: str
    municipality_name: str
    scenario_path: Path
    scenario: Scenario
    candidates: tuple[CandidateHospital, ...]
    provenance: dict[str, Any]
    resource_municipality_codes: dict[str, dict[str, str]]


def _required(record: dict[str, Any], key: str, source: Path) -> Any:
    if key not in record:
        raise ValueError(f"{source}: missing required field {key!r}")
    return record[key]


def load_municipal_placement_problem(path: str | Path) -> MunicipalPlacementProblem:
    """Load one municipality. Missing real inputs fail instead of being invented."""
    manifest_path = Path(path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{manifest_path}: placement manifest not found") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path}: root must be an object")
    code = str(_required(manifest, "municipality_code", manifest_path)).strip()
    name = str(_required(manifest, "municipality_name", manifest_path)).strip()
    scenario_path = manifest_path.parent / str(_required(manifest, "scenario", manifest_path))
    scenario = load_scenario(scenario_path)
    raw_candidates = _required(manifest, "candidate_hospitals", manifest_path)
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError(f"{manifest_path}: candidate_hospitals must be a non-empty array")
    candidates: list[CandidateHospital] = []
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, dict):
            raise ValueError(f"{manifest_path}: candidate_hospitals[{index}] must be an object")
        capabilities = _required(raw, "capabilities", manifest_path)
        if not isinstance(capabilities, list) or not capabilities or any(not isinstance(item, str) or not item.strip() for item in capabilities):
            raise ValueError(f"{manifest_path}: candidate_hospitals[{index}].capabilities must be a non-empty string array")
        capacity = _required(raw, "capacity", manifest_path)
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise ValueError(f"{manifest_path}: candidate_hospitals[{index}].capacity must be an integer")
        candidates.append(CandidateHospital(
            id=str(_required(raw, "id", manifest_path)).strip(),
            name=str(_required(raw, "name", manifest_path)).strip(),
            municipality_code=str(_required(raw, "municipality_code", manifest_path)).strip(),
            source_id=str(_required(raw, "source_id", manifest_path)).strip(),
            candidate_type=str(raw.get("candidate_type", "new_build")).strip(),
            location=str(_required(raw, "location", manifest_path)).strip(),
            capabilities=frozenset(item.strip() for item in capabilities),
            capacity=capacity,
            success_when_available=float(_required(raw, "success_when_available", manifest_path)),
            success_when_unavailable=float(raw.get("success_when_unavailable", 0.0)),
            treatment_minutes=float(raw.get("treatment_minutes", 45.0)),
            transfer_delay_minutes=float(raw.get("transfer_delay_minutes", 5.0)),
            cost=float(raw.get("cost", 0.0)),
        ))
    problem = MunicipalPlacementProblem(
        municipality_code=code,
        municipality_name=name,
        scenario_path=scenario_path,
        scenario=scenario,
        candidates=tuple(candidates),
        provenance=dict(manifest.get("provenance", {})),
        resource_municipality_codes={
            str(group): {str(ident): str(value) for ident, value in dict(records).items()}
            for group, records in dict(manifest.get("resource_municipality_codes", {})).items()
        },
    )
    validate_municipal_placement_problem(problem)
    return problem


def validate_municipal_placement_problem(problem: MunicipalPlacementProblem) -> None:
    code = problem.municipality_code
    prefix = f"{code}::"
    graph_nodes = set(problem.scenario.network.edges)
    graph_nodes.update(node for neighbors in problem.scenario.network.edges.values() for node in neighbors)
    if not graph_nodes or any(not node.startswith(prefix) for node in graph_nodes):
        raise ValueError(f"{code}: every road node must belong to this municipality and start with {prefix!r}")
    if not problem.scenario.villages:
        raise ValueError(f"{code}: real demand points are required")
    if not problem.scenario.ambulances:
        raise ValueError(f"{code}: real local ambulance/station inputs are required")
    local_locations = {
        "demand": tuple(problem.scenario.villages),
        "existing hospital": tuple(hospital.location for hospital in problem.scenario.hospitals.values()),
        "ambulance": tuple(ambulance.location for ambulance in problem.scenario.ambulances.values()),
        "standby": tuple(problem.scenario.standby_nodes),
    }
    for label, locations in local_locations.items():
        external = [location for location in locations if not location.startswith(prefix)]
        if external:
            raise ValueError(f"{code}: cross-municipality {label} location is forbidden: {external[0]!r}")
        missing = [location for location in locations if location not in graph_nodes]
        if missing:
            raise ValueError(f"{code}: {label} location has no road-time node: {missing[0]!r}")
        without_coordinates = [location for location in locations if location not in problem.scenario.network.positions]
        if without_coordinates:
            raise ValueError(f"{code}: {label} location has no actual coordinate: {without_coordinates[0]!r}")
    required_provenance = {"hospital_source", "demand_source", "road_time_source", "candidate_source"}
    absent_provenance = sorted(required_provenance - set(problem.provenance))
    if absent_provenance:
        raise ValueError(f"{code}: missing provenance fields: {', '.join(absent_provenance)}")
    road_source = problem.provenance.get("road_time_source")
    required_road_fields = {"provider", "extracted_at", "routing_profile", "unit"}
    if not isinstance(road_source, dict) or required_road_fields - set(road_source) or road_source.get("unit") != "minutes":
        raise ValueError(f"{code}: road_time_source must include provider, extracted_at, routing_profile, and unit='minutes'")
    expected_registry = {
        "demand": set(problem.scenario.villages),
        "hospitals": set(problem.scenario.hospitals),
        "ambulances": set(problem.scenario.ambulances),
    }
    for group, expected_ids in expected_registry.items():
        records = problem.resource_municipality_codes.get(group, {})
        if set(records) != expected_ids:
            raise ValueError(f"{code}: resource_municipality_codes.{group} must exactly identify every local resource")
        if any(resource_code != code for resource_code in records.values()):
            raise ValueError(f"{code}: authoritative municipality code mismatch in {group}")
    candidate_ids = set()
    existing_locations = {hospital.location for hospital in problem.scenario.hospitals.values()}
    for candidate in problem.candidates:
        if not all((candidate.id, candidate.name, candidate.source_id, candidate.location)):
            raise ValueError("candidate id, name, source_id, and location must be non-empty")
        if candidate.municipality_code != code:
            raise ValueError(f"{candidate.id}: cross-municipality candidate is forbidden")
        if candidate.candidate_type != "new_build":
            raise ValueError(f"{candidate.id}: only candidate_type='new_build' is currently supported")
        if candidate.id in problem.scenario.hospitals or candidate.id in candidate_ids:
            raise ValueError(f"{candidate.id}: duplicate hospital/candidate id")
        candidate_ids.add(candidate.id)
        if candidate.location not in graph_nodes:
            raise ValueError(f"{candidate.id}: candidate node has no measured road-time input")
        if candidate.location not in problem.scenario.network.positions:
            raise ValueError(f"{candidate.id}: candidate location has no actual coordinate")
        if candidate.location in existing_locations:
            raise ValueError(f"{candidate.id}: new-build candidate duplicates an existing hospital location")
        if (candidate.capacity < 1 or not 0 <= candidate.success_when_available <= 1
                or not 0 <= candidate.success_when_unavailable <= 1
                or candidate.treatment_minutes < 0 or candidate.transfer_delay_minutes < 0
                or candidate.cost < 0):
            raise ValueError(f"{candidate.id}: invalid capacity, success probability, or duration")
    demand_locations = set(problem.scenario.villages)
    response_origins = {ambulance.location for ambulance in problem.scenario.ambulances.values()}
    response_origins.update(problem.scenario.standby_nodes)
    facility_locations = {hospital.location for hospital in problem.scenario.hospitals.values()}
    facility_locations.update(candidate.location for candidate in problem.candidates)
    required_routes = {(origin, demand) for origin in response_origins for demand in demand_locations}
    required_routes.update((demand, facility) for demand in demand_locations for facility in facility_locations)
    required_routes.update((origin, target) for origin in facility_locations for target in facility_locations | set(problem.scenario.standby_nodes))
    for source, target in required_routes:
        if source != target and not math.isfinite(problem.scenario.network.travel_time(source, target)):
            raise ValueError(f"{code}: missing measured directional road route {source!r} -> {target!r}")


def _scenario_with_candidates(problem: MunicipalPlacementProblem, selected: tuple[CandidateHospital, ...]) -> Scenario:
    clone = scenario_from_dict(scenario_to_dict(problem.scenario), source=str(problem.scenario_path))
    for candidate in selected:
        clone.hospitals[candidate.id] = candidate.activate()
    return clone


CI95_NOTE = (
    "95% CI is the normal approximation mean +/- 1.96*sd/sqrt(n) over independent episodes; "
    "it is null when fewer than 2 episodes were run because the dispersion cannot be estimated"
)


def _estimate(values: list[float]) -> dict[str, Any]:
    mean = fmean(values)
    if len(values) < 2:
        standard_deviation = ci95_low = ci95_high = None
    else:
        standard_deviation = stdev(values)
        half_width = 1.96 * standard_deviation / math.sqrt(len(values))
        ci95_low, ci95_high = mean - half_width, mean + half_width
    return {
        "mean": mean,
        "standard_deviation": standard_deviation,
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
        "ci95_note": CI95_NOTE,
        "values": values,
    }


def optimize_hospital_placement(
    problem: MunicipalPlacementProblem,
    *,
    new_hospitals: int,
    episodes: int,
    seed: int,
    max_combinations: int = 10_000,
) -> dict[str, Any]:
    """Exhaustively evaluate local candidate combinations with paired seeds."""
    if not 0 <= new_hospitals <= len(problem.candidates):
        raise ValueError("new_hospitals must be between zero and the candidate count")
    if episodes < 1:
        raise ValueError("episodes must be at least 1")
    count = math.comb(len(problem.candidates), new_hospitals)
    if count > max_combinations:
        raise ValueError(f"exact search needs {count} combinations, exceeding max_combinations={max_combinations}")
    episode_seeds = [seed + episode for episode in range(episodes)]
    baseline_values = [
        Simulation(_scenario_with_candidates(problem, ()), policy=GreedySurvivalPolicy(), seed=episode_seed).run()["expected_saved"]
        for episode_seed in episode_seeds
    ]
    rows: list[dict[str, Any]] = []
    for selected in combinations(problem.candidates, new_hospitals):
        values = [
            Simulation(_scenario_with_candidates(problem, selected), policy=GreedySurvivalPolicy(), seed=episode_seed).run()["expected_saved"]
            for episode_seed in episode_seeds
        ]
        differences = [value - baseline for value, baseline in zip(values, baseline_values)]
        rows.append({
            "candidate_ids": [candidate.id for candidate in selected],
            "candidate_names": [candidate.name for candidate in selected],
            "total_cost": sum(candidate.cost for candidate in selected),
            "expected_saved": _estimate(values),
            "incremental_expected_saved": _estimate(differences),
            "expected_saved_mean": fmean(values),
            "incremental_expected_saved_mean": fmean(differences),
            "episodes": [
                {"episode": index, "seed": episode_seed, "expected_saved": value, "baseline": baseline, "difference": difference}
                for index, (episode_seed, value, baseline, difference) in enumerate(zip(episode_seeds, values, baseline_values, differences))
            ],
        })
    rows.sort(key=lambda row: (-row["incremental_expected_saved_mean"], row["total_cost"], row["candidate_ids"]))
    return {
        "municipality_code": problem.municipality_code,
        "municipality_name": problem.municipality_name,
        "objective": "local expected survivors",
        "cross_municipality_resources_allowed": False,
        "search": "exact exhaustive combinations",
        "new_hospitals": new_hospitals,
        "episodes": episodes,
        "baseline_existing_only": _estimate(baseline_values),
        "baseline_expected_saved_mean": fmean(baseline_values),
        "best": rows[0],
        "alternatives": rows,
        "provenance": problem.provenance,
    }


def optimize_municipality_directory(
    root: str | Path,
    *,
    new_hospitals: int,
    episodes: int,
    seed: int,
    max_combinations: int = 10_000,
) -> dict[str, Any]:
    """Optimize each municipality independently, then aggregate only its results."""
    root_path = Path(root)
    manifests = sorted(root_path.glob("*/placement.json"))
    if not manifests:
        raise ValueError(f"{root_path}: no municipality placement.json files found")
    results: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for index, manifest in enumerate(manifests):
        problem = load_municipal_placement_problem(manifest)
        if problem.municipality_code in seen_codes:
            raise ValueError(f"{problem.municipality_code}: duplicate municipality manifest")
        seen_codes.add(problem.municipality_code)
        results.append(optimize_hospital_placement(
            problem,
            new_hospitals=new_hospitals,
            episodes=episodes,
            seed=seed + index * 1_000_000,
            max_combinations=max_combinations,
        ))
    # Municipalities use disjoint seed streams, so the summed increment's
    # variance is the sum of the per-municipality variances of the mean.
    incremental_mean = sum(result["best"]["incremental_expected_saved_mean"] for result in results)
    if episodes < 2:
        incremental_sd = incremental_low = incremental_high = None
    else:
        best_estimates = [result["best"]["incremental_expected_saved"] for result in results]
        incremental_sd = math.sqrt(sum(estimate["standard_deviation"] ** 2 for estimate in best_estimates))
        half_width = 1.96 * math.sqrt(sum(
            estimate["standard_deviation"] ** 2 / len(estimate["values"]) for estimate in best_estimates
        ))
        incremental_low, incremental_high = incremental_mean - half_width, incremental_mean + half_width
    return {
        "calculation_unit": "independent municipality",
        "cross_municipality_resources_allowed": False,
        "municipality_count": len(results),
        "municipalities": results,
        "aggregate_after_optimization": {
            "baseline_expected_saved_mean": sum(result["baseline_expected_saved_mean"] for result in results),
            "optimized_expected_saved_mean": sum(result["best"]["expected_saved_mean"] for result in results),
            "incremental_expected_saved_mean": incremental_mean,
            "incremental_expected_saved_standard_deviation": incremental_sd,
            "incremental_expected_saved_ci95_low": incremental_low,
            "incremental_expected_saved_ci95_high": incremental_high,
            "aggregation_note": (
                "sum of each municipality's best incremental mean; municipalities are simulated independently "
                "with disjoint seeds, so sd_total = sqrt(sum(sd_i^2)) and the 95% CI is "
                "mean +/- 1.96*sqrt(sum(sd_i^2 / n_i)) (normal approximation); null when fewer than 2 episodes"
            ),
        },
    }


__all__ = [
    "CandidateHospital",
    "MunicipalPlacementProblem",
    "load_municipal_placement_problem",
    "validate_municipal_placement_problem",
    "optimize_hospital_placement",
    "optimize_municipality_directory",
]
