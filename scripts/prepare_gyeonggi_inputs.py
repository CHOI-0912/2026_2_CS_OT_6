"""Normalize downloaded official sources into 31 municipality staging folders.

This script deliberately stops before geocoding, route calculation, demand-rate
calibration, and candidate-site selection.  Missing inputs remain explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable


CODE_RE = re.compile(r"\((\d{10})\)\s*$")
GYEONGGI_NAMES = {
    "수원시", "성남시", "고양시", "용인시", "부천시", "안산시", "안양시", "남양주시",
    "화성시", "평택시", "의정부시", "시흥시", "파주시", "광명시", "김포시", "군포시",
    "광주시", "이천시", "양주시", "오산시", "구리시", "안성시", "포천시", "의왕시",
    "하남시", "여주시", "양평군", "과천시", "가평군", "연천군", "동두천시",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="cp949", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_and_verify_manifest(raw_dir: Path) -> dict:
    manifest = json.loads((raw_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for dataset in manifest["datasets"]:
        if "path" not in dataset:
            continue
        path = raw_dir / dataset["path"]
        if not path.is_file() or path.stat().st_size != dataset["size_bytes"] or sha256(path) != dataset["sha256"]:
            raise ValueError(f"raw source failed manifest verification: {path}")
    return manifest


def parse_population_label(label: str) -> tuple[str, str, list[str]]:
    match = CODE_RE.search(label)
    if not match:
        raise ValueError(f"population row has no 10-digit administrative code: {label!r}")
    code = match.group(1)
    name = CODE_RE.sub("", label).strip()
    return code, name, name.split()


def municipality_from_address(address: str) -> str | None:
    match = re.match(r"^경기도\s+([^\s]+(?:시|군))\b", address.strip())
    return match.group(1) if match and match.group(1) in GYEONGGI_NAMES else None


def municipality_from_fire_station_name(name: str) -> str | None:
    matches = [municipality for municipality in GYEONGGI_NAMES if municipality[:-1] in name]
    return matches[0] if len(matches) == 1 else None


def integer(value: str) -> int:
    return int(value.replace(",", "").strip() or 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw_dir = args.raw_dir.resolve()
    output_dir = args.output_dir.resolve()
    manifest = load_and_verify_manifest(raw_dir)

    hospitals = read_csv(raw_dir / "gyeonggi_emergency_medical_institutions_20250917_primary.csv")
    fleet = [
        row for row in read_csv(raw_dir / "nfa_ambulances_by_fire_station_119_center_20250630.csv")
        if row["시도"] in {"경기", "경기북부"}
    ]
    centers = [
        row for row in read_csv(raw_dir / "nfa_119_safety_centers_20231231.csv")
        if row["시도본부"] == "경기도"
    ]
    population = read_csv(raw_dir / "mois_resident_population_all_eup_myeon_dong_202608.csv")

    parsed_population = []
    for row in population:
        if not row["행정구역"].lstrip().startswith("경기도"):
            continue
        code, full_name, parts = parse_population_label(row["행정구역"])
        municipality = parts[1] if len(parts) >= 2 and parts[1] in GYEONGGI_NAMES else None
        parsed_population.append((row, code, full_name, parts, municipality))

    municipal_totals = {
        parts[1]: {"code": code[:5], "population": integer(row["2026년08월_총인구수"])}
        for row, code, _full_name, parts, _municipality in parsed_population
        if len(parts) == 2 and parts[1] in GYEONGGI_NAMES
    }
    if set(municipal_totals) != GYEONGGI_NAMES:
        missing = sorted(GYEONGGI_NAMES - set(municipal_totals))
        extra = sorted(set(municipal_totals) - GYEONGGI_NAMES)
        raise ValueError(f"expected 31 municipalities; missing={missing}, extra={extra}")

    center_lookup = {(row["소방서"].strip(), row["119안전센터명"].strip()): row for row in centers}
    station_municipalities: dict[str, set[str]] = {}
    for center in centers:
        municipality = municipality_from_address(center["주소"])
        if municipality:
            station_municipalities.setdefault(center["소방서"].strip(), set()).add(municipality)

    ambulances_by_municipality: dict[str, list[dict[str, object]]] = {name: [] for name in GYEONGGI_NAMES}
    unresolved_ambulances: list[dict[str, object]] = []
    for row in fleet:
        station = row["소방서"].strip()
        center_name = row["안전센터"].strip()
        regional_unit = row["지역대"].strip()
        matched_center = center_lookup.get((station, center_name))
        address = matched_center["주소"].strip() if matched_center else ""
        municipality = municipality_from_address(address) if address else None
        if municipality is None:
            inferred = station_municipalities.get(station, set())
            municipality = next(iter(inferred)) if len(inferred) == 1 else None
        assignment = "address_exact" if municipality_from_address(address) else ("fire_station_address_inferred" if municipality else "unresolved")
        if municipality is None:
            municipality = municipality_from_fire_station_name(station)
            if municipality:
                assignment = "official_fire_station_name_inferred"
        record = {
            "source_province": row["시도"],
            "fire_station": station,
            "safety_center": center_name,
            "regional_unit": regional_unit,
            "ambulance_count": integer(row["수량"]),
            "base_name": regional_unit or center_name or station,
            "address": "" if regional_unit else address,
            "latitude": "",
            "longitude": "",
            "coordinate_source": "",
            "matched_address": "",
            "kakao_place_id": "",
            "municipality_assignment": assignment,
            "geocoding_status": "pending_kakao_api",
            "routing_status": "pending_kakao_api",
        }
        if municipality:
            ambulances_by_municipality[municipality].append(record)
        else:
            unresolved_ambulances.append({**record, "reason": "no unique municipality from official center address"})

    summaries = []
    for municipality, total in sorted(municipal_totals.items(), key=lambda item: item[1]["code"]):
        code = total["code"]
        folder = output_dir / code
        hospital_rows = [
            {
                "hospital_name": row["병원명/센터명"].strip(),
                "emergency_category": row["업무구분명"].strip(),
                "phone": row["대표전화번호"].strip(),
                "road_address": row["소재지도로명주소"].strip(),
                "lot_address": row["소재지지번주소"].strip(),
                "postal_code": row["소재지우편번호"].strip(),
                "latitude": row["위도"].strip(),
                "longitude": row["경도"].strip(),
                "coordinate_source": "official_gyeonggi_emergency_medical_institutions",
                "matched_address": row["소재지도로명주소"].strip(),
                "municipality_code": code,
                "routing_status": "pending_kakao_api",
            }
            for row in hospitals if row["시군명"].strip() == municipality
        ]
        demand_rows = [
            {
                "administrative_code": admin_code,
                "administrative_name": full_name,
                "municipality_code": code,
                "population": integer(row["2026년08월_총인구수"]),
                "households": integer(row["2026년08월_세대수"]),
                "latitude": "",
                "longitude": "",
                "coordinate_source": "",
                "matched_address": "",
                "kakao_place_id": "",
                "geocoding_status": "pending_kakao_api",
                "demand_rate_status": "pending_119_activity_api",
            }
            for row, admin_code, full_name, _parts, assigned in parsed_population
            if assigned == municipality and not admin_code.endswith("00000")
        ]
        ambulance_rows = ambulances_by_municipality[municipality]
        write_csv(folder / "existing_hospitals.csv", [
            "hospital_name", "emergency_category", "phone", "road_address", "lot_address",
            "postal_code", "latitude", "longitude", "coordinate_source", "matched_address", "municipality_code", "routing_status",
        ], hospital_rows)
        write_csv(folder / "demand_population.csv", [
            "administrative_code", "administrative_name", "municipality_code", "population", "households",
            "latitude", "longitude", "coordinate_source", "matched_address", "kakao_place_id", "geocoding_status", "demand_rate_status",
        ], demand_rows)
        write_csv(folder / "ambulance_bases.csv", [
            "source_province", "fire_station", "safety_center", "regional_unit", "ambulance_count",
            "base_name", "address", "latitude", "longitude", "coordinate_source", "matched_address", "kakao_place_id", "municipality_assignment",
            "geocoding_status", "routing_status",
        ], ambulance_rows)
        write_csv(folder / "candidate_sites.csv", [
            "candidate_id", "candidate_name", "municipality_code", "road_address", "latitude", "longitude",
            "candidate_source", "land_feasibility_status", "coordinate_source", "matched_address", "kakao_place_id", "geocoding_status", "routing_status",
        ], [])
        readiness = {
            "municipality_code": code,
            "municipality_name": municipality,
            "existing_emergency_hospital_count": len(hospital_rows),
            "demand_point_count": len(demand_rows),
            "ambulance_base_row_count": len(ambulance_rows),
            "ambulance_count": sum(int(row["ambulance_count"]) for row in ambulance_rows),
            "ready_for_simulation": False,
            "blocking_inputs": [
                "ambulance and demand-point geocoding",
                "actual directional road travel-time matrix",
                "municipal 119 demand-rate calibration",
                "physically feasible new-hospital candidate sites",
                "hospital capacity/capability calibration"
            ],
        }
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append({
            "municipality_code": code,
            "municipality_name": municipality,
            "population_202608": total["population"],
            "demand_point_count": len(demand_rows),
            "existing_emergency_hospital_count": len(hospital_rows),
            "ambulance_base_row_count": len(ambulance_rows),
            "ambulance_count": readiness["ambulance_count"],
            "ready_for_simulation": False,
        })

    write_csv(output_dir / "municipality_summary.csv", [
        "municipality_code", "municipality_name", "population_202608", "demand_point_count",
        "existing_emergency_hospital_count", "ambulance_base_row_count", "ambulance_count", "ready_for_simulation",
    ], summaries)
    write_csv(output_dir / "unresolved_ambulance_bases.csv", [
        "source_province", "fire_station", "safety_center", "regional_unit", "ambulance_count", "base_name", "reason",
    ], unresolved_ambulances)
    build_manifest = {
        "source_manifest": str(raw_dir / "source_manifest.json"),
        "source_manifest_sha256": sha256(raw_dir / "source_manifest.json"),
        "municipality_count": len(summaries),
        "hospital_rows": sum(row["existing_emergency_hospital_count"] for row in summaries),
        "demand_rows": sum(row["demand_point_count"] for row in summaries),
        "ambulance_rows": sum(row["ambulance_base_row_count"] for row in summaries),
        "ambulance_count": sum(row["ambulance_count"] for row in summaries),
        "unresolved_ambulance_rows": len(unresolved_ambulances),
        "ready_for_simulation": False,
        "no_synthetic_fallbacks": True,
        "raw_manifest_dataset_count": len(manifest["datasets"]),
    }
    (output_dir / "build_manifest.json").write_text(json.dumps(build_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(build_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
