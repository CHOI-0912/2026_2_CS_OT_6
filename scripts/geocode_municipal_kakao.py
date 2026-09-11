"""Geocode staged municipal inputs with a Kakao Developers REST API key.

Generalized replacement for ``scripts/geocode_gyeonggi_kakao.py``, which hard-codes the
``경기도`` keyword prefix and Gyeonggi-only name corrections.  The coordinate_source
labels are identical so downstream consumers do not need to change.

Demand coordinates are optional because an administrative office is only a transparent
representative point, not an observed patient location or a population-weighted centroid.
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


def ambulance_attempts(row: dict[str, str], province: str, municipality: str) -> list[tuple[str, str]]:
    """Official address first, then progressively specific facility names."""
    attempts: list[tuple[str, str]] = []
    address = row.get("address", "").strip()
    if address:
        attempts.append(("address", address))
    base_name = row.get("base_name", "").strip()
    fire_station = row.get("fire_station", "").strip()
    if base_name.endswith("지역대") and "119지역대" not in base_name:
        regional_name = base_name.removesuffix("지역대") + "119지역대"
        attempts.append(("keyword", f"{province} {municipality} {regional_name}"))
    attempts.extend([
        ("keyword", f"{province} {municipality} {base_name}"),
        ("keyword", f"{province} {municipality} {fire_station} {base_name}"),
        ("keyword", f"{municipality} {base_name}"),
    ])
    return attempts


def demand_attempts(row: dict[str, str], province: str, municipality: str) -> list[tuple[str, str]]:
    """The 행정동 label carries the full official name, whose 시·도 prefix Kakao may not index."""
    full_name = row.get("administrative_name", "").strip()
    parts = full_name.split()
    dong = parts[-1] if parts else ""
    district = parts[-2] if len(parts) >= 3 else ""
    tail = " ".join(parts[1:]) if len(parts) > 1 else full_name
    return [
        ("keyword", f"{full_name} 행정복지센터"),
        ("keyword", f"{province} {tail} 행정복지센터"),
        ("keyword", f"{municipality} {district} {dong} 행정복지센터".replace("  ", " ")),
        ("keyword", f"{municipality} {dong} 행정복지센터"),
        ("keyword", f"{municipality} {district} {dong} 주민센터".replace("  ", " ")),
    ]


def candidate_attempts(row: dict[str, str], province: str, municipality: str) -> list[tuple[str, str]]:
    return [
        ("address", row.get("road_address", "").strip()),
        ("keyword", f"{province} {municipality} {row.get('candidate_name', '')}".strip()),
    ]


ATTEMPT_BUILDERS = {
    "ambulance": ambulance_attempts,
    "demand": demand_attempts,
    "candidate": candidate_attempts,
}
SOURCE_LABELS = {
    ("ambulance", "address"): "kakao_local_address_search",
    ("ambulance", "keyword"): "kakao_local_keyword_search",
    ("candidate", "address"): "kakao_local_address_search",
    ("candidate", "keyword"): "kakao_local_keyword_search",
    ("demand", "keyword"): "kakao_administrative_office_representative_proxy",
}


def geocode_rows(
    path: Path,
    client: KakaoApiClient,
    *,
    province: str,
    municipality: str,
    row_kind: str,
    requests_per_second: float,
) -> tuple[int, int]:
    fields, rows = read_rows(path)
    completed = failed = 0
    for row in rows:
        if row.get("latitude") and row.get("longitude"):
            continue
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        for method, query in ATTEMPT_BUILDERS[row_kind](row, province, municipality):
            if not query.strip() or (method, query) in seen:
                continue
            seen.add((method, query))
            try:
                result = (
                    client.geocode_address(query) if method == "address"
                    else client.search_keyword(query)
                )
            except KakaoApiError as exc:
                errors.append(str(exc))
            else:
                if not belongs_to_municipality(result, municipality):
                    errors.append(f"outside {municipality}: {result.get('matched_address')!r}")
                else:
                    row["longitude"] = str(result["longitude"])
                    row["latitude"] = str(result["latitude"])
                    row["coordinate_source"] = SOURCE_LABELS[(row_kind, method)]
                    row["matched_address"] = str(result.get("matched_address", ""))
                    row["kakao_place_id"] = str(result.get("place_id", ""))
                    row["geocoding_status"] = "verified_municipality_match"
                    completed += 1
                    break
            finally:
                time.sleep(1.0 / requests_per_second)
        else:
            row["geocoding_status"] = "failed: " + "; ".join(errors)
            failed += 1
        write_rows(path, fields, rows)
    return completed, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument(
        "--province-keyword", required=True,
        help="Kakao 검색어에 붙일 시·도명 (예: 전북). 공식 전체 명칭보다 짧은 통용 명칭이 잘 검색된다.",
    )
    parser.add_argument("--municipality-code", action="append", default=[])
    parser.add_argument("--api-key-env", default="KAKAO_REST_API_KEY")
    parser.add_argument("--include-demand-representatives", action="store_true")
    parser.add_argument("--include-candidates", action="store_true")
    parser.add_argument("--requests-per-second", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.requests_per_second <= 0:
        parser.error("--requests-per-second must be positive")
    key = os.environ.get(args.api_key_env, "")
    if not key:
        parser.error(
            "missing Kakao Developers REST API key; find it at "
            "Kakao Developers > 앱 > 해당 앱 > 앱 > 플랫폼 키 > REST API 키"
        )
    root = args.processed_dir.resolve()
    _, summary_rows = read_rows(root / "municipality_summary.csv")
    names = {row["municipality_code"]: row["municipality_name"] for row in summary_rows}
    selected = set(args.municipality_code) or set(names)
    unknown = selected - set(names)
    if unknown:
        parser.error(f"unknown municipality codes: {sorted(unknown)}")

    client = KakaoApiClient(key)
    completed = failed = 0
    for code in sorted(selected):
        folder = root / code
        kinds = ["ambulance"]
        if args.include_demand_representatives:
            kinds.append("demand")
        if args.include_candidates:
            kinds.append("candidate")
        for kind in kinds:
            filename = {
                "ambulance": "ambulance_bases.csv",
                "demand": "demand_population.csv",
                "candidate": "candidate_sites.csv",
            }[kind]
            done, errors = geocode_rows(
                folder / filename, client,
                province=args.province_keyword, municipality=names[code],
                row_kind=kind, requests_per_second=args.requests_per_second,
            )
            completed += done
            failed += errors
    print(f"geocoded={completed} failed={failed}")
    if not args.include_demand_representatives:
        print(
            "demand points were not geocoded; administrative-office proxies require "
            "explicit --include-demand-representatives"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
