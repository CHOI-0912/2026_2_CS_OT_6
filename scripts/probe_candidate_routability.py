"""Drop grid candidates that Kakao Mobility cannot route to or from (no nearby road).

One probe pair per candidate (candidate -> nearest existing hospital or ambulance base
and back) costs two API calls instead of the ~180 pairs a full collection would waste
on an unroutable cell.  Rows that fail are moved to <candidates file>_unroutable.csv with
the Kakao message; nothing is invented for them.

--candidates-file also probes an ambulance standby list (standby_candidates.csv).  A cell
that already has a measured route in road_times.csv under its "<code>::candidate::<id>"
node is routable by evidence, so it is marked without spending two more calls.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.env_file import load_env_file
from ambulance_sim.kakao_api import KakaoApiClient, KakaoApiError


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def measured_nodes(folder: Path) -> set[str]:
    """Nodes that already appear in road_times.csv; only successful routes are recorded there."""
    path = folder / "road_times.csv"
    if not path.is_file():
        return set()
    return {row[key] for row in read_rows(path) for key in ("origin_id", "destination_id")}


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Eligibility helper only: picks the closest probe target, never a travel time.
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = phi2 - phi1, math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


def probe(client: KakaoApiClient, candidate: dict[str, str], targets: list[tuple[float, float]], pause: float) -> str | None:
    """Return None when both directions route, else the Kakao failure message."""
    lat, lon = float(candidate["latitude"]), float(candidate["longitude"])
    target_lat, target_lon = min(targets, key=lambda point: great_circle_km(lat, lon, point[0], point[1]))
    for origin, destination in (((lon, lat), (target_lon, target_lat)), ((target_lon, target_lat), (lon, lat))):
        try:
            client.driving_time(origin[0], origin[1], destination[0], destination[1])
        except KakaoApiError as exc:
            text = str(exc)
            if text.startswith("Kakao Mobility route failed") or text.startswith("Kakao Mobility returned no route"):
                return text
            raise
        finally:
            time.sleep(pause)
    return None


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--municipality-code", action="append", required=True)
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    parser.add_argument("--requests-per-second", type=float, default=5.0)
    parser.add_argument("--only-prefix", default="GRID-", help="Probe only candidate ids with this prefix (default GRID-).")
    parser.add_argument("--candidates-file", default="candidate_sites.csv", help="File to probe inside each municipality folder.")
    parser.add_argument("--unroutable-file", default=None, help="Where dropped rows go (default <candidates file stem>_unroutable.csv).")
    args = parser.parse_args(argv)
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error(f"set {args.api_key_env} to the Kakao Developers REST API key")
    client = KakaoApiClient(key)
    pause = 1.0 / args.requests_per_second
    for code in args.municipality_code:
        folder = args.processed_dir.resolve() / code
        candidates_path = folder / args.candidates_file
        candidates = read_rows(candidates_path)
        fields = list(candidates[0].keys()) if candidates else []
        targets = [(float(row["latitude"]), float(row["longitude"])) for row in read_rows(folder / "existing_hospitals.csv")]
        targets += [(float(row["latitude"]), float(row["longitude"])) for row in read_rows(folder / "ambulance_bases.csv")]
        if not targets:
            raise ValueError(f"{code}: no hospital or ambulance base coordinates to probe against")
        measured = measured_nodes(folder)
        kept: list[dict[str, str]] = []
        dropped: list[dict[str, str]] = []
        calls = 0
        reused = 0
        for candidate in candidates:
            if not candidate["candidate_id"].startswith(args.only_prefix):
                kept.append(candidate)
                continue
            if f"{code}::candidate::{candidate['candidate_id']}" in measured:
                # The same cell was already routed for the hospital matrix; that is the proof.
                candidate["routing_status"] = "probe_routable"
                kept.append(candidate)
                reused += 1
                continue
            failure = probe(client, candidate, targets, pause)
            calls += 2
            if failure is None:
                candidate["routing_status"] = "probe_routable"
                kept.append(candidate)
            else:
                candidate["routing_status"] = "unroutable_no_nearby_road"
                dropped.append({**candidate, "probe_error": failure.replace(key, "***"),
                                "probed_at": datetime.now(timezone.utc).isoformat()})
        write_rows(candidates_path, fields, kept)
        if dropped:
            unroutable_path = folder / (args.unroutable_file or f"{candidates_path.stem}_unroutable.csv")
            previous = read_rows(unroutable_path) if unroutable_path.exists() else []
            write_rows(unroutable_path, fields + ["probe_error", "probed_at"], previous + dropped)
        print(f"{code}: {candidates_path.name}: probed {calls // 2} candidates with {calls} calls "
              f"({reused} already measured in road_times.csv); kept {len(kept)}, dropped {len(dropped)}"
              + (f" -> {', '.join(row['candidate_id'] for row in dropped)}" if dropped else ""))
        if dropped:
            print(f"{code}: rerun scripts/build_grid_candidates.py is NOT needed; rerun collect_kakao_routes.py for the affected --pair-set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
