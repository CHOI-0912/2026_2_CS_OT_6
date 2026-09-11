"""Attach official HIRA capacity and E-Gen evaluation fields to staged hospitals."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

CAPACITY_SOURCE = "https://opendata.hira.or.kr/op/opc/selectOpenData.do?sno=11925"
EVALUATION_SOURCE = "https://www.e-gen.or.kr/nemc/business_medical_institution_evaluation.do?tabId=9"
ADDED_FIELDS = [
    "hira_hospital_name", "hira_encrypted_care_symbol", "hira_institution_type",
    "capacity_snapshot_period", "emergency_room_beds", "critical_care_beds",
    "inpatient_beds", "registered_emergency_institution", "registered_adult_icu",
    "ct_units", "mri_units", "capacity_match_method", "capacity_match_score",
    "capacity_match_review_required", "capacity_source", "capacity_semantics",
    "egen_institution_code", "egen_evaluation_year", "egen_overall_grade",
    "egen_mandatory_area_result", "egen_match_review_required", "egen_evaluation_source",
]


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


def key(row: dict[str, str], name_field: str) -> tuple[str, str]:
    return row["municipality_code"].strip(), row[name_field].strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    args = parser.parse_args()
    _, capacity_rows = read_rows(args.capacity.resolve())
    _, evaluation_rows = read_rows(args.evaluation.resolve())
    capacity = {key(row, "staged_hospital_name"): row for row in capacity_rows}
    evaluation = {key(row, "staged_hospital_name"): row for row in evaluation_rows}
    attached = reviewed = 0
    root = args.processed_dir.resolve()
    for folder in sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdigit()):
        path = folder / "existing_hospitals.csv"
        fields, hospitals = read_rows(path)
        for hospital in hospitals:
            lookup = (folder.name, hospital["hospital_name"].strip())
            cap = capacity.get(lookup)
            eva = evaluation.get(lookup)
            if cap is None:
                raise ValueError(f"missing HIRA match for {lookup}")
            hospital.update({
                "hira_hospital_name": cap["hira_hospital_name"],
                "hira_encrypted_care_symbol": cap["encrypted_care_symbol"],
                "hira_institution_type": cap["institution_type"],
                "capacity_snapshot_period": cap["snapshot_period"],
                "emergency_room_beds": cap["응급실병상수"],
                "critical_care_beds": cap["critical_care_beds_total"],
                "inpatient_beds": cap["inpatient_beds_total_from_reported_fields"],
                "registered_emergency_institution": cap["registered_emergency_institution"],
                "registered_adult_icu": cap["registered_adult_icu"],
                "ct_units": cap["ct_units"], "mri_units": cap["mri_units"],
                "capacity_match_method": cap["match_method"],
                "capacity_match_score": cap["match_score"],
                "capacity_match_review_required": cap["match_review_required"],
                "capacity_source": CAPACITY_SOURCE,
                "capacity_semantics": cap["capacity_semantics"],
            })
            if eva is not None:
                hospital.update({
                    "egen_institution_code": eva["egen_institution_code"],
                    "egen_evaluation_year": eva["evaluation_year"],
                    "egen_overall_grade": eva["overall_grade"],
                    "egen_mandatory_area_result": eva["mandatory_area_result"],
                    "egen_match_review_required": eva["match_review_required"],
                    "egen_evaluation_source": EVALUATION_SOURCE,
                })
            attached += 1
            reviewed += cap["match_review_required"].strip().lower() == "true"
        write_rows(path, fields + [field for field in ADDED_FIELDS if field not in fields], hospitals)
    print(f"attached={attached} capacity_review_required={reviewed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
