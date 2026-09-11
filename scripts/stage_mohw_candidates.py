"""Stage current MOHW public-health facilities as planning-review candidates."""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
import re
import unicodedata

SOURCE_URL = "https://www.data.go.kr/data/3072692/fileData.do"
STATUS = "requires_planning_review_existing_public_health_facility"
FIELDS = [
    "candidate_id", "candidate_name", "municipality_code", "road_address",
    "latitude", "longitude", "candidate_source", "land_feasibility_status",
    "coordinate_source", "matched_address", "kakao_place_id", "geocoding_status", "routing_status",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, records: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)


def normalized_address(address: str) -> str:
    """Road address up to the first '(' or ',', without spaces or the 경기(도) prefix."""
    text = unicodedata.normalize("NFKC", address or "")
    text = re.split(r"[(,]", text, maxsplit=1)[0]
    text = "".join(text.split()).lower()
    return re.sub(r"^경기도?", "", text)


def merge_candidates(existing: list[dict[str, str]], staged: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Replace MOHW- rows with `staged`; keep other rows unless their address duplicates one."""
    seen = {normalized_address(row["road_address"]) for row in staged}
    kept: list[dict[str, str]] = []
    dropped = 0
    for row in existing:
        if row["candidate_id"].startswith("MOHW-"):
            continue
        key = normalized_address(row.get("road_address", ""))
        if key and key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return staged + kept, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--processed-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.processed_dir.resolve()
    summary = read_rows(root / "municipality_summary.csv")
    codes = {row["municipality_name"]: row["municipality_code"] for row in summary}
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_rows(args.source.resolve()):
        municipality = row["정규화시군"].strip()
        if municipality not in codes:
            raise ValueError(f"unknown Gyeonggi municipality: {municipality!r}")
        grouped[codes[municipality]].append(row)

    total = 0
    kept_total = 0
    dropped_total = 0
    for code in sorted(codes.values()):
        # .get keeps municipalities without MOHW rows out of the defaultdict, so the
        # summary's municipalities= count only covers those that received rows.
        source_rows = sorted(grouped.get(code, []), key=lambda row: (row["보건기관명"], row["주소"]))
        staged = []
        for index, row in enumerate(source_rows, start=1):
            staged.append({
                "candidate_id": f"MOHW-20251231-{code}-{index:03d}",
                "candidate_name": row["보건기관명"],
                "municipality_code": code,
                "road_address": row["주소"],
                "latitude": "", "longitude": "",
                "candidate_source": SOURCE_URL,
                "land_feasibility_status": STATUS,
                "coordinate_source": "", "matched_address": "", "kakao_place_id": "",
                "geocoding_status": "pending_kakao_api",
                "routing_status": "pending_kakao_api",
            })
        target = root / code / "candidate_sites.csv"
        existing = read_rows(target) if target.exists() else []
        merged, dropped = merge_candidates(existing, staged)
        kept = len(merged) - len(staged)
        if kept or dropped:
            print(f"{code}: kept {kept} non-MOHW rows, dropped {dropped} duplicating MOHW addresses")
        write_rows(target, merged)
        total += len(staged)
        kept_total += kept
        dropped_total += dropped
    print(f"staged={total} municipalities={len(grouped)} kept_non_mohw={kept_total} dropped_non_mohw={dropped_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
