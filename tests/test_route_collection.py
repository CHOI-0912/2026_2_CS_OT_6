from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ambulance_sim.kakao_api import KakaoApiError
from scripts import collect_kakao_routes
from scripts.collect_kakao_routes import FIELDS, read_rows, resource_pairs, standby_pairs, write_manifest


def test_resource_pairs_stay_within_base_and_facility_nodes() -> None:
    pairs = resource_pairs({"base-a", "base-b"}, {"hospital-a", "hospital-b"})
    assert len(pairs) == 10
    assert ("base-a", "hospital-a") in pairs
    assert ("hospital-a", "base-a") in pairs
    assert ("hospital-a", "hospital-b") in pairs
    assert ("base-a", "base-b") not in pairs


def test_resource_manifest_is_never_simulation_ready(tmp_path: Path) -> None:
    required = [("base", "hospital")]
    write_manifest(
        tmp_path,
        pair_set="resources",
        required=required,
        completed=set(required),
    )
    manifest = json.loads((tmp_path / "road_times_manifest.json").read_text(encoding="utf-8"))
    assert manifest["complete"] is True
    assert manifest["simulation_ready"] is False


def test_only_complete_simulation_manifest_is_simulation_ready(tmp_path: Path) -> None:
    required = [("base", "demand"), ("demand", "hospital")]
    write_manifest(
        tmp_path,
        pair_set="simulation",
        required=required,
        completed={required[0]},
    )
    partial = json.loads((tmp_path / "road_times_manifest.json").read_text(encoding="utf-8"))
    assert partial["complete"] is False
    assert partial["simulation_ready"] is False

    write_manifest(
        tmp_path,
        pair_set="simulation",
        required=required,
        completed=set(required),
    )
    complete = json.loads((tmp_path / "road_times_manifest.json").read_text(encoding="utf-8"))
    assert complete["complete"] is True
    assert complete["simulation_ready"] is True


# --- main() with a fake Kakao client -----------------------------------------------------

FAKE_KEY = "test-key-never-printed"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _processed_dir(tmp_path: Path) -> Path:
    """One municipality with 1 demand, 1 base, 1 hospital, 1 candidate: 9 simulation pairs."""
    folder = tmp_path / "41999"
    folder.mkdir()
    _write_csv(folder / "demand_population.csv", ["administrative_code", "latitude", "longitude"],
               [{"administrative_code": "4199901000", "latitude": "37.30", "longitude": "127.00"}])
    _write_csv(folder / "ambulance_bases.csv", ["base_name", "latitude", "longitude"],
               [{"base_name": "b", "latitude": "37.31", "longitude": "127.01"}])
    _write_csv(folder / "existing_hospitals.csv", ["hospital_name", "latitude", "longitude"],
               [{"hospital_name": "h", "latitude": "37.32", "longitude": "127.02"}])
    _write_csv(folder / "candidate_sites.csv", ["candidate_id", "latitude", "longitude"],
               [{"candidate_id": "MOHW-20251231-41999-001", "latitude": "37.33", "longitude": "127.03"}])
    return tmp_path


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], *, fail_at: int | None = None) -> tuple[int, list]:
    calls: list = []

    class FakeClient:
        def __init__(self, key: str):
            self.key = key

        def driving_time(self, *coordinates: float, priority: str = "RECOMMEND") -> dict:
            calls.append(coordinates)
            if fail_at is not None and len(calls) == fail_at:
                raise KakaoApiError(f"Kakao API HTTP 401: rejected key {self.key}")
            return {"duration_seconds": 600, "duration_minutes": 10.0, "distance_meters": 5000, "priority": priority}

    monkeypatch.setenv("KAKAO_REST_API_KEY", FAKE_KEY)
    monkeypatch.setattr(collect_kakao_routes, "load_env_file", lambda _path: None)
    monkeypatch.setattr(collect_kakao_routes, "KakaoApiClient", FakeClient)
    monkeypatch.setattr(collect_kakao_routes.time, "sleep", lambda _seconds: None)
    return collect_kakao_routes.main(argv), calls


def _manifest(root: Path) -> dict:
    return json.loads((root / "41999" / "road_times_manifest.json").read_text(encoding="utf-8"))


def test_legacy_road_times_gain_pair_set_column_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _processed_dir(tmp_path)
    output = root / "41999" / "road_times.csv"
    _write_csv(output, [field for field in FIELDS if field != "pair_set"], [{
        "municipality_code": "41999", "origin_id": "41999::ambulance_base::001",
        "destination_id": "41999::demand::4199901000", "duration_minutes": "10.0",
        "duration_seconds": "600", "distance_meters": "5000",
        "routing_provider": "Kakao Mobility Directions API", "routing_profile": "RECOMMEND",
        "collected_at": "2026-09-09T00:00:00+00:00",
    }])

    code, calls = _run(monkeypatch, ["--processed-dir", str(root)])
    assert code == 0
    assert len(calls) == 8  # the legacy row is reused, not re-collected

    payload = output.read_bytes()
    assert payload.startswith(b"\xef\xbb\xbf") and payload.count(b"\xef\xbb\xbf") == 1
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle)) == FIELDS
    rows = read_rows(output)
    assert len(rows) == 9
    assert rows[0]["pair_set"] == "unknown_before_pair_set_column"
    assert {row["pair_set"] for row in rows[1:]} == {"simulation"}


def test_max_calls_stops_with_manifest_and_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _processed_dir(tmp_path)
    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--max-calls", "2"])
    assert code == 0
    assert len(calls) == 2
    manifest = _manifest(root)
    assert (manifest["completed_pair_count"], manifest["required_pair_count"]) == (2, 9)
    assert manifest["simulation_ready"] is False
    out = capsys.readouterr().out
    assert "--max-calls=2" in out
    assert "41999: 2/9" in out

    code, calls = _run(monkeypatch, ["--processed-dir", str(root)])
    assert code == 0
    assert len(calls) == 7
    assert _manifest(root)["simulation_ready"] is True
    assert "41999: 9/9" in capsys.readouterr().out
    assert {row["pair_set"] for row in read_rows(root / "41999" / "road_times.csv")} == {"simulation"}


def test_limit_and_max_calls_use_the_smaller_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _processed_dir(tmp_path)
    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--limit", "5", "--max-calls", "3"])
    assert code == 0
    assert len(calls) == 3
    assert "--max-calls=3" in capsys.readouterr().out

    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--limit", "2", "--max-calls", "5"])
    assert code == 0
    assert len(calls) == 2
    assert "--limit=2" in capsys.readouterr().out
    assert _manifest(root)["completed_pair_count"] == 5


def test_kakao_api_error_writes_manifest_and_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _processed_dir(tmp_path)
    code, calls = _run(monkeypatch, ["--processed-dir", str(root)], fail_at=3)
    assert code == 2
    assert len(calls) == 3
    assert len(read_rows(root / "41999" / "road_times.csv")) == 2
    assert _manifest(root)["completed_pair_count"] == 2
    captured = capsys.readouterr()
    assert "Kakao API HTTP 401" in captured.err
    assert FAKE_KEY not in captured.err + captured.out
    assert "41999: 2/9" in captured.out

    code, calls = _run(monkeypatch, ["--processed-dir", str(root)])
    assert code == 0
    assert len(calls) == 7
    assert _manifest(root)["simulation_ready"] is True


# --- standby pair set ---------------------------------------------------------------------

CODE = "41999"
BOM = "﻿".encode("utf-8")
DEMAND = f"{CODE}::demand::4199901000"
BASES = [f"{CODE}::ambulance_base::001", f"{CODE}::ambulance_base::002"]
HOSPITAL = f"{CODE}::hospital::001"
CANDIDATE = f"{CODE}::candidate::MOHW-20251231-41999-001"
POSTS = [f"{CODE}::standby::GRID-1KM-{CODE}-000-000", f"{CODE}::standby::GRID-1KM-{CODE}-000-001"]


def test_standby_pairs_cover_every_post_and_treat_a_base_as_a_post() -> None:
    pairs = standby_pairs({"d"}, {"b1", "b2"}, {"h"}, {"p"})
    assert len(pairs) == 12  # 1*(1+1+2*2) + 2*(1+1) + 2*1
    for expected in (("p", "d"), ("h", "p"), ("p", "b1"), ("b1", "p"), ("b1", "d"), ("h", "b1"), ("b1", "b2")):
        assert expected in pairs
    assert ("b1", "b1") not in pairs
    assert ("d", "p") not in pairs


def _standby_dir(tmp_path: Path, *, legacy_routes: bool = False, with_candidates: bool = True) -> Path:
    """One municipality with 1 demand, 2 bases, 1 hospital and 2 posts: 18 standby pairs.

    The first post sits on the grid cell that was already collected as a hospital candidate,
    so its six contract routes can be copied instead of measured again.
    """
    root = _processed_dir(tmp_path)
    folder = root / CODE
    _write_csv(folder / "ambulance_bases.csv", ["base_name", "latitude", "longitude"], [
        {"base_name": "b1", "latitude": "37.31", "longitude": "127.01"},
        {"base_name": "b2", "latitude": "37.34", "longitude": "127.04"},
    ])
    _write_csv(folder / "standby_candidates.csv", ["candidate_id", "latitude", "longitude"], [
        {"candidate_id": f"GRID-1KM-{CODE}-000-000", "latitude": "37.33", "longitude": "127.03"},
        {"candidate_id": f"GRID-1KM-{CODE}-000-001", "latitude": "37.35", "longitude": "127.05"},
    ])
    if not with_candidates:
        (folder / "candidate_sites.csv").unlink()
        return root
    fields = [field for field in FIELDS if field != "reused_from"] if legacy_routes else FIELDS
    measured = [(CANDIDATE, DEMAND), (HOSPITAL, CANDIDATE)]
    measured += [(CANDIDATE, base) for base in BASES] + [(base, CANDIDATE) for base in BASES]
    _write_csv(folder / "road_times.csv", fields, [
        {field: value for field, value in {
            "municipality_code": CODE, "origin_id": origin, "destination_id": destination,
            "duration_minutes": "12.5", "duration_seconds": "750", "distance_meters": "6000",
            "routing_provider": "Kakao Mobility Directions API", "routing_profile": "RECOMMEND",
            "collected_at": "2026-09-10T00:00:00+00:00", "pair_set": "simulation", "reused_from": "",
        }.items() if field in fields}
        for origin, destination in measured
    ])
    return root


def test_standby_reuses_the_measured_candidate_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _standby_dir(tmp_path)
    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--pair-set", "standby"])
    assert code == 0
    assert len(calls) == 12  # 18 required pairs minus the 6 copied from the candidate rows

    rows = read_rows(root / CODE / "road_times.csv")
    assert len(rows) == 24
    copied = [row for row in rows if row["reused_from"]]
    assert len(copied) == 6
    assert {(row["origin_id"], row["destination_id"]) for row in copied} == {
        (POSTS[0], DEMAND), (HOSPITAL, POSTS[0]),
        *((POSTS[0], base) for base in BASES), *((base, POSTS[0]) for base in BASES),
    }
    first = next(row for row in copied if row["destination_id"] == DEMAND)
    assert first["reused_from"] == f"{CANDIDATE} -> {DEMAND}"
    assert first["pair_set"] == "standby"
    assert first["duration_minutes"] == "12.5"
    assert {row["pair_set"] for row in rows} == {"simulation", "standby"}
    assert "reused 6 measured candidate routes" in capsys.readouterr().out

    manifest = json.loads((root / CODE / "road_times_standby_manifest.json").read_text(encoding="utf-8"))
    assert (manifest["pair_set"], manifest["required_pair_count"], manifest["completed_pair_count"]) == ("standby", 18, 18)
    assert manifest["standby_ready"] is True
    assert manifest["simulation_ready"] is False
    # The hospital matrix keeps its own manifest untouched.
    assert not (root / CODE / "road_times_manifest.json").exists()


def test_standby_adds_the_reused_from_column_to_an_older_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _standby_dir(tmp_path, legacy_routes=True)
    output = root / CODE / "road_times.csv"
    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--pair-set", "standby"])
    assert code == 0
    assert len(calls) == 12

    payload = output.read_bytes()
    assert payload.startswith(BOM) and payload.count(BOM) == 1
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        assert next(csv.reader(handle)) == FIELDS
    rows = read_rows(output)
    assert [row["reused_from"] for row in rows[:6]] == [""] * 6
    assert [row["pair_set"] for row in rows[:6]] == ["simulation"] * 6


def test_standby_collects_every_pair_without_a_candidate_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _standby_dir(tmp_path, with_candidates=False)
    code, calls = _run(monkeypatch, ["--processed-dir", str(root), "--pair-set", "standby"])
    assert code == 0
    assert len(calls) == 18
    assert json.loads((root / CODE / "road_times_standby_manifest.json").read_text(encoding="utf-8"))["standby_ready"] is True
