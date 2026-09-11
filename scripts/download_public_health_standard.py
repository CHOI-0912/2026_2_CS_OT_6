"""Download the official national public-health institution standard dataset.

The public data portal builds its CSV client-side from two unauthenticated JSON
endpoints.  This script preserves the raw response and writes a Gyeonggi-only
CSV plus a provenance manifest.  It does not read project credentials.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener


LANDING_URL = "https://www.data.go.kr/data/15107750/standard.do"
HEADER_URL = "https://www.data.go.kr/download/columList.json?pk=15107750&ext=CSV"
DATA_URL = "https://www.data.go.kr/download/standard.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(opener, url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": LANDING_URL})
    with opener.open(request, timeout=60) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    opener = build_opener()
    fetch(opener, LANDING_URL)
    header = json.loads(fetch(opener, HEADER_URL).decode("utf-8"))
    params: list[tuple[str, str | int]] = [
        ("publicDataPk", "15107750"),
        ("svcTableNm", header["tableVO"]["svcTableNm"]),
        ("totalCount", header["totalCount"]),
        ("perPage", 10000),
        ("page", 1),
    ]
    params.extend(("colNmList", name) for name in header["tableVO"]["colNmList"])
    raw_bytes = fetch(opener, f"{DATA_URL}?{urlencode(params)}")
    records = json.loads(raw_bytes.decode("utf-8"))
    if len(records) != int(header["totalCount"]):
        raise RuntimeError(f"row mismatch: expected {header['totalCount']}, got {len(records)}")

    raw_path = output_dir / "national_public_health_standard_raw.json"
    raw_path.write_bytes(raw_bytes)
    gyeonggi = [row for row in records if row.get("CTPV_NM") == "경기도"]
    columns = list(header["tableVO"]["colNmList"])
    extra_columns = sorted({key for row in records for key in row} - set(columns))
    columns.extend(extra_columns)
    csv_path = output_dir / "gyeonggi_public_health_standard.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(gyeonggi)

    municipalities: dict[str, int] = {}
    for row in gyeonggi:
        name = row.get("SGG_NM", "")
        municipalities[name] = municipalities.get(name, 0) + 1
    manifest = {
        "dataset_name": "전국보건기관표준데이터",
        "dataset_id": "15107750",
        "official_source": LANDING_URL,
        "retrieval_method": "unauthenticated public-data-portal standard JSON endpoints",
        "candidate_interpretation": "existing public-health facility; requires planning review",
        "national_rows": len(records),
        "gyeonggi_rows": len(gyeonggi),
        "gyeonggi_municipality_counts": dict(sorted(municipalities.items())),
        "source_reported_total_count": int(header["totalCount"]),
        "files": {
            raw_path.name: {"sha256": sha256(raw_path), "rows": len(records)},
            csv_path.name: {"sha256": sha256(csv_path), "rows": len(gyeonggi)},
        },
        "limitations": [
            "The portal's current standard aggregation contains only participating provider datasets, not all municipalities.",
            "Addresses identify existing facilities; they do not prove land ownership, construction feasibility, or emergency-care capability.",
        ],
    }
    manifest_path = output_dir / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
