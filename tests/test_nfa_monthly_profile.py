from __future__ import annotations

from statistics import fmean

from scripts.build_municipal_placement_inputs import daily_calls_for
from scripts.prepare_nfa_monthly_profile import METRICS, monthly_profile, read_headquarters_rows


def test_monthly_profile_normalizes_each_year_before_averaging() -> None:
    records = []
    for year, scale in ((2022, 10), (2023, 100)):
        for month in range(1, 13):
            records.append({
                "year": year,
                "month": month,
                "dispatches": scale * month,
                "transports": scale * month * 2,
                "patients": scale * month * 3,
            })
    profile = monthly_profile(records)
    assert len(profile) == 12
    for metric in METRICS:
        values = [row[f"{metric}_seasonality_multiplier"] for row in profile]
        assert abs(fmean(values) - 1.0) < 1e-12
    assert profile[0]["dispatches_seasonality_multiplier"] < 1
    assert profile[-1]["dispatches_seasonality_multiplier"] > 1


def test_population_proportional_calls_uses_only_selected_year(tmp_path) -> None:
    source = tmp_path / "nfa.csv"
    source.write_text(
        "년도,월,본부구분,출동건수,이송건수,이송환자수\n"
        "2022,1,경기,999,0,0\n"
        "2023,1,경기,36525,0,0\n"
        "2023,1,서울,99999,0,0\n",
        encoding="cp949",
    )
    # The population-proportional estimate now lives in the placement-input
    # builder, which takes the provincial annual total as an explicit parameter,
    # so the official CSV is aggregated by headquarters and year first.
    annual_dispatches = sum(
        row["dispatches"] for row in read_headquarters_rows(source, "경기") if row["year"] == 2023
    )
    assert annual_dispatches == 36525
    daily_calls, detail, municipality_name = daily_calls_for(
        "41110",
        [
            {"municipality_code": "41110", "municipality_name": "수원시", "population_202608": "100"},
            {"municipality_code": "41130", "municipality_name": "성남시", "population_202608": "900"},
        ],
        {
            "method": "population_proportional_provincial_dispatches",
            "provincial_annual_dispatches": annual_dispatches,
            "year": 2023,
            "provincial_population_reference": "MOIS resident population 2026-08",
            "daily_calls_by_municipality": None,
            "basis": "test fixture",
        },
    )
    assert abs(daily_calls - 10.0) < 1e-12
    assert detail["observed_municipal_calls"] is False
    assert municipality_name == "수원시"
