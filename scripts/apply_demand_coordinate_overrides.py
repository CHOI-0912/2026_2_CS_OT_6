"""Resolve ambiguous administrative-office representative points from official addresses."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.env_file import load_env_file
from ambulance_sim.kakao_api import KakaoApiClient


OVERRIDES = {
    "4129054000": {
        "address": "경기도 과천시 별양로 174",
        "source": "https://www.gccity.go.kr/csc/contents.do?mId=0401010000",
    },
    "4143052000": {
        "address": "경기도 의왕시 부곡시장길 75",
        "source": "https://www.uiwang.go.kr/bugok/index",
    },
    "4136054000": {
        "address": "경기도 남양주시 홍유릉로 55",
        "source": "https://www.nyj.go.kr/www/contents.do?key=2780",
    },
}


def main() -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    args = parser.parse_args()
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error(f"missing {args.api_key_env}")
    client = KakaoApiClient(key)
    root = args.processed_dir.resolve()
    changed = set()
    for path in sorted(root.glob("*/demand_population.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        for field in ("coordinate_reference_url", "coordinate_basis"):
            if field not in fields:
                fields.append(field)
        for row in rows:
            code = row["administrative_code"]
            if code not in OVERRIDES:
                continue
            override = OVERRIDES[code]
            result = client.geocode_address(override["address"])
            municipality = row["administrative_name"].split()[1]
            if municipality not in result["matched_address"]:
                raise ValueError(f"{code}: geocoded address is outside {municipality}")
            row.update({
                "latitude": str(result["latitude"]),
                "longitude": str(result["longitude"]),
                "coordinate_source": "official_municipal_office_address_geocoded_by_kakao",
                "matched_address": result["matched_address"],
                "kakao_place_id": "",
                "geocoding_status": "verified_municipality_match_official_address",
                "coordinate_reference_url": override["source"],
                "coordinate_basis": "administrative-office representative; not incident coordinate",
            })
            changed.add(code)
        temporary = path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    missing = set(OVERRIDES) - changed
    if missing:
        raise ValueError(f"override administrative codes not found: {sorted(missing)}")
    print(f"official_address_overrides={len(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
