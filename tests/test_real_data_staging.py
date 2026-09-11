from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PROCESSED = ROOT / "data" / "processed" / "gyeonggi_20260909"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_real_staging_preserves_all_31_municipalities_and_official_totals() -> None:
    summary = _rows(PROCESSED / "municipality_summary.csv")
    assert len(summary) == 31
    assert sum(int(row["population_202608"]) for row in summary) == 13_773_918
    assert sum(int(row["demand_point_count"]) for row in summary) == 604
    assert sum(int(row["existing_emergency_hospital_count"]) for row in summary) == 73
    assert sum(int(row["ambulance_count"]) for row in summary) == 290


def test_zero_hospital_municipalities_are_kept_empty_without_fake_facilities() -> None:
    summary = _rows(PROCESSED / "municipality_summary.csv")
    zero_hospital = {row["municipality_name"]: row["municipality_code"] for row in summary if row["existing_emergency_hospital_count"] == "0"}
    assert set(zero_hospital) == {"동두천시", "과천시", "의왕시", "하남시", "양주시", "가평군"}
    for code in zero_hospital.values():
        assert _rows(PROCESSED / code / "existing_hospitals.csv") == []


MOHW_STATUS = "requires_planning_review_existing_public_health_facility"
GRID_STATUS = "requires_planning_review_vacant_site_unverified"


def test_staging_candidates_are_all_labelled_for_planning_review() -> None:
    """Two candidate definitions coexist: population-filtered grid cells (41110 so far)
    and MOHW public-health facilities elsewhere. Neither is a confirmed building site."""
    manifest = json.loads((PROCESSED / "build_manifest.json").read_text(encoding="utf-8"))
    assert isinstance(manifest["ready_for_simulation"], bool)
    assert manifest["no_synthetic_fallbacks"] is True
    assert manifest["unresolved_ambulance_rows"] == 0
    candidates_by_code: dict[str, list[dict[str, str]]] = {}
    mohw_total = 0
    for municipality in PROCESSED.iterdir():
        if not municipality.is_dir():
            continue
        readiness = json.loads((municipality / "readiness.json").read_text(encoding="utf-8"))
        # Readiness is derived, never asserted by hand: ready exactly when nothing blocks.
        assert readiness["ready_for_simulation"] == (readiness["blocking_inputs"] == [])
        candidates = _rows(municipality / "candidate_sites.csv")
        candidates_by_code[municipality.name] = candidates
        for candidate in candidates:
            assert candidate["municipality_code"] == municipality.name
            assert bool(candidate["latitude"]) == bool(candidate["longitude"])
            if candidate["candidate_id"].startswith("MOHW-"):
                mohw_total += 1
                assert candidate["candidate_source"].startswith("https://www.data.go.kr/data/")
                assert candidate["land_feasibility_status"] == MOHW_STATUS
            else:
                assert candidate["candidate_id"].startswith("GRID-")
                assert "grid" in candidate["candidate_source"]
                assert candidate["land_feasibility_status"] == GRID_STATUS
                assert candidate["coordinate_source"] == "grid_cell_center"
    assert len(candidates_by_code) == 31
    assert all(candidates_by_code.values())
    assert mohw_total == 332  # 336 MOHW facilities minus the 4 Suwon rows moved to the reference file
    assert all(row["candidate_id"].startswith("GRID-") for row in candidates_by_code["41110"])
    assert len(_rows(PROCESSED / "41110" / "candidate_sites_public_health_reference.csv")) == 4
    assert {code: len(candidates_by_code[code]) for code in ("41250", "41290", "41430", "41450", "41610", "41820")} == {
        "41250": 1, "41290": 2, "41430": 2, "41450": 3, "41610": 16, "41820": 21,
    }


def test_hira_static_capacity_is_attached_without_being_called_realtime() -> None:
    hospitals = []
    for municipality in PROCESSED.iterdir():
        if municipality.is_dir():
            hospitals.extend(_rows(municipality / "existing_hospitals.csv"))
    assert len(hospitals) == 73
    assert all(int(row["emergency_room_beds"]) > 0 for row in hospitals)
    assert all("not real-time" in row["capacity_semantics"] for row in hospitals)
    assert sum(row["capacity_match_review_required"].lower() == "true" for row in hospitals) == 1
    suwon = [row for row in hospitals if row["municipality_code"] == "41110"]
    assert len(suwon) == 7
    assert sum(int(row["emergency_room_beds"]) for row in suwon) == 201


def test_all_demand_representatives_have_coordinates_without_duplicate_points() -> None:
    demand = []
    for municipality in PROCESSED.iterdir():
        if municipality.is_dir():
            demand.extend(_rows(municipality / "demand_population.csv"))
    assert len(demand) == 604
    assert all(row["latitude"] and row["longitude"] for row in demand)
    coordinates = [(row["latitude"], row["longitude"]) for row in demand]
    assert len(set(coordinates)) == len(coordinates)
    # Representative points come from Kakao proxies, except the three administrative
    # offices resolved from official municipal addresses by apply_demand_coordinate_overrides.py.
    allowed_sources = {
        "kakao_administrative_office_representative_proxy",
        "official_municipal_office_address_geocoded_by_kakao",
    }
    assert {row["coordinate_source"] for row in demand} <= allowed_sources
    assert sum(row["coordinate_source"] == "official_municipal_office_address_geocoded_by_kakao" for row in demand) == 3
