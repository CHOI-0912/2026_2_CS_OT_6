"""Collect resumable, municipality-local Kakao Mobility road-time matrices."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.env_file import load_env_file
from ambulance_sim.kakao_api import KakaoApiClient, KakaoApiError


FIELDS = [
    "municipality_code", "origin_id", "destination_id", "duration_minutes",
    "duration_seconds", "distance_meters", "routing_provider", "routing_profile", "collected_at",
    "pair_set", "reused_from",
]
LEGACY_PAIR_SET = "unknown_before_pair_set_column"
STANDBY_FILE = "standby_candidates.csv"
MANIFEST_NAMES = {"standby": "road_times_standby_manifest.json"}
DEFAULT_MANIFEST_NAME = "road_times_manifest.json"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_existing_routes(output: Path) -> list[dict[str, str]]:
    """Read collected routes, rewriting an older file once to add any column it predates."""
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if all(field in fields for field in FIELDS):
        return rows
    for row in rows:
        row.setdefault("pair_set", LEGACY_PAIR_SET)
        row.setdefault("reused_from", "")
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)
    return rows


UNROUTABLE_FIELDS = ["municipality_code", "origin_id", "destination_id", "error", "recorded_at"]


def is_unroutable(exc: KakaoApiError) -> bool:
    """Route-level failures (no road near a point) versus transport/quota/auth errors."""
    text = str(exc)
    return text.startswith("Kakao Mobility route failed") or text.startswith("Kakao Mobility returned no route")


def record_unroutable(folder: Path, origin: str, destination: str, message: str) -> None:
    path = folder / "road_times_unroutable.csv"
    new_file = not path.exists()
    with path.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=UNROUTABLE_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({
            "municipality_code": folder.name, "origin_id": origin, "destination_id": destination,
            "error": message, "recorded_at": datetime.now(timezone.utc).isoformat(),
        })


def coordinate(row: dict[str, str], label: str) -> tuple[float, float]:
    try:
        return float(row["longitude"]), float(row["latitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: verified longitude/latitude are required before routing") from exc


def locations(
    folder: Path,
    *,
    require_demands: bool,
    require_candidates: bool,
    include_candidates: bool,
) -> tuple[dict[str, tuple[float, float]], set[str], set[str], set[str]]:
    nodes: dict[str, tuple[float, float]] = {}
    demands: set[str] = set()
    bases: set[str] = set()
    facilities: set[str] = set()
    code = folder.name
    for row in read_rows(folder / "demand_population.csv"):
        ident = f"{code}::demand::{row['administrative_code']}"
        if row.get("longitude") and row.get("latitude"):
            nodes[ident] = coordinate(row, ident)
        elif require_demands:
            coordinate(row, ident)
        else:
            continue
        demands.add(ident)
    for index, row in enumerate(read_rows(folder / "ambulance_bases.csv"), start=1):
        ident = f"{code}::ambulance_base::{index:03d}"
        nodes[ident] = coordinate(row, ident)
        bases.add(ident)
    for index, row in enumerate(read_rows(folder / "existing_hospitals.csv"), start=1):
        ident = f"{code}::hospital::{index:03d}"
        nodes[ident] = coordinate(row, ident)
        facilities.add(ident)
    candidate_path = folder / "candidate_sites.csv"
    candidates = read_rows(candidate_path) if candidate_path.is_file() else []
    if require_candidates and not candidates:
        raise ValueError(f"{code}: candidate_sites.csv is empty")
    if include_candidates:
        for row in candidates:
            ident = f"{code}::candidate::{row['candidate_id']}"
            nodes[ident] = coordinate(row, ident)
            facilities.add(ident)
    return nodes, demands, bases, facilities


def required_pairs(demands: set[str], bases: set[str], facilities: set[str]) -> list[tuple[str, str]]:
    pairs = {(origin, demand) for origin in bases | facilities for demand in demands}
    pairs.update((demand, facility) for demand in demands for facility in facilities)
    pairs.update((origin, target) for origin in facilities for target in facilities | bases if origin != target)
    return sorted(pairs)


def _point_key(point: tuple[float, float]) -> tuple[str, str]:
    """A grid cell is the same physical place when its coordinates match to six decimals."""
    return (f"{point[0]:.6f}", f"{point[1]:.6f}")


def standby_locations(
    folder: Path,
) -> tuple[dict[str, tuple[float, float]], set[str], set[str], set[str], set[str], dict[str, str]]:
    """Nodes, demands, bases, hospitals, standby posts, and the candidate twin of each post.

    A standby post is a grid cell centre; when the same cell was already collected as a
    hospital candidate, its routes are reused instead of being paid for a second time.
    """
    nodes, demands, bases, hospitals = locations(
        folder, require_demands=True, require_candidates=False, include_candidates=False
    )
    code = folder.name
    candidate_by_point: dict[tuple[str, str], str] = {}
    candidate_path = folder / "candidate_sites.csv"
    if candidate_path.is_file():
        for row in read_rows(candidate_path):
            ident = f"{code}::candidate::{row['candidate_id']}"
            candidate_by_point[_point_key(coordinate(row, ident))] = ident
    standby_rows = read_rows(folder / STANDBY_FILE)
    if not standby_rows:
        raise ValueError(f"{code}: {STANDBY_FILE} is empty; run build_grid_candidates.py --mode standby first")
    posts: set[str] = set()
    twin: dict[str, str] = {}
    for row in standby_rows:
        ident = f"{code}::standby::{row['candidate_id']}"
        point = coordinate(row, ident)
        nodes[ident] = point
        posts.add(ident)
        candidate = candidate_by_point.get(_point_key(point))
        if candidate is not None:
            twin[ident] = candidate
    return nodes, demands, bases, hospitals, posts, twin


def standby_pairs(demands: set[str], bases: set[str], hospitals: set[str], posts: set[str]) -> list[tuple[str, str]]:
    """Every standby post needs: post -> demand, hospital -> post, post -> base and base -> post.

    Every existing 119 안전센터 is a standby post as well, so the bases join the posts here.
    """
    all_posts = posts | bases
    pairs = {(post, demand) for post in all_posts for demand in demands}
    pairs.update((hospital, post) for hospital in hospitals for post in all_posts)
    pairs.update((post, base) for post in all_posts for base in bases if post != base)
    pairs.update((base, post) for post in all_posts for base in bases if post != base)
    return sorted(pairs)


def standby_reuse_rows(
    pairs: list[tuple[str, str]],
    completed: set[tuple[str, str]],
    existing: list[dict[str, str]],
    twin: dict[str, str],
) -> list[dict[str, str]]:
    """Copy an already-measured candidate route onto the standby ids of the same cell."""
    by_pair = {(row["origin_id"], row["destination_id"]): row for row in existing}
    copied: list[dict[str, str]] = []
    for origin, destination in pairs:
        if (origin, destination) in completed:
            continue
        source = (twin.get(origin, origin), twin.get(destination, destination))
        if source == (origin, destination) or source not in by_pair:
            continue
        copied.append({
            **{field: by_pair[source].get(field, "") for field in FIELDS},
            "origin_id": origin,
            "destination_id": destination,
            "pair_set": "standby",
            "reused_from": f"{source[0]} -> {source[1]}",
        })
    return copied


def resource_pairs(bases: set[str], facilities: set[str]) -> list[tuple[str, str]]:
    """Build useful local routes before verified demand coordinates are available."""
    pairs = {(base, facility) for base in bases for facility in facilities}
    pairs.update((facility, base) for facility in facilities for base in bases)
    pairs.update((origin, target) for origin in facilities for target in facilities if origin != target)
    return sorted(pairs)


def write_manifest(
    folder: Path,
    *,
    pair_set: str,
    required: list[tuple[str, str]],
    completed: set[tuple[str, str]],
) -> None:
    covered = sum(pair in completed for pair in required)
    payload = {
        "municipality_code": folder.name,
        "pair_set": pair_set,
        "routing_provider": "Kakao Mobility Directions API",
        "routing_profile": "RECOMMEND",
        "required_pair_count": len(required),
        "completed_pair_count": covered,
        "complete": covered == len(required),
        "simulation_ready": pair_set == "simulation" and covered == len(required),
        "standby_ready": pair_set == "standby" and covered == len(required),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # The standby matrix keeps its own manifest so a finished hospital matrix stays untouched.
    (folder / MANIFEST_NAMES.get(pair_set, DEFAULT_MANIFEST_NAME)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{folder.name}: {covered}/{len(required)}")


def call_budget(limit: int | None, max_calls: int | None) -> int | None:
    values = [value for value in (limit, max_calls) if value is not None]
    return min(values) if values else None


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--municipality-code", action="append", default=[])
    parser.add_argument(
        "--pair-set",
        choices=("simulation", "operations", "resources", "standby"),
        default="simulation",
        help=(
            "simulation includes candidates; operations needs demand and existing hospitals; "
            "resources collects base/facility routes only; standby collects the ambulance "
            "standby-post routes from standby_candidates.csv into road_times_standby_manifest.json"
        ),
    )
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    parser.add_argument("--requests-per-second", type=float, default=5.0)
    parser.add_argument("--limit", type=int, help="Development-only call limit; omitted means all missing routes.")
    parser.add_argument("--max-calls", type=int, help="Daily quota guard; stop after this many API calls and resume later.")
    args = parser.parse_args(argv)
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error(
            f"set {args.api_key_env} to the official Kakao Developers REST API key "
            "from 앱 > 플랫폼 키 > REST API 키"
        )
    if args.requests_per_second <= 0:
        parser.error("--requests-per-second must be positive")
    root = args.processed_dir.resolve()
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    if args.municipality_code:
        requested = set(args.municipality_code)
        folders = [folder for folder in folders if folder.name in requested]
        missing = requested - {folder.name for folder in folders}
        if missing:
            parser.error(f"municipality folders not found: {sorted(missing)}")
    client = KakaoApiClient(key)
    budget = call_budget(args.limit, args.max_calls)
    total_called = 0
    for folder in folders:
        twin: dict[str, str] = {}
        if args.pair_set == "standby":
            nodes, demands, bases, hospitals, posts, twin = standby_locations(folder)
            pairs = standby_pairs(demands, bases, hospitals, posts)
        else:
            nodes, demands, bases, facilities = locations(
                folder,
                require_demands=args.pair_set in {"simulation", "operations"},
                require_candidates=args.pair_set == "simulation",
                include_candidates=args.pair_set != "operations",
            )
            pairs = (
                required_pairs(demands, bases, facilities)
                if args.pair_set in {"simulation", "operations"}
                else resource_pairs(bases, facilities)
            )
        output = folder / "road_times.csv"
        existing = read_existing_routes(output) if output.exists() else []
        completed = {(row["origin_id"], row["destination_id"]) for row in existing}
        copied = standby_reuse_rows(pairs, completed, existing, twin) if twin else []
        completed.update((row["origin_id"], row["destination_id"]) for row in copied)
        missing_pairs = [pair for pair in pairs if pair not in completed]
        if budget is not None:
            missing_pairs = missing_pairs[:max(0, budget - total_called)]
        unroutable: set[tuple[str, str]] = set()
        new_file = not output.exists()
        with output.open("a", encoding="utf-8-sig" if new_file else "utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            if copied:
                writer.writerows(copied)
                handle.flush()
                print(f"{folder.name}: reused {len(copied)} measured candidate routes as standby routes")
            for origin, destination in missing_pairs:
                origin_lon, origin_lat = nodes[origin]
                destination_lon, destination_lat = nodes[destination]
                try:
                    route = client.driving_time(origin_lon, origin_lat, destination_lon, destination_lat)
                except KakaoApiError as exc:
                    message = str(exc).replace(key, "***")
                    if is_unroutable(exc):
                        # A grid cell centre far from any road cannot be routed.  Record the pair
                        # and keep going; the candidate is removed by the caller, not guessed here.
                        record_unroutable(folder, origin, destination, message)
                        unroutable.add((origin, destination))
                        total_called += 1
                        time.sleep(1.0 / args.requests_per_second)
                        continue
                    write_manifest(folder, pair_set=args.pair_set, required=pairs, completed=completed)
                    print(f"{folder.name}: Kakao API error after {total_called} new routes: {message}", file=sys.stderr)
                    print("rerun without changing output to resume", file=sys.stderr)
                    return 2
                writer.writerow({
                    "municipality_code": folder.name,
                    "origin_id": origin,
                    "destination_id": destination,
                    "duration_minutes": route["duration_minutes"],
                    "duration_seconds": route["duration_seconds"],
                    "distance_meters": route["distance_meters"],
                    "routing_provider": "Kakao Mobility Directions API",
                    "routing_profile": route["priority"],
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "pair_set": args.pair_set,
                    "reused_from": "",
                })
                handle.flush()
                completed.add((origin, destination))
                total_called += 1
                if budget is not None and total_called >= budget:
                    write_manifest(folder, pair_set=args.pair_set, required=pairs, completed=completed)
                    if args.max_calls is not None and total_called >= args.max_calls:
                        print(f"stopped at --max-calls={args.max_calls} (daily quota guard); rerun without changing output to resume")
                    else:
                        print(f"stopped at --limit={args.limit}; rerun without changing output to resume")
                    return 0
                time.sleep(1.0 / args.requests_per_second)
        write_manifest(folder, pair_set=args.pair_set, required=pairs, completed=completed)
        if unroutable:
            nodes_involved = sorted({node for pair in unroutable for node in pair if node.split("::")[1] in {"candidate", "standby"}})
            print(
                f"{folder.name}: {len(unroutable)} pairs unroutable (see road_times_unroutable.csv); "
                f"candidates involved: {', '.join(nodes_involved) or 'none'}",
                file=sys.stderr,
            )
    print(f"collected {total_called} new directional routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
