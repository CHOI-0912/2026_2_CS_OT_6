"""Normalize nationwide official sources into one municipality staging folder.

Generalized replacement for ``scripts/prepare_gyeonggi_inputs.py``.  That script is
hard-wired to the 31 Gyeonggi municipalities and to a province-specific emergency
medical institution CSV that only Gyeonggi publishes.  This script instead takes the
province and municipality on the command line and builds the emergency hospital list
from two nationwide official sources: the E-Gen annual evaluation PDF (institution
list, category, grade) joined to the HIRA facility snapshot (address, coordinates,
beds, equipment).

Like the Gyeonggi script it deliberately stops before route calculation, demand-rate
calibration, and candidate-site selection.  Missing inputs remain explicit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

CODE_RE = re.compile(r"\((\d{10})\)\s*$")
CAPACITY_SOURCE = "https://opendata.hira.or.kr/op/opc/selectOpenData.do?sno=11925"
EVALUATION_SOURCE = "https://www.e-gen.or.kr/nemc/business_medical_institution_evaluation.do?tabId=9"
CAPACITY_SEMANTICS = "HIRA static facility snapshot; not real-time availability"

# Official names for the same province differ between publishers and between
# reference dates.  Every alias below is asserted against the file that must contain
# it, so a silent rename turns into a loud failure instead of an empty output.
PROVINCE_ALIASES: dict[str, dict[str, object]] = {
    "전북특별자치도": {
        # 행정안전부 주민등록 인구 (2026-08 기준) 는 개편 후 명칭을 쓴다.
        "population_prefixes": ["전북특별자치도"],
        # 소방청 구급차 정보 (2025-06-30) 의 `시도` 는 축약 명칭이다.
        "fleet_provinces": ["전북"],
        # 소방청 119안전센터 현황 (2023-12-31) 은 개편 전 명칭이 남아 있다.
        "center_provinces": ["전라북도"],
        # 주소 문자열에는 세 가지가 모두 나타난다.
        "address_prefixes": ["전북특별자치도", "전라북도", "전북"],
        "hira_province": "전북",
        "egen_area": "전북",
    },
    "경기도": {
        "population_prefixes": ["경기도"],
        "fleet_provinces": ["경기", "경기북부"],
        "center_provinces": ["경기도"],
        "address_prefixes": ["경기도", "경기"],
        "hira_province": "경기",
        "egen_area": "경기",
    },
}

HOSPITAL_FIELDS = [
    "hospital_name", "emergency_category", "phone", "road_address", "lot_address",
    "postal_code", "latitude", "longitude", "coordinate_source", "matched_address",
    "municipality_code", "routing_status",
    "hira_hospital_name", "hira_encrypted_care_symbol", "hira_institution_type",
    "capacity_snapshot_period", "emergency_room_beds", "critical_care_beds",
    "inpatient_beds", "registered_emergency_institution", "registered_adult_icu",
    "ct_units", "mri_units", "capacity_match_method", "capacity_match_score",
    "capacity_match_review_required", "capacity_source", "capacity_semantics",
    "egen_institution_code", "egen_evaluation_year", "egen_overall_grade",
    "egen_mandatory_area_result", "egen_match_review_required", "egen_evaluation_source",
]
DEMAND_FIELDS = [
    "administrative_code", "administrative_name", "municipality_code", "population",
    "households", "latitude", "longitude", "coordinate_source", "matched_address",
    "kakao_place_id", "geocoding_status", "demand_rate_status",
]
AMBULANCE_FIELDS = [
    "source_province", "fire_station", "safety_center", "regional_unit", "ambulance_count",
    "base_name", "address", "latitude", "longitude", "coordinate_source", "matched_address",
    "kakao_place_id", "municipality_assignment", "geocoding_status", "routing_status",
]
CANDIDATE_FIELDS = [
    "candidate_id", "candidate_name", "municipality_code", "road_address", "latitude",
    "longitude", "candidate_source", "land_feasibility_status", "coordinate_source",
    "matched_address", "kakao_place_id", "geocoding_status", "routing_status",
]
BED_COLUMNS = [
    "일반입원실상급병상수", "일반입원실일반병상수", "성인중환자병상수", "소아중환자병상수",
    "신생아중환자병상수", "응급실병상수", "정신과폐쇄상급병상수", "정신과폐쇄일반병상수",
    "정신과개방상급병상수", "정신과개방일반병상수", "격리병실병상수", "무균치료실병상수",
]
CRITICAL_CARE_COLUMNS = ["성인중환자병상수", "소아중환자병상수", "신생아중환자병상수"]
INPATIENT_COLUMNS = [column for column in BED_COLUMNS if column != "응급실병상수"]


# --------------------------------------------------------------------------- io


def read_csv(path: Path, encoding: str = "cp949") -> list[dict[str, str]]:
    with path.open("r", encoding=encoding, newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_and_verify_manifest(raw_dir: Path) -> dict:
    manifest = json.loads((raw_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for dataset in manifest["datasets"]:
        if "path" not in dataset:
            continue
        path = raw_dir / dataset["path"]
        if (
            not path.is_file()
            or path.stat().st_size != dataset["size_bytes"]
            or sha256(path) != dataset["sha256"]
        ):
            raise ValueError(f"raw source failed manifest verification: {path}")
    return manifest


# ----------------------------------------------------------------- name helpers


def aliases_for(province: str) -> dict[str, object]:
    if province not in PROVINCE_ALIASES:
        raise ValueError(
            f"unknown province {province!r}; add its official aliases to PROVINCE_ALIASES "
            f"(known: {sorted(PROVINCE_ALIASES)})"
        )
    return PROVINCE_ALIASES[province]


def assert_alias_present(label: str, expected: list[str], observed: set[str]) -> None:
    missing = [name for name in expected if name not in observed]
    if missing:
        raise ValueError(
            f"{label}: official source no longer contains {missing}; observed values are "
            f"{sorted(observed)}. Update PROVINCE_ALIASES instead of guessing."
        )


def parse_population_label(label: str) -> tuple[str, str, list[str]]:
    match = CODE_RE.search(label)
    if not match:
        raise ValueError(f"population row has no 10-digit administrative code: {label!r}")
    code = match.group(1)
    name = CODE_RE.sub("", label).strip()
    return code, name, name.split()


def municipality_from_address(address: str, address_prefixes: list[str]) -> str | None:
    """Return the 시/군 name of an official address, or None when the province differs."""
    stripped = address.strip()
    for prefix in address_prefixes:
        if not stripped.startswith(prefix):
            continue
        match = re.match(rf"^{re.escape(prefix)}\s+(\S+?(?:시|군))(?:\s|$)", stripped)
        if match:
            return match.group(1)
    return None


def municipality_stem(municipality: str) -> str:
    return re.sub(r"(시|군)$", "", municipality)


def integer(value: str) -> int:
    return int(str(value).replace(",", "").strip() or 0)


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return "".join(character for character in text if character.isalnum())


def name_similarity(left: object, right: object) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if min(len(a), len(b)) >= 4 and (a in b or b in a):
        return 1.0
    matcher = SequenceMatcher(None, a, b)
    overlap = matcher.find_longest_match().size / min(len(a), len(b))
    return max(matcher.ratio(), overlap)


# ------------------------------------------------------------------- extractors


def build_demand_rows(
    population: list[dict[str, str]],
    *,
    province: str,
    municipality: str,
    municipality_code: str,
    population_prefixes: list[str],
    population_column: str,
    household_column: str,
) -> tuple[list[dict[str, object]], int]:
    observed = {row["행정구역"].lstrip().split()[0] for row in population}
    assert_alias_present("행정안전부 주민등록 인구 시도명", population_prefixes, observed)

    rows: list[dict[str, object]] = []
    municipal_total: int | None = None
    for row in population:
        label = row["행정구역"].lstrip()
        if label.split()[0] not in population_prefixes:
            continue
        code, full_name, parts = parse_population_label(label)
        if len(parts) < 2 or parts[1] != municipality:
            continue
        if len(parts) == 2:
            municipal_total = integer(row[population_column])
            continue
        if code.endswith("00000"):  # 자치구 소계 행
            continue
        rows.append({
            "administrative_code": code,
            "administrative_name": full_name,
            "municipality_code": municipality_code,
            "population": integer(row[population_column]),
            "households": integer(row[household_column]),
            "latitude": "",
            "longitude": "",
            "coordinate_source": "",
            "matched_address": "",
            "kakao_place_id": "",
            "geocoding_status": "pending_kakao_api",
            "demand_rate_status": "pending_119_activity_api",
        })
    if not rows:
        raise ValueError(
            f"no 행정동 rows for {province} {municipality}; check the official name spelling"
        )
    if municipal_total is None:
        raise ValueError(f"no municipal total row for {province} {municipality}")
    return rows, municipal_total


def build_ambulance_rows(
    fleet: list[dict[str, str]],
    centers: list[dict[str, str]],
    *,
    municipality: str,
    fleet_provinces: list[str],
    center_provinces: list[str],
    address_prefixes: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    assert_alias_present(
        "소방청 구급차 정보 시도", fleet_provinces, {row["시도"] for row in fleet}
    )
    assert_alias_present(
        "소방청 119안전센터 현황 시도본부", center_provinces, {row["시도본부"] for row in centers}
    )
    province_fleet = [row for row in fleet if row["시도"] in fleet_provinces]
    province_centers = [row for row in centers if row["시도본부"] in center_provinces]

    center_lookup = {
        (row["소방서"].strip(), row["119안전센터명"].strip()): row for row in province_centers
    }
    station_municipalities: dict[str, set[str]] = {}
    for center in province_centers:
        name = municipality_from_address(center["주소"], address_prefixes)
        if name:
            station_municipalities.setdefault(center["소방서"].strip(), set()).add(name)

    stem = municipality_stem(municipality)
    assigned: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for row in province_fleet:
        station = row["소방서"].strip()
        center_name = row["안전센터"].strip()
        regional_unit = row["지역대"].strip()
        matched_center = center_lookup.get((station, center_name))
        address = matched_center["주소"].strip() if matched_center else ""
        from_address = municipality_from_address(address, address_prefixes) if address else None
        name = from_address
        assignment = "address_exact" if from_address else "unresolved"
        if name is None:
            inferred = station_municipalities.get(station, set())
            if len(inferred) == 1:
                name = next(iter(inferred))
                assignment = "fire_station_address_inferred"
        if name is None and stem in station:
            name = municipality
            assignment = "official_fire_station_name_inferred"
        record = {
            "source_province": row["시도"],
            "fire_station": station,
            "safety_center": center_name,
            "regional_unit": regional_unit,
            "ambulance_count": integer(row["수량"]),
            "base_name": regional_unit or center_name or station,
            "address": "" if regional_unit else address,
            "latitude": "",
            "longitude": "",
            "coordinate_source": "",
            "matched_address": "",
            "kakao_place_id": "",
            "municipality_assignment": assignment,
            "geocoding_status": "pending_kakao_api",
            "routing_status": "pending_kakao_api",
        }
        if name == municipality:
            assigned.append(record)
        elif name is None and stem in station:
            unresolved.append({**record, "reason": "fire station name matches the target municipality but no official address resolved"})
    if not assigned:
        raise ValueError(
            f"no ambulance rows for {municipality}; fire-station names in the official file are "
            f"{sorted({row['소방서'] for row in province_fleet})}"
        )
    if unresolved:
        raise ValueError(
            "unresolved ambulance rows for the target municipality: "
            + ", ".join(f"{row['fire_station']}/{row['base_name']}" for row in unresolved)
        )
    return assigned, unresolved


def extract_egen_rows(pdf_path: Path, area: str) -> list[dict[str, str]]:
    import fitz  # PyMuPDF, already used elsewhere in this project

    columns = [
        "지역", "응급의료기관 종별", "기관명", "평가종합등급", "필수영역", "전담의사",
        "전담전문의", "전담간호사", "전담의사의 전문성", "전담간호사의 전문성",
        "병상포화지수", "중증상병 해당환자의 재실시간", "체류환자 지수",
        "적정시간 내 전문의 직접 진료율", "최종치료 제공률", "전입중증응급환자 진료 제공률", "비고",
    ]
    document = fitz.open(pdf_path)
    areas: set[str] = set()
    rows: list[dict[str, str]] = []
    for number in range(document.page_count):
        for table in document.load_page(number).find_tables().tables:
            for cells in table.extract():
                values = ["" if cell is None else " ".join(str(cell).split()) for cell in cells]
                if len(values) != len(columns) or values[0] in {"", "지역"} or not values[2]:
                    continue
                areas.add(values[0])
                if values[0] == area:
                    rows.append(dict(zip(columns, values)))
    document.close()
    assert_alias_present("E-Gen 응급의료기관 평가 지역", [area], areas)
    if not rows:
        raise ValueError(f"no E-Gen evaluation rows for area {area!r}")
    return rows


def build_hospital_rows(
    egen_rows: list[dict[str, str]],
    *,
    municipality: str,
    municipality_code: str,
    hira_province: str,
    hira_dir: Path,
    evaluation_year: int,
) -> list[dict[str, object]]:
    import pandas as pd

    def hira_file(prefix: str) -> Path:
        matches = [path for path in hira_dir.glob("**/*.xlsx") if path.name.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f"expected exactly one HIRA workbook starting with {prefix!r}, got {matches}")
        return matches[0]

    info = pd.read_excel(
        hira_file("1."),
        usecols=[
            "암호화요양기호", "요양기관명", "종별코드명", "시도코드명", "시군구코드명",
            "우편번호", "주소", "전화번호", "좌표(X)", "좌표(Y)",
        ],
    )
    assert_alias_present("HIRA 시도코드명", [hira_province], set(info["시도코드명"].dropna()))
    province_info = info[info["시도코드명"].eq(hira_province)].copy()
    province_info["_normalized_name"] = province_info["요양기관명"].map(normalize)

    stem = municipality_stem(municipality)
    local_codes = {
        str(value) for value in province_info["시군구코드명"].dropna().unique()
        if str(value) == municipality or str(value).startswith(stem)
    }
    if not local_codes:
        raise ValueError(
            f"HIRA has no 시군구코드명 for {municipality}; observed "
            f"{sorted(str(value) for value in province_info['시군구코드명'].dropna().unique())}"
        )

    selected: list[tuple[dict[str, str], object, float, str]] = []
    for egen in egen_rows:
        target = normalize(egen["기관명"])
        exact = province_info[province_info["_normalized_name"].eq(target)]
        if not exact.empty:
            match, score, method = exact.iloc[0], 1.0, "exact_normalized_name"
        else:
            scores = province_info["요양기관명"].map(lambda name: name_similarity(egen["기관명"], name))
            if scores.max() < 0.72:
                continue
            match, score, method = province_info.loc[scores.idxmax()], float(scores.max()), "fuzzy_name"
        if str(match["시군구코드명"]) in local_codes:
            selected.append((egen, match, score, method))
    if not selected:
        raise ValueError(f"no E-Gen emergency institution resolved into {municipality}")

    care_symbols = {str(match["암호화요양기호"]) for _egen, match, _score, _method in selected}
    facility = pd.read_excel(hira_file("3."))
    facility = facility[facility["암호화요양기호"].astype(str).isin(care_symbols)]
    facility_by_id = {str(row["암호화요양기호"]): row for _, row in facility.iterrows()}
    missing = care_symbols - set(facility_by_id)
    if missing:
        raise ValueError(f"HIRA 시설정보 has no row for encrypted care symbols {sorted(missing)}")

    special = pd.read_excel(hira_file("10."))
    special = special[special["암호화요양기호"].astype(str).isin(care_symbols)]
    services_by_id: dict[str, set[str]] = {}
    for care_symbol, group in special.groupby(special["암호화요양기호"].astype(str)):
        services_by_id[care_symbol] = {str(value) for value in group["검색코드명"].dropna()}

    equipment = pd.read_excel(hira_file("7."))
    equipment = equipment[equipment["암호화요양기호"].astype(str).isin(care_symbols)]
    equipment_by_id: dict[str, dict[str, int]] = {}
    for care_symbol, group in equipment.groupby(equipment["암호화요양기호"].astype(str)):
        equipment_by_id[care_symbol] = {
            str(row["장비코드명"]): int(row["장비대수"])
            for _, row in group.iterrows()
            if pd.notna(row["장비코드명"]) and pd.notna(row["장비대수"])
        }

    rows: list[dict[str, object]] = []
    for egen, match, score, method in selected:
        care_symbol = str(match["암호화요양기호"])
        beds = facility_by_id[care_symbol]
        counted = {column: (0 if pd.isna(beds[column]) else int(beds[column])) for column in BED_COLUMNS}
        services = services_by_id.get(care_symbol, set())
        equipment_counts = equipment_by_id.get(care_symbol, {})
        address = str(match["주소"]).strip()
        rows.append({
            "hospital_name": egen["기관명"],
            "emergency_category": egen["응급의료기관 종별"],
            "phone": str(match["전화번호"] or "").strip(),
            "road_address": address,
            "lot_address": "",
            "postal_code": str(match["우편번호"] or "").strip(),
            "latitude": f"{float(match['좌표(Y)']):.7f}",
            "longitude": f"{float(match['좌표(X)']):.7f}",
            "coordinate_source": "hira_hospital_information_service_2026_06",
            "matched_address": address,
            "municipality_code": municipality_code,
            "routing_status": "pending_kakao_api",
            "hira_hospital_name": str(match["요양기관명"]).strip(),
            "hira_encrypted_care_symbol": care_symbol,
            "hira_institution_type": str(match["종별코드명"]).strip(),
            "capacity_snapshot_period": "2026-06",
            "emergency_room_beds": counted["응급실병상수"],
            "critical_care_beds": sum(counted[column] for column in CRITICAL_CARE_COLUMNS),
            "inpatient_beds": sum(counted[column] for column in INPATIENT_COLUMNS),
            "registered_emergency_institution": "응급의료기관" in services,
            "registered_adult_icu": "성인 중환자실" in services,
            "ct_units": equipment_counts.get("CT", 0),
            "mri_units": equipment_counts.get("MRI", 0),
            "capacity_match_method": method,
            "capacity_match_score": round(float(score), 6),
            "capacity_match_review_required": score < 0.78,
            "capacity_source": CAPACITY_SOURCE,
            "capacity_semantics": CAPACITY_SEMANTICS,
            # 전국 평가 PDF 에는 기관코드(INSTT_CD) 열이 없다. 명단을 그 PDF 에서 직접
            # 읽었으므로 기관 대조는 필요 없지만, 코드 값은 비워 둔다.
            "egen_institution_code": "",
            "egen_evaluation_year": evaluation_year,
            "egen_overall_grade": egen["평가종합등급"],
            "egen_mandatory_area_result": egen["필수영역"],
            "egen_match_review_required": False,
            "egen_evaluation_source": EVALUATION_SOURCE,
        })
    rows.sort(key=lambda row: str(row["hospital_name"]))
    return rows


# -------------------------------------------------------------------- main flow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--province", required=True, help="공식 시·도명 (예: 전북특별자치도)")
    parser.add_argument("--municipality", required=True, help="공식 시·군명 (예: 전주시)")
    parser.add_argument("--municipality-code", required=True, help="5자리 시·군 코드 (예: 52110)")
    parser.add_argument("--nationwide-dir", type=Path, required=True)
    parser.add_argument("--capacity-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--population-month", default="2026년08월")
    parser.add_argument("--evaluation-year", type=int, default=2024)
    parser.add_argument(
        "--egen-extract-out", type=Path,
        help="선택: 해당 시·도의 E-Gen 평가 원문 추출 결과를 이 CSV 로 남긴다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_dir = args.nationwide_dir.resolve()
    capacity_dir = args.capacity_dir.resolve()
    output_root = args.output_root.resolve()
    code = args.municipality_code
    alias = aliases_for(args.province)
    manifest = load_and_verify_manifest(raw_dir)

    population = read_csv(raw_dir / "mois_resident_population_all_eup_myeon_dong_202608.csv")
    fleet = read_csv(raw_dir / "nfa_ambulances_by_fire_station_119_center_20250630.csv")
    centers = read_csv(raw_dir / "nfa_119_safety_centers_20231231.csv")

    demand_rows, municipal_total = build_demand_rows(
        population,
        province=args.province,
        municipality=args.municipality,
        municipality_code=code,
        population_prefixes=alias["population_prefixes"],
        population_column=f"{args.population_month}_총인구수",
        household_column=f"{args.population_month}_세대수",
    )
    ambulance_rows, unresolved = build_ambulance_rows(
        fleet, centers,
        municipality=args.municipality,
        fleet_provinces=alias["fleet_provinces"],
        center_provinces=alias["center_provinces"],
        address_prefixes=alias["address_prefixes"],
    )
    egen_rows = extract_egen_rows(
        capacity_dir / "egen_2024_evaluation_all_emergency_institutions.pdf", alias["egen_area"]
    )
    if args.egen_extract_out:
        write_csv(args.egen_extract_out.resolve(), list(egen_rows[0]), egen_rows)
    hospital_rows = build_hospital_rows(
        egen_rows,
        municipality=args.municipality,
        municipality_code=code,
        hira_province=alias["hira_province"],
        hira_dir=capacity_dir / "hira_2026_06_extracted",
        evaluation_year=args.evaluation_year,
    )

    folder = output_root / code
    write_csv(folder / "existing_hospitals.csv", HOSPITAL_FIELDS, hospital_rows)
    write_csv(folder / "demand_population.csv", DEMAND_FIELDS, demand_rows)
    write_csv(folder / "ambulance_bases.csv", AMBULANCE_FIELDS, ambulance_rows)
    write_csv(folder / "candidate_sites.csv", CANDIDATE_FIELDS, [])

    ambulance_count = sum(int(row["ambulance_count"]) for row in ambulance_rows)
    readiness = {
        "municipality_code": code,
        "municipality_name": args.municipality,
        "existing_emergency_hospital_count": len(hospital_rows),
        "demand_point_count": len(demand_rows),
        "ambulance_base_row_count": len(ambulance_rows),
        "ambulance_count": ambulance_count,
        "ready_for_simulation": False,
        "blocking_inputs": [
            "ambulance and demand-point geocoding",
            "actual directional road travel-time matrix",
            "municipal 119 demand-rate calibration",
            "physically feasible standby-post candidate sites",
        ],
    }
    (folder / "readiness.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = [{
        "municipality_code": code,
        "municipality_name": args.municipality,
        f"population_{args.population_month.replace('년', '').replace('월', '')}": municipal_total,
        "demand_point_count": len(demand_rows),
        "existing_emergency_hospital_count": len(hospital_rows),
        "ambulance_base_row_count": len(ambulance_rows),
        "ambulance_count": ambulance_count,
        "ready_for_simulation": False,
    }]
    write_csv(output_root / "municipality_summary.csv", list(summary[0]), summary)
    write_csv(
        output_root / "unresolved_ambulance_bases.csv",
        ["source_province", "fire_station", "safety_center", "regional_unit", "ambulance_count", "base_name", "reason"],
        unresolved,
    )

    build_manifest = {
        "source_manifest": str(raw_dir / "source_manifest.json"),
        "source_manifest_sha256": sha256(raw_dir / "source_manifest.json"),
        "province": args.province,
        "municipality_count": 1,
        "hospital_rows": len(hospital_rows),
        "demand_rows": len(demand_rows),
        "ambulance_rows": len(ambulance_rows),
        "ambulance_count": ambulance_count,
        "unresolved_ambulance_rows": len(unresolved),
        "ready_for_simulation": False,
        "no_synthetic_fallbacks": True,
        "raw_manifest_dataset_count": len(manifest["datasets"]),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "build_manifest.json").write_text(
        json.dumps(build_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(build_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
