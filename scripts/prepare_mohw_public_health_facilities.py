"""Filter the official MOHW national public-health facility file to Gyeonggi."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


SOURCE_URL = "https://www.data.go.kr/data/3072692/fileData.do"
ATTACHMENT_URL = (
    "https://www.data.go.kr/cmm/cmm/fileDownload.do?"
    "atchFileId=FILE_000000003692397&fileDetailSn=1&insertDataPrcus=N"
)
STATUS = "requires_planning_review_existing_public_health_facility"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_source(path: Path) -> tuple[str, list[dict[str, str]]]:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return encoding, list(csv.DictReader(payload.decode(encoding).splitlines()))
        except UnicodeDecodeError:
            continue
    raise ValueError("unsupported source encoding")


def municipality(row: dict[str, str]) -> str:
    raw = row["시군구"].strip()
    # One MOHW row has 시군구='경기도', but both its name and address identify
    # it as the Siheung Jungbu Health Living Support Center.
    if raw == "경기도" and "시흥시" in (row.get("주소", "") + row.get("상위기관명", "")):
        return "시흥시"
    return raw.split()[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    encoding, rows = read_source(source)
    gyeonggi = [row for row in rows if row.get("시도") == "경기도"]
    selected = []
    for row in gyeonggi:
        selected.append({
            **row,
            "정규화시군": municipality(row),
            "후보해석상태": STATUS,
        })
    output = output_dir / "gyeonggi_public_health_institutions_20251231.csv"
    fields = list(selected[0])
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(selected)

    counts = Counter(row["정규화시군"] for row in selected)
    types = Counter(row["기관유형"] for row in selected)
    manifest = {
        "dataset_name": "보건복지부_전국 지역보건의료기관 현황_20251231",
        "dataset_id": "3072692",
        "official_source": SOURCE_URL,
        "official_attachment": ATTACHMENT_URL,
        "source_encoding": encoding,
        "candidate_interpretation": "existing public-health facility; requires planning review",
        "national_rows": len(rows),
        "gyeonggi_rows": len(selected),
        "gyeonggi_municipalities": len(counts),
        "gyeonggi_municipality_counts": dict(sorted(counts.items())),
        "gyeonggi_type_counts": dict(sorted(types.items())),
        "source_quality_notes": [
            "All 31 Gyeonggi municipalities are represented after collapsing district-level names to their parent city.",
            "One source row labels 시군구 as 경기도; its parent/name and address identify it as 시흥시 and the normalized field records that correction.",
            "The source provides postal addresses but no coordinates; geocoding and municipality-boundary validation remain required.",
            "Existing public-health facilities are not automatically feasible hospital construction sites and do not imply emergency-care capability.",
        ],
        "files": {
            source.name: {"sha256": sha256(source), "rows": len(rows)},
            output.name: {"sha256": sha256(output), "rows": len(selected)},
        },
    }
    manifest_path = output_dir / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
