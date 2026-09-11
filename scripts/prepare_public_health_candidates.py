"""Stage official public-health facilities as planning-review candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


FIELDS = [
    "candidate_id", "candidate_name", "municipality_code", "road_address",
    "latitude", "longitude", "candidate_source", "land_feasibility_status",
    "coordinate_source", "matched_address", "kakao_place_id",
    "geocoding_status", "routing_status",
]

SOURCES = {
    "gyeonggi_ansan_public_health_facilities_20260128.csv": {
        "code": "41270", "url": "https://www.data.go.kr/data/15036776/fileData.do",
        "name": "경기도 안산시 보건지소등 현황",
    },
    "gyeonggi_guri_public_health_facilities_20240607.csv": {
        "code": "41310", "url": "https://www.data.go.kr/data/3038708/fileData.do",
        "name": "경기도 구리시 공공보건기관현황",
    },
    "gyeonggi_yangju_medical_facilities_20260720.csv": {
        "code": "41630", "url": "https://www.data.go.kr/data/3079714/fileData.do",
        "name": "경기도 양주시 의료시설 현황",
    },
    "gyeonggi_yeoju_public_health_facilities_20251216.csv": {
        "code": "41670", "url": "https://www.data.go.kr/data/15118550/fileData.do",
        "name": "경기도 여주시 생활지도",
    },
}


def read_csv(path: Path) -> list[dict[str, str]]:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = payload.decode(encoding)
            return list(csv.DictReader(text.splitlines()))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", payload, 0, 1, "unsupported CSV encoding")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_rows(filename: str, source_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if "ansan" in filename or "guri" in filename:
        return source_rows
    if "yangju" in filename:
        allowed = {"보건소", "보건지소", "보건진료소"}
        return [row for row in source_rows if row["의료기관종별"] in allowed]
    if "yeoju" in filename:
        endings = ("보건소", "보건지소", "보건진료소", "건강생활지원센터")
        return [row for row in source_rows if row["장소유형"] == "공공의료" and row["장소명"].endswith(endings)]
    return []


def normalize(filename: str, row: dict[str, str], code: str, index: int, url: str) -> dict[str, str]:
    if "ansan" in filename:
        name, address, lat, lon = row["시설명"], row["소재지도로명주소"], "", ""
    elif "guri" in filename:
        name, address, lat, lon = row["의료기관명"], row["의료기관주소(도로명)"], row["위도"], row["경도"]
    elif "yangju" in filename:
        name, address, lat, lon = row["의료기관명"], row["의료기관주소(도로명)"], "", ""
    else:
        name, address, lat, lon = row["장소명"], row["소재지도로명주소"], row["위도"], row["경도"]
    return {
        "candidate_id": f"public_health_{code}_{index:03d}",
        "candidate_name": name.strip(), "municipality_code": code,
        "road_address": address.strip(), "latitude": lat.strip(), "longitude": lon.strip(),
        "candidate_source": url,
        "land_feasibility_status": "requires_planning_review_existing_public_health_facility",
        "coordinate_source": "official_source_coordinates" if lat and lon else "",
        "matched_address": address.strip() if lat and lon else "", "kakao_place_id": "",
        "geocoding_status": "official_coordinates" if lat and lon else "pending_kakao_address_geocoding",
        "routing_status": "pending_kakao_api",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--processed-dir", type=Path, required=True)
    args = parser.parse_args()
    raw_dir, processed_dir = args.raw_dir.resolve(), args.processed_dir.resolve()
    manifest = {"candidate_definition": "existing public-health facilities requiring planning review", "datasets": []}
    totals: dict[str, int] = {}
    for filename, meta in SOURCES.items():
        path = raw_dir / filename
        source_rows = read_csv(path)
        selected = selected_rows(filename, source_rows)
        staged = [normalize(filename, row, meta["code"], i + 1, meta["url"]) for i, row in enumerate(selected)]
        target = processed_dir / meta["code"] / "candidate_sites.csv"
        existing = []
        if target.exists():
            with target.open("r", encoding="utf-8-sig", newline="") as handle:
                existing = [row for row in csv.DictReader(handle) if not row.get("candidate_id", "").startswith("public_health_")]
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(existing + staged)
        totals[meta["code"]] = len(staged)
        manifest["datasets"].append({
            "name": meta["name"], "source_url": meta["url"], "file": filename,
            "sha256": sha256(path), "raw_rows": len(source_rows), "selected_candidate_rows": len(staged),
        })
    (raw_dir / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
