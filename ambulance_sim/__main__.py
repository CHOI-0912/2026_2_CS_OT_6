from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Callable

from .engine import Simulation, build_default_scenario
from .experiment import evaluate_standby_candidates, run_experiment, write_experiment_outputs
from .io import load_scenario
from .hospital_placement import (
    load_municipal_placement_problem,
    optimize_hospital_placement,
    optimize_municipality_directory,
)
from .model import Scenario
from .standby_placement import load_municipal_standby_problem, optimize_standby_placement
from .legacy.gyeonggi_centroid import build_gyeonggi_municipal_scenario, gyeonggi_municipal_summary
from .legacy.national_aggregate import build_national_overview_scenario, national_data_summary
from .policy import GreedySurvivalPolicy, NearestHospitalPolicy, NoRepositionPolicy
from .visualize import write_visualization


POLICIES = {
    "greedy": GreedySurvivalPolicy,
    "no-reposition": NoRepositionPolicy,
    "nearest": NearestHospitalPolicy,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and compare ambulance SMDP policies.")
    parser.add_argument("--scenario", type=Path, help="UTF-8 JSON scenario; defaults to the built-in synthetic scenario.")
    parser.add_argument("--hospital-placement", type=Path, metavar="PLACEMENT_JSON", help="Optimize new hospitals inside one municipality using real local inputs.")
    parser.add_argument("--municipality-root", type=Path, help="Optimize each */placement.json municipality independently.")
    parser.add_argument("--new-hospitals", type=int, help="Number of candidate hospitals to activate in each municipality.")
    parser.add_argument("--max-combinations", type=int, default=10_000, help="Safety bound for exact candidate search.")
    parser.add_argument("--standby-placement", type=Path, metavar="STANDBY_JSON", help="Reposition the existing fleet to standby posts inside one municipality.")
    parser.add_argument("--movable", type=int, help="Maximum ambulances the greedy standby search may move (default: the whole fleet).")
    parser.add_argument("--shortlist", type=int, help="Search only the top-K standby posts by demand coverage, plus every existing base.")
    parser.add_argument("--allow-shared-posts", action="store_true", help="Allow more than one ambulance to stand by at the same post.")
    parser.add_argument("--legacy-overview", action="store_true", help="Explicitly enable archived aggregate/centroid overview modes (not placement analysis).")
    parser.add_argument("--national", action="store_true", help="[LEGACY] Use the 17-province aggregate overview scenario.")
    parser.add_argument("--region", help="Show one province in national mode, by Korean name or code (example: 경기).")
    parser.add_argument("--detail", choices=("province", "municipal"), default="province", help="Geographic calculation unit (default: province).")
    parser.add_argument("--national-scale", type=float, default=0.01, help="Share of official national demand to simulate (default: 0.01).")
    parser.add_argument("--national-fleet", type=int, help="Representative ambulances (default: national 34, one-region 6).")
    parser.add_argument("--seed", type=int, default=42, help="First random seed.")
    parser.add_argument("--hours", type=float, help="Override episode duration in hours (default: scenario value or 24).")
    parser.add_argument("--ambulances", type=int, help="Built-in scenario fleet size (default: 3).")
    parser.add_argument("--policy", choices=POLICIES, default="greedy", help="Policy for a single-policy run.")
    parser.add_argument("--compare", action="store_true", help="Compare nearest, no-reposition, and greedy policies.")
    parser.add_argument("--episodes", type=int, default=1, help="Number of paired episodes (default: 1).")
    parser.add_argument("--output-dir", type=Path, help="Write experiment_summary.json and episode_results.csv.")
    parser.add_argument("--placement-ambulance", help="Evaluate ambulance standby candidates (not hospital placement).")
    parser.add_argument("--candidate", action="append", default=[], help="Standby node candidate; repeat for each node.")
    parser.add_argument(
        "--background-location", action="append", default=[], metavar="NODE=WEIGHT",
        help="Random initial-location weight for non-target ambulances; repeat as needed.",
    )
    parser.add_argument("--json", action="store_true", help="Print complete JSON rather than a compact summary.")
    parser.add_argument(
        "--visualize", nargs="?", const=Path("outputs/simulation.html"), type=Path,
        help="Write an interactive HTML playback (default: outputs/simulation.html).",
    )
    return parser


def _ci_text(estimate: dict) -> str:
    if estimate["ci95_low"] is None:
        return "95% CI n/a: needs at least 2 episodes"
    return f"95% CI {estimate['ci95_low']:.6f}..{estimate['ci95_high']:.6f}"


def _scenario_factory(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Callable[[], Scenario]:
    if args.hours is not None and args.hours <= 0:
        parser.error("--hours must be positive")
    if args.ambulances is not None and args.ambulances < 1:
        parser.error("--ambulances must be at least 1")
    if args.scenario is not None and args.national:
        parser.error("--scenario cannot be combined with --national")
    if args.national and not args.legacy_overview:
        parser.error("--national is archived; add --legacy-overview to run it explicitly")
    if args.legacy_overview and not args.national:
        parser.error("--legacy-overview currently requires --national")
    if args.region is not None and not args.national:
        parser.error("--region requires --national")
    if args.detail == "municipal" and not args.national:
        parser.error("--detail municipal requires --national --region 경기")
    if args.detail == "municipal" and (args.region or "").lower() not in {"경기", "경기도", "gyeonggi"}:
        parser.error("--detail municipal currently supports only --region 경기")
    if args.scenario is not None and args.ambulances is not None:
        parser.error("--ambulances cannot be combined with --scenario; edit the scenario fleet instead")
    if args.national and args.ambulances is not None:
        parser.error("--ambulances cannot be combined with --national; use --national-fleet")

    if args.national:
        hours = 24.0 if args.hours is None else args.hours
        if args.detail == "municipal":
            return lambda: build_gyeonggi_municipal_scenario(
                hours=hours,
                demand_scale=args.national_scale,
                representative_ambulances=args.national_fleet,
            )
        return lambda: build_national_overview_scenario(
            hours=hours,
            demand_scale=args.national_scale,
            representative_ambulances=args.national_fleet,
            region=args.region,
        )

    if args.scenario is None:
        hours = 24.0 if args.hours is None else args.hours
        ambulances = 3 if args.ambulances is None else args.ambulances
        return lambda: build_default_scenario(hours=hours, ambulances=ambulances)

    def factory() -> Scenario:
        scenario = load_scenario(args.scenario)
        if args.hours is not None:
            scenario.horizon_minutes = args.hours * 60.0
        return scenario

    return factory


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.national_fleet is None:
        args.national_fleet = 31 if args.detail == "municipal" else (6 if args.region else 34)
    if args.episodes < 1:
        parser.error("--episodes must be at least 1")
    placement_inputs = int(args.hospital_placement is not None) + int(args.municipality_root is not None)
    if placement_inputs > 1:
        parser.error("choose either --hospital-placement or --municipality-root")
    if placement_inputs and args.new_hospitals is None:
        parser.error("hospital placement requires --new-hospitals")
    if not placement_inputs and args.new_hospitals is not None:
        parser.error("--new-hospitals requires --hospital-placement or --municipality-root")
    if placement_inputs and any((args.scenario, args.national, args.legacy_overview, args.compare, args.placement_ambulance, args.visualize)):
        parser.error("hospital placement cannot be combined with simulation, legacy overview, standby placement, or visualization modes")
    if args.max_combinations < 1:
        parser.error("--max-combinations must be at least 1")
    standby_input = args.standby_placement is not None
    if standby_input and placement_inputs:
        parser.error("choose either hospital placement or --standby-placement")
    if standby_input and any((args.scenario, args.national, args.legacy_overview, args.compare, args.placement_ambulance, args.visualize)):
        parser.error("--standby-placement cannot be combined with simulation, legacy overview, standby-candidate, or visualization modes")
    if not standby_input and (args.movable is not None or args.shortlist is not None or args.allow_shared_posts):
        parser.error("--movable, --shortlist, and --allow-shared-posts require --standby-placement")
    if args.movable is not None and args.movable < 0:
        parser.error("--movable must be at least 0")
    if args.shortlist is not None and args.shortlist < 1:
        parser.error("--shortlist must be at least 1")
    if args.placement_ambulance and args.compare:
        parser.error("--placement-ambulance cannot be combined with --compare")
    if args.candidate and not args.placement_ambulance:
        parser.error("--candidate requires --placement-ambulance")
    if args.visualize is not None and (args.compare or args.placement_ambulance or args.episodes != 1):
        parser.error("--visualize supports one episode; omit --compare/--placement-ambulance and use --episodes 1")
    if args.episodes < 2 and (args.compare or placement_inputs or standby_input):
        print("warning: confidence intervals need at least 2 episodes; --episodes 1 reports null CI fields", file=sys.stderr)
    try:
        if standby_input:
            standby_problem = load_municipal_standby_problem(args.standby_placement)
            result = optimize_standby_placement(
                standby_problem,
                episodes=args.episodes,
                seed=args.seed,
                movable=args.movable,
                shortlist=args.shortlist,
                allow_shared_posts=args.allow_shared_posts,
            )
            if args.output_dir is not None:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                result_path = args.output_dir / "standby_placement_result.json"
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"wrote: {result_path}")
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                best = result["best"]
                print(f"municipality: {result['municipality_name']} ({result['municipality_code']})")
                print(f"moved_ambulances: {len(best['moved_ambulances'])} of {len(standby_problem.ambulance_home_bases)}")
                print(f"expected_saved: {best['expected_saved']['mean']:.6f} ({_ci_text(best['expected_saved'])})")
                print(f"increment_vs_home_bases: {best['incremental_expected_saved']['mean']:.6f}")
            return 0
        if placement_inputs:
            if args.hospital_placement is not None:
                problem = load_municipal_placement_problem(args.hospital_placement)
                result = optimize_hospital_placement(
                    problem,
                    new_hospitals=args.new_hospitals,
                    episodes=args.episodes,
                    seed=args.seed,
                    max_combinations=args.max_combinations,
                )
            else:
                result = optimize_municipality_directory(
                    args.municipality_root,
                    new_hospitals=args.new_hospitals,
                    episodes=args.episodes,
                    seed=args.seed,
                    max_combinations=args.max_combinations,
                )
            if args.output_dir is not None:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                result_path = args.output_dir / "hospital_placement_result.json"
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"wrote: {result_path}")
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.hospital_placement is not None:
                best = result["best"]
                print(f"municipality: {result['municipality_name']} ({result['municipality_code']})")
                print(f"selected: {', '.join(best['candidate_names']) or '(none)'}")
                print(f"expected_saved: {best['expected_saved_mean']:.6f}")
                print(f"increment_vs_existing: {best['incremental_expected_saved_mean']:.6f}")
            else:
                print(f"independently optimized municipalities: {result['municipality_count']}")
                print(f"increment_vs_existing: {result['aggregate_after_optimization']['incremental_expected_saved_mean']:.6f}")
            return 0
        scenario_factory = _scenario_factory(args, parser)
        if args.placement_ambulance:
            candidates = args.candidate or list(scenario_factory().standby_nodes)
            background_weights: dict[str, float] = {}
            for item in args.background_location:
                if "=" not in item:
                    parser.error("--background-location must use NODE=WEIGHT")
                node, raw_weight = item.rsplit("=", 1)
                try:
                    background_weights[node] = float(raw_weight)
                except ValueError:
                    parser.error(f"invalid background-location weight: {item!r}")
            result = evaluate_standby_candidates(
                scenario_factory,
                args.placement_ambulance,
                candidates,
                args.episodes,
                args.seed,
                background_location_weights=background_weights or None,
            )
        elif not args.compare and args.episodes == 1 and args.output_dir is None:
            simulation = Simulation(
                scenario_factory(), policy=POLICIES[args.policy](), seed=args.seed,
                trace=args.visualize is not None,
            )
            result = simulation.run()
            if args.visualize is not None:
                trace_payload = simulation.trace_payload()
                trace_payload["metadata"] = {
                    "seed": args.seed,
                    "policy": args.policy,
                    "scenario": "gyeonggi_municipal_2024" if args.detail == "municipal" else (f"regional_overview:{args.region}" if args.region else ("national_17_overview" if args.national else (str(args.scenario) if args.scenario is not None else "built-in synthetic"))),
                    "demand_scale": args.national_scale if args.national else 1.0,
                    "detail": args.detail,
                }
                if args.national:
                    trace_payload["metadata"].update(gyeonggi_municipal_summary() if args.detail == "municipal" else national_data_summary(args.region))
                visualization_path = write_visualization(
                    trace_payload,
                    args.visualize,
                    title="경기도 31개 시·군 구급차 상황실" if args.detail == "municipal" else (f"{national_data_summary(args.region)['region']} 구급차 상황실" if args.national else "구급차 SMDP 상황실"),
                )
                result["visualization"] = str(visualization_path)
            if args.national:
                result["national_data"] = gyeonggi_municipal_summary() if args.detail == "municipal" else national_data_summary(args.region)
                result["national_demand_scale"] = args.national_scale
                result["representative_ambulances"] = args.national_fleet
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("Ambulance SMDP episode")
                for key, value in result.items():
                    print(f"{key}: {value}")
            return 0

        else:
            policies = POLICIES if args.compare else {args.policy: POLICIES[args.policy]}
            result = run_experiment(scenario_factory, policies, args.episodes, args.seed)
        result["scenario_source"] = "gyeonggi_municipal_2024" if args.detail == "municipal" else (f"regional_overview:{args.region}" if args.region else ("national_17_overview" if args.national else (str(args.scenario) if args.scenario is not None else "built-in synthetic")))
        if args.national:
            result["national_data"] = gyeonggi_municipal_summary() if args.detail == "municipal" else national_data_summary(args.region)
            result["national_demand_scale"] = args.national_scale
            result["representative_ambulances"] = args.national_fleet
        result["hours_override"] = args.hours
        result["ambulance_count_override"] = args.ambulances
        if args.output_dir is not None:
            json_path, csv_path = write_experiment_outputs(result, args.output_dir)
            print(f"wrote: {json_path}")
            print(f"wrote: {csv_path}")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"episodes: {result['episodes']}")
            for policy, metrics in result["estimates"].items():
                saved = metrics["expected_saved"]
                print(f"{policy}: expected_saved={saved['mean']:.6f} ({_ci_text(saved)})")
            for comparison, estimate in result["paired_expected_saved_differences"].items():
                print(f"{comparison}: {estimate['mean']:.6f} ({_ci_text(estimate)})")
        return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
