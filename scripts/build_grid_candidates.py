"""Build population-filtered grid candidate sites for a municipality.

--mode hospital (default): candidates are grid cells where nothing exists today,
cells inside the municipality, outside the top population share (NIMBY), and at
least the minimum separation from every existing emergency hospital and
public-health facility.

--mode standby: ambulance standby posts, grid cells inside the municipality with
residents and at least the minimum separation from every existing 119 안전센터.
No population share is excluded; a standby post is a parking spot, not a building.

Administrative membership comes from Kakao Local coord2regioncode; nothing is
synthesized and an unknown 행정동 code stops the run.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.env_file import load_env_file
from ambulance_sim.kakao_api import KakaoApiClient, KakaoApiError
from scripts.collect_kakao_routes import read_rows


BASE_FIELDS = [
    "candidate_id", "candidate_name", "municipality_code", "road_address",
    "latitude", "longitude", "candidate_source", "land_feasibility_status",
    "coordinate_source", "matched_address", "kakao_place_id",
    "geocoding_status", "routing_status",
]
EXTRA_FIELDS = [
    "cell_population_estimate", "administrative_code", "nearest_facility_km", "grid_row", "grid_col",
]
PUBLIC_HEALTH_PREFIX = "MOHW-"
MANIFEST_NAME = "grid_candidates_manifest.json"
REFERENCE_NAME = "candidate_sites_public_health_reference.csv"
STANDBY_NAME = "standby_candidates.csv"
STANDBY_MANIFEST_NAME = "standby_candidates_manifest.json"
HAVERSINE_NOTE = (
    "Haversine great-circle distance is used here ONLY as a site-eligibility rule "
    "(minimum separation from existing facilities), never as a travel time."
)
POPULATION_NOTE = (
    "cell_population_estimate is an estimate: the 행정동 resident population divided evenly "
    "among the grid cells whose centre falls in that 행정동 (uniform within-동 allocation)."
)


def haversine_km(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    """Great-circle distance in km; a site-eligibility rule only, never a travel time."""
    radius_km = 6371.0088
    phi_a, phi_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(longitude_b - longitude_a)
    half = math.sin(delta_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(half))


def build_grid(demand_rows: list[dict[str, str]], grid_km: float, code: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Lay the same grid as scripts/build_folium_municipal_simulator.py add_grid, one cell margin included."""
    latitudes = [float(row["latitude"]) for row in demand_rows]
    longitudes = [float(row["longitude"]) for row in demand_rows]
    center_latitude = sum(latitudes) / len(latitudes)
    latitude_step = grid_km / 111.32
    longitude_step = grid_km / (111.32 * math.cos(math.radians(center_latitude)))
    min_latitude, max_latitude = min(latitudes) - latitude_step, max(latitudes) + latitude_step
    min_longitude, max_longitude = min(longitudes) - longitude_step, max(longitudes) + longitude_step
    rows = math.ceil((max_latitude - min_latitude) / latitude_step)
    columns = math.ceil((max_longitude - min_longitude) / longitude_step)
    cells = []
    for row in range(rows):
        for column in range(columns):
            south = min_latitude + row * latitude_step
            west = min_longitude + column * longitude_step
            cells.append({
                "cell_id": f"GRID-{grid_km:g}KM-{code}-{row:03d}-{column:03d}",
                "grid_row": row,
                "grid_col": column,
                "latitude": south + latitude_step / 2,
                "longitude": west + longitude_step / 2,
            })
    geometry = {
        "grid_km": grid_km,
        "rows": rows,
        "columns": columns,
        "latitude_step_degrees": latitude_step,
        "longitude_step_degrees": longitude_step,
        "center_latitude_for_longitude_step": center_latitude,
        "envelope": {
            "min_latitude": min_latitude, "max_latitude": max_latitude,
            "min_longitude": min_longitude, "max_longitude": max_longitude,
        },
        "envelope_basis": "demand_population.csv representative points plus one cell margin",
    }
    return cells, geometry


def save_manifest(folder: Path, payload: dict[str, Any]) -> None:
    (folder / MANIFEST_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_region_cache(folder: Path) -> dict[str, dict[str, str]]:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        return {}
    cache = json.loads(path.read_text(encoding="utf-8")).get("region_code_cache")
    return cache if isinstance(cache, dict) else {}


def save_region_cache(folder: Path, cache: dict[str, dict[str, str]]) -> None:
    """Persist the shared region cache after every call without dropping a finished manifest."""
    path = folder / MANIFEST_NAME
    payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"municipality_code": folder.name}
    payload["status"] = "collecting_region_codes"
    payload["region_code_cache"] = cache
    save_manifest(folder, payload)


def region_document(documents: list[dict[str, Any]], region_type: str) -> dict[str, Any] | None:
    return next((document for document in documents if document.get("region_type") == region_type), None)


def collect_region_codes(
    client: KakaoApiClient,
    folder: Path,
    cells: list[dict[str, Any]],
    cache: dict[str, dict[str, str]],
    requests_per_second: float,
) -> int:
    """Attach the 행정동/법정동 of every cell centre, caching after each call so a rerun resumes."""
    called = 0
    for cell in cells:
        key = f"{cell['longitude']:.6f},{cell['latitude']:.6f}"
        if key not in cache:
            documents = client.region_codes(cell["longitude"], cell["latitude"])
            administrative = region_document(documents, "H")
            legal = region_document(documents, "B")
            if administrative is None or legal is None:
                raise ValueError(f"{cell['cell_id']}: Kakao returned no 행정동(H)/법정동(B) region document")
            cache[key] = {
                "administrative_code": str(administrative.get("code", "")),
                "administrative_name": str(administrative.get("address_name", "")),
                "legal_address_name": str(legal.get("address_name", "")),
            }
            called += 1
            save_region_cache(folder, cache)
            time.sleep(1.0 / requests_per_second)
        cell["region"] = cache[key]
    return called


def municipality_prefixes(demand_rows: list[dict[str, str]], code: str) -> set[str]:
    """The 시군구 code prefixes staged for this municipality.

    수원시 (41110) and the other 특례시 carry 자치구 codes in their 행정동 codes
    (41111 장안구, 41113 권선구, 41115 팔달구, 41117 영통구), so membership cannot be
    decided by the municipality code alone; the staged demand file defines it.
    """
    foreign = sorted({row["municipality_code"] for row in demand_rows} - {code})
    if foreign:
        raise ValueError(f"{code}: demand_population.csv contains cross-municipality rows {foreign}")
    return {row["administrative_code"][:5] for row in demand_rows}


def allocate_population(
    cells: list[dict[str, Any]], demand_rows: list[dict[str, str]], code: str, prefixes: set[str]
) -> list[dict[str, Any]]:
    """Keep the cells inside the municipality and split each 행정동 population evenly among them."""
    populations = {row["administrative_code"]: int(row["population"]) for row in demand_rows}
    inside = [cell for cell in cells if cell["region"]["administrative_code"][:5] in prefixes]
    counts = Counter(cell["region"]["administrative_code"] for cell in inside)
    unknown = sorted(set(counts) - set(populations))
    if unknown:
        raise ValueError(
            f"{code}: 행정동 코드 {unknown} from Kakao coord2regioncode is not in demand_population.csv; "
            "stage the missing 행정동 instead of guessing its population"
        )
    for cell in inside:
        administrative_code = cell["region"]["administrative_code"]
        cell["administrative_code"] = administrative_code
        cell["cell_population_estimate"] = populations[administrative_code] / counts[administrative_code]
    return inside


def exclude_top_population(cells: list[dict[str, Any]], share: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop the highest-population cells (NIMBY); ties at the cutoff are all dropped."""
    rank_cutoff = math.ceil(share * len(cells))
    if rank_cutoff <= 0:
        return list(cells), {"rank_cutoff": 0, "cutoff_population": None, "tie_expanded": False, "excluded_cells": 0}
    ranked = sorted(cells, key=lambda cell: cell["cell_population_estimate"], reverse=True)
    cutoff_population = ranked[min(rank_cutoff, len(ranked)) - 1]["cell_population_estimate"]
    kept = [cell for cell in cells if cell["cell_population_estimate"] < cutoff_population]
    excluded = len(cells) - len(kept)
    detail = {
        "rank_cutoff": rank_cutoff,
        "cutoff_population": cutoff_population,
        "tie_expanded": excluded > rank_cutoff,
        "excluded_cells": excluded,
    }
    return kept, detail


def facility_points(folder: Path, public_health_rows: list[dict[str, str]]) -> list[tuple[float, float, str]]:
    """Every place that already provides care: emergency hospitals plus public-health facilities."""
    points = [
        (float(row["latitude"]), float(row["longitude"]), row["hospital_name"])
        for row in read_rows(folder / "existing_hospitals.csv")
    ]
    points.extend(
        (float(row["latitude"]), float(row["longitude"]), row["candidate_name"])
        for row in public_health_rows
    )
    if not points:
        raise ValueError(f"{folder.name}: no existing hospital or public-health facility to measure separation from")
    return points


def base_points(folder: Path) -> list[tuple[float, float, str]]:
    """Existing 119 안전센터; the only separation reference a standby post is measured against."""
    points = [
        (float(row["latitude"]), float(row["longitude"]), row["base_name"])
        for row in read_rows(folder / "ambulance_bases.csv")
    ]
    if not points:
        raise ValueError(f"{folder.name}: no ambulance base to measure standby separation from")
    return points


def filter_by_distance(
    cells: list[dict[str, Any]], points: list[tuple[float, float, str]], min_distance_km: float
) -> list[dict[str, Any]]:
    kept = []
    for cell in cells:
        distance_km = min(
            haversine_km(cell["latitude"], cell["longitude"], latitude, longitude)
            for latitude, longitude, _ in points
        )
        cell["nearest_facility_km"] = distance_km
        if distance_km >= min_distance_km:
            kept.append(cell)
    return kept


def read_candidate_file(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if fields[:len(BASE_FIELDS)] != BASE_FIELDS:
        raise ValueError(f"{path}: unexpected candidate header {fields}")
    return rows


def public_health_rows(folder: Path) -> list[dict[str, str]]:
    """The MOHW public-health rows, from candidate_sites.csv or a previous run's reference file."""
    for name in ("candidate_sites.csv", REFERENCE_NAME):
        path = folder / name
        if path.is_file():
            rows = [row for row in read_candidate_file(path) if row["candidate_id"].startswith(PUBLIC_HEALTH_PREFIX)]
            if rows:
                return rows
    raise ValueError(
        f"{folder.name}: no {PUBLIC_HEALTH_PREFIX}* public-health row found in candidate_sites.csv or {REFERENCE_NAME}; "
        "stage the MOHW public-health facilities first"
    )


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def candidate_row(cell: dict[str, Any], code: str, grid_km: float) -> dict[str, Any]:
    return {
        "candidate_id": cell["cell_id"],
        "candidate_name": f"{cell['region']['legal_address_name']} 격자 {cell['grid_row']}-{cell['grid_col']}",
        "municipality_code": code,
        "road_address": cell["region"]["legal_address_name"],
        "latitude": f"{cell['latitude']:.10f}",
        "longitude": f"{cell['longitude']:.10f}",
        "candidate_source": f"population-filtered {grid_km:g} km grid; Kakao coord2regioncode administrative membership",
        "land_feasibility_status": "requires_planning_review_vacant_site_unverified",
        "coordinate_source": "grid_cell_center",
        "matched_address": cell["region"]["administrative_name"],
        "kakao_place_id": "",
        "geocoding_status": "verified_municipality_match",
        "routing_status": "pending_kakao_api",
        "cell_population_estimate": f"{cell['cell_population_estimate']:.1f}",
        "administrative_code": cell["administrative_code"],
        "nearest_facility_km": f"{cell['nearest_facility_km']:.3f}",
        "grid_row": cell["grid_row"],
        "grid_col": cell["grid_col"],
    }


def standby_row(cell: dict[str, Any], code: str, grid_km: float) -> dict[str, Any]:
    """A standby post keeps the candidate header; only source and feasibility wording differ."""
    row = candidate_row(cell, code, grid_km)
    row["candidate_source"] = (
        f"population-weighted {grid_km:g} km grid standby post; Kakao coord2regioncode membership"
    )
    row["land_feasibility_status"] = "requires_review_standby_post_unverified"
    return row


def simulation_pair_count(demands: int, bases: int, hospitals: int, candidates: int) -> int:
    facilities = hospitals + candidates
    return (bases + facilities) * demands + demands * facilities + facilities * (facilities + bases - 1)


def build_municipality(
    folder: Path,
    client: KakaoApiClient | None,
    *,
    grid_km: float,
    exclude_top_population_share: float,
    min_distance_km: float,
    requests_per_second: float,
    overwrite: bool,
    dry_run: bool,
) -> int:
    code = folder.name
    demand_rows = read_rows(folder / "demand_population.csv")
    if not demand_rows:
        raise ValueError(f"{code}: demand_population.csv is empty")
    cells, geometry = build_grid(demand_rows, grid_km, code)
    print(f"{code}: {geometry['rows']}x{geometry['columns']} = {len(cells)} cells of {grid_km:g} km over the demand envelope")
    if dry_run:
        return 0

    reference_path = folder / REFERENCE_NAME
    if reference_path.exists() and not overwrite:
        raise ValueError(f"{code}: {REFERENCE_NAME} already exists from an earlier grid run; pass --overwrite to rebuild")
    health_rows = public_health_rows(folder)
    if client is None:
        raise ValueError(f"{code}: a Kakao client is required unless --dry-run is used")

    prefixes = municipality_prefixes(demand_rows, code)
    cache = load_region_cache(folder)
    called = collect_region_codes(client, folder, cells, cache, requests_per_second)
    inside = allocate_population(cells, demand_rows, code, prefixes)
    populated = [cell for cell in inside if cell["cell_population_estimate"] > 0]
    after_population, exclusion = exclude_top_population(populated, exclude_top_population_share)
    points = facility_points(folder, health_rows)
    selected = filter_by_distance(after_population, points, min_distance_km)
    selected.sort(key=lambda cell: cell["cell_id"])

    write_csv(reference_path, BASE_FIELDS, health_rows)
    write_csv(
        folder / "candidate_sites.csv",
        BASE_FIELDS + EXTRA_FIELDS,
        [candidate_row(cell, code, grid_km) for cell in selected],
    )
    counts = {
        "total_cells": len(cells),
        "inside_municipality": len(inside),
        "population_positive": len(populated),
        "after_top_population_exclusion": len(after_population),
        "after_distance_filter": len(selected),
    }
    save_manifest(folder, {
        "municipality_code": code,
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/build_grid_candidates.py",
        "candidate_definition": (
            "grid cells with no existing care facility: inside the municipality, population outside the top "
            f"{exclude_top_population_share:.0%} of cells, and at least {min_distance_km:g} km from every "
            "existing emergency hospital and public-health facility"
        ),
        "parameters": {
            "grid_km": grid_km,
            "exclude_top_population_share": exclude_top_population_share,
            "min_distance_km": min_distance_km,
        },
        "grid": geometry,
        "counts": counts,
        "top_population_exclusion": exclusion,
        "separation_reference": {
            "emergency_hospitals": len(points) - len(health_rows),
            "public_health_facilities": len(health_rows),
            "public_health_reference_file": REFERENCE_NAME,
        },
        "population_allocation_note": POPULATION_NOTE,
        "haversine_note": HAVERSINE_NOTE,
        "administrative_membership_source": "Kakao Local coord2regioncode (region_type H = 행정동, B = 법정동)",
        "municipality_administrative_prefixes": sorted(prefixes),
        "municipality_membership_rule": (
            "a cell is inside the municipality when its 행정동 code starts with one of the 시군구 prefixes "
            "staged in demand_population.csv (수원시 uses the 자치구 codes 41111/41113/41115/41117, not 41110); "
            "a cell inside those prefixes whose 행정동 code is missing from demand_population.csv stops the run"
        ),
        "new_region_code_calls": called,
        "region_code_cache": dict(sorted(cache.items())),
    })

    print(
        f"{code}: cells {counts['total_cells']} -> inside {counts['inside_municipality']} -> "
        f"population>0 {counts['population_positive']} -> after top "
        f"{exclude_top_population_share:.0%} exclusion {counts['after_top_population_exclusion']} -> "
        f"after >={min_distance_km:g}km separation {counts['after_distance_filter']}"
    )
    if exclusion["tie_expanded"]:
        print(
            f"{code}: the top-population cutoff fell on a tie at {exclusion['cutoff_population']:.1f} residents, "
            f"so {exclusion['excluded_cells']} cells were excluded instead of {exclusion['rank_cutoff']}"
        )
    print(f"{code}: {len(health_rows)} public-health rows moved to {REFERENCE_NAME}")
    print(f"{code}: {len(selected)} grid candidates written to candidate_sites.csv ({called} new Kakao region-code calls)")
    pairs = simulation_pair_count(
        len(demand_rows), len(read_rows(folder / "ambulance_bases.csv")),
        len(points) - len(health_rows), len(selected),
    )
    print(
        f"{code}: candidates changed, so road_times_manifest.json is now stale; re-run "
        f"scripts/collect_kakao_routes.py --pair-set simulation to collect {pairs} directional pairs"
    )
    return 0


def standby_pair_count(demands: int, bases: int, hospitals: int, posts: int) -> int:
    """Contract routes for every post: post -> demand, hospital -> post, post <-> base.

    Every base is a standby post too, so the base posts add the base-to-base routes
    plus their own demand and hospital legs.
    """
    return posts * (demands + hospitals + 2 * bases) + bases * (demands + hospitals) + bases * (bases - 1)


def build_standby_municipality(
    folder: Path,
    client: KakaoApiClient | None,
    *,
    grid_km: float,
    min_distance_km: float,
    requests_per_second: float,
    overwrite: bool,
    dry_run: bool,
) -> int:
    """Write standby_candidates.csv: every populated cell away from the existing 119 안전센터."""
    code = folder.name
    demand_rows = read_rows(folder / "demand_population.csv")
    if not demand_rows:
        raise ValueError(f"{code}: demand_population.csv is empty")
    cells, geometry = build_grid(demand_rows, grid_km, code)
    print(f"{code}: {geometry['rows']}x{geometry['columns']} = {len(cells)} cells of {grid_km:g} km over the demand envelope")
    if dry_run:
        return 0

    output_path = folder / STANDBY_NAME
    if output_path.exists() and not overwrite:
        raise ValueError(f"{code}: {STANDBY_NAME} already exists from an earlier standby run; pass --overwrite to rebuild")
    if client is None:
        raise ValueError(f"{code}: a Kakao client is required unless --dry-run is used")

    prefixes = municipality_prefixes(demand_rows, code)
    cache = load_region_cache(folder)
    called = collect_region_codes(client, folder, cells, cache, requests_per_second)
    inside = allocate_population(cells, demand_rows, code, prefixes)
    populated = [cell for cell in inside if cell["cell_population_estimate"] > 0]
    points = base_points(folder)
    selected = filter_by_distance(populated, points, min_distance_km)
    selected.sort(key=lambda cell: cell["cell_id"])

    write_csv(output_path, BASE_FIELDS + EXTRA_FIELDS, [standby_row(cell, code, grid_km) for cell in selected])
    counts = {
        "total_cells": len(cells),
        "inside_municipality": len(inside),
        "population_positive": len(populated),
        "after_distance_filter": len(selected),
    }
    payload = {
        "municipality_code": code,
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "scripts/build_grid_candidates.py --mode standby",
        "candidate_definition": (
            "grid cells usable as an ambulance standby post: inside the municipality, resident "
            f"population above zero, and at least {min_distance_km:g} km from every existing 119 안전센터; "
            "no top-population exclusion, because a standby post is a parking spot and not a building"
        ),
        "parameters": {"mode": "standby", "grid_km": grid_km, "min_distance_km": min_distance_km},
        "grid": geometry,
        "counts": counts,
        "separation_reference": {"ambulance_bases": len(points), "file": "ambulance_bases.csv"},
        "existing_bases_are_posts": (
            f"the {len(points)} existing 119 안전센터 are standby posts as well; they are added by "
            "scripts/build_municipal_standby_inputs.py, not listed in this file"
        ),
        "population_allocation_note": POPULATION_NOTE,
        "haversine_note": HAVERSINE_NOTE,
        "administrative_membership_source": "Kakao Local coord2regioncode (region_type H = 행정동, B = 법정동)",
        "municipality_administrative_prefixes": sorted(prefixes),
        "new_region_code_calls": called,
        "region_code_cache_file": MANIFEST_NAME,
    }
    (folder / STANDBY_MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{code}: cells {counts['total_cells']} -> inside {counts['inside_municipality']} -> "
        f"population>0 {counts['population_positive']} -> after >={min_distance_km:g}km "
        f"separation from every 119 안전센터 {counts['after_distance_filter']}"
    )
    print(f"{code}: {len(selected)} standby posts written to {STANDBY_NAME} ({called} new Kakao region-code calls)")
    pairs = standby_pair_count(
        len(demand_rows), len(points), len(read_rows(folder / "existing_hospitals.csv")), len(selected),
    )
    print(
        f"{code}: run scripts/probe_candidate_routability.py --candidates-file {STANDBY_NAME}, then "
        f"scripts/collect_kakao_routes.py --pair-set standby to collect {pairs} directional pairs"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Build population-filtered grid candidate sites for a municipality.")
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--municipality-code", action="append", required=True, help="Repeatable.")
    parser.add_argument(
        "--mode",
        choices=("hospital", "standby"),
        default="hospital",
        help="hospital writes candidate_sites.csv; standby writes standby_candidates.csv (ambulance standby posts).",
    )
    parser.add_argument("--grid-km", type=float, default=1.0)
    parser.add_argument("--exclude-top-population-share", type=float, default=0.30)
    parser.add_argument(
        "--min-distance-km", type=float, default=None,
        help="Minimum separation from the reference facilities; default 2.0 for hospital, 1.0 for standby.",
    )
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    parser.add_argument("--requests-per-second", type=float, default=5.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="No API calls; prints the grid size only.")
    args = parser.parse_args(argv)
    if args.grid_km <= 0:
        parser.error("--grid-km must be positive")
    if not 0 <= args.exclude_top_population_share < 1:
        parser.error("--exclude-top-population-share must be in [0, 1)")
    min_distance_km = args.min_distance_km if args.min_distance_km is not None else (
        1.0 if args.mode == "standby" else 2.0
    )
    if min_distance_km < 0:
        parser.error("--min-distance-km must not be negative")
    if args.requests_per_second <= 0:
        parser.error("--requests-per-second must be positive")
    key = os.environ.get(args.api_key_env, "")
    if not key and not args.dry_run:
        parser.error(
            f"set {args.api_key_env} to the official Kakao Developers REST API key "
            "from 앱 > 플랫폼 키 > REST API 키"
        )
    root = args.processed_dir.resolve()
    folders = [root / code for code in args.municipality_code]
    missing = [folder.name for folder in folders if not folder.is_dir()]
    if missing:
        parser.error(f"municipality folders not found: {missing}")
    client = None if args.dry_run else KakaoApiClient(key)
    try:
        for folder in folders:
            if args.mode == "standby":
                build_standby_municipality(
                    folder, client,
                    grid_km=args.grid_km,
                    min_distance_km=min_distance_km,
                    requests_per_second=args.requests_per_second,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                )
                continue
            build_municipality(
                folder, client,
                grid_km=args.grid_km,
                exclude_top_population_share=args.exclude_top_population_share,
                min_distance_km=min_distance_km,
                requests_per_second=args.requests_per_second,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
    except KakaoApiError as exc:
        print(f"error: {str(exc).replace(key, '***') if key else exc}", file=sys.stderr)
        print("rerun the same command to resume from the cached region codes", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"error: missing column or field {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
