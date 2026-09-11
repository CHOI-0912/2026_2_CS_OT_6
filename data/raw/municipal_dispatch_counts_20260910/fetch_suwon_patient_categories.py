# -*- coding: utf-8 -*-
"""수원시(41110) 구급활동 원자료에서 환자 증상·발생장소·연령대 분포를 집계한다.

경기데이터드림 `구급활동 현황` Sheet에서 수원시 행만 다시 받아
(1) `환자증상1`(patnt_symptms_type) 값별 건수와 비율,
(2) `구급처종명`(relif_occurplc_type) 값별 건수와 비율,
(3) 환자연령 10세 단위 구간별 건수를 집계한다.

원본 응답은 약 20MB이고 그대로 저장하지 않는다. 집계 결과만
`data/processed/calibration/suwon_patient_category_shares_2025.csv`에 저장하고,
검증을 위해 응답 바이트 수와 응답 본문 SHA-256, 원본 행 수를 매니페스트에 기록한다.

재현:
    python data/raw/municipal_dispatch_counts_20260910/fetch_suwon_patient_categories.py
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

SUWON_CODE = "41110"
SUWON_STATIONS = ("수원소방서", "수원남부소방서")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "data" / "processed" / "calibration"
OUT_CSV = OUT_DIR / "suwon_patient_category_shares_2025.csv"
OUT_MANIFEST = OUT_DIR / "suwon_patient_category_shares_2025.manifest.json"

NULL_LABEL = "<미기재>"

# 모델의 4개 환자 유형으로 묶는 제안이다. 확정된 분류가 아니다.
MODEL_TYPE_PROPOSAL: dict[str, str] = {
    # cardiac: 심정지와 심혈관 계열 증상
    "심정지": "cardiac",
    "흉통": "cardiac",
    "가슴불편감": "cardiac",
    "심계항진": "cardiac",
    "실신": "cardiac",
    # stroke: 뇌혈관·신경 계열 증상
    "의식장애": "stroke",
    "마비": "stroke",
    "경련/발작": "stroke",
    # trauma: 외상 계열 증상
    "열상": "trauma",
    "찰과상": "trauma",
    "타박상": "trauma",
    "골절": "trauma",
    "염좌": "trauma",
    "탈구": "trauma",
    "화상": "trauma",
    "절단": "trauma",
    "압궤손상": "trauma",
    "그 밖의출혈": "trauma",
    "기타이물질": "trauma",
    "기도이물": "trauma",
    "저체온증": "trauma",
}


def open_session() -> tuple[requests.Session, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    landing = session.get(LANDING, timeout=60)
    landing.raise_for_status()
    token = re.search(r'name="CSRFToken"\s+value="([^"]*)"', landing.text)
    if token is None:
        raise SystemExit("CSRFToken을 랜딩 페이지에서 찾지 못했다.")
    return session, token.group(1)


def age_band(age: float | None) -> str:
    if age is None:
        return NULL_LABEL
    value = int(age)
    if value < 0 or value > 120:
        return "이상치(0세 미만 또는 120세 초과)"
    if value >= 100:
        return "100세 이상"
    return "%d-%d세" % (value // 10 * 10, value // 10 * 10 + 9)


def base_place(value: str | None) -> str:
    """`도로외교통지역(인도)`처럼 괄호로 자유 기술이 붙은 값에서 기본 분류만 남긴다."""
    if value is None:
        return NULL_LABEL
    return re.sub(r"\s*\(.*$", "", value).strip() or NULL_LABEL


def model_type(symptom: str | None) -> str:
    if symptom is None:
        return NULL_LABEL
    return MODEL_TYPE_PROPOSAL.get(symptom, "minor")


def counter_rows(
    label: str, counter: collections.Counter, total: int
) -> list[list[object]]:
    non_null = total - counter.get(NULL_LABEL, 0)
    rows = []
    for value, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        share_non_null = "" if value == NULL_LABEL or non_null == 0 else round(count / non_null * 100, 4)
        rows.append(
            [
                label,
                value,
                count,
                round(count / total * 100, 4),
                share_non_null,
            ]
        )
    return rows


def main() -> int:
    session, token = open_session()
    started = dt.datetime.now().astimezone()
    response = session.post(
        SHEET_URL,
        data={
            "infId": INF_ID,
            "infSeq": "1",
            "SIGUN_CD": SUWON_CODE,
            "CSRFToken": token,
            "_csrf": token,
        },
        headers={"Referer": LANDING, "X-Requested-With": "XMLHttpRequest"},
        timeout=600,
    )
    response.raise_for_status()
    if "json" not in (response.headers.get("Content-Type") or ""):
        raise SystemExit("응답이 JSON이 아니다: %s" % response.headers)
    raw = response.content
    payload = response.json()
    all_rows = payload["data"]
    if payload["total"] != len(all_rows):
        raise SystemExit(
            "total(%s) != len(data)(%s)" % (payload["total"], len(all_rows))
        )

    # 남양주·양주 사례처럼 시군코드를 신뢰하지 않고 출동소방서명으로 다시 걸러낸다.
    rows = [r for r in all_rows if r["gout_firesttn_nm"] in SUWON_STATIONS]
    dropped = len(all_rows) - len(rows)
    years = sorted({str(r["sum_yr"]) for r in rows})
    if len(years) != 1:
        raise SystemExit("집계년도가 하나가 아니다: %s" % ", ".join(years))
    total = len(rows)
    if total == 0:
        raise SystemExit("수원 소방서 행이 하나도 없다.")

    symptoms = collections.Counter(
        (r["patnt_symptms_type"] or NULL_LABEL) for r in rows
    )
    places_raw = collections.Counter(
        (r["relif_occurplc_type"] or NULL_LABEL) for r in rows
    )
    places_base = collections.Counter(base_place(r["relif_occurplc_type"]) for r in rows)
    ages = collections.Counter(age_band(r["patnt_age"]) for r in rows)
    proposal = collections.Counter(model_type(r["patnt_symptms_type"]) for r in rows)
    by_station = collections.Counter(r["gout_firesttn_nm"] for r in rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "category_type",
                "category_value",
                "dispatches",
                "share_of_all_rows_pct",
                "share_of_recorded_rows_pct",
            ]
        )
        for label, counter in (
            ("patnt_symptms_type", symptoms),
            ("model_type_proposal", proposal),
            ("relif_occurplc_type_base", places_base),
            ("relif_occurplc_type_raw", places_raw),
            ("patnt_age_band_10y", ages),
        ):
            writer.writerows(counter_rows(label, counter, total))

    numeric_ages = [r["patnt_age"] for r in rows if r["patnt_age"] is not None]
    manifest = {
        "manifest_version": 1,
        "title": "수원시 구급활동 환자 증상·발생장소·연령대 분포 (2025년 집계)",
        "publisher": "경기도소방재난본부 / 경기데이터드림(경기도)",
        "landing_url": LANDING,
        "source_url": SHEET_URL,
        "request_method": "POST",
        "request_params": {
            "infId": INF_ID,
            "infSeq": "1",
            "SIGUN_CD": SUWON_CODE,
            "CSRFToken": "<랜딩 페이지에서 추출>",
        },
        "open_api_key_used": False,
        "login_required": False,
        "downloaded_at": started.isoformat(timespec="seconds"),
        "generator": "data/raw/municipal_dispatch_counts_20260910/fetch_suwon_patient_categories.py",
        "response_bytes": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_row_count": len(all_rows),
        "rows_used_after_station_filter": total,
        "rows_dropped_by_station_filter": dropped,
        "station_filter": list(SUWON_STATIONS),
        "rows_by_station": dict(by_station),
        "source_year": years[0],
        "output_csv": OUT_CSV.name,
        "output_csv_sha256": hashlib.sha256(OUT_CSV.read_bytes()).hexdigest(),
        "distinct_values": {
            "patnt_symptms_type": len(symptoms),
            "relif_occurplc_type_raw": len(places_raw),
            "relif_occurplc_type_base": len(places_base),
            "patnt_age_band_10y": len(ages),
        },
        "missing_value_counts": {
            "patnt_symptms_type": symptoms.get(NULL_LABEL, 0),
            "relif_occurplc_type": places_raw.get(NULL_LABEL, 0),
            "patnt_age": ages.get(NULL_LABEL, 0),
        },
        "patnt_age_range": {
            "min": min(numeric_ages) if numeric_ages else None,
            "max": max(numeric_ages) if numeric_ages else None,
            "recorded_count": len(numeric_ages),
        },
        "model_type_proposal": MODEL_TYPE_PROPOSAL,
        "notes": [
            "원본 응답 약 20MB는 저장하지 않았다. 응답 바이트 수와 SHA-256으로 재현을 검증한다.",
            "share_of_all_rows_pct는 전체 %d행 기준 비율이고, share_of_recorded_rows_pct는 해당 열에 값이 기재된 행만을 분모로 삼은 비율이다." % total,
            "relif_occurplc_type_raw는 `도로외교통지역(인도)`처럼 괄호 안에 자유 기술이 붙은 값을 그대로 보존한 분포다. relif_occurplc_type_base는 괄호 앞 기본 분류만 남긴 분포다.",
            "model_type_proposal은 모델의 4개 환자 유형(cardiac, stroke, trauma, minor)으로 묶어 본 제안이며 확정된 분류가 아니다. MODEL_TYPE_PROPOSAL에 없는 증상은 모두 minor로 처리했다.",
            "이 데이터셋에는 신고시각이나 출동시각 같은 시간대 열이 없다. 시간대별 수요 곡선은 이 자료로 만들 수 없다.",
            "이 데이터셋에는 사고종별(교통사고·추락 등) 열도 없다. trauma 비율은 외상 계열 증상만으로 추정한 값이므로 실제보다 낮게 나올 수 있다.",
        ],
    }
    OUT_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("수원 원본 행:", len(all_rows), "소방서 필터 통과:", total, "제외:", dropped)
    print("증상 구분 수:", len(symptoms), "발생장소 원값 수:", len(places_raw))
    print("증상 미기재:", symptoms.get(NULL_LABEL, 0), "연령 미기재:", ages.get(NULL_LABEL, 0))
    print("4개 유형 제안 분포:", dict(proposal.most_common()))
    print("저장:", OUT_CSV)
    print("저장:", OUT_MANIFEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
