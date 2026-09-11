"""Strict, municipality-local standby-post assignment for an existing fleet.

This module never creates an ambulance, a station, or a road time.  It consumes
one municipality's Scenario, whose graph already holds measured travel times,
plus a manifest that names every standby post together with the source it came
from.  The decision variable is only *where an existing ambulance stands by*
during the free window; the fleet size and the home stations are inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from .engine import Simulation
# The confidence-interval convention is deliberately shared with hospital
# placement so both optimizers report the same statistic.
from .hospital_placement import _estimate
from .io import load_scenario, scenario_from_dict, scenario_to_dict
from .model import Scenario
from .policy import ScheduledStandbyPolicy


POST_TYPES = ("grid_cell", "existing_base")


@dataclass(frozen=True)
class StandbyPost:
    id: str
    source_id: str
    name: str
    post_type: str
    municipality_code: str
    location: str
    cell_population_estimate: float | None = None


@dataclass(frozen=True)
class MunicipalStandbyProblem:
    municipality_code: str
    municipality_name: str
    scenario_path: Path
    scenario: Scenario
    posts: tuple[StandbyPost, ...]
    ambulance_home_bases: dict[str, str]
    free_hours: tuple[int, ...]
    home_hours: tuple[int, ...]
    provenance: dict[str, Any]
    resource_municipality_codes: dict[str, dict[str, str]]

    def post_locations(self) -> dict[str, str]:
        return {post.id: post.location for post in self.posts}


def _required(record: Mapping[str, Any], key: str, source: Path) -> Any:
    if key not in record:
        raise ValueError(f"{source}: missing required field {key!r}")
    return record[key]


def _hours(raw: Any, key: str, source: Path) -> tuple[int, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{source}: schedule.{key} must be a non-empty array of hours")
    hours: list[int] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
            raise ValueError(f"{source}: schedule.{key} must contain integers from 0 to 23")
        hours.append(value)
    return tuple(hours)


def load_municipal_standby_problem(path: str | Path) -> MunicipalStandbyProblem:
    """Load one municipality's standby problem; missing real inputs fail loudly."""
    manifest_path = Path(path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{manifest_path}: standby manifest not found") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path}: root must be an object")
    code = str(_required(manifest, "municipality_code", manifest_path)).strip()
    name = str(_required(manifest, "municipality_name", manifest_path)).strip()
    scenario_path = manifest_path.parent / str(_required(manifest, "scenario", manifest_path))
    scenario = load_scenario(scenario_path)

    raw_posts = _required(manifest, "standby_posts", manifest_path)
    if not isinstance(raw_posts, list) or not raw_posts:
        raise ValueError(f"{manifest_path}: standby_posts must be a non-empty array")
    posts: list[StandbyPost] = []
    for index, raw in enumerate(raw_posts):
        if not isinstance(raw, dict):
            raise ValueError(f"{manifest_path}: standby_posts[{index}] must be an object")
        population = raw.get("cell_population_estimate", None)
        if population is not None and (isinstance(population, bool) or not isinstance(population, (int, float))):
            raise ValueError(f"{manifest_path}: standby_posts[{index}].cell_population_estimate must be a number or null")
        posts.append(StandbyPost(
            id=str(_required(raw, "id", manifest_path)).strip(),
            source_id=str(_required(raw, "source_id", manifest_path)).strip(),
            name=str(_required(raw, "name", manifest_path)).strip(),
            post_type=str(_required(raw, "post_type", manifest_path)).strip(),
            municipality_code=str(_required(raw, "municipality_code", manifest_path)).strip(),
            location=str(_required(raw, "location", manifest_path)).strip(),
            cell_population_estimate=None if population is None else float(population),
        ))

    raw_homes = _required(manifest, "ambulance_home_bases", manifest_path)
    if not isinstance(raw_homes, dict) or not raw_homes:
        raise ValueError(f"{manifest_path}: ambulance_home_bases must be a non-empty object")
    home_bases = {str(ident).strip(): str(node).strip() for ident, node in raw_homes.items()}

    schedule = _required(manifest, "schedule", manifest_path)
    if not isinstance(schedule, dict):
        raise ValueError(f"{manifest_path}: schedule must be an object")
    free_hours = _hours(_required(schedule, "free_hours", manifest_path), "free_hours", manifest_path)
    home_hours = _hours(_required(schedule, "home_hours", manifest_path), "home_hours", manifest_path)

    problem = MunicipalStandbyProblem(
        municipality_code=code,
        municipality_name=name,
        scenario_path=scenario_path,
        scenario=scenario,
        posts=tuple(posts),
        ambulance_home_bases=home_bases,
        free_hours=free_hours,
        home_hours=home_hours,
        provenance=dict(manifest.get("provenance", {})),
        resource_municipality_codes={
            str(group): {str(ident): str(value) for ident, value in dict(records).items()}
            for group, records in dict(manifest.get("resource_municipality_codes", {})).items()
        },
    )
    validate_municipal_standby_problem(problem)
    return problem


def validate_municipal_standby_problem(problem: MunicipalStandbyProblem) -> None:
    code = problem.municipality_code
    prefix = f"{code}::"
    base_prefix = f"{code}::ambulance_base::"
    post_prefix = f"{code}::standby::"
    scenario = problem.scenario
    graph_nodes = set(scenario.network.edges)
    graph_nodes.update(node for neighbors in scenario.network.edges.values() for node in neighbors)
    if not graph_nodes or any(not node.startswith(prefix) for node in graph_nodes):
        raise ValueError(f"{code}: every road node must belong to this municipality and start with {prefix!r}")
    if not scenario.villages:
        raise ValueError(f"{code}: real demand points are required")
    if not scenario.ambulances:
        raise ValueError(f"{code}: real local ambulance/station inputs are required")
    if not problem.posts:
        raise ValueError(f"{code}: at least one standby post is required")

    local_locations = {
        "demand": tuple(scenario.villages),
        "existing hospital": tuple(hospital.location for hospital in scenario.hospitals.values()),
        "ambulance": tuple(ambulance.location for ambulance in scenario.ambulances.values()),
        "standby": tuple(scenario.standby_nodes),
        "standby post": tuple(post.location for post in problem.posts),
    }
    for label, locations in local_locations.items():
        external = [location for location in locations if not location.startswith(prefix)]
        if external:
            raise ValueError(f"{code}: cross-municipality {label} location is forbidden: {external[0]!r}")
        missing = [location for location in locations if location not in graph_nodes]
        if missing:
            raise ValueError(f"{code}: {label} location has no road-time node: {missing[0]!r}")
        without_coordinates = [location for location in locations if location not in scenario.network.positions]
        if without_coordinates:
            raise ValueError(f"{code}: {label} location has no actual coordinate: {without_coordinates[0]!r}")

    required_provenance = {
        "hospital_source", "demand_source", "candidate_source",
        "road_time_source", "model_parameters", "generated_by",
    }
    absent_provenance = sorted(required_provenance - set(problem.provenance))
    if absent_provenance:
        raise ValueError(f"{code}: missing provenance fields: {', '.join(absent_provenance)}")
    road_source = problem.provenance.get("road_time_source")
    required_road_fields = {"provider", "extracted_at", "routing_profile", "unit"}
    if not isinstance(road_source, dict) or required_road_fields - set(road_source) or road_source.get("unit") != "minutes":
        raise ValueError(f"{code}: road_time_source must include provider, extracted_at, routing_profile, and unit='minutes'")

    expected_registry = {
        "demand": set(scenario.villages),
        "hospitals": set(scenario.hospitals),
        "ambulances": set(scenario.ambulances),
    }
    for group, expected_ids in expected_registry.items():
        records = problem.resource_municipality_codes.get(group, {})
        if set(records) != expected_ids:
            raise ValueError(f"{code}: resource_municipality_codes.{group} must exactly identify every local resource")
        if any(resource_code != code for resource_code in records.values()):
            raise ValueError(f"{code}: authoritative municipality code mismatch in {group}")

    # Raises when the two windows overlap or leave an hour of the day undefined.
    ScheduledStandbyPolicy(problem.free_hours, problem.home_hours)

    if set(problem.ambulance_home_bases) != set(scenario.ambulances):
        raise ValueError(f"{code}: ambulance_home_bases must name exactly every ambulance in the scenario")
    for ident, ambulance in scenario.ambulances.items():
        home = problem.ambulance_home_bases[ident]
        if not home.startswith(base_prefix):
            raise ValueError(f"{code}: ambulance {ident} home station must be an ambulance_base node: {home!r}")
        if home not in graph_nodes:
            raise ValueError(f"{code}: ambulance {ident} home station has no road-time node: {home!r}")
        if ambulance.home_base != home:
            raise ValueError(f"{code}: ambulance {ident} scenario home_base disagrees with the manifest")
        if ambulance.assigned_post is not None:
            raise ValueError(f"{code}: ambulance {ident} must start with no assigned standby post")

    seen_ids: set[str] = set()
    seen_locations: set[str] = set()
    for post in problem.posts:
        if not all((post.id, post.source_id, post.name, post.location)):
            raise ValueError(f"{code}: post id, source_id, name, and location must be non-empty")
        if post.municipality_code != code:
            raise ValueError(f"{post.id}: cross-municipality standby post is forbidden")
        if post.post_type not in POST_TYPES:
            raise ValueError(f"{post.id}: post_type must be one of: {', '.join(POST_TYPES)}")
        if post.id in seen_ids:
            raise ValueError(f"{post.id}: duplicate standby post id")
        seen_ids.add(post.id)
        if post.location in seen_locations:
            raise ValueError(f"{post.id}: duplicate standby post location {post.location!r}")
        seen_locations.add(post.location)
        expected_node_prefix = base_prefix if post.post_type == "existing_base" else post_prefix
        if not post.location.startswith(expected_node_prefix):
            raise ValueError(f"{post.id}: a {post.post_type} post node must start with {expected_node_prefix!r}")
        if post.location not in scenario.standby_nodes:
            raise ValueError(f"{post.id}: standby post is missing from scenario.standby_nodes")

    demand_nodes = set(_demand_nodes(scenario))
    base_nodes = set(problem.ambulance_home_bases.values())
    hospital_locations = {hospital.location for hospital in scenario.hospitals.values()}
    post_locations = set(seen_locations)
    required_routes = {(post, demand) for post in post_locations for demand in demand_nodes}
    required_routes.update((hospital, post) for hospital in hospital_locations for post in post_locations)
    required_routes.update((post, base) for post in post_locations for base in base_nodes)
    required_routes.update((base, post) for base in base_nodes for post in post_locations)
    # The baseline (every ambulance at home) needs the ordinary operating routes too:
    # station -> demand, demand -> hospital, hospital -> hospital (transfers) and
    # hospital -> station (restock/return).  Missing ones would silently strand patients.
    required_routes.update((base, demand) for base in base_nodes for demand in demand_nodes)
    required_routes.update((demand, hospital) for demand in demand_nodes for hospital in hospital_locations)
    required_routes.update((origin, target) for origin in hospital_locations for target in hospital_locations | base_nodes)
    for source, target in sorted(required_routes):
        if source != target and not math.isfinite(scenario.network.travel_time(source, target)):
            raise ValueError(f"{code}: missing measured directional road route {source!r} -> {target!r}")


def _demand_nodes(scenario: Scenario) -> tuple[str, ...]:
    nodes = set(scenario.villages)
    for rates in scenario.hourly_village_rates.values():
        nodes.update(rates)
    return tuple(sorted(nodes))


def _coverage_score(problem: MunicipalStandbyProblem, post: StandbyPost) -> float:
    """Deterministic pre-selection score: mean hourly demand weighted by nearness."""
    scenario = problem.scenario
    total = 0.0
    for village in _demand_nodes(scenario):
        travel = scenario.network.travel_time(post.location, village)
        if not math.isfinite(travel):
            continue
        mean_rate = fmean([scenario.demand_rate(village, hour * 60.0) for hour in range(24)])
        total += mean_rate / (1.0 + travel)
    return total


def _shortlisted_posts(problem: MunicipalStandbyProblem, shortlist: int | None) -> tuple[StandbyPost, ...]:
    if shortlist is None:
        return problem.posts
    if shortlist < 1:
        raise ValueError("shortlist must be at least 1")
    ranked = sorted(problem.posts, key=lambda post: (-_coverage_score(problem, post), post.id))
    keep = {post.id for post in ranked[:shortlist]}
    # Every existing station stays available so the search can always keep a
    # vehicle where it already is.
    keep.update(post.id for post in problem.posts if post.post_type == "existing_base")
    return tuple(post for post in problem.posts if post.id in keep)


def _scenario_with_assignment(problem: MunicipalStandbyProblem, assignment: Mapping[str, str]) -> Scenario:
    clone = scenario_from_dict(scenario_to_dict(problem.scenario), source=str(problem.scenario_path))
    locations = problem.post_locations()
    for ident, ambulance in clone.ambulances.items():
        post_id = assignment.get(ident)
        ambulance.assigned_post = None if post_id is None else locations[post_id]
    return clone


def _episode_values(
    problem: MunicipalStandbyProblem, assignment: Mapping[str, str], episode_seeds: list[int]
) -> list[float]:
    return [
        Simulation(
            _scenario_with_assignment(problem, assignment),
            policy=ScheduledStandbyPolicy(problem.free_hours, problem.home_hours),
            seed=episode_seed,
        ).run()["expected_saved"]
        for episode_seed in episode_seeds
    ]


def evaluate_assignment(
    problem: MunicipalStandbyProblem,
    assignment: Mapping[str, str],
    episodes: int,
    seed: int,
) -> dict[str, Any]:
    """Run full episodes for one assignment and return its expected-survivor estimate."""
    if episodes < 1:
        raise ValueError("episodes must be at least 1")
    unknown_ambulances = sorted(set(assignment) - set(problem.scenario.ambulances))
    if unknown_ambulances:
        raise ValueError(f"unknown ambulance ids: {', '.join(unknown_ambulances)}")
    known_posts = {post.id for post in problem.posts}
    unknown_posts = sorted(set(assignment.values()) - known_posts)
    if unknown_posts:
        raise ValueError(f"unknown standby post ids: {', '.join(unknown_posts)}")
    episode_seeds = [seed + episode for episode in range(episodes)]
    return _estimate(_episode_values(problem, assignment, episode_seeds))


def optimize_standby_placement(
    problem: MunicipalStandbyProblem,
    *,
    episodes: int,
    seed: int,
    movable: int | None = None,
    shortlist: int | None = None,
    allow_shared_posts: bool = False,
) -> dict[str, Any]:
    """Greedy forward search over (ambulance, post) moves with paired seeds.

    The baseline keeps every ambulance at its own station.  Each round evaluates
    every remaining move and accepts the one with the largest mean expected
    survivors; the search stops when no move improves on the current assignment
    or when ``movable`` vehicles have been moved.  This is a greedy solution and
    is not guaranteed to be the global optimum.
    """
    if episodes < 1:
        raise ValueError("episodes must be at least 1")
    fleet = list(problem.scenario.ambulances)
    if movable is None:
        movable = len(fleet)
    elif not 0 <= movable <= len(fleet):
        raise ValueError("movable must be between zero and the fleet size")
    posts = _shortlisted_posts(problem, shortlist)
    locations = problem.post_locations()
    episode_seeds = [seed + episode for episode in range(episodes)]

    baseline_values = _episode_values(problem, {}, episode_seeds)
    assignment: dict[str, str] = {}
    current_values = baseline_values
    current_mean = fmean(baseline_values)
    steps: list[dict[str, Any]] = []

    def incremental(values: list[float]) -> dict[str, Any]:
        return _estimate([value - baseline for value, baseline in zip(values, baseline_values)])

    while len(assignment) < movable:
        best: tuple[tuple[float, str, str], StandbyPost, list[float]] | None = None
        evaluated = 0
        for ambulance_id in fleet:
            if ambulance_id in assignment:
                continue
            for post in posts:
                if not allow_shared_posts and post.id in assignment.values():
                    continue
                values = _episode_values(problem, {**assignment, ambulance_id: post.id}, episode_seeds)
                evaluated += 1
                # Ties break on the ids so the same inputs always give the same move.
                key = (-fmean(values), ambulance_id, post.id)
                if best is None or key < best[0]:
                    best = (key, post, values)
        if best is None:
            break
        (negative_mean, ambulance_id, _), post, values = best
        gain = -negative_mean - current_mean
        if gain <= 0:
            break
        assignment[ambulance_id] = post.id
        current_values, current_mean = values, -negative_mean
        steps.append({
            "step": len(steps) + 1,
            "ambulance_id": ambulance_id,
            "post_id": post.id,
            "post_name": post.name,
            "post_location": post.location,
            "home_base": problem.ambulance_home_bases[ambulance_id],
            "evaluated_moves": evaluated,
            "assignment": dict(assignment),
            "expected_saved": _estimate(values),
            "incremental_expected_saved": incremental(values),
            "step_gain_mean": gain,
        })

    moved = [
        ambulance_id for ambulance_id, post_id in assignment.items()
        if locations[post_id] != problem.ambulance_home_bases[ambulance_id]
    ]
    return {
        "municipality_code": problem.municipality_code,
        "municipality_name": problem.municipality_name,
        "objective": "local expected survivors",
        "cross_municipality_resources_allowed": False,
        "search": "greedy forward selection over (ambulance, post) moves with paired episode seeds",
        "search_note": "greedy solution; the global optimum over all assignments is not guaranteed",
        "episodes": episodes,
        "movable": movable,
        "allow_shared_posts": allow_shared_posts,
        "shortlisted_posts": [post.id for post in posts],
        "baseline_home_bases": _estimate(baseline_values),
        "best": {
            "assignment": dict(assignment),
            "moved_ambulances": moved,
            "expected_saved": _estimate(current_values),
            "incremental_expected_saved": incremental(current_values),
        },
        "steps": steps,
        "provenance": problem.provenance,
        "schedule": {"free_hours": list(problem.free_hours), "home_hours": list(problem.home_hours)},
    }


__all__ = [
    "StandbyPost",
    "MunicipalStandbyProblem",
    "load_municipal_standby_problem",
    "validate_municipal_standby_problem",
    "evaluate_assignment",
    "optimize_standby_placement",
]
