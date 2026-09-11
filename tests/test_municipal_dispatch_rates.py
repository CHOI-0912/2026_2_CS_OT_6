from __future__ import annotations

import csv
import json

import pytest

from scripts.prepare_municipal_dispatch_rates import (
    STATION_TO_MUNICIPALITY,
    aggregate,
    main,
    read_rows,
)

HEADER = "sum_yr,sigun_cd,sigun_nm,gout_firesttn_nm,gout_safe_center_nm,dispatch_rows,rows_with_patient_sex\n"


def write_fixture(path, body: str) -> None:
    path.write_text(HEADER + body, encoding="utf-8")


def test_multi_station_city_is_summed_and_station_name_overrides_sigun_code(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    write_fixture(
        source,
        # 수원은 소방서 2개를 합산한다.
        "2025,41110,수원시,수원소방서,정자119안전센터,100,80\n"
        "2025,41110,수원시,수원남부소방서,남부119안전센터,50,40\n"
        # 원본이 남양주 행을 양주시(41630)로 잘못 표기한 경우를 재현한다.
        "2025,41630,양주시,남양주소방서,와부119안전센터,30,20\n"
        "2025,41630,양주시,양주소방서,회천119안전센터,10,8\n",
    )
    year, aggregated = aggregate(read_rows(source))

    assert year == "2025"
    assert aggregated["41110"]["annual_dispatches"] == 150
    assert sorted(aggregated["41110"]["stations"]) == ["수원남부소방서", "수원소방서"]
    assert aggregated["41360"]["municipality_name"] == "남양주시"
    assert aggregated["41360"]["annual_dispatches"] == 30
    assert aggregated["41630"]["annual_dispatches"] == 10


def test_unmapped_station_fails_loudly(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    write_fixture(source, "2025,41110,수원시,수원서부소방서,없는119안전센터,10,5\n")
    with pytest.raises(SystemExit) as excinfo:
        aggregate(read_rows(source))
    assert "수원서부소방서" in str(excinfo.value)


def test_mixed_years_fail_loudly(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    write_fixture(
        source,
        "2024,41110,수원시,수원소방서,정자119안전센터,10,5\n"
        "2025,41110,수원시,수원소방서,정자119안전센터,10,5\n",
    )
    with pytest.raises(SystemExit) as excinfo:
        aggregate(read_rows(source))
    assert "집계년도" in str(excinfo.value)


def test_missing_column_fails_loudly(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    source.write_text("sum_yr,sigun_nm,dispatch_rows\n2025,수원시,10\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        read_rows(source)
    assert "gout_firesttn_nm" in str(excinfo.value)


def test_main_writes_csv_and_manifest_with_input_sha256(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    write_fixture(
        source,
        "2025,41110,수원시,수원소방서,정자119안전센터,365,300\n"
        "2025,41800,연천군,연천소방서,연천119안전센터,730,700\n",
    )
    out_dir = tmp_path / "out"
    assert main(["--input", str(source), "--output-dir", str(out_dir)]) == 0

    rows = list(csv.DictReader((out_dir / "municipal_daily_calls_2025.csv").open(encoding="utf-8")))
    assert [r["municipality_code"] for r in rows] == ["41110", "41800"]
    assert rows[0]["annual_dispatches"] == "365"
    assert float(rows[0]["daily_calls"]) == pytest.approx(1.0)
    assert float(rows[1]["daily_calls"]) == pytest.approx(2.0)
    assert rows[0]["source_year"] == "2025"
    assert "data.gg.go.kr" in rows[0]["source_url"]
    assert rows[1]["mapping_note"] == "연천소방서 단독 관할"

    manifest = json.loads(
        (out_dir / "municipal_daily_calls_2025.manifest.json").read_text(encoding="utf-8")
    )
    import hashlib

    assert manifest["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["days_in_year"] == 365
    assert manifest["municipality_count"] == 2
    assert manifest["annual_dispatches_total"] == 1095


def test_leap_year_uses_366_days(tmp_path) -> None:
    source = tmp_path / "counts.csv"
    write_fixture(source, "2024,41110,수원시,수원소방서,정자119안전센터,366,300\n")
    out_dir = tmp_path / "out"
    assert main(["--input", str(source), "--output-dir", str(out_dir)]) == 0
    rows = list(csv.DictReader((out_dir / "municipal_daily_calls_2024.csv").open(encoding="utf-8")))
    assert float(rows[0]["daily_calls"]) == pytest.approx(1.0)


def test_mapping_table_covers_31_municipalities() -> None:
    codes = {code for code, _ in STATION_TO_MUNICIPALITY.values()}
    assert len(codes) == 31
    assert all(code.startswith("41") and len(code) == 5 for code in codes)
