from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed" / "gyeonggi_20260909"
RAW = ROOT / "data" / "raw" / "official_hospital_capacity_20260910"


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(ch for ch in text if ch.isalnum())


def municipality_token(address: str) -> str:
    parts = str(address or "").replace("경기 ", "경기도 ").split()
    if len(parts) >= 2 and parts[0] == "경기도":
        return re.sub(r"(시|군)$", "", parts[1])
    return ""


def similarity(a: object, b: object) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def name_similarity(a: object, b: object) -> float:
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    if min(len(left), len(right)) >= 4 and (left in right or right in left):
        return 1.0
    matcher = SequenceMatcher(None, left, right)
    overlap = matcher.find_longest_match().size / min(len(left), len(right))
    return max(matcher.ratio(), overlap)


def address_similarity(a: object, b: object) -> float:
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    if min(len(left), len(right)) >= 8 and (left in right or right in left):
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def load_existing(processed: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(processed.glob("*/existing_hospitals.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row = dict(row)
                row["source_file"] = str(path.relative_to(ROOT)).replace("\\", "/")
                rows.append(row)
    return rows


def find_hira_file(raw: Path, prefix: str) -> Path:
    matches = [
        path
        for path in raw.glob("hira_2026_06_extracted/**/*.xlsx")
        if path.name.startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one HIRA file starting with {prefix!r}, got {matches}")
    return matches[0]


def best_hira_match(existing: dict[str, str], hira: pd.DataFrame) -> tuple[pd.Series, float, str]:
    target_name = existing["hospital_name"]
    target_address = existing.get("road_address") or existing.get("lot_address") or ""
    token = municipality_token(target_address)
    candidates = hira
    if token:
        local = hira[hira["주소"].fillna("").str.contains(token, regex=False)]
        if not local.empty:
            candidates = local

    target_normalized = normalize(target_name)
    exact = candidates[candidates["_normalized_name"] == target_normalized]
    if not exact.empty:
        row = exact.iloc[0]
        return row, 1.0, "exact_normalized_name"

    scored: list[tuple[float, int]] = []
    for idx, row in candidates.iterrows():
        name_score = name_similarity(target_name, row["요양기관명"])
        address_score = address_similarity(target_address, row["주소"])
        score = 0.55 * name_score + 0.45 * address_score
        scored.append((score, idx))
    score, idx = max(scored)
    return hira.loc[idx], score, "fuzzy_name_address"


def greedy_one_to_one(existing: list[dict[str, str]], official: list[dict[str, object]]) -> dict[int, tuple[int, float]]:
    pairs: list[tuple[float, int, int]] = []
    for i, left in enumerate(existing):
        for j, right in enumerate(official):
            pairs.append((name_similarity(left["hospital_name"], right["INSTT_NM"]), i, j))
    pairs.sort(reverse=True)
    result: dict[int, tuple[int, float]] = {}
    used: set[int] = set()
    for score, i, j in pairs:
        if i not in result and j not in used:
            result[i] = (j, score)
            used.add(j)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED)
    parser.add_argument("--raw-dir", type=Path, default=RAW)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    processed, raw = args.processed_dir.resolve(), args.raw_dir.resolve()
    existing = load_existing(processed)
    if len(existing) != 73:
        raise RuntimeError(f"Expected 73 staged emergency hospitals, got {len(existing)}")

    facility_path = find_hira_file(raw, "3.")
    facility = pd.read_excel(facility_path)
    facility = facility[facility["시도코드명"].eq("경기")].copy()
    facility["_normalized_name"] = facility["요양기관명"].map(normalize)

    bed_columns = [
        "일반입원실상급병상수",
        "일반입원실일반병상수",
        "성인중환자병상수",
        "소아중환자병상수",
        "신생아중환자병상수",
        "분만실병상수",
        "수술실병상수",
        "응급실병상수",
        "정신과폐쇄상급병상수",
        "정신과폐쇄일반병상수",
        "정신과개방상급병상수",
        "정신과개방일반병상수",
        "격리병실병상수",
        "무균치료실병상수",
    ]
    output: list[dict[str, object]] = []
    for hospital in existing:
        match, score, method = best_hira_match(hospital, facility)
        record: dict[str, object] = {
            "municipality_code": hospital["municipality_code"],
            "staged_hospital_name": hospital["hospital_name"],
            "staged_road_address": hospital["road_address"],
            "hira_hospital_name": match["요양기관명"],
            "hira_address": match["주소"],
            "encrypted_care_symbol": match["암호화요양기호"],
            "institution_type": match["종별코드명"],
            "snapshot_period": "2026-06",
            "capacity_semantics": "HIRA static facility snapshot; not real-time availability",
            "match_method": method,
            "match_score": round(float(score), 6),
            "match_review_required": score < 0.78,
        }
        for column in bed_columns:
            value = 0 if pd.isna(match[column]) else int(match[column])
            record[column] = value
        record["critical_care_beds_total"] = sum(
            int(record[column])
            for column in ["성인중환자병상수", "소아중환자병상수", "신생아중환자병상수"]
        )
        record["inpatient_beds_total_from_reported_fields"] = sum(
            int(record[column])
            for column in [
                "일반입원실상급병상수",
                "일반입원실일반병상수",
                "성인중환자병상수",
                "소아중환자병상수",
                "신생아중환자병상수",
                "정신과폐쇄상급병상수",
                "정신과폐쇄일반병상수",
                "정신과개방상급병상수",
                "정신과개방일반병상수",
                "격리병실병상수",
                "무균치료실병상수",
            ]
        )
        output.append(record)

    matched_ids = {str(record["encrypted_care_symbol"]) for record in output}
    special = pd.read_excel(find_hira_file(raw, "10."))
    special = special[special["암호화요양기호"].astype(str).isin(matched_ids)]
    services_by_id = (
        special.groupby(special["암호화요양기호"].astype(str))["검색코드명"]
        .apply(lambda values: sorted(set(str(value) for value in values if pd.notna(value))))
        .to_dict()
    )
    equipment = pd.read_excel(find_hira_file(raw, "7."))
    equipment = equipment[equipment["암호화요양기호"].astype(str).isin(matched_ids)]
    equipment_by_id: dict[str, dict[str, int]] = {}
    for care_symbol, group in equipment.groupby(equipment["암호화요양기호"].astype(str)):
        equipment_by_id[care_symbol] = {
            str(row["장비코드명"]): int(row["장비대수"])
            for _, row in group.iterrows()
            if pd.notna(row["장비코드명"]) and pd.notna(row["장비대수"])
        }

    for record in output:
        care_symbol = str(record["encrypted_care_symbol"])
        services = services_by_id.get(care_symbol, [])
        equipment_counts = equipment_by_id.get(care_symbol, {})
        record["registered_special_services"] = " | ".join(services)
        record["registered_emergency_institution"] = "응급의료기관" in services
        record["registered_adult_icu"] = "성인 중환자실" in services
        record["registered_pediatric_icu"] = "소아 중환자실" in services
        record["registered_neonatal_icu"] = "신생아 중환자실" in services
        record["registered_tavi"] = "경피적 대동맥판삽입 실시기관" in services
        record["registered_ventricular_assist_device"] = any(
            "심실 보조장치 치료술" in service for service in services
        )
        record["ct_units"] = equipment_counts.get("CT", 0)
        record["mri_units"] = equipment_counts.get("MRI", 0)
        record["pet_units"] = equipment_counts.get("양전자단층촬영기 (PET)", 0)

    capacity_out = raw / "hospital_capacity_hira_matched_73.csv"
    pd.DataFrame(output).to_csv(capacity_out, index=False, encoding="utf-8-sig")

    evaluation_path = raw / "egen_2024_evaluation_gyeonggi.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    if len(evaluation) != 73:
        raise RuntimeError(f"Expected 73 E-Gen evaluation rows, got {len(evaluation)}")
    pd.DataFrame(evaluation).to_csv(
        raw / "egen_2024_evaluation_gyeonggi.csv", index=False, encoding="utf-8-sig"
    )

    assignments = greedy_one_to_one(existing, evaluation)
    eval_output: list[dict[str, object]] = []
    for i, hospital in enumerate(existing):
        j, score = assignments[i]
        row = evaluation[j]
        eval_output.append(
            {
                "municipality_code": hospital["municipality_code"],
                "staged_hospital_name": hospital["hospital_name"],
                "egen_institution_name": row["INSTT_NM"],
                "egen_institution_code": row["INSTT_CD"],
                "emergency_institution_type": row["ASORT_SE_CD"],
                "overall_grade": row["ALL_TOT"],
                "mandatory_area_result": row["PASS_FAIL"],
                "emergency_physician_grade": row["IDX_507"],
                "emergency_specialist_grade": row["IDX_513"],
                "emergency_nurse_grade": row["IDX_519"],
                "physician_specialty_grade": row["IDX_525"],
                "nurse_specialty_grade": row["IDX_530"],
                "bed_saturation_grade": row["IDX_709"],
                "severe_patient_length_of_stay_grade": row["IDX_715"],
                "long_stay_patient_index_grade": row["IDX_719"],
                "timely_specialist_direct_care_grade": row["IDX_494"],
                "definitive_treatment_provision_grade": row["IDX_739"],
                "regional_119_transfer_acceptance_share_grade": row["IDX_743"],
                "evaluation_year": 2024,
                "match_score": round(float(score), 6),
                "match_review_required": score < 0.72,
            }
        )
    pd.DataFrame(eval_output).to_csv(
        raw / "hospital_evaluation_egen_matched_73.csv", index=False, encoding="utf-8-sig"
    )

    summary = {
        "staged_hospitals": len(existing),
        "hira_matches": len(output),
        "distinct_hira_facilities": len({row["encrypted_care_symbol"] for row in output}),
        "hira_manual_review_required": sum(bool(row["match_review_required"]) for row in output),
        "egen_evaluation_rows": len(evaluation),
        "egen_matches": len(eval_output),
        "egen_manual_review_required": sum(
            bool(row["match_review_required"]) for row in eval_output
        ),
    }
    (raw / "match_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
