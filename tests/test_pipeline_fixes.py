from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.refresh_gyeonggi_readiness import DEMAND_BLOCKER, LAND_REVIEW_ADVISORY, TREATMENT_BLOCKER
from scripts.refresh_gyeonggi_readiness import main as refresh_readiness
from scripts.stage_mohw_candidates import FIELDS as CANDIDATE_FIELDS
from scripts.stage_mohw_candidates import main as stage_mohw_candidates
from scripts.stage_mohw_candidates import normalized_address


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


# --- refresh_gyeonggi_readiness ------------------------------------------------------------

CODE = "41110"
PLANNING_REVIEW = "requires_planning_review_existing_public_health_facility"


def _processed_dir(tmp_path: Path, *, simulation_ready: bool = True) -> Path:
    """One fully geocoded municipality whose only open items are calibration and land review."""
    root = tmp_path / "processed"
    folder = root / CODE
    folder.mkdir(parents=True)
    _write_csv(folder / "ambulance_bases.csv", ["base_name", "latitude", "longitude"],
               [{"base_name": "b", "latitude": "37.31", "longitude": "127.01"}])
    _write_csv(folder / "demand_population.csv", ["administrative_code", "latitude", "longitude"],
               [{"administrative_code": "4111156000", "latitude": "37.30", "longitude": "127.00"}])
    _write_csv(folder / "existing_hospitals.csv",
               ["hospital_name", "latitude", "longitude", "emergency_room_beds", "capacity_match_review_required"],
               [{"hospital_name": "h", "latitude": "37.32", "longitude": "127.02",
                 "emergency_room_beds": "20", "capacity_match_review_required": "False"}])
    _write_csv(folder / "candidate_sites.csv", ["candidate_id", "latitude", "longitude", "land_feasibility_status"],
               [{"candidate_id": "MOHW-20251231-41110-001", "latitude": "37.33", "longitude": "127.03",
                 "land_feasibility_status": PLANNING_REVIEW}])
    (folder / "readiness.json").write_text(
        json.dumps({"municipality_code": CODE, "ready_for_simulation": False, "blocking_inputs": []}), encoding="utf-8"
    )
    (folder / "road_times_manifest.json").write_text(
        json.dumps({"pair_set": "simulation", "simulation_ready": simulation_ready}), encoding="utf-8"
    )
    (root / "build_manifest.json").write_text(json.dumps({"ready_for_simulation": False}), encoding="utf-8")
    return root


def _parameters(tmp_path: Path, *, status: str, method: str, daily_calls: dict | None = None) -> Path:
    path = tmp_path / f"model_parameters_{status}.json"
    path.write_text(json.dumps({
        "schema_version": 1, "status": status,
        "demand": {"method": method, "daily_calls_by_municipality": daily_calls},
    }), encoding="utf-8")
    return path


def _refresh(root: Path, parameters: Path | None = None) -> tuple[dict, dict]:
    argv = ["--processed-dir", str(root)]
    if parameters is not None:
        argv += ["--parameters", str(parameters)]
    assert refresh_readiness(argv) == 0
    readiness = json.loads((root / CODE / "readiness.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "build_manifest.json").read_text(encoding="utf-8"))
    return readiness, manifest


def test_readiness_without_parameters_keeps_calibration_blockers_and_advisory_land_review(tmp_path: Path) -> None:
    readiness, manifest = _refresh(_processed_dir(tmp_path))
    assert readiness["blocking_inputs"] == [DEMAND_BLOCKER, TREATMENT_BLOCKER]
    assert readiness["advisory_inputs"] == [LAND_REVIEW_ADVISORY]
    assert readiness["ready_for_simulation"] is False
    assert manifest["ready_for_simulation"] is False
    assert manifest["ready_municipality_codes"] == []


def test_missing_parameters_file_keeps_calibration_blockers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    readiness, _ = _refresh(_processed_dir(tmp_path), tmp_path / "absent.json")
    assert readiness["blocking_inputs"] == [DEMAND_BLOCKER, TREATMENT_BLOCKER]
    assert "parameters file not found" in capsys.readouterr().err


def test_provisional_population_proportional_parameters_label_the_demand_blocker(tmp_path: Path) -> None:
    parameters = _parameters(tmp_path, status="provisional", method="population_proportional_provincial_dispatches")
    readiness, manifest = _refresh(_processed_dir(tmp_path), parameters)
    assert readiness["blocking_inputs"] == [
        f"{DEMAND_BLOCKER} (population-proportional estimate in use)", TREATMENT_BLOCKER,
    ]
    assert readiness["ready_for_simulation"] is False
    assert manifest["ready_for_simulation"] is False


def test_calibrated_observed_parameters_make_the_municipality_ready(tmp_path: Path) -> None:
    parameters = _parameters(
        tmp_path, status="calibrated", method="observed_municipal_dispatches", daily_calls={CODE: 118.4},
    )
    readiness, manifest = _refresh(_processed_dir(tmp_path), parameters)
    assert readiness["blocking_inputs"] == []
    assert readiness["advisory_inputs"] == [LAND_REVIEW_ADVISORY]
    assert readiness["ready_for_simulation"] is True
    assert manifest["ready_for_simulation"] is True
    assert manifest["ready_municipality_codes"] == [CODE]


def test_calibrated_parameters_without_this_municipality_keep_the_demand_blocker(tmp_path: Path) -> None:
    parameters = _parameters(
        tmp_path, status="calibrated", method="observed_municipal_dispatches", daily_calls={"41130": 90.0},
    )
    readiness, manifest = _refresh(_processed_dir(tmp_path), parameters)
    assert readiness["blocking_inputs"] == [DEMAND_BLOCKER]
    assert manifest["ready_municipality_codes"] == []


def test_calibrated_parameters_do_not_bypass_the_road_matrix_blocker(tmp_path: Path) -> None:
    parameters = _parameters(
        tmp_path, status="calibrated", method="observed_municipal_dispatches", daily_calls={CODE: 118.4},
    )
    readiness, manifest = _refresh(_processed_dir(tmp_path, simulation_ready=False), parameters)
    assert readiness["blocking_inputs"] == ["actual directional road travel-time matrix"]
    assert manifest["ready_for_simulation"] is False


def test_invalid_parameter_status_fails_loud(tmp_path: Path) -> None:
    parameters = _parameters(tmp_path, status="draft", method="observed_municipal_dispatches")
    with pytest.raises(ValueError, match="status"):
        refresh_readiness(["--processed-dir", str(_processed_dir(tmp_path)), "--parameters", str(parameters)])


# --- stage_mohw_candidates -----------------------------------------------------------------

def test_normalized_address_ignores_annotations_spacing_and_province_prefix() -> None:
    mohw = normalized_address("경기도 안산시 단원구 화랑로 250 (초지동, 단원보건소) 단원보건소")
    assert mohw == normalized_address("안산시 단원구 화랑로 250(초지동)")
    assert mohw == normalized_address("경기 안산시 단원구 화랑로 250")
    assert normalized_address("경기도 안산시 단원구 부부로 43, 3층(원곡동)") == normalized_address("경기도 안산시 단원구 부부로 43 (원곡동)")
    assert normalized_address("경기도 안산시 단원구 부부로 43") != normalized_address("경기도 안산시 단원구 부부로 4")


def _candidate(candidate_id: str, name: str, address: str) -> dict[str, str]:
    row = {field: "" for field in CANDIDATE_FIELDS}
    row.update({
        "candidate_id": candidate_id, "candidate_name": name, "municipality_code": "41270",
        "road_address": address, "candidate_source": "https://www.data.go.kr/data/15036776/fileData.do",
        "land_feasibility_status": PLANNING_REVIEW, "geocoding_status": "pending_kakao_address_geocoding",
        "routing_status": "pending_kakao_api",
    })
    return row


def test_stage_mohw_candidates_merges_non_mohw_rows_and_dedupes_by_address(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "processed"
    (root / "41270").mkdir(parents=True)
    (root / "41310").mkdir()
    _write_csv(root / "municipality_summary.csv", ["municipality_code", "municipality_name"],
               [{"municipality_code": "41270", "municipality_name": "안산시"},
                {"municipality_code": "41310", "municipality_name": "구리시"}])
    source = tmp_path / "mohw.csv"
    _write_csv(source, ["정규화시군", "보건기관명", "주소"], [
        {"정규화시군": "안산시", "보건기관명": "안산시단원보건소", "주소": "경기도 안산시 단원구 화랑로 250 (초지동, 단원보건소) 단원보건소"},
        {"정규화시군": "안산시", "보건기관명": "원곡보건지소", "주소": "경기도 안산시 단원구 부부로 43 (원곡동)"},
    ])
    _write_csv(root / "41270" / "candidate_sites.csv", CANDIDATE_FIELDS, [
        _candidate("public_health_41270_001", "단원보건소", "안산시 단원구 화랑로 250(초지동)"),
        _candidate("public_health_41270_002", "원곡보건지소", "경기도 안산시 단원구 부부로 43, 3층(원곡동)"),
        _candidate("public_health_41270_003", "신길보건지소", "경기도 안산시 단원구 신길중앙로 15"),
        _candidate("MOHW-20251231-41270-009", "옛 MOHW 행", "경기도 안산시 옛길 1"),
    ])

    assert stage_mohw_candidates(["--source", str(source), "--processed-dir", str(root)]) == 0

    rows = _read_csv(root / "41270" / "candidate_sites.csv")
    assert [row["candidate_id"] for row in rows] == [
        "MOHW-20251231-41270-001", "MOHW-20251231-41270-002", "public_health_41270_003",
    ]
    assert rows[2]["candidate_name"] == "신길보건지소"
    assert all(row["land_feasibility_status"] == PLANNING_REVIEW for row in rows)
    assert _read_csv(root / "41310" / "candidate_sites.csv") == []
    out = capsys.readouterr().out
    assert "41270: kept 1 non-MOHW rows, dropped 2 duplicating MOHW addresses" in out
    assert "staged=2 municipalities=1 kept_non_mohw=1 dropped_non_mohw=2" in out


# --- build_hospital_capacity_match ---------------------------------------------------------

def test_capacity_match_arguments_default_to_project_paths() -> None:
    pytest.importorskip("pandas")
    from scripts import build_hospital_capacity_match as module

    defaults = module.build_parser().parse_args([])
    assert defaults.processed_dir == module.PROCESSED
    assert defaults.raw_dir == module.RAW
    custom = module.build_parser().parse_args(["--processed-dir", "p", "--raw-dir", "r"])
    assert (custom.processed_dir, custom.raw_dir) == (Path("p"), Path("r"))
