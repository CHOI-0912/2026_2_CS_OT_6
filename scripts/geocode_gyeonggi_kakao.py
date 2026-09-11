"""Geocode official addresses with a Kakao Developers REST API key.

Demand coordinates are optional because an administrative office is only a
transparent representative point, not an observed patient location or a
population-weighted centroid.
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ambulance_sim.env_file import load_env_file
from ambulance_sim.kakao_api import KakaoApiClient, KakaoApiError


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def belongs_to_municipality(result: dict, municipality_name: str) -> bool:
    return municipality_name in str(result.get("matched_address", ""))


def geocode_ambulance(row: dict[str, str], client: KakaoApiClient, municipality_name: str) -> tuple[dict, str]:
    """Try the official address first, then progressively specific facility names."""
    attempts: list[tuple[str, str]] = []
    address = row.get("address", "").strip().replace("겅기도", "경기도")
    if address:
        attempts.append(("address", address))

    base_name = row.get("base_name", "").strip()
    fire_station = row.get("fire_station", "").strip()
    if "119구조대119안전센터" in base_name:
        attempts.append(("keyword", f"경기도 {municipality_name} {fire_station} 119구조대"))
    elif "수난구조대119안전센터" in base_name:
        attempts.append(("keyword", f"경기도 {municipality_name} {fire_station} 수난구조대"))
    if base_name.endswith("지역대") and "119지역대" not in base_name:
        regional_name = base_name.removesuffix("지역대") + "119지역대"
        attempts.append(("keyword", f"경기도 {municipality_name} {regional_name}"))
        attempts.append(("keyword", f"경기도 {municipality_name} {base_name.removesuffix('지역대')}면 119지역대"))
        attempts.append(("keyword", f"경기도 {municipality_name} {base_name.removesuffix('지역대')}면 소방서"))
    # 가평119안전센터 was officially renamed 대곡119안전센터 in 2025.
    if fire_station == "가평소방서" and base_name == "대곡119안전센터":
        attempts.append(("keyword", "경기도 가평군 가평119안전센터"))
    attempts.extend([
        ("keyword", f"경기도 {municipality_name} {base_name}"),
        ("keyword", f"경기도 {municipality_name} {fire_station} {base_name}"),
    ])

    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for method, query in attempts:
        if not query or (method, query) in seen:
            continue
        seen.add((method, query))
        try:
            result = client.geocode_address(query) if method == "address" else client.search_keyword(query)
            if not belongs_to_municipality(result, municipality_name):
                errors.append(f"outside {municipality_name}: {result.get('matched_address')!r}")
                continue
            source = "kakao_local_address_search" if method == "address" else "kakao_local_keyword_search"
            return result, source
        except KakaoApiError as exc:
            errors.append(str(exc))
    raise KakaoApiError("; ".join(errors))


def geocode_candidate(row: dict[str, str], client: KakaoApiClient, municipality_name: str) -> tuple[dict, str]:
    attempts = [
        ("address", row.get("road_address", "").strip()),
        ("keyword", f"경기도 {municipality_name} {row.get('candidate_name', '')}"),
    ]
    errors: list[str] = []
    for method, query in attempts:
        if not query:
            continue
        try:
            result = client.geocode_address(query) if method == "address" else client.search_keyword(query)
            if not belongs_to_municipality(result, municipality_name):
                errors.append(f"outside {municipality_name}: {result.get('matched_address')!r}")
                continue
            source = "kakao_local_address_search" if method == "address" else "kakao_local_keyword_search"
            return result, source
        except KakaoApiError as exc:
            errors.append(str(exc))
    raise KakaoApiError("; ".join(errors))


def geocode_rows(
    path: Path,
    client: KakaoApiClient,
    municipality_name: str,
    *,
    row_kind: str,
    requests_per_second: float,
) -> tuple[int, int]:
    fields, rows = read_rows(path)
    completed = failed = 0
    for row in rows:
        if row.get("latitude") and row.get("longitude"):
            continue
        try:
            if row_kind == "ambulance":
                result, source = geocode_ambulance(row, client, municipality_name)
            elif row_kind == "candidate":
                result, source = geocode_candidate(row, client, municipality_name)
            elif row_kind == "demand":
                result = client.search_keyword(f"{row['administrative_name']} 행정복지센터")
                source = "kakao_administrative_office_representative_proxy"
            else:
                raise ValueError(f"unsupported row kind: {row_kind}")
            if not belongs_to_municipality(result, municipality_name):
                raise KakaoApiError(f"result is outside {municipality_name}: {result.get('matched_address')!r}")
            row["longitude"] = str(result["longitude"])
            row["latitude"] = str(result["latitude"])
            row["coordinate_source"] = source
            row["matched_address"] = str(result.get("matched_address", ""))
            row["kakao_place_id"] = str(result.get("place_id", ""))
            row["geocoding_status"] = "verified_municipality_match"
            completed += 1
        except (KakaoApiError, ValueError) as exc:
            row["geocoding_status"] = f"failed: {exc}"
            failed += 1
        write_rows(path, fields, rows)
        time.sleep(1.0 / requests_per_second)
    return completed, failed


def main() -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--municipality-code", action="append", default=[])
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    parser.add_argument("--include-demand-representatives", action="store_true")
    parser.add_argument("--include-candidates", action="store_true")
    parser.add_argument("--requests-per-second", type=float, default=5.0)
    args = parser.parse_args()
    if args.requests_per_second <= 0:
        parser.error("--requests-per-second must be positive")
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error(
            "missing Kakao Developers REST API key; find it at "
            "Kakao Developers > 앱 > 해당 앱 > 앱 > 플랫폼 키 > REST API 키"
        )
    root = args.processed_dir.resolve()
    summary_fields, summary_rows = read_rows(root / "municipality_summary.csv")
    names = {row["municipality_code"]: row["municipality_name"] for row in summary_rows}
    selected = set(args.municipality_code) or set(names)
    unknown = selected - set(names)
    if unknown:
        parser.error(f"unknown municipality codes: {sorted(unknown)}")
    client = KakaoApiClient(key)
    completed = failed = 0
    for code in sorted(selected):
        folder = root / code
        done, errors = geocode_rows(
            folder / "ambulance_bases.csv", client, names[code],
            row_kind="ambulance", requests_per_second=args.requests_per_second,
        )
        completed += done
        failed += errors
        if args.include_demand_representatives:
            done, errors = geocode_rows(
                folder / "demand_population.csv", client, names[code],
                row_kind="demand", requests_per_second=args.requests_per_second,
            )
            completed += done
            failed += errors
        if args.include_candidates:
            done, errors = geocode_rows(
                folder / "candidate_sites.csv", client, names[code],
                row_kind="candidate", requests_per_second=args.requests_per_second,
            )
            completed += done
            failed += errors
    print(f"geocoded={completed} failed={failed}")
    if not args.include_demand_representatives:
        print("demand points were not geocoded; administrative-office proxies require explicit --include-demand-representatives")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
