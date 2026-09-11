"""Extract 전주시 119 구급 출동건수 from the NFA EMS statistical yearbook PDF.

The yearbook's 부록 `시·도 소방기관별 구급활동 현황` lists every 소방서 and
119안전센터/지역대 nationwide.  전주시 is covered exactly by 전주완산소방서 and
전주덕진소방서, so summing their centers gives an observed municipal count rather than
a population-proportional estimate.

Run from the project root:

    python data/raw/jeonju_sources_20260911/extract_jeonju_dispatch_counts.py
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fitz  # PyMuPDF

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF = PROJECT_ROOT / "data/raw/municipal_dispatch_counts_20260910/nfa_119_ems_service_statistical_yearbook_2025.pdf"
RAW_OUT = Path(__file__).resolve().parent / "nfa_yearbook_2025_jeonbuk_station_dispatches.csv"
CALIBRATION = PROJECT_ROOT / "data/processed/calibration"
LANDING_URL = "https://www.nfa.go.kr/nfa/releaseinformation/statisticalinformation/main/?boardId=bbs_0000000000000019&mode=view&cntId=71"
FILE_URL = "https://www.nfa.go.kr/board/file/bbs_0000000000000019/71/FILE_000000000024779/202506271741134568"
JEONJU_STATIONS = ("전주완산소방서", "전주덕진소방서")
REFERENCE_YEAR = 2024
DAYS_IN_YEAR = 366  # 2024 is a leap year
PROVINCE_TOTAL = {"출동건수": 151304, "이송건수": 78765, "이송인원": 79661}  # 시·도별 현황 표, 전북 행


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def integer(value: str) -> int:
    return int(value.replace(",", "").strip())


def extract_station_rows(pdf: Path) -> list[dict[str, object]]:
    """Return every 전북 소방기관 row of the appendix table."""
    document = fitz.open(pdf)
    rows: list[dict[str, object]] = []
    for number in range(document.page_count):
        page = document.load_page(number)
        for table in page.find_tables().tables:
            for cells in table.extract():
                values = ["" if cell is None else " ".join(str(cell).split()) for cell in cells]
                if len(values) != 7 or not values[1].endswith(("소방서", "소방본부")):
                    continue
                station = values[1]
                unit = (values[2] + values[3]).strip()
                try:
                    dispatches = integer(values[4])
                    transports = integer(values[5])
                except ValueError:
                    continue
                rows.append({
                    "pdf_page_index_zero_based": number,
                    "fire_station": station,
                    "unit": unit,
                    "dispatches": dispatches,
                    "transports": transports,
                })
    document.close()
    return rows


def main() -> int:
    if not PDF.is_file():
        raise SystemExit(f"missing official yearbook PDF: {PDF}")
    rows = extract_station_rows(PDF)
    jeonju = [row for row in rows if row["fire_station"] in JEONJU_STATIONS]
    if not jeonju:
        raise SystemExit("no 전주완산/전주덕진 rows found; the yearbook layout changed")
    stations = {row["fire_station"] for row in jeonju}
    missing = set(JEONJU_STATIONS) - stations
    if missing:
        raise SystemExit(f"yearbook is missing {sorted(missing)}")

    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    with RAW_OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["pdf_page_index_zero_based", "fire_station", "unit", "dispatches", "transports"]
        )
        writer.writeheader()
        writer.writerows(sorted(jeonju, key=lambda row: (row["fire_station"], row["unit"])))

    annual = sum(int(row["dispatches"]) for row in jeonju)
    transports = sum(int(row["transports"]) for row in jeonju)
    daily = round(annual / DAYS_IN_YEAR, 4)
    by_station = {
        station: sum(int(row["dispatches"]) for row in jeonju if row["fire_station"] == station)
        for station in JEONJU_STATIONS
    }

    CALIBRATION.mkdir(parents=True, exist_ok=True)
    output = CALIBRATION / f"municipal_daily_calls_jeonju_{REFERENCE_YEAR}.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "municipality_code", "municipality_name", "annual_dispatches", "daily_calls",
                "source_url", "source_year", "mapping_note",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "municipality_code": "52110",
            "municipality_name": "전주시",
            "annual_dispatches": annual,
            "daily_calls": daily,
            "source_url": LANDING_URL,
            "source_year": REFERENCE_YEAR,
            "mapping_note": "전주완산소방서 + 전주덕진소방서 관할 119안전센터 9개소 합산(관측값, 인구비례 추정 아님)",
        })

    manifest = {
        "generated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
        "generator": "data/raw/jeonju_sources_20260911/extract_jeonju_dispatch_counts.py",
        "source_title": "2025년도 119구급서비스 통계연보(최종), 소방청, 작성기준일 2024-12-31",
        "source_landing_url": LANDING_URL,
        "source_file_url": FILE_URL,
        "source_year": str(REFERENCE_YEAR),
        "days_in_year": DAYS_IN_YEAR,
        "input_path": str(PDF.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "input_sha256": sha256(PDF),
        "input_rows": len(jeonju),
        "output_csv": output.name,
        "municipality_count": 1,
        "annual_dispatches_total": annual,
        "annual_transports_total": transports,
        "station_count": len(JEONJU_STATIONS),
        "dispatches_by_station": by_station,
        "province_total_2024": PROVINCE_TOTAL,
        "jeonju_share_of_province": round(annual / PROVINCE_TOTAL["출동건수"], 6),
        "notes": [
            "daily_calls = annual_dispatches / 366 (2024년은 윤년)",
            "전주시 행은 연보에 따로 없고, 관할이 전주시와 정확히 일치하는 두 소방서의 안전센터 행을 합산한 값이다.",
            "annual_dispatches는 구급 출동건수이며 이송건수·이송인원이 아니다.",
            "연보는 2024년 기준이고 구급차 배치 자료는 2025-06-30 기준이므로 기준일이 서로 다르다.",
        ],
    }
    (CALIBRATION / f"municipal_daily_calls_jeonju_{REFERENCE_YEAR}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"annual": annual, "daily": daily, "by_station": by_station}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
