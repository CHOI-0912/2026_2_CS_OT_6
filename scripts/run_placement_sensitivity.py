"""Sensitivity analysis and seed sweep for one municipality's hospital placement.

Each sweep changes exactly one factor of the loaded placement problem, keeps
every other input fixed, and re-runs the exact placement search with the same
seed so that the comparison against the baseline run is paired.  The seed
sweep re-runs the untouched problem with different seeds instead.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.hospital_placement import (
    MunicipalPlacementProblem,
    load_municipal_placement_problem,
    optimize_hospital_placement,
    validate_municipal_placement_problem,
)
from ambulance_sim.io import scenario_from_dict, scenario_to_dict

DEFAULT_SWEEPS: dict[str, tuple[float, ...]] = {
    "daily_calls_scale": (0.8, 1.2),
    "treatment_minutes": (45.0, 120.0),
    "restock_minutes": (5.0, 15.0),
    "success_when_unavailable": (0.0, 0.5),
    "decay_rate_scale": (0.5, 2.0),
}
SWEEP_NAMES = (*DEFAULT_SWEEPS, "candidate_capacity", "transfer_delay_minutes")
SEED_SWEEP_STEP = 1000


def apply_sweep(problem: MunicipalPlacementProblem, sweep: str, value: float) -> MunicipalPlacementProblem:
    """Return a copy of ``problem`` with exactly one factor changed."""
    data = scenario_to_dict(problem.scenario)
    candidates = problem.candidates
    if sweep == "daily_calls_scale":
        data["villages"] = {node: rate * value for node, rate in data["villages"].items()}
        data["hourly_village_rates"] = {
            hour: {node: rate * value for node, rate in rows.items()}
            for hour, rows in data["hourly_village_rates"].items()
        }
    elif sweep == "treatment_minutes":
        for hospital in data["hospitals"].values():
            hospital["treatment_minutes"] = value
        candidates = tuple(replace(candidate, treatment_minutes=value) for candidate in candidates)
    elif sweep == "restock_minutes":
        for ambulance in data["ambulances"].values():
            ambulance["restock_minutes"] = value
    elif sweep == "success_when_unavailable":
        clamped = min(1.0, max(0.0, value))
        for hospital in data["hospitals"].values():
            hospital["success_when_unavailable"] = clamped
        candidates = tuple(replace(candidate, success_when_unavailable=clamped) for candidate in candidates)
    elif sweep == "decay_rate_scale":
        for profile in data["profiles"]:
            profile["decay_rate"] = profile["decay_rate"] * value
    elif sweep == "candidate_capacity":
        if value != int(value):
            raise ValueError(f"candidate_capacity must be an integer, got {value}")
        candidates = tuple(replace(candidate, capacity=int(value)) for candidate in candidates)
    elif sweep == "transfer_delay_minutes":
        for hospital in data["hospitals"].values():
            hospital["transfer_delay_minutes"] = value
        candidates = tuple(replace(candidate, transfer_delay_minutes=value) for candidate in candidates)
    else:
        raise ValueError(f"unknown sweep {sweep!r}; choose from {', '.join(SWEEP_NAMES)}")
    modified = replace(
        problem,
        scenario=scenario_from_dict(data, source=str(problem.scenario_path)),
        candidates=candidates,
    )
    validate_municipal_placement_problem(modified)
    return modified


def parse_sweeps(specs: list[str] | None) -> dict[str, tuple[float, ...]]:
    if not specs:
        return dict(DEFAULT_SWEEPS)
    sweeps: dict[str, tuple[float, ...]] = {}
    for spec in specs:
        name, separator, raw_values = spec.partition("=")
        name = name.strip()
        if not separator or name not in SWEEP_NAMES:
            raise ValueError(f"--sweep expects <name>=<v1,v2,...> with name in {', '.join(SWEEP_NAMES)}: {spec!r}")
        values = tuple(float(item) for item in raw_values.split(",") if item.strip())
        if not values:
            raise ValueError(f"--sweep {name}: at least one value is required")
        sweeps[name] = values
    return sweeps


def _run_row(result: dict[str, Any], baseline_best: list[str], *, run_type: str, sweep: str | None, value: float | None, seed: int) -> dict[str, Any]:
    ranking = [row["candidate_ids"] for row in result["alternatives"]]
    best = result["best"]
    return {
        "run_type": run_type,
        "sweep": sweep,
        "value": value,
        "seed": seed,
        "best_candidate_ids": best["candidate_ids"],
        "incremental_mean": best["incremental_expected_saved_mean"],
        "ci95": [best["incremental_expected_saved"]["ci95_low"], best["incremental_expected_saved"]["ci95_high"]],
        "rank_of_baseline_best": ranking.index(baseline_best) + 1,
    }


def run_sensitivity(
    problem: MunicipalPlacementProblem,
    *,
    new_hospitals: int,
    episodes: int,
    seed: int,
    sweeps: dict[str, tuple[float, ...]],
    seed_sweep: int,
    max_combinations: int = 10_000,
) -> dict[str, Any]:
    def optimize(target: MunicipalPlacementProblem, run_seed: int) -> dict[str, Any]:
        return optimize_hospital_placement(
            target, new_hospitals=new_hospitals, episodes=episodes, seed=run_seed, max_combinations=max_combinations,
        )

    baseline_result = optimize(problem, seed)
    baseline_best = baseline_result["best"]["candidate_ids"]
    baseline = _run_row(baseline_result, baseline_best, run_type="baseline", sweep=None, value=None, seed=seed)
    sweep_rows = [
        _run_row(optimize(apply_sweep(problem, sweep, value), seed), baseline_best, run_type="sweep", sweep=sweep, value=value, seed=seed)
        for sweep, values in sweeps.items()
        for value in values
    ]
    seed_rows = []
    for offset in range(1, seed_sweep + 1):
        run_seed = seed + offset * SEED_SWEEP_STEP
        seed_rows.append(_run_row(optimize(problem, run_seed), baseline_best, run_type="seed", sweep=None, value=None, seed=run_seed))
    checks = sweep_rows + seed_rows
    stays_best = sum(row["best_candidate_ids"] == baseline_best for row in checks)
    ever_best = sorted({tuple(row["best_candidate_ids"]) for row in (baseline, *checks)})
    return {
        "municipality_code": problem.municipality_code,
        "municipality_name": problem.municipality_name,
        "new_hospitals": new_hospitals,
        "episodes": episodes,
        "seed": seed,
        "baseline": baseline,
        "sweeps": sweep_rows,
        "seed_sweep": seed_rows,
        "robustness": {
            "runs": len(checks),
            "baseline_best_stays_best": stays_best,
            "fraction": stays_best / len(checks) if checks else None,
            "ever_best_combinations": [list(ids) for ids in ever_best],
        },
    }


def _rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [summary["baseline"], *summary["sweeps"], *summary["seed_sweep"]]


def write_outputs(summary: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "sensitivity_summary.json"
    csv_path = output_dir / "sensitivity_table.csv"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["run_type", "sweep", "value", "seed", "best_candidate_ids", "incremental_mean", "ci95_low", "ci95_high", "rank_of_baseline_best"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in _rows(summary):
            writer.writerow({
                "run_type": row["run_type"],
                "sweep": row["sweep"] or "",
                "value": "" if row["value"] is None else row["value"],
                "seed": row["seed"],
                "best_candidate_ids": "|".join(row["best_candidate_ids"]),
                "incremental_mean": row["incremental_mean"],
                "ci95_low": row["ci95"][0],
                "ci95_high": row["ci95"][1],
                "rank_of_baseline_best": row["rank_of_baseline_best"],
            })
    return json_path, csv_path


def print_table(summary: dict[str, Any]) -> None:
    print(f"[{summary['municipality_code']} {summary['municipality_name']}] 신설 {summary['new_hospitals']}곳, 에피소드 {summary['episodes']}회, 기준 시드 {summary['seed']}")
    print(f"{'구분':<26}{'값':>8}  {'시드':>7}  {'최적 조합':<32}{'증분 생존':>10}  {'95% CI':<24}{'기준최적 순위':>6}")
    for row in _rows(summary):
        label = row["sweep"] or row["run_type"]
        value = "" if row["value"] is None else f"{row['value']:g}"
        interval = f"{row['ci95'][0]:.3f}~{row['ci95'][1]:.3f}"
        print(f"{label:<26}{value:>8}  {row['seed']:>7}  {'+'.join(row['best_candidate_ids']):<32}{row['incremental_mean']:>10.3f}  {interval:<24}{row['rank_of_baseline_best']:>6}")
    robustness = summary["robustness"]
    fraction = "n/a" if robustness["fraction"] is None else f"{robustness['fraction']:.3f}"
    print(f"강건성: 기준 최적 조합 {'+'.join(summary['baseline']['best_candidate_ids'])} 1위 유지 {robustness['baseline_best_stays_best']}/{robustness['runs']} ({fraction})")
    print("1위 경험 조합: " + ", ".join("+".join(ids) for ids in robustness["ever_best_combinations"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--placement", type=Path, required=True, help="Municipality placement.json manifest.")
    parser.add_argument("--new-hospitals", type=int, required=True)
    parser.add_argument("--episodes", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sweep", action="append", metavar="NAME=V1,V2,...", help=f"One of: {', '.join(SWEEP_NAMES)}. Repeatable; defaults to the built-in set.")
    parser.add_argument("--seed-sweep", type=int, default=0, help=f"Re-run the baseline problem with this many extra seeds (seed+{SEED_SWEEP_STEP}, seed+{2 * SEED_SWEEP_STEP}, ...).")
    parser.add_argument("--max-combinations", type=int, default=10_000, help="Safety bound for the exact candidate search (same as the CLI).")
    args = parser.parse_args(argv)
    if args.seed_sweep < 0:
        parser.error("--seed-sweep must be zero or more")
    try:
        sweeps = parse_sweeps(args.sweep)
    except ValueError as exc:
        parser.error(str(exc))
    problem = load_municipal_placement_problem(args.placement)
    summary = run_sensitivity(
        problem,
        new_hospitals=args.new_hospitals,
        episodes=args.episodes,
        seed=args.seed,
        sweeps=sweeps,
        seed_sweep=args.seed_sweep,
        max_combinations=args.max_combinations,
    )
    json_path, csv_path = write_outputs(summary, args.output_dir)
    print_table(summary)
    print(f"저장: {json_path}, {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
