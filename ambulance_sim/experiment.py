from __future__ import annotations

from dataclasses import dataclass
import csv
import json
import math
from pathlib import Path
import statistics
from typing import Callable, Mapping, Protocol

from .engine import Simulation
from .initialization import randomized_initial_locations
from .model import Scenario
from .policy import FixedPlacementPolicy


class PolicyLike(Protocol):
    def choose_ambulance(self, sim: Simulation, patient): ...
    def choose_hospital(self, sim: Simulation, patient, location: str, now: float): ...
    def choose_restock_location(self, sim: Simulation, ambulance): ...
    def choose_standby_location(self, sim: Simulation, ambulance): ...


ScenarioFactory = Callable[[], Scenario]
PolicyFactory = Callable[[], PolicyLike]


CI95_NOTE = (
    "95% CI is the normal approximation mean +/- 1.96*sd/sqrt(n) over independent episodes; "
    "it is null when fewer than 2 episodes were run because the dispersion cannot be estimated"
)


@dataclass(frozen=True)
class MetricEstimate:
    sample_size: int
    mean: float
    # ``None`` when a single episode leaves no dispersion to estimate.
    standard_deviation: float | None
    ci95_low: float | None
    ci95_high: float | None

    def as_dict(self) -> dict[str, float | int | str | None]:
        def optional(value: float | None) -> float | None:
            return None if value is None else round(value, 6)

        return {
            "sample_size": self.sample_size,
            "mean": round(self.mean, 6),
            "standard_deviation": optional(self.standard_deviation),
            "ci95_low": optional(self.ci95_low),
            "ci95_high": optional(self.ci95_high),
            "ci95_note": CI95_NOTE,
        }


def _estimate(values: list[float]) -> MetricEstimate:
    if not values:
        raise ValueError("at least one episode is required")
    mean = statistics.fmean(values)
    if len(values) < 2:
        return MetricEstimate(len(values), mean, None, None, None)
    sd = statistics.stdev(values)
    half_width = 1.96 * sd / math.sqrt(len(values))
    return MetricEstimate(len(values), mean, sd, mean - half_width, mean + half_width)


def run_experiment(
    scenario_factory: ScenarioFactory,
    policies: Mapping[str, PolicyFactory],
    episodes: int,
    base_seed: int = 0,
) -> dict[str, object]:
    """Compare policies with common random-number seeds.

    Every policy sees the same sequence of episode seeds.  A fresh scenario and
    policy instance are created for every run because the event engine mutates
    ambulance, hospital, and patient state.
    """
    if episodes < 1:
        raise ValueError("episodes must be at least 1")
    if not policies:
        raise ValueError("at least one policy is required")

    raw: dict[str, list[dict[str, object]]] = {name: [] for name in policies}
    for offset in range(episodes):
        seed = base_seed + offset
        for name, policy_factory in policies.items():
            summary = Simulation(scenario_factory(), policy=policy_factory(), seed=seed).run()
            raw[name].append(summary)

    return _assemble_result(raw, episodes, base_seed, list(policies))


def _assemble_result(
    raw: dict[str, list[dict[str, object]]],
    episodes: int,
    base_seed: int,
    names: list[str],
) -> dict[str, object]:
    estimates: dict[str, dict[str, dict[str, float]]] = {}
    for name, rows in raw.items():
        numeric_keys = sorted({
            key for row in rows for key, value in row.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        })
        estimates[name] = {}
        for key in numeric_keys:
            values = [
                float(row[key]) for row in rows
                if isinstance(row.get(key), (int, float)) and not isinstance(row.get(key), bool)
            ]
            if values:
                estimates[name][key] = _estimate(values).as_dict()

    paired_differences: dict[str, dict[str, float]] = {}
    if len(names) > 1:
        baseline = names[0]
        for challenger in names[1:]:
            differences = [
                float(other["expected_saved"]) - float(base["expected_saved"])
                for base, other in zip(raw[baseline], raw[challenger])
            ]
            paired_differences[f"{challenger}_minus_{baseline}"] = _estimate(differences).as_dict()

    return {
        "episodes": episodes,
        "base_seed": base_seed,
        "policy_order": names,
        "estimates": estimates,
        "paired_expected_saved_differences": paired_differences,
        "runs": raw,
    }


def evaluate_standby_candidates(
    scenario_factory: ScenarioFactory,
    target_ambulance_id: str,
    candidates: list[str] | tuple[str, ...],
    episodes: int,
    base_seed: int = 0,
    *,
    background_location_weights: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Evaluate one permanent standby-location decision through full episodes.

    The first candidate is the paired-comparison baseline.  When background
    weights are supplied, every candidate sees the same seed-specific locations
    for all other ambulances while the target ambulance remains fixed.
    """
    if episodes < 1:
        raise ValueError("episodes must be at least 1")
    candidate_names = list(dict.fromkeys(candidates))
    if not candidate_names:
        raise ValueError("at least one standby candidate is required")
    raw: dict[str, list[dict[str, object]]] = {candidate: [] for candidate in candidate_names}
    for offset in range(episodes):
        seed = base_seed + offset
        for candidate in candidate_names:
            scenario = scenario_factory()
            if target_ambulance_id not in scenario.ambulances:
                raise ValueError(f"unknown target ambulance {target_ambulance_id!r}")
            if candidate not in scenario.standby_nodes:
                raise ValueError(f"candidate {candidate!r} is not in scenario.standby_nodes")
            if background_location_weights:
                scenario = randomized_initial_locations(
                    scenario,
                    background_location_weights,
                    seed=seed,
                    fixed_ambulance_ids=frozenset({target_ambulance_id}),
                )
            scenario.ambulances[target_ambulance_id].location = candidate
            policy = FixedPlacementPolicy({target_ambulance_id: candidate})
            raw[candidate].append(Simulation(scenario, policy=policy, seed=seed).run())
    result = _assemble_result(raw, episodes, base_seed, candidate_names)
    result["experiment_type"] = "standby_placement"
    result["target_ambulance_id"] = target_ambulance_id
    result["background_location_weights"] = dict(background_location_weights or {})
    return result


def write_experiment_outputs(result: dict[str, object], output_directory: str | Path) -> tuple[Path, Path]:
    """Write a complete JSON result and a tidy per-episode CSV table."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "experiment_summary.json"
    csv_path = output / "episode_results.csv"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    runs = result.get("runs")
    if not isinstance(runs, dict):
        raise ValueError("result does not contain experiment runs")
    rows: list[dict[str, object]] = []
    base_seed = int(result["base_seed"])
    for policy, summaries in runs.items():
        if not isinstance(summaries, list):
            raise ValueError("each policy run collection must be a list")
        for episode, summary in enumerate(summaries):
            if not isinstance(summary, dict):
                raise ValueError("each episode summary must be a mapping")
            row = {
                "policy": policy,
                "episode": episode,
                "seed": base_seed + episode,
            }
            for key, value in summary.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    row[key] = value
                else:
                    row[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
            rows.append(row)

    fixed = ["policy", "episode", "seed"]
    dynamic = sorted({key for row in rows for key in row if key not in fixed})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fixed + dynamic)
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path
