"""Refresh generated municipality readiness after acquisition steps."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


DEMAND_BLOCKER = "municipal 119 demand-rate calibration"
TREATMENT_BLOCKER = "patient-type treatment success calibration"
LAND_REVIEW_ADVISORY = "candidate-site land feasibility review"
PARAMETER_STATUSES = ("provisional", "calibrated")
DEMAND_METHODS = ("population_proportional_provincial_dispatches", "observed_municipal_dispatches")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def complete_coordinates(rows: list[dict[str, str]]) -> int:
    return sum(bool(row.get("latitude") and row.get("longitude")) for row in rows)


def load_parameters(path: Path | None) -> dict | None:
    """Load a model parameter file (data/processed/calibration/model_parameters_<status>.json)."""
    if path is None:
        return None
    if not path.exists():
        print(f"parameters file not found: {path}; calibration blockers kept", file=sys.stderr)
        return None
    parameters = json.loads(path.read_text(encoding="utf-8"))
    if parameters.get("status") not in PARAMETER_STATUSES:
        raise ValueError(f"{path}: status must be one of {PARAMETER_STATUSES}")
    if parameters.get("demand", {}).get("method") not in DEMAND_METHODS:
        raise ValueError(f"{path}: demand.method must be one of {DEMAND_METHODS}")
    return parameters


def demand_blocker(parameters: dict | None, code: str) -> str | None:
    if parameters is None:
        return DEMAND_BLOCKER
    demand = parameters["demand"]
    if demand["method"] == "observed_municipal_dispatches" and code in (demand.get("daily_calls_by_municipality") or {}):
        return None
    if demand["method"] == "population_proportional_provincial_dispatches":
        return f"{DEMAND_BLOCKER} (population-proportional estimate in use)"
    return DEMAND_BLOCKER


def treatment_blocker(parameters: dict | None) -> str | None:
    if parameters is not None and parameters["status"] == "calibrated":
        return None
    return TREATMENT_BLOCKER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument(
        "--parameters", type=Path,
        help="model parameter JSON; without it (or while provisional) calibration blockers stay",
    )
    args = parser.parse_args(argv)
    root = args.processed_dir.resolve()
    parameters = load_parameters(args.parameters)

    totals = {"ambulance_rows": 0, "ambulance_rows_with_coordinates": 0,
              "demand_rows": 0, "demand_rows_with_coordinates": 0,
              "hospital_rows": 0, "hospital_rows_with_coordinates": 0,
              "candidate_rows": 0, "candidate_rows_with_coordinates": 0}
    ready_codes: list[str] = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        readiness_path = folder / "readiness.json"
        if not readiness_path.exists():
            continue
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        ambulance = read_csv(folder / "ambulance_bases.csv")
        demand = read_csv(folder / "demand_population.csv")
        hospitals = read_csv(folder / "existing_hospitals.csv")
        candidates = read_csv(folder / "candidate_sites.csv")

        ambulance_done = complete_coordinates(ambulance)
        demand_done = complete_coordinates(demand)
        hospital_done = complete_coordinates(hospitals)
        candidate_done = complete_coordinates(candidates)
        readiness.update({
            "ambulance_base_rows_with_coordinates": ambulance_done,
            "demand_points_with_coordinates": demand_done,
            "existing_hospitals_with_coordinates": hospital_done,
            "candidate_site_count": len(candidates),
            "candidate_sites_with_coordinates": candidate_done,
        })
        blockers: list[str] = []
        advisories: list[str] = []
        if ambulance_done != len(ambulance):
            blockers.append("ambulance-base geocoding")
        if demand_done != len(demand):
            blockers.append("demand-point geocoding")
        if hospital_done != len(hospitals):
            blockers.append("existing-hospital geocoding")
        route_manifest_path = folder / "road_times_manifest.json"
        route_manifest = (
            json.loads(route_manifest_path.read_text(encoding="utf-8"))
            if route_manifest_path.exists() else {}
        )
        if not route_manifest.get("simulation_ready", False):
            blockers.append("actual directional road travel-time matrix")
        blocker = demand_blocker(parameters, folder.name)
        if blocker:
            blockers.append(blocker)
        if not candidates:
            blockers.append("physically feasible new-hospital candidate sites")
        elif any(row.get("land_feasibility_status", "").startswith("requires_planning_review") for row in candidates):
            advisories.append(LAND_REVIEW_ADVISORY)
        if any(
            not row.get("emergency_room_beds")
            or row.get("capacity_match_review_required", "").strip().lower() == "true"
            for row in hospitals
        ):
            blockers.append("hospital static capacity matching")
        blocker = treatment_blocker(parameters)
        if blocker:
            blockers.append(blocker)
        readiness["blocking_inputs"] = blockers
        readiness["advisory_inputs"] = advisories
        readiness["ready_for_simulation"] = not blockers
        if not blockers:
            ready_codes.append(folder.name)
        readiness_path.write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        for prefix, rows, done in (
            ("ambulance", ambulance, ambulance_done),
            ("demand", demand, demand_done),
            ("hospital", hospitals, hospital_done),
            ("candidate", candidates, candidate_done),
        ):
            totals[f"{prefix}_rows"] += len(rows)
            totals[f"{prefix}_rows_with_coordinates"] += done

    manifest_path = root / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(totals)
    manifest["ready_for_simulation"] = bool(ready_codes)
    manifest["ready_municipality_codes"] = ready_codes
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
