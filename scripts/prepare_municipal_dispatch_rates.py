# -*- coding: utf-8 -*-
"""경기도 시·군별 119 구급 출동건수와 일일 신고율을 만든다.

입력: `data/raw/municipal_dispatch_counts_20260910/gg_ambulance_activity_counts_by_station_2025.csv`
      (경기데이터드림 `구급활동 현황` Sheet 전수 수집 결과를 출동소방서·안전센터 단위 건수로 집계한 표)
출력: `data/processed/calibration/municipal_daily_calls_<집계년도>.csv`
      `data/processed/calibration/municipal_daily_calls_<집계년도>.manifest.json`

배정 기준은 원본의 `sigun_cd`가 아니라 `gout_firesttn_nm`(출동소방서)다.
원본 `sigun_cd`에는 남양주 행이 양주시(41630)로 들어가 있는 오류가 있다.
소방서가 2개 이상인 시(수원·성남·고양·용인·평택)는 소방서 건수를 합산한다.
표에 없는 소방서명이 나오면 즉시 실패한다.

사용:
    python scripts/prepare_municipal_dispatch_rates.py
    python scripts/prepare_municipal_dispatch_rates.py --input <csv> --output-dir <dir>
"""
from __future__ import annotations

import argparse
import calendar
import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    REPO_ROOT
    / "data"
    / "raw"
    / "municipal_dispatch_counts_20260910"
    / "gg_ambulance_activity_counts_by_station_2025.csv"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "processed" / "calibration"

SOURCE_LANDING_URL = (
    "https://data.gg.go.kr/portal/data/service/selectServicePage.do"
    "?infId=SE00GA6F273B8PIJ9N8412495661&infSeq=1"
)
SOURCE_REQUEST_URL = "https://data.gg.go.kr/portal/data/sheet/searchSheetData.do"
SOURCE_TITLE = "경기데이터드림 구급활동 현황 (경기도소방재난본부)"

# 출동소방서 → (시·군 코드, 시·군명). 경기데이터드림 원본에 등장하는 36개 소방서 전체.
STATION_TO_MUNICIPALITY: dict[str, tuple[str, str]] = {
    "수원소방서": ("41110", "수원시"),
    "수원남부소방서": ("41110", "수원시"),
    "성남소방서": ("41130", "성남시"),
    "분당소방서": ("41130", "성남시"),
    "의정부소방서": ("41150", "의정부시"),
    "안양소방서": ("41170", "안양시"),
    "부천소방서": ("41190", "부천시"),
    "광명소방서": ("41210", "광명시"),
    "평택소방서": ("41220", "평택시"),
    "송탄소방서": ("41220", "평택시"),
    "동두천소방서": ("41250", "동두천시"),
    "안산소방서": ("41270", "안산시"),
    "고양소방서": ("41280", "고양시"),
    "일산소방서": ("41280", "고양시"),
    "과천소방서": ("41290", "과천시"),
    "구리소방서": ("41310", "구리시"),
    "남양주소방서": ("41360", "남양주시"),
    "오산소방서": ("41370", "오산시"),
    "시흥소방서": ("41390", "시흥시"),
    "군포소방서": ("41410", "군포시"),
    "의왕소방서": ("41430", "의왕시"),
    "하남소방서": ("41450", "하남시"),
    "용인소방서": ("41460", "용인시"),
    "용인서부소방서": ("41460", "용인시"),
    "파주소방서": ("41480", "파주시"),
    "이천소방서": ("41500", "이천시"),
    "안성소방서": ("41550", "안성시"),
    "김포소방서": ("41570", "김포시"),
    "화성소방서": ("41590", "화성시"),
    "광주소방서": ("41610", "광주시"),
    "양주소방서": ("41630", "양주시"),
    "포천소방서": ("41650", "포천시"),
    "여주소방서": ("41670", "여주시"),
    "연천소방서": ("41800", "연천군"),
    "가평소방서": ("41820", "가평군"),
    "양평소방서": ("41830", "양평군"),
}

REQUIRED_COLUMNS = ["sum_yr", "gout_firesttn_nm", "dispatch_rows"]


def read_rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                "입력 파일에 필요한 열이 없다: %s (있는 열: %s)"
                % (", ".join(missing), reader.fieldnames)
            )
        return list(reader)


def aggregate(rows: list[dict[str, str]]) -> tuple[str, dict[str, dict]]:
    if not rows:
        raise SystemExit("입력 파일에 데이터 행이 없다.")

    years = sorted({row["sum_yr"].strip() for row in rows})
    if len(years) != 1:
        raise SystemExit(
            "집계년도가 하나가 아니다: %s. 연도별로 파일을 분리해서 실행해야 한다."
            % ", ".join(years)
        )
    year = years[0]

    unmapped = sorted(
        {
            row["gout_firesttn_nm"].strip()
            for row in rows
            if row["gout_firesttn_nm"].strip() not in STATION_TO_MUNICIPALITY
        }
    )
    if unmapped:
        raise SystemExit(
            "매핑 표에 없는 출동소방서가 있다: %s\n"
            "scripts/prepare_municipal_dispatch_rates.py의 STATION_TO_MUNICIPALITY에 "
            "해당 소방서의 시·군 코드를 명시적으로 추가해야 한다." % ", ".join(unmapped)
        )

    result: dict[str, dict] = {}
    for row in rows:
        station = row["gout_firesttn_nm"].strip()
        code, name = STATION_TO_MUNICIPALITY[station]
        entry = result.setdefault(
            code,
            {
                "municipality_code": code,
                "municipality_name": name,
                "annual_dispatches": 0,
                "stations": collections.Counter(),
            },
        )
        count = int(row["dispatch_rows"])
        entry["annual_dispatches"] += count
        entry["stations"][station] += count
    return year, result


def relative_to_repo(path: pathlib.Path) -> str:
    """저장소 기준 상대 경로로 적되, 저장소 밖의 경로는 그대로 적는다."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def mapping_note(entry: dict) -> str:
    stations = sorted(entry["stations"])
    if len(stations) == 1:
        return "%s 단독 관할" % stations[0]
    return "%s 합산(%d개 소방서)" % (" + ".join(stations), len(stations))


def write_outputs(
    year: str,
    aggregated: dict[str, dict],
    input_path: pathlib.Path,
    input_rows: int,
    output_dir: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    days = 366 if calendar.isleap(int(year)) else 365
    csv_path = output_dir / ("municipal_daily_calls_%s.csv" % year)
    manifest_path = output_dir / ("municipal_daily_calls_%s.manifest.json" % year)

    ordered = sorted(aggregated.values(), key=lambda e: e["municipality_code"])
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "municipality_code",
                "municipality_name",
                "annual_dispatches",
                "daily_calls",
                "source_url",
                "source_year",
                "mapping_note",
            ]
        )
        for entry in ordered:
            writer.writerow(
                [
                    entry["municipality_code"],
                    entry["municipality_name"],
                    entry["annual_dispatches"],
                    round(entry["annual_dispatches"] / days, 4),
                    SOURCE_LANDING_URL,
                    year,
                    mapping_note(entry),
                ]
            )

    manifest = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "scripts/prepare_municipal_dispatch_rates.py",
        "source_title": SOURCE_TITLE,
        "source_landing_url": SOURCE_LANDING_URL,
        "source_request_url": SOURCE_REQUEST_URL,
        "source_year": year,
        "days_in_year": days,
        "input_path": relative_to_repo(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "input_rows": input_rows,
        "output_csv": csv_path.name,
        "municipality_count": len(ordered),
        "annual_dispatches_total": sum(e["annual_dispatches"] for e in ordered),
        "station_count": len({s for e in ordered for s in e["stations"]}),
        "station_to_municipality": {
            station: code for station, (code, _) in STATION_TO_MUNICIPALITY.items()
        },
        "notes": [
            "daily_calls = annual_dispatches / %d (집계년도 %s)" % (days, year),
            "시·군 배정은 원본 sigun_cd가 아니라 출동소방서명 기준이다. 원본 sigun_cd는 남양주 행을 양주시(41630)로 표기하는 오류가 있다.",
            "annual_dispatches는 구급 출동건수(행 수)이며 이송건수·이송인원이 아니다.",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return csv_path, manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    if not args.input.exists():
        raise SystemExit("입력 파일이 없다: %s" % args.input)

    rows = read_rows(args.input)
    year, aggregated = aggregate(rows)
    csv_path, manifest_path = write_outputs(
        year, aggregated, args.input, len(rows), args.output_dir
    )

    total = sum(e["annual_dispatches"] for e in aggregated.values())
    days = 366 if calendar.isleap(int(year)) else 365
    print("집계년도 %s, 시·군 %d개, 연간 출동 %d건, 도 전체 일평균 %.1f건"
          % (year, len(aggregated), total, total / days))
    print("저장:", csv_path)
    print("저장:", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
