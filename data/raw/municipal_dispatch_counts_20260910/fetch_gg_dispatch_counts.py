# -*- coding: utf-8 -*-
"""경기데이터드림 '구급활동 현황' Sheet 원자료를 시·군별로 받아 집계표로 저장한다.

원자료는 구급 출동 1건 = 1행이며 전 31개 시·군 합계가 약 90만 행, 약 200MB다.
저장소에 원본 JSON을 그대로 두지 않기 위해
(집계년도, 시군코드, 시군명, 출동소방서, 출동안전센터) 단위 건수로 집계해 CSV로 남긴다.
검증을 위해 시·군별 응답 바이트 수와 응답 본문 SHA-256을 manifest에 함께 기록한다.

재현:
    python data/raw/municipal_dispatch_counts_20260910/fetch_gg_dispatch_counts.py
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys
import time

import requests

INF_ID = "SE00GA6F273B8PIJ9N8412495661"
LANDING = (
    "https://data.gg.go.kr/portal/data/service/selectServicePage.do"
    "?infId=%s&infSeq=1" % INF_ID
)
SHEET_URL = "https://data.gg.go.kr/portal/data/sheet/searchSheetData.do"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
OUT_DIR = pathlib.Path(__file__).resolve().parent
COUNTS_CSV = OUT_DIR / "gg_ambulance_activity_counts_by_station_2025.csv"
MANIFEST = OUT_DIR / "manifest.json"

# 교차검증용 2차 출처: 소방청 119구급서비스 통계연보(시·도 단위)
NFA_YEARBOOK_VIEW_URL = (
    "https://www.nfa.go.kr/nfa/releaseinformation/statisticalinformation/main/"
    "?boardId=bbs_0000000000000019&mode=view&cntId=71"
)
NFA_YEARBOOK_FILE_URL = (
    "https://www.nfa.go.kr/board/file/bbs_0000000000000019/71/"
    "FILE_000000000024779/202506271741134568"
)
NFA_YEARBOOK_PDF = OUT_DIR / "nfa_119_ems_service_statistical_yearbook_2025.pdf"


def open_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    landing = session.get(LANDING, timeout=60)
    landing.raise_for_status()
    token = re.search(r'name="CSRFToken"\s+value="([^"]*)"', landing.text)
    if token is None:
        raise SystemExit("CSRFToken을 랜딩 페이지에서 찾지 못했다.")
    return session, token.group(1)


def fetch_sigun(session: requests.Session, token: str, code: str) -> tuple[dict, bytes]:
    response = session.post(
        SHEET_URL,
        data={
            "infId": INF_ID,
            "infSeq": "1",
            "SIGUN_CD": code,
            "CSRFToken": token,
            "_csrf": token,
        },
        headers={"Referer": LANDING, "X-Requested-With": "XMLHttpRequest"},
        timeout=600,
    )
    response.raise_for_status()
    if "json" not in (response.headers.get("Content-Type") or ""):
        raise SystemExit("시군 %s 응답이 JSON이 아니다: %s" % (code, response.headers))
    return response.json(), response.content


def fetch_nfa_yearbook(session: requests.Session) -> dict:
    """소방청 119구급서비스 통계연보 PDF를 받아 시·도별 경기 행을 교차검증용으로 뽑는다."""
    response = session.get(
        NFA_YEARBOOK_FILE_URL, headers={"Referer": NFA_YEARBOOK_VIEW_URL}, timeout=600
    )
    response.raise_for_status()
    body = response.content
    if body[:4] != b"%PDF":
        raise SystemExit("소방청 통계연보 응답이 PDF가 아니다: %r" % body[:80])
    NFA_YEARBOOK_PDF.write_bytes(body)

    gyeonggi_row = None
    try:
        import fitz  # PyMuPDF, 이미 설치된 라이브러리만 사용한다.
    except ImportError:
        pass
    else:
        with fitz.open(NFA_YEARBOOK_PDF) as document:
            for index in range(document.page_count):
                lines = [l.strip() for l in document[index].get_text().split("\n") if l.strip()]
                if "시․도별 현황" not in lines and "시도별 현황" not in lines:
                    continue
                for position, line in enumerate(lines):
                    if line == "경기":
                        numbers = [
                            v.replace(",", "")
                            for v in lines[position + 1 : position + 8]
                            if re.fullmatch(r"[\d,]+", v)
                        ]
                        if len(numbers) >= 3:
                            gyeonggi_row = {
                                "page": index + 1,
                                "dispatches": int(numbers[0]),
                                "transports": int(numbers[1]),
                                "transported_patients": int(numbers[2]),
                            }
                            break
                if gyeonggi_row:
                    break

    return {
        "id": "nfa_ems_service_statistical_yearbook",
        "title": "소방청 2025년도 119구급서비스 통계연보(최종) (2024.12.31. 기준)",
        "publisher": "소방청",
        "landing_url": NFA_YEARBOOK_VIEW_URL,
        "source_url": NFA_YEARBOOK_FILE_URL,
        "request_method": "GET",
        "open_api_key_used": False,
        "login_required": False,
        "downloaded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "path": NFA_YEARBOOK_PDF.name,
        "format": "PDF",
        "encoding": None,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "row_count_excluding_header": None,
        "gyeonggi_province_row": gyeonggi_row,
        "notes": [
            "이 자료는 시·도(본부) 단위이며 시·군 단위가 아니다. 경기데이터드림 시·군별 집계의 총량 교차검증용으로만 사용한다.",
            "PyMuPDF로 '시․도별 현황' 표의 경기 행만 추출했다. 나머지 표는 수동 확인이 필요하다.",
        ],
    }


def main() -> int:
    session, token = open_session()
    meta = session.get(
        "https://data.gg.go.kr/portal/data/sheet/selectSheetMeta.do",
        params={"infId": INF_ID, "infSeq": "1"},
        headers={"Referer": LANDING, "X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    ).json()
    sigun_filter = next(f for f in meta["filters"] if f["srcColId"] == "SIGUN_CD")
    options = sorted(sigun_filter["options"], key=lambda o: o["code"])
    print("시군 필터 수:", len(options))

    counts: dict[tuple[str, str, str, str, str], list[int]] = collections.OrderedDict()
    per_sigun = []
    started = dt.datetime.now().astimezone()
    for option in options:
        code, name = option["code"], option["name"]
        t0 = time.time()
        payload, raw = fetch_sigun(session, token, code)
        rows = payload["data"]
        if payload["total"] != len(rows):
            raise SystemExit(
                "시군 %s total(%s) != len(data)(%s)" % (code, payload["total"], len(rows))
            )
        names = set()
        transported = 0
        for row in rows:
            names.add(row["sigun_nm"])
            key = (
                str(row["sum_yr"]),
                code,
                row["sigun_nm"],
                row["gout_firesttn_nm"] or "",
                row["gout_safe_center_nm"] or "",
            )
            slot = counts.setdefault(key, [0, 0])
            slot[0] += 1
            if row["patnt_sex_div_nm"]:
                slot[1] += 1
                transported += 1
        per_sigun.append(
            {
                "sigun_code": code,
                "sigun_name": name,
                "row_count": len(rows),
                "rows_with_patient_sex": transported,
                "sigun_name_values_in_rows": sorted(names),
                "years_in_rows": sorted({str(r["sum_yr"]) for r in rows}),
                "response_bytes": len(raw),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "elapsed_seconds": round(time.time() - t0, 2),
            }
        )
        print(
            "%s %-6s rows=%-7d bytes=%-10d %.1fs"
            % (code, name, len(rows), len(raw), time.time() - t0)
        )

    with COUNTS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sum_yr",
                "sigun_cd",
                "sigun_nm",
                "gout_firesttn_nm",
                "gout_safe_center_nm",
                "dispatch_rows",
                "rows_with_patient_sex",
            ]
        )
        for key, slot in counts.items():
            writer.writerow(list(key) + slot)

    digest = hashlib.sha256(COUNTS_CSV.read_bytes()).hexdigest()
    total_rows = sum(item["row_count"] for item in per_sigun)
    years = sorted({key[0] for key in counts})
    datasets = [
        {
            "id": "gg_ambulance_activity_counts_by_station",
            "title": "경기데이터드림 구급활동 현황 (Sheet, 시군코드 필터 전수 수집)",
            "publisher": "경기도소방재난본부 / 경기데이터드림(경기도)",
            "landing_url": LANDING,
            "source_url": SHEET_URL,
            "request_method": "POST",
            "request_params": {
                "infId": INF_ID,
                "infSeq": "1",
                "SIGUN_CD": "<31개 시군코드 각각>",
                "CSRFToken": "<랜딩 페이지에서 추출>",
            },
            "open_api_key_used": False,
            "login_required": False,
            "downloaded_at": started.isoformat(timespec="seconds"),
            "path": COUNTS_CSV.name,
            "format": "CSV",
            "encoding": "UTF-8",
            "sha256": digest,
            "row_count_excluding_header": len(counts),
            "raw_record_grain": "구급 출동 1건 = 1행",
            "raw_row_total": total_rows,
            "years": years,
            "columns_in_source": [c["Header"] for c in meta["columns"]],
            "notes": [
                "원본 응답은 시·군별 JSON이며 31개 합계 %d행이다. 저장소 용량 때문에 원본 JSON은 보관하지 않고 (집계년도, 시군, 출동소방서, 출동안전센터) 건수로 집계해 저장했다." % total_rows,
                "검증용으로 시·군별 응답 바이트 수와 응답 본문 SHA-256을 per_sigun에 기록했다. 같은 스크립트를 재실행하면 동일한 집계가 나온다.",
                "dispatch_rows = 해당 안전센터의 구급활동 행 수(= 출동건수). rows_with_patient_sex = 환자성별 값이 있는 행 수로, 환자 접촉/이송이 있었던 건의 하한 추정치다.",
                "Sheet 탭은 최신 1개년만 제공한다. 수집 시점의 집계년도는 %s이다." % years,
                "원본 시군코드 오류: SIGUN_CD=41360(남양주시)로 조회하면 0행이고, SIGUN_CD=41630(양주시)에 남양주소방서 행이 함께 들어 있다. 따라서 시군코드가 아니라 출동소방서명을 기준으로 시·군에 배정해야 한다.",
            ],
            "sigun_code_anomaly": {
                "empty_sigun_codes": [
                    x["sigun_code"] for x in per_sigun if x["row_count"] == 0
                ],
                "description": "41360(남양주시) 응답이 0행이며 남양주소방서 행이 41630(양주시) 응답에 포함되어 있다. 후처리는 gout_firesttn_nm 기준 매핑을 사용한다.",
            },
            "per_sigun": per_sigun,
        }
    ]
    datasets.append(fetch_nfa_yearbook(session))
    MANIFEST.write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "created_at": started.isoformat(timespec="seconds"),
                "purpose": "경기도 시·군별 119 구급 출동건수 원자료와 교차검증 자료",
                "datasets": datasets,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("집계 행 수:", len(counts), "원본 행 합계:", total_rows)
    print("저장:", COUNTS_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
