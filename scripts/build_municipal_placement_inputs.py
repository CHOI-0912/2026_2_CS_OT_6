"""Generate municipality-local placement.json and scenario.json from staged real inputs.

Every value comes from the processed municipality folder or the model
parameter file.  A missing input stops the run; nothing is synthesized.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.hospital_placement import load_municipal_placement_problem
from ambulance_sim.io import scenario_from_dict
from ambulance_sim.legacy.gyeonggi_centroid import MUNICIPALITIES
from scripts.collect_kakao_routes import coordinate, read_rows, required_pairs

KST = timezone(timedelta(hours=9))
# Only the ASCII folder slug is taken from the legacy table; no legacy numbers are used.
ASCII_SLUGS = {datum.name: datum.code for datum in MUNICIPALITIES}
DEMAND_METHODS = ("population_proportional_provincial_dispatches", "observed_municipal_dispatches")
HOURS = [str(hour) for hour in range(24)]


def _require(mapping: Any, key: str, path: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ValueError(f"{path}: missing required field {key!r}")
    return mapping[key]


def _probability(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"{path}: must be a probability between 0 and 1")
    return float(value)


def _non_negative(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{path}: must be a finite number >= 0")
    return float(value)


def _hourly_table(raw: Any, path: str) -> None:
    if not isinstance(raw, dict) or sorted(raw, key=int) != HOURS:
        raise ValueError(f"{path}: must contain exactly the hour keys 0..23")


def load_parameters(path: Path) -> dict[str, Any]:
    """Load and validate a model_parameters_<status>.json file."""
    try:
        parameters = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{path}: model parameter file not found") from exc
    label = str(path)
    if _require(parameters, "schema_version", label) != 1:
        raise ValueError(f"{label}: schema_version must be 1")
    if _require(parameters, "status", label) not in {"provisional", "calibrated"}:
        raise ValueError(f"{label}: status must be 'provisional' or 'calibrated'")
    _require(parameters, "generated_at", label)
    profiles = _require(parameters, "profiles", label)
    if not isinstance(profiles, list) or not profiles:
        raise ValueError(f"{label}: profiles must be a non-empty array")
    names: list[str] = []
    for index, profile in enumerate(profiles):
        profile_path = f"{label}: profiles[{index}]"
        names.append(str(_require(profile, "name", profile_path)))
        _probability(_require(profile, "share", profile_path), f"{profile_path}.share")
        for key in ("golden_minutes", "decay_rate", "scene_minutes"):
            _non_negative(_require(profile, key, profile_path), f"{profile_path}.{key}")
        _probability(_require(profile, "field_success", profile_path), f"{profile_path}.field_success")
        _require(profile, "basis", profile_path)
    if len(set(names)) != len(names):
        raise ValueError(f"{label}: duplicate profile name")
    share_total = sum(float(profile["share"]) for profile in profiles)
    if abs(share_total - 1.0) > 1e-6:
        raise ValueError(f"{label}: profile shares sum to {share_total}, not 1.0")

    weights = _require(parameters, "hourly_profile_weights", label)
    if weights is not None:
        _hourly_table(weights, f"{label}: hourly_profile_weights")
        for hour, row in weights.items():
            if set(row) - set(names):
                raise ValueError(f"{label}: hourly_profile_weights.{hour} names unknown profiles")
            for name, value in row.items():
                _non_negative(value, f"{label}: hourly_profile_weights.{hour}.{name}")
    multipliers = _require(parameters, "hourly_demand_multipliers", label)
    if multipliers is not None:
        _hourly_table(multipliers, f"{label}: hourly_demand_multipliers")
        values = [_non_negative(value, f"{label}: hourly_demand_multipliers.{hour}") for hour, value in multipliers.items()]
        if abs(sum(values) / 24 - 1.0) > 1e-6:
            raise ValueError(f"{label}: hourly_demand_multipliers must average 1.0")

    hospital = _require(parameters, "hospital", label)
    categories = _require(hospital, "categories", f"{label}: hospital")
    if not isinstance(categories, list) or not categories:
        raise ValueError(f"{label}: hospital.categories must be a non-empty array")
    success_by_category = _require(hospital, "success_by_category", f"{label}: hospital")
    capabilities_by_category = _require(hospital, "capabilities_by_category", f"{label}: hospital")
    for category in categories:
        success = _require(success_by_category, category, f"{label}: hospital.success_by_category")
        if set(success) != set(names):
            raise ValueError(f"{label}: hospital.success_by_category[{category}] must cover every profile")
        for name, value in success.items():
            _probability(value, f"{label}: hospital.success_by_category[{category}].{name}")
        capabilities = _require(capabilities_by_category, category, f"{label}: hospital.capabilities_by_category")
        if not isinstance(capabilities, list) or not capabilities or set(capabilities) - set(names):
            raise ValueError(f"{label}: hospital.capabilities_by_category[{category}] must be a non-empty subset of the profiles")
    unavailable = _require(hospital, "success_when_unavailable_by_profile", f"{label}: hospital")
    if set(unavailable) != set(names):
        raise ValueError(f"{label}: hospital.success_when_unavailable_by_profile must cover every profile")
    for name, value in unavailable.items():
        _probability(value, f"{label}: hospital.success_when_unavailable_by_profile.{name}")
    for key in ("treatment_minutes", "transfer_delay_minutes"):
        _non_negative(_require(hospital, key, f"{label}: hospital"), f"{label}: hospital.{key}")
    _require(hospital, "basis", f"{label}: hospital")

    candidate = _require(parameters, "candidate", label)
    if _require(candidate, "category_assumption", f"{label}: candidate") not in categories:
        raise ValueError(f"{label}: candidate.category_assumption must be one of hospital.categories")
    beds = _require(candidate, "capacity_beds", f"{label}: candidate")
    if isinstance(beds, bool) or not isinstance(beds, int) or beds < 1:
        raise ValueError(f"{label}: candidate.capacity_beds must be a positive integer")
    for key in ("treatment_minutes", "cost"):
        _non_negative(_require(candidate, key, f"{label}: candidate"), f"{label}: candidate.{key}")
    _require(candidate, "basis", f"{label}: candidate")
    _non_negative(_require(_require(parameters, "ambulance", label), "restock_minutes", f"{label}: ambulance"), f"{label}: ambulance.restock_minutes")

    demand = _require(parameters, "demand", label)
    if _require(demand, "method", f"{label}: demand") not in DEMAND_METHODS:
        raise ValueError(f"{label}: demand.method must be one of {DEMAND_METHODS}")
    dispatches = _require(demand, "provincial_annual_dispatches", f"{label}: demand")
    if isinstance(dispatches, bool) or not isinstance(dispatches, int) or dispatches < 1:
        raise ValueError(f"{label}: demand.provincial_annual_dispatches must be a positive integer")
    _require(demand, "year", f"{label}: demand")
    _require(demand, "provincial_population_reference", f"{label}: demand")
    observed = _require(demand, "daily_calls_by_municipality", f"{label}: demand")
    if observed is not None:
        if not isinstance(observed, dict):
            raise ValueError(f"{label}: demand.daily_calls_by_municipality must be null or an object")
        for code, value in observed.items():
            if _non_negative(value, f"{label}: demand.daily_calls_by_municipality.{code}") <= 0:
                raise ValueError(f"{label}: demand.daily_calls_by_municipality.{code} must be positive")
    _require(demand, "basis", f"{label}: demand")
    if not isinstance(_require(parameters, "sources", label), list):
        raise ValueError(f"{label}: sources must be an array")
    return parameters


def _relative(path: Path) -> str:
    try:
        return os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")
    except ValueError:  # different drive on Windows
        return str(path)


def _uniform(values: dict[str, float], label: str) -> float:
    if len(set(values.values())) != 1:
        raise ValueError(
            f"{label}: the placement loader accepts one success value per candidate, "
            f"but the parameters differ by profile: {values}"
        )
    return next(iter(values.values()))


def _true(row: dict[str, str], key: str) -> bool:
    return row.get(key, "").strip().lower() == "true"


def daily_calls_for(code: str, summary_rows: list[dict[str, str]], demand: dict[str, Any]) -> tuple[float, dict[str, Any], str]:
    selected = next((row for row in summary_rows if row["municipality_code"] == code), None)
    if selected is None:
        raise ValueError(f"{code}: not listed in municipality_summary.csv")
    municipal_population = int(selected["population_202608"])
    provincial_population = sum(int(row["population_202608"]) for row in summary_rows)
    if municipal_population <= 0 or provincial_population <= 0:
        raise ValueError(f"{code}: population_202608 must be positive")
    observed = demand["daily_calls_by_municipality"] or {}
    if demand["method"] == "observed_municipal_dispatches" and code in observed:
        daily_calls = float(observed[code])
        method = "observed_municipal_dispatches"
    else:
        daily_calls = demand["provincial_annual_dispatches"] / 365.25 * municipal_population / provincial_population
        method = "population_proportional_provincial_dispatches"
    detail = {
        "method": method,
        "basis": demand["basis"],
        "year": demand["year"],
        "provincial_annual_dispatches": demand["provincial_annual_dispatches"],
        "provincial_population_reference": demand["provincial_population_reference"],
        "provincial_population": provincial_population,
        "municipal_population": municipal_population,
        "daily_calls_model_input": daily_calls,
        "observed_municipal_calls": method == "observed_municipal_dispatches",
    }
    return daily_calls, detail, selected["municipality_name"]


def build_municipality(
    folder: Path,
    summary_rows: list[dict[str, str]],
    parameters: dict[str, Any],
    parameters_path: Path,
    output_root: Path,
    *,
    overwrite: bool,
    horizon_days: int = 1,
    cooldown_minutes: float = 360.0,
) -> Path:
    """Write <output_root>/<code>_<slug>/{scenario,placement}.json and re-load them as a contract check."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be zero or more")
    code = folder.name
    manifest_path = folder / "road_times_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"{code}: road_times_manifest.json is missing; collect the simulation road-time matrix first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("simulation_ready") is not True:
        raise ValueError(f"{code}: road_times_manifest.json is not simulation_ready (pair_set={manifest.get('pair_set')!r}, complete={manifest.get('complete')!r})")
    road_times_path = folder / "road_times.csv"
    if not road_times_path.is_file():
        raise ValueError(f"{code}: road_times.csv is missing")

    daily_calls, demand_detail, municipality_name = daily_calls_for(code, summary_rows, parameters["demand"])
    slug = ASCII_SLUGS.get(municipality_name)
    if slug is None:
        raise ValueError(f"{code}: no ASCII folder slug is known for {municipality_name!r}")
    output_dir = output_root / f"{code}_{slug}"
    scenario_path = output_dir / "scenario.json"
    placement_path = output_dir / "placement.json"
    if not overwrite and (scenario_path.exists() or placement_path.exists()):
        raise ValueError(f"{output_dir}: scenario.json/placement.json already exist; pass --overwrite to replace them")

    profiles = parameters["profiles"]
    names = [profile["name"] for profile in profiles]
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
    base_nodes: list[str] = []
    for index, row in enumerate(read_rows(folder / "ambulance_bases.csv"), start=1):
        node = f"{code}::ambulance_base::{index:03d}"
        geo[node] = coordinate(row, node)
        base_nodes.append(node)
        count = int(row["ambulance_count"])
        if count < 0:
            raise ValueError(f"{node}: ambulance_count must not be negative")
        for vehicle in range(1, count + 1):
            ambulances[f"A{index:02d}-{vehicle}"] = {
                "location": node,
                "status": "idle",
                "restock_minutes": parameters["ambulance"]["restock_minutes"],
            }

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
        # The per-profile maps cover every capability, so the scalar fallbacks are never read;
        # they stay 0 so an uncovered profile can never silently succeed.
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

    candidate_parameters = parameters["candidate"]
    candidate_category = candidate_parameters["category_assumption"]
    candidate_capabilities = list(hospital_parameters["capabilities_by_category"][candidate_category])
    candidate_success = _uniform(
        {name: hospital_parameters["success_by_category"][candidate_category][name] for name in candidate_capabilities},
        f"{code}: candidate success_by_category[{candidate_category}]",
    )
    candidate_unavailable = _uniform(
        {name: hospital_parameters["success_when_unavailable_by_profile"][name] for name in candidate_capabilities},
        f"{code}: candidate success_when_unavailable_by_profile",
    )
    candidate_rows = read_rows(folder / "candidate_sites.csv")
    if not candidate_rows:
        raise ValueError(f"{code}: candidate_sites.csv is empty")
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(candidate_rows, start=1):
        node = f"{code}::candidate::{row['candidate_id']}"
        if row["municipality_code"] != code:
            raise ValueError(f"{node}: cross-municipality candidate row is forbidden")
        geo[node] = coordinate(row, node)
        candidates.append({
            "id": f"C{index:02d}",
            "name": row["candidate_name"],
            "municipality_code": code,
            "source_id": row["candidate_id"],
            "candidate_type": "new_build",
            "location": node,
            "capabilities": candidate_capabilities,
            "capacity": candidate_parameters["capacity_beds"],
            "success_when_available": candidate_success,
            "success_when_unavailable": candidate_unavailable,
            "treatment_minutes": candidate_parameters["treatment_minutes"],
            "cost": candidate_parameters["cost"],
            "category_assumption": candidate_category,
            "candidate_source": row["candidate_source"],
            "land_feasibility_status": row["land_feasibility_status"],
        })
    candidate_nodes = [record["location"] for record in candidates]

    edges: dict[str, dict[str, float]] = {node: {} for node in geo}
    for row in read_rows(road_times_path):
        origin, destination = row["origin_id"], row["destination_id"]
        if origin in geo and destination in geo:
            duration = float(row["duration_minutes"])
            if not math.isfinite(duration) or duration < 0:
                raise ValueError(f"{code}: invalid duration_minutes for {origin!r} -> {destination!r}")
            edges[origin][destination] = duration
    missing = [
        pair for pair in required_pairs(set(villages), set(base_nodes), set(hospital_nodes) | set(candidate_nodes))
        if pair[1] not in edges[pair[0]]
    ]
    if missing:
        raise ValueError(
            f"{code}: {len(missing)} required directional road routes are missing from road_times.csv, "
            f"for example {missing[0][0]!r} -> {missing[0][1]!r}"
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
        "standby_nodes": base_nodes + hospital_nodes,
        # Hour-of-day tables wrap every 24 h, so a multi-day horizon repeats the daily
        # pattern while ambulances and beds carry over across midnight.  Arrivals stop
        # at the cut-off; the cool-down lets patients already in the system finish.
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
    placement = {
        "municipality_code": code,
        "municipality_name": municipality_name,
        "scenario": "scenario.json",
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
                "official_identifiers": "scenario.json hospitals.<id>.official_identifiers",
                "dataset_details": "see provenance.sources",
            },
            "demand_source": demand_detail,
            "candidate_source": {
                "datasets": sorted({row["candidate_source"] for row in candidate_rows}),
                "land_feasibility_status": sorted({row["land_feasibility_status"] for row in candidate_rows}),
                "interpretation": "existing public-health facilities; requires planning review before any site is presented as feasible",
                "model_assumption": candidate_parameters["basis"],
            },
            "road_time_source": {
                "provider": _require(manifest, "routing_provider", str(manifest_path)),
                "extracted_at": _require(manifest, "updated_at", str(manifest_path)),
                "routing_profile": _require(manifest, "routing_profile", str(manifest_path)),
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
                "script": "scripts/build_municipal_placement_inputs.py",
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
        },
        "candidate_hospitals": candidates,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, payload in ((scenario_path, scenario), (placement_path, placement)):
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    load_municipal_placement_problem(placement_path)
    return placement_path


def select_folders(processed: Path, codes: list[str]) -> list[Path]:
    if codes:
        folders = [processed / code for code in codes]
        missing = [folder.name for folder in folders if not folder.is_dir()]
        if missing:
            raise ValueError(f"municipality folders not found: {missing}")
        return folders
    folders = []
    for folder in sorted(path for path in processed.iterdir() if path.is_dir()):
        manifest_path = folder / "road_times_manifest.json"
        if manifest_path.is_file() and json.loads(manifest_path.read_text(encoding="utf-8")).get("simulation_ready") is True:
            folders.append(folder)
    if not folders:
        raise ValueError(f"{processed}: no municipality has a simulation-ready road-time matrix")
    return folders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate municipality-local placement.json/scenario.json from staged real inputs.")
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True, help="model_parameters_<status>.json")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "gyeonggi_real")
    parser.add_argument("--municipality-code", action="append", default=[], help="Repeatable; default is every simulation-ready folder.")
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
        for folder in select_folders(processed, args.municipality_code):
            print(build_municipality(
                folder, summary_rows, parameters, args.parameters.resolve(), args.output_root.resolve(),
                overwrite=args.overwrite, horizon_days=args.horizon_days, cooldown_minutes=args.cooldown_minutes,
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
