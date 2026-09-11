"""Generate municipality-local standby.json and scenario_standby.json from staged real inputs.

The fleet is the one that already exists: every ambulance keeps the 119 안전센터 it
belongs to as its home base and starts with no assigned standby post, so the only
decision left to the optimizer is where a vehicle stands by during the free window.
Every value comes from the processed municipality folder or the model parameter
file.  A missing input stops the run; nothing is synthesized.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.io import load_scenario, scenario_from_dict
from scripts.build_municipal_placement_inputs import (
    ASCII_SLUGS,
    HOURS,
    KST,
    _relative,
    _true,
    daily_calls_for,
    load_parameters,
)
from scripts.collect_kakao_routes import STANDBY_FILE, coordinate, read_rows, required_pairs, standby_pairs

MANIFEST_NAME = "road_times_standby_manifest.json"
# docs/decisions_and_tradeoffs_ko.md 13절: 07:00~01:00 is the free window and
# 01:00~06:00 is the window in which a vehicle aims to be at its own station.
FREE_HOURS = (0,) + tuple(range(7, 24))
HOME_HOURS = tuple(range(1, 7))
SCHEDULE_BASIS = (
    "user decision recorded in docs/decisions_and_tradeoffs_ko.md 13절: free standby from 07:00 "
    "through 01:00, home-station window from 01:00 to 06:00 at the lowest priority"
)


def build_municipality(
    folder: Path,
    summary_rows: list[dict[str, str]],
    parameters: dict[str, Any],
    parameters_path: Path,
    output_root: Path,
    *,
    slug: str | None = None,
    overwrite: bool,
    horizon_days: int = 1,
    cooldown_minutes: float = 360.0,
) -> Path:
    """Write <output_root>/<code>_<slug>/{scenario_standby,standby}.json and re-load them as a contract check."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be zero or more")
    code = folder.name
    manifest_path = folder / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(
            f"{code}: {MANIFEST_NAME} is missing; collect the standby road-time matrix first "
            "(scripts/collect_kakao_routes.py --pair-set standby)"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("standby_ready") is not True:
        raise ValueError(
            f"{code}: {MANIFEST_NAME} is not standby_ready "
            f"(pair_set={manifest.get('pair_set')!r}, complete={manifest.get('complete')!r})"
        )
    road_times_path = folder / "road_times.csv"
    if not road_times_path.is_file():
        raise ValueError(f"{code}: road_times.csv is missing")

    daily_calls, demand_detail, municipality_name = daily_calls_for(code, summary_rows, parameters["demand"])
    folder_slug = slug or ASCII_SLUGS.get(municipality_name)
    if folder_slug is None:
        raise ValueError(f"{code}: no ASCII folder slug is known for {municipality_name!r}; pass --slug")
    output_dir = output_root / f"{code}_{folder_slug}"
    scenario_path = output_dir / "scenario_standby.json"
    standby_path = output_dir / "standby.json"
    if not overwrite and (scenario_path.exists() or standby_path.exists()):
        raise ValueError(f"{output_dir}: standby.json/scenario_standby.json already exist; pass --overwrite to replace them")

    profiles = parameters["profiles"]
    hospital_parameters = parameters["hospital"]
    geo: dict[str, tuple[float, float]] = {}  # node -> (longitude, latitude)

    demand_rows = read_rows(folder / "demand_population.csv")
    if not demand_rows:
        raise ValueError(f"{code}: demand_population.csv is empty")
    population_total = sum(int(row["population"]) for row in demand_rows)
    if population_total <= 0:
        raise ValueError(f"{code}: demand population total must be positive")
    villages: dict[str, float] = {}
    for row in demand_rows:
        node = f"{code}::demand::{row['administrative_code']}"
        if row["municipality_code"] != code:
            raise ValueError(f"{node}: cross-municipality demand row is forbidden")
        geo[node] = coordinate(row, node)
        villages[node] = daily_calls * int(row["population"]) / population_total / 24.0

    ambulances: dict[str, dict[str, Any]] = {}
    home_bases: dict[str, str] = {}
    base_nodes: list[str] = []
    base_rows = read_rows(folder / "ambulance_bases.csv")
    if not base_rows:
        raise ValueError(f"{code}: ambulance_bases.csv is empty; the existing fleet is the input of this model")
    for index, row in enumerate(base_rows, start=1):
        node = f"{code}::ambulance_base::{index:03d}"
        geo[node] = coordinate(row, node)
        base_nodes.append(node)
        count = int(row["ambulance_count"])
        if count < 0:
            raise ValueError(f"{node}: ambulance_count must not be negative")
        for vehicle in range(1, count + 1):
            ident = f"A{index:02d}-{vehicle}"
            ambulances[ident] = {
                "location": node,
                "status": "idle",
                "restock_minutes": parameters["ambulance"]["restock_minutes"],
                "home_base": node,
                # The optimizer is the only thing allowed to fill this in.
                "assigned_post": None,
            }
            home_bases[ident] = node
    if not ambulances:
        raise ValueError(f"{code}: no ambulance is staged; the existing fleet is the input of this model")

    hospitals: dict[str, dict[str, Any]] = {}
    hospital_rows = read_rows(folder / "existing_hospitals.csv")
    for index, row in enumerate(hospital_rows, start=1):
        node = f"{code}::hospital::{index:03d}"
        if row["municipality_code"] != code:
            raise ValueError(f"{node}: cross-municipality hospital row is forbidden")
        geo[node] = coordinate(row, node)
        category = row["emergency_category"].strip()
        if category not in hospital_parameters["categories"]:
            raise ValueError(f"{node} ({row['hospital_name']}): emergency_category {category!r} is not in the model parameters")
        if _true(row, "capacity_match_review_required"):
            raise ValueError(f"{node} ({row['hospital_name']}): review the HIRA capacity match before simulation")
        capacity = int(row["emergency_room_beds"])
        if capacity < 1:
            raise ValueError(f"{node} ({row['hospital_name']}): reported emergency-room beds must be positive")
        capabilities = list(hospital_parameters["capabilities_by_category"][category])
        hospitals[f"H{index:02d}"] = {
            "location": node,
            "capabilities": capabilities,
            "success_when_available": 0.0,
            "success_when_unavailable": 0.0,
            "success_by_profile": {name: hospital_parameters["success_by_category"][category][name] for name in capabilities},
            "unavailable_success_by_profile": {name: hospital_parameters["success_when_unavailable_by_profile"][name] for name in capabilities},
            "capacity": capacity,
            "treatment_minutes": hospital_parameters["treatment_minutes"],
            "transfer_delay_minutes": hospital_parameters["transfer_delay_minutes"],
            "official_identifiers": {
                "hospital_name": row["hospital_name"],
                "emergency_category": category,
                "hira_encrypted_care_symbol": row["hira_encrypted_care_symbol"],
                "egen_institution_code": row["egen_institution_code"],
                "capacity_snapshot_period": row["capacity_snapshot_period"],
                "capacity_source": row["capacity_source"],
            },
        }
    hospital_nodes = [record["location"] for record in hospitals.values()]

    post_rows = read_rows(folder / STANDBY_FILE)
    if not post_rows:
        raise ValueError(f"{code}: {STANDBY_FILE} is empty; run build_grid_candidates.py --mode standby first")
    posts: list[dict[str, Any]] = []
    for index, row in enumerate(post_rows, start=1):
        node = f"{code}::standby::{row['candidate_id']}"
        if row["municipality_code"] != code:
            raise ValueError(f"{node}: cross-municipality standby post is forbidden")
        if row["routing_status"] == "unroutable_no_nearby_road":
            raise ValueError(
                f"{node}: {STANDBY_FILE} still lists a post Kakao cannot route to; "
                "rerun scripts/probe_candidate_routability.py so the row is moved out"
            )
        geo[node] = coordinate(row, node)
        posts.append({
            "id": f"P{index:03d}",
            "source_id": row["candidate_id"],
            "name": row["candidate_name"],
            "post_type": "grid_cell",
            "municipality_code": code,
            "location": node,
            "cell_population_estimate": float(row["cell_population_estimate"]),
        })
    # Every existing 119 안전센터 is a standby post as well, so "keep the vehicle where it
    # already is" is always inside the search space.
    for offset, (node, row) in enumerate(zip(base_nodes, base_rows), start=len(posts) + 1):
        posts.append({
            "id": f"P{offset:03d}",
            "source_id": row["base_name"],
            "name": row["base_name"],
            "post_type": "existing_base",
            "municipality_code": code,
            "location": node,
            "cell_population_estimate": None,
        })
    post_nodes = [record["location"] for record in posts]

    edges: dict[str, dict[str, float]] = {node: {} for node in geo}
    for row in read_rows(road_times_path):
        origin, destination = row["origin_id"], row["destination_id"]
        if origin in geo and destination in geo:
            duration = float(row["duration_minutes"])
            if not math.isfinite(duration) or duration < 0:
                raise ValueError(f"{code}: invalid duration_minutes for {origin!r} -> {destination!r}")
            edges[origin][destination] = duration
    # The standby contract (post -> demand, hospital -> post, post <-> base) plus the routes
    # every episode needs anyway (demand -> hospital, hospital -> hospital, base -> demand).
    contract = sorted(
        set(standby_pairs(set(villages), set(base_nodes), set(hospital_nodes), set(post_nodes)))
        | set(required_pairs(set(villages), set(base_nodes), set(hospital_nodes)))
    )
    missing = [pair for pair in contract if pair[1] not in edges[pair[0]]]
    if missing:
        raise ValueError(
            f"{code}: {len(missing)} required directional road routes are missing from road_times.csv, "
            f"for example {missing[0][0]!r} -> {missing[0][1]!r}; collect "
            "scripts/collect_kakao_routes.py --pair-set operations and --pair-set standby"
        )

    multipliers = parameters["hourly_demand_multipliers"]
    weights = parameters["hourly_profile_weights"]
    scenario = {
        "schema_version": 1,
        "network": {
            "edges": edges,
            "positions": {node: [longitude, latitude] for node, (longitude, latitude) in geo.items()},
        },
        "villages": villages,
        "hospitals": hospitals,
        "ambulances": ambulances,
        "profiles": [
            {key: profile[key] for key in ("name", "golden_minutes", "decay_rate", "scene_minutes", "field_success")}
            for profile in profiles
        ],
        # Stations, hospitals and posts are all places a vehicle may wait; the schedule and
        # the assignment, not this list, decide where it actually stands by.
        "standby_nodes": list(dict.fromkeys(base_nodes + hospital_nodes + post_nodes)),
        "horizon_minutes": 1440 * horizon_days + cooldown_minutes,
        "arrival_cutoff_minutes": 1440 * horizon_days,
        "max_transfers": 2,
        "hourly_village_rates": (
            {hour: {node: rate * multipliers[hour] for node, rate in villages.items()} for hour in HOURS}
            if multipliers is not None else {}
        ),
        "hourly_profile_probabilities": (
            weights if weights is not None
            else {hour: {profile["name"]: profile["share"] for profile in profiles} for hour in HOURS}
        ),
    }
    scenario_from_dict(scenario, source=str(scenario_path))

    demand_detail["allocation_within_municipality"] = (
        "resident population share of each administrative dong in demand_population.csv; "
        + ("hourly multipliers from the model parameters" if multipliers is not None else "flat hourly rate")
    )
    demand_detail["coordinate_source"] = sorted({row["coordinate_source"] for row in demand_rows})
    demand_detail["coordinate_caveat"] = "administrative-office representative points; not incident coordinates"
    standby = {
        "municipality_code": code,
        "municipality_name": municipality_name,
        "scenario": scenario_path.name,
        "schedule": {
            "free_hours": list(FREE_HOURS),
            "home_hours": list(HOME_HOURS),
            "basis": SCHEDULE_BASIS,
        },
        "provenance": {
            "hospital_source": {
                "registry": sorted({row["coordinate_source"] for row in hospital_rows}),
                "capacity": {
                    "field": "emergency_room_beds",
                    "source": sorted({row["capacity_source"] for row in hospital_rows}),
                    "snapshot_period": sorted({row["capacity_snapshot_period"] for row in hospital_rows}),
                    "semantics": sorted({row["capacity_semantics"] for row in hospital_rows}),
                },
                "evaluation": {
                    "source": sorted({row["egen_evaluation_source"] for row in hospital_rows}),
                    "year": sorted({row["egen_evaluation_year"] for row in hospital_rows}),
                },
                "treatment_success_basis": hospital_parameters["basis"],
                "official_identifiers": "scenario_standby.json hospitals.<id>.official_identifiers",
                "dataset_details": "see provenance.sources",
            },
            "demand_source": demand_detail,
            "candidate_source": {
                "datasets": sorted({row["candidate_source"] for row in post_rows}),
                "land_feasibility_status": sorted({row["land_feasibility_status"] for row in post_rows}),
                "grid_post_count": len(post_rows),
                "existing_base_post_count": len(base_nodes),
                "interpretation": (
                    "1 km grid cells with residents that are at least the configured distance from every "
                    "existing 119 안전센터, plus the existing 119 안전센터 themselves; a grid cell is a parking "
                    "spot whose real usability as a standby place is not verified"
                ),
                "fleet_assumption": (
                    "the existing fleet is repositioned only; no ambulance, station or vehicle is added"
                ),
            },
            "road_time_source": {
                "provider": manifest["routing_provider"],
                "extracted_at": manifest["updated_at"],
                "routing_profile": manifest["routing_profile"],
                "unit": "minutes",
                "pair_set": manifest.get("pair_set"),
                "required_pair_count": manifest.get("required_pair_count"),
                "file": "road_times.csv",
            },
            "coordinate_order": "network.positions are [longitude, latitude] in WGS84",
            "model_parameters": {
                "path": _relative(parameters_path),
                "status": parameters["status"],
                "generated_at": parameters["generated_at"],
            },
            "sources": parameters["sources"],
            "generated_by": {
                "script": "scripts/build_municipal_standby_inputs.py",
                "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
                "processed_dir": _relative(folder),
                "horizon_days": horizon_days,
                "arrival_cutoff_minutes": 1440 * horizon_days,
                "cooldown_minutes": cooldown_minutes,
                "episode_rule": "patients arrive only before the cut-off; the run continues through the cool-down so they can be resolved",
            },
        },
        "resource_municipality_codes": {
            "demand": {node: code for node in villages},
            "hospitals": {ident: code for ident in hospitals},
            "ambulances": {ident: code for ident in ambulances},
            "standby_posts": {record["id"]: code for record in posts},
        },
        "ambulance_home_bases": home_bases,
        "standby_posts": posts,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((scenario_path, scenario), (standby_path, standby)):
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    load_scenario(scenario_path)
    return standby_path


def select_folders(processed: Path, codes: list[str]) -> list[Path]:
    if codes:
        folders = [processed / code for code in codes]
        missing = [folder.name for folder in folders if not folder.is_dir()]
        if missing:
            raise ValueError(f"municipality folders not found: {missing}")
        return folders
    folders = []
    for folder in sorted(path for path in processed.iterdir() if path.is_dir()):
        manifest_path = folder / MANIFEST_NAME
        if manifest_path.is_file() and json.loads(manifest_path.read_text(encoding="utf-8")).get("standby_ready") is True:
            folders.append(folder)
    if not folders:
        raise ValueError(f"{processed}: no municipality has a standby-ready road-time matrix")
    return folders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate municipality-local standby.json/scenario_standby.json from staged real inputs.")
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True, help="model_parameters_<status>.json")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "gyeonggi_real")
    parser.add_argument("--municipality-code", action="append", default=[], help="Repeatable; default is every standby-ready folder.")
    parser.add_argument("--slug", default=None, help="ASCII output folder suffix; required for a municipality outside the built-in table.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--horizon-days", type=int, default=1, help="Days during which patients arrive (default 1).")
    parser.add_argument("--cooldown-minutes", type=float, default=360.0, help="Extra minutes after the last arrival day so open cases can finish (default 360).")
    args = parser.parse_args(argv)
    if args.horizon_days < 1:
        parser.error("--horizon-days must be at least 1")
    if args.cooldown_minutes < 0:
        parser.error("--cooldown-minutes must be zero or more")
    processed = args.processed_dir.resolve()
    try:
        parameters = load_parameters(args.parameters.resolve())
        summary_rows = read_rows(processed / "municipality_summary.csv")
        folders = select_folders(processed, args.municipality_code)
        if args.slug is not None and len(folders) > 1:
            parser.error("--slug names one output folder, so pass a single --municipality-code with it")
        for folder in folders:
            print(build_municipality(
                folder, summary_rows, parameters, args.parameters.resolve(), args.output_root.resolve(),
                slug=args.slug, overwrite=args.overwrite,
                horizon_days=args.horizon_days, cooldown_minutes=args.cooldown_minutes,
            ))
    except KeyError as exc:
        print(f"error: missing column or field {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
