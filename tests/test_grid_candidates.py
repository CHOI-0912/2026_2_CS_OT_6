from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ambulance_sim.kakao_api import KakaoApiClient
from scripts.build_grid_candidates import (
    BASE_FIELDS,
    EXTRA_FIELDS,
    REFERENCE_NAME,
    STANDBY_MANIFEST_NAME,
    STANDBY_NAME,
    build_grid,
    build_municipality,
    build_standby_municipality,
    haversine_km,
    simulation_pair_count,
    standby_pair_count,
)
from scripts.collect_kakao_routes import read_rows, standby_pairs


CODE = "41110"
# 행정동 by grid column: 파장동 keeps a population, 율천동 is the dense one,
# 정자1동 is empty and the last two columns fall outside the municipality.
DONG_BY_COLUMN = {
    0: ("4111156000", "파장동"),
    1: ("4111156600", "율천동"),
    2: ("4111156600", "율천동"),
    3: ("4111157100", "정자1동"),
    4: ("4113100100", "분당동"),
    5: ("4113100100", "분당동"),
}
POPULATION = {"4111156000": 1000, "4111156600": 6000, "4111157100": 0}


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _demand_rows() -> list[dict[str, object]]:
    return [
        {"administrative_code": code, "administrative_name": f"경기도 테스트시 {name}", "municipality_code": CODE,
         "population": POPULATION[code], "latitude": latitude, "longitude": longitude}
        for (code, name), latitude, longitude in zip(
            [DONG_BY_COLUMN[0], DONG_BY_COLUMN[1], DONG_BY_COLUMN[3]],
            [37.200, 37.203, 37.206], [127.00, 127.02, 127.04],
        )
    ]


def _candidate_row(index: int, latitude: float, longitude: float) -> dict[str, object]:
    return {
        "candidate_id": f"MOHW-20251231-{CODE}-{index:03d}", "candidate_name": f"보건소{index}",
        "municipality_code": CODE, "road_address": "경기도 테스트시 보건로 1",
        "latitude": latitude, "longitude": longitude,
        "candidate_source": "https://www.data.go.kr/data/3072692/fileData.do",
        "land_feasibility_status": "requires_planning_review_existing_public_health_facility",
        "coordinate_source": "kakao_local_address_search", "matched_address": "경기 테스트시 보건로 1",
        "kakao_place_id": "", "geocoding_status": "verified_municipality_match",
        "routing_status": "pending_kakao_api",
    }


def _processed(tmp_path: Path) -> Path:
    folder = tmp_path / "processed" / CODE
    folder.mkdir(parents=True)
    _write_csv(folder / "demand_population.csv", _demand_rows())
    _write_csv(folder / "ambulance_bases.csv", [
        {"base_name": "테스트119안전센터", "ambulance_count": 2, "latitude": 37.2, "longitude": 127.0},
    ])
    # The hospital sits on the row-0 column-0 cell centre, so that 파장동 cell fails the separation rule.
    cells, _ = build_grid(read_rows(folder / "demand_population.csv"), 1.0, CODE)
    nearest = min(cells, key=lambda cell: (cell["grid_row"], cell["grid_col"]))
    _write_csv(folder / "existing_hospitals.csv", [
        {"hospital_name": "테스트병원", "municipality_code": CODE,
         "latitude": nearest["latitude"], "longitude": nearest["longitude"]},
    ])
    _write_csv(folder / "candidate_sites.csv", [
        _candidate_row(1, 37.5, 127.5), _candidate_row(2, 37.6, 127.6),
    ])
    return folder


def _fake_region_codes(folder: Path, *, override: dict[int, tuple[str, str]] | None = None):
    """Map every grid-cell centre to its column's 행정동/법정동 documents."""
    cells, _ = build_grid(read_rows(folder / "demand_population.csv"), 1.0, CODE)
    longitudes = sorted({cell["longitude"] for cell in cells})
    mapping = {**DONG_BY_COLUMN, **(override or {})}

    def region_codes(self, longitude: float, latitude: float) -> list[dict[str, object]]:
        column = min(range(len(longitudes)), key=lambda index: abs(longitudes[index] - longitude))
        code, name = mapping[column]
        return [
            {"region_type": "B", "code": f"{code[:8]}00", "address_name": f"경기도 테스트시 {name}"},
            {"region_type": "H", "code": code, "address_name": f"경기도 테스트시 {name}"},
        ]

    return region_codes


def _build(folder: Path, monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(KakaoApiClient, "region_codes", _fake_region_codes(folder, **kwargs))
    build_municipality(
        folder, KakaoApiClient("test-key"),
        grid_km=1.0, exclude_top_population_share=0.30, min_distance_km=0.5,
        requests_per_second=1000.0, overwrite=False, dry_run=False,
    )


def _build_standby(folder: Path, monkeypatch: pytest.MonkeyPatch, *, min_distance_km: float, **kwargs) -> None:
    monkeypatch.setattr(KakaoApiClient, "region_codes", _fake_region_codes(folder, **kwargs))
    build_standby_municipality(
        folder, KakaoApiClient("test-key"),
        grid_km=1.0, min_distance_km=min_distance_km,
        requests_per_second=1000.0, overwrite=False, dry_run=False,
    )


def test_haversine_matches_a_known_meridian_degree() -> None:
    assert haversine_km(37.0, 127.0, 37.0, 127.0) == 0.0
    assert haversine_km(37.0, 127.0, 38.0, 127.0) == pytest.approx(111.195, abs=0.01)
    assert haversine_km(37.5666, 126.9784, 35.1798, 129.0750) == pytest.approx(325.0, abs=2.0)


def test_grid_uses_the_demand_envelope_with_one_cell_margin(tmp_path: Path) -> None:
    folder = _processed(tmp_path)
    cells, geometry = build_grid(read_rows(folder / "demand_population.csv"), 1.0, CODE)
    assert (geometry["rows"], geometry["columns"]) == (3, 6)
    assert len(cells) == 18
    assert cells[0]["cell_id"] == f"GRID-1KM-{CODE}-000-000"
    assert geometry["latitude_step_degrees"] == pytest.approx(1.0 / 111.32)


def test_filter_stages_and_manifest_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _processed(tmp_path)
    _build(folder, monkeypatch)
    manifest = json.loads((folder / "grid_candidates_manifest.json").read_text(encoding="utf-8"))
    # 18 cells; the last two columns are 성남시; 정자1동 has no residents; the 율천동 cells tie at the
    # top-population cutoff; the hospital sits on one of the three surviving 파장동 cells.
    assert manifest["counts"] == {
        "total_cells": 18,
        "inside_municipality": 12,
        "population_positive": 9,
        "after_top_population_exclusion": 3,
        "after_distance_filter": 2,
    }
    assert manifest["top_population_exclusion"]["rank_cutoff"] == 3
    assert manifest["top_population_exclusion"]["excluded_cells"] == 6
    assert manifest["top_population_exclusion"]["tie_expanded"] is True
    assert "never as a travel time" in manifest["haversine_note"]
    assert len(manifest["region_code_cache"]) == 18
    assert manifest["municipality_administrative_prefixes"] == ["41111"]
    assert manifest["parameters"]["min_distance_km"] == 0.5


def test_selected_candidate_keeps_the_distant_cell_and_the_expected_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _processed(tmp_path)
    _build(folder, monkeypatch)
    with (folder / "candidate_sites.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    assert fields == BASE_FIELDS + EXTRA_FIELDS
    assert [row["candidate_id"] for row in rows] == [f"GRID-1KM-{CODE}-001-000", f"GRID-1KM-{CODE}-002-000"]
    row = rows[0]
    assert row["candidate_name"] == "경기도 테스트시 파장동 격자 1-0"
    assert row["road_address"] == "경기도 테스트시 파장동"
    assert row["administrative_code"] == "4111156000"
    assert row["candidate_source"].startswith("population-filtered 1 km grid")
    assert row["land_feasibility_status"] == "requires_planning_review_vacant_site_unverified"
    assert row["coordinate_source"] == "grid_cell_center"
    assert row["geocoding_status"] == "verified_municipality_match"
    assert row["routing_status"] == "pending_kakao_api"
    assert float(row["cell_population_estimate"]) == pytest.approx(333.3)
    assert float(row["nearest_facility_km"]) == pytest.approx(1.0, abs=0.02)
    assert (row["grid_row"], row["grid_col"]) == ("1", "0")


def test_public_health_rows_move_to_the_reference_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _processed(tmp_path)
    _build(folder, monkeypatch)
    with (folder / REFERENCE_NAME).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert list(reader.fieldnames or []) == BASE_FIELDS
        reference = list(reader)
    assert [row["candidate_id"] for row in reference] == [
        f"MOHW-20251231-{CODE}-001", f"MOHW-20251231-{CODE}-002",
    ]
    assert not [row for row in read_rows(folder / "candidate_sites.csv") if row["candidate_id"].startswith("MOHW-")]


def test_rerun_without_overwrite_refuses_to_replace_the_reference_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _processed(tmp_path)
    _build(folder, monkeypatch)
    with pytest.raises(ValueError, match="--overwrite"):
        _build(folder, monkeypatch)


def test_unknown_administrative_code_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _processed(tmp_path)
    with pytest.raises(ValueError, match="4111199900"):
        _build(folder, monkeypatch, override={2: ("4111199900", "없는동")})


def test_simulation_pair_count_matches_the_collected_matrix_formula() -> None:
    assert simulation_pair_count(44, 11, 7, 4) == 1683


# --- standby posts ------------------------------------------------------------------------

# The only base sits at 37.2/127.0, one half-cell from the four cell centres around it,
# so a 1 km rule drops exactly those four and keeps the five cells further out.
NEAR_BASE = [f"GRID-1KM-{CODE}-000-000", f"GRID-1KM-{CODE}-000-001",
             f"GRID-1KM-{CODE}-001-000", f"GRID-1KM-{CODE}-001-001"]
AWAY_FROM_BASE = [f"GRID-1KM-{CODE}-000-002", f"GRID-1KM-{CODE}-001-002",
                  f"GRID-1KM-{CODE}-002-000", f"GRID-1KM-{CODE}-002-001", f"GRID-1KM-{CODE}-002-002"]


def test_standby_drops_only_the_cells_beside_an_existing_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _processed(tmp_path)
    _build_standby(folder, monkeypatch, min_distance_km=1.0)
    with (folder / STANDBY_NAME).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    assert fields == BASE_FIELDS + EXTRA_FIELDS
    assert [row["candidate_id"] for row in rows] == AWAY_FROM_BASE
    # 율천동 holds the densest cells and the hospital-mode run excludes every one of them;
    # a standby post has no top-population rule, so four of the five survivors are 율천동.
    assert sum(float(row["cell_population_estimate"]) == 1000.0 for row in rows) == 4
    row = rows[0]
    assert row["land_feasibility_status"] == "requires_review_standby_post_unverified"
    assert row["candidate_source"] == (
        "population-weighted 1 km grid standby post; Kakao coord2regioncode membership"
    )
    assert float(row["nearest_facility_km"]) == pytest.approx(1.581, abs=0.02)
    manifest = json.loads((folder / STANDBY_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["counts"] == {
        "total_cells": 18, "inside_municipality": 12, "population_positive": 9, "after_distance_filter": 5,
    }
    assert manifest["parameters"] == {"mode": "standby", "grid_km": 1.0, "min_distance_km": 1.0}
    assert "top_population_exclusion" not in manifest
    assert manifest["separation_reference"]["file"] == "ambulance_bases.csv"
    assert "never as a travel time" in manifest["haversine_note"]


def test_standby_measures_bases_only_and_ignores_the_hospital(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = _processed(tmp_path)
    _build_standby(folder, monkeypatch, min_distance_km=0.5)
    rows = read_rows(folder / STANDBY_NAME)
    assert [row["candidate_id"] for row in rows] == sorted(NEAR_BASE + AWAY_FROM_BASE)
    # The hospital stands on this cell centre; for a standby post that is irrelevant and the
    # reported separation is the distance to the base, not to the hospital on the spot.
    beside_hospital = next(row for row in rows if row["candidate_id"] == f"GRID-1KM-{CODE}-000-000")
    assert float(beside_hospital["nearest_facility_km"]) == pytest.approx(0.707, abs=0.02)


def test_standby_reuses_the_cached_region_codes_and_leaves_candidate_sites_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _processed(tmp_path)
    _build(folder, monkeypatch)
    before = (folder / "candidate_sites.csv").read_bytes()

    def forbidden(self, longitude: float, latitude: float) -> list[dict[str, object]]:
        raise AssertionError("the cached region codes must be reused instead of calling Kakao again")

    monkeypatch.setattr(KakaoApiClient, "region_codes", forbidden)
    build_standby_municipality(
        folder, KakaoApiClient("test-key"),
        grid_km=1.0, min_distance_km=1.0, requests_per_second=1000.0, overwrite=False, dry_run=False,
    )
    manifest = json.loads((folder / STANDBY_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["new_region_code_calls"] == 0
    assert (folder / "candidate_sites.csv").read_bytes() == before


def test_standby_rerun_without_overwrite_refuses_to_replace_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _processed(tmp_path)
    _build_standby(folder, monkeypatch, min_distance_km=1.0)
    with pytest.raises(ValueError, match="--overwrite"):
        _build_standby(folder, monkeypatch, min_distance_km=1.0)


def test_standby_pair_count_matches_the_collected_pair_set() -> None:
    demands = {f"d{index}" for index in range(3)}
    bases = {f"b{index}" for index in range(2)}
    hospitals = {f"h{index}" for index in range(2)}
    posts = {f"p{index}" for index in range(4)}
    assert standby_pair_count(3, 2, 2, 4) == len(standby_pairs(demands, bases, hospitals, posts))
    assert standby_pair_count(44, 11, 7, 31) == 2934
