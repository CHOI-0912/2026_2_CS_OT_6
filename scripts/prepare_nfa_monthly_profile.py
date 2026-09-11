"""Derive a province-level monthly seasonality profile from official NFA totals.

This output is deliberately not municipal demand. Each year's twelve months are
normalized first, so long-term volume changes do not masquerade as seasonality.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean


SOURCE_FIELDS = {
    "year": "년도",
    "month": "월",
    "headquarters": "본부구분",
    "dispatches": "출동건수",
    "transports": "이송건수",
    "patients": "이송환자수",
}
METRICS = ("dispatches", "transports", "patients")


def read_headquarters_rows(path: Path, headquarters: str) -> list[dict[str, int]]:
    with path.open("r", encoding="cp949", newline="") as handle:
        source = list(csv.DictReader(handle))
    selected = []
    for row in source:
        if row[SOURCE_FIELDS["headquarters"]].strip() != headquarters:
            continue
        selected.append({
            "year": int(row[SOURCE_FIELDS["year"]]),
            "month": int(row[SOURCE_FIELDS["month"]]),
            **{metric: int(row[SOURCE_FIELDS[metric]].replace(",", "")) for metric in METRICS},
        })
    return selected


def monthly_profile(records: list[dict[str, int]]) -> list[dict[str, float | int]]:
    by_year: dict[int, list[dict[str, int]]] = defaultdict(list)
    for row in records:
        by_year[row["year"]].append(row)
    if not by_year or any({row["month"] for row in rows} != set(range(1, 13)) for rows in by_year.values()):
        raise ValueError("every selected year must contain exactly months 1 through 12")

    normalized: dict[tuple[int, int, str], float] = {}
    for year, rows in by_year.items():
        for metric in METRICS:
            annual_month_mean = fmean(row[metric] for row in rows)
            if annual_month_mean <= 0:
                raise ValueError(f"{year}: {metric} must have a positive annual total")
            for row in rows:
                normalized[(year, row["month"], metric)] = row[metric] / annual_month_mean

    output = []
    for month in range(1, 13):
        month_rows = [row for row in records if row["month"] == month]
        output.append({
            "month": month,
            "years": len(by_year),
            **{f"{metric}_total": sum(row[metric] for row in month_rows) for metric in METRICS},
            **{
                f"{metric}_seasonality_multiplier": fmean(
                    normalized[(year, month, metric)] for year in by_year
                )
                for metric in METRICS
            },
        })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--headquarters", default="경기")
    args = parser.parse_args()
    records = read_headquarters_rows(args.source, args.headquarters)
    profile = monthly_profile(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(profile[0]))
        writer.writeheader()
        writer.writerows(profile)
    source_bytes = args.source.read_bytes()
    manifest = {
        "source_file": str(args.source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "headquarters": args.headquarters,
        "years": sorted({row["year"] for row in records}),
        "method": "within-year monthly count divided by that year's monthly mean, then averaged across years",
        "scope_warning": "province-level temporal seasonality only; not municipal spatial demand",
        "output_file": str(args.output),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
