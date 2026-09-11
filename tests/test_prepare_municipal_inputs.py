from __future__ import annotations

import pytest

from scripts.prepare_municipal_inputs import (
    aliases_for,
    build_ambulance_rows,
    build_demand_rows,
    municipality_from_address,
    municipality_stem,
    name_similarity,
    parse_population_label,
)

JEONBUK = aliases_for("전북특별자치도")
POPULATION_COLUMN = "2026년08월_총인구수"
HOUSEHOLD_COLUMN = "2026년08월_세대수"


def _population_row(label: str, population: str, households: str) -> dict[str, str]:
    return {"행정구역": label, POPULATION_COLUMN: population, HOUSEHOLD_COLUMN: households}


def _population_fixture() -> list[dict[str, str]]:
    """A miniature of the 행정안전부 file: province, municipality, district, 행정동."""
    return [
        _population_row("전북특별자치도  (5200000000)", "1,716,965", "830,000"),
        _population_row("전북특별자치도 전주시 (5211000000)", "618,908", "297,574"),
        _population_row("전북특별자치도 전주시 완산구 (5211100000)", "312,490", "152,917"),
        _population_row("전북특별자치도 전주시 완산구 중앙동(5211151000)", "9,374", "4,791"),
        _population_row("전북특별자치도 전주시 덕진구 (5211300000)", "306,418", "144,657"),
        _population_row("전북특별자치도 전주시 덕진구 금암동(5211357500)", "17,891", "11,799"),
        _population_row("전북특별자치도 익산시 (5214000000)", "260,000", "125,000"),
        _population_row("전북특별자치도 익산시 모현동(5214051000)", "20,000", "9,000"),
    ]


def _fleet_fixture() -> list[dict[str, str]]:
    return [
        {"시도": "전북", "소방서": "전주완산소방서", "안전센터": "효자119안전센터", "지역대": "", "수량": "2"},
        # 2023년 안전센터 명단에 없는 신설 센터. 소방서 이름으로 배정되어야 한다.
        {"시도": "전북", "소방서": "전주완산소방서", "안전센터": "서신119안전센터", "지역대": "", "수량": "1"},
        # 소방서 이름에 시·군명이 없지만 관할 센터 주소가 모두 전주시이므로 추론된다.
        {"시도": "전북", "소방서": "덕진소방서", "안전센터": "조촌119안전센터", "지역대": "", "수량": "1"},
        {"시도": "전북", "소방서": "임실소방서", "안전센터": "임실119안전센터", "지역대": "", "수량": "1"},
        {"시도": "경기", "소방서": "수원소방서", "안전센터": "영통119안전센터", "지역대": "", "수량": "1"},
    ]


def _center_fixture() -> list[dict[str, str]]:
    return [
        {"시도본부": "전라북도", "소방서": "전주완산소방서", "119안전센터명": "효자119안전센터",
         "주소": "전라북도 전주시 완산구 거마평로 73"},
        {"시도본부": "전라북도", "소방서": "전주완산소방서", "119안전센터명": "임실119안전센터",
         "주소": "전라북도 임실군 임실읍 감천로 33"},
        {"시도본부": "전라북도", "소방서": "덕진소방서", "119안전센터명": "금암119안전센터",
         "주소": "전라북도 전주시 덕진구 백제대로 611"},
        {"시도본부": "전라북도", "소방서": "임실소방서", "119안전센터명": "임실119안전센터",
         "주소": "전라북도 임실군 임실읍 감천로 33"},
        {"시도본부": "경기도", "소방서": "수원소방서", "119안전센터명": "영통119안전센터",
         "주소": "경기도 수원시 영통구 매영로 325"},
    ]


def _build_demand(**overrides):
    keywords = dict(
        province="전북특별자치도",
        municipality="전주시",
        municipality_code="52110",
        population_prefixes=JEONBUK["population_prefixes"],
        population_column=POPULATION_COLUMN,
        household_column=HOUSEHOLD_COLUMN,
    )
    keywords.update(overrides)
    return build_demand_rows(_population_fixture(), **keywords)


def _build_ambulances(**overrides):
    keywords = dict(
        municipality="전주시",
        fleet_provinces=JEONBUK["fleet_provinces"],
        center_provinces=JEONBUK["center_provinces"],
        address_prefixes=JEONBUK["address_prefixes"],
    )
    keywords.update(overrides)
    return build_ambulance_rows(_fleet_fixture(), _center_fixture(), **keywords)


def test_parse_population_label_splits_code_and_name():
    code, name, parts = parse_population_label("전북특별자치도 전주시 완산구 중앙동(5211151000)")
    assert code == "5211151000"
    assert name == "전북특별자치도 전주시 완산구 중앙동"
    assert parts[-1] == "중앙동"


def test_parse_population_label_rejects_missing_code():
    with pytest.raises(ValueError):
        parse_population_label("전북특별자치도 전주시 완산구 중앙동")


def test_municipality_from_address_accepts_old_and_new_province_names():
    prefixes = JEONBUK["address_prefixes"]
    assert municipality_from_address("전라북도 전주시 완산구 거마평로 73", prefixes) == "전주시"
    assert municipality_from_address("전북특별자치도 전주시 덕진구 건지로 20", prefixes) == "전주시"
    assert municipality_from_address("전라북도 임실군 임실읍 감천로 33", prefixes) == "임실군"
    assert municipality_from_address("경기도 수원시 영통구 매영로 325", prefixes) is None


def test_municipality_stem_and_name_similarity():
    assert municipality_stem("전주시") == "전주"
    assert municipality_stem("완주군") == "완주"
    assert name_similarity("대자인병원", "대자인병원") == 1.0
    assert name_similarity("전북대학교병원", "원광대학교병원") < 1.0


def test_build_demand_rows_keeps_only_target_administrative_dong():
    rows, total = _build_demand()
    assert total == 618908
    assert [row["administrative_code"] for row in rows] == ["5211151000", "5211357500"]
    assert rows[0]["administrative_name"] == "전북특별자치도 전주시 완산구 중앙동"
    assert rows[0]["municipality_code"] == "52110"
    assert rows[0]["population"] == 9374
    assert rows[0]["households"] == 4791
    assert rows[0]["geocoding_status"] == "pending_kakao_api"
    assert rows[0]["demand_rate_status"] == "pending_119_activity_api"


def test_build_demand_rows_fails_loudly_on_unknown_municipality():
    with pytest.raises(ValueError, match="no 행정동 rows"):
        _build_demand(municipality="정읍시")


def test_build_demand_rows_fails_loudly_on_renamed_province():
    with pytest.raises(ValueError, match="전라북도"):
        _build_demand(population_prefixes=["전라북도"])


def test_build_ambulance_rows_assigns_by_address_then_by_station():
    rows, unresolved = _build_ambulances()
    assert unresolved == []
    assert [(row["base_name"], row["municipality_assignment"]) for row in rows] == [
        ("효자119안전센터", "address_exact"),
        ("서신119안전센터", "official_fire_station_name_inferred"),
        ("조촌119안전센터", "fire_station_address_inferred"),
    ]
    assert sum(int(row["ambulance_count"]) for row in rows) == 4
    assert rows[0]["address"] == "전라북도 전주시 완산구 거마평로 73"
    assert rows[1]["address"] == ""  # 2023년 명단에 없으므로 주소를 지어내지 않는다
    assert rows[0]["source_province"] == "전북"


def test_build_ambulance_rows_fails_loudly_on_wrong_fleet_province_label():
    with pytest.raises(ValueError, match="소방청 구급차 정보 시도"):
        _build_ambulances(fleet_provinces=["전북특별자치도"])


def test_build_ambulance_rows_fails_loudly_on_wrong_center_province_label():
    with pytest.raises(ValueError, match="소방청 119안전센터 현황 시도본부"):
        _build_ambulances(center_provinces=["전북특별자치도"])


def test_build_ambulance_rows_fails_loudly_when_municipality_absent():
    with pytest.raises(ValueError, match="no ambulance rows"):
        _build_ambulances(municipality="군산시")


def test_aliases_for_rejects_unknown_province():
    with pytest.raises(ValueError, match="unknown province"):
        aliases_for("전라북도")
