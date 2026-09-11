from __future__ import annotations

import math
from typing import Mapping


def allocate_hourly_demand(
    monthly_calls: float,
    days_in_month: int,
    population: Mapping[str, float],
    elderly_share: Mapping[str, float],
    *,
    elderly_effect: float = 0.0,
    hourly_multipliers: Mapping[int, float] | None = None,
) -> dict[int, dict[str, float]]:
    """Disaggregate one regional monthly total into village/hour Poisson rates.

    Spatial weights are `population * (1 + elderly_effect * elderly_share)`.
    Hourly multipliers are normalized, so the rates integrate back to the input
    monthly call count exactly in expectation.
    """
    if not math.isfinite(monthly_calls) or monthly_calls < 0:
        raise ValueError("monthly_calls must be finite and non-negative")
    if not isinstance(days_in_month, int) or isinstance(days_in_month, bool) or days_in_month < 1:
        raise ValueError("days_in_month must be a positive integer")
    if not math.isfinite(elderly_effect) or elderly_effect < 0:
        raise ValueError("elderly_effect must be finite and non-negative")
    if not population:
        raise ValueError("population must not be empty")

    spatial: dict[str, float] = {}
    for village, raw_population in population.items():
        pop = float(raw_population)
        share = float(elderly_share.get(village, 0.0))
        if not math.isfinite(pop) or pop < 0:
            raise ValueError(f"population for {village!r} must be finite and non-negative")
        if not math.isfinite(share) or share < 0 or share > 1:
            raise ValueError(f"elderly_share for {village!r} must be between 0 and 1")
        spatial[village] = pop * (1.0 + elderly_effect * share)
    spatial_total = sum(spatial.values())
    if spatial_total <= 0:
        raise ValueError("weighted population must be positive")

    multipliers = {hour: 1.0 for hour in range(24)}
    if hourly_multipliers is not None:
        for hour, value in hourly_multipliers.items():
            if not isinstance(hour, int) or isinstance(hour, bool) or hour not in range(24):
                raise ValueError("hourly multiplier keys must be integers from 0 to 23")
            multiplier = float(value)
            if not math.isfinite(multiplier) or multiplier < 0:
                raise ValueError(f"hourly multiplier for {hour} must be finite and non-negative")
            multipliers[hour] = multiplier
    temporal_total = sum(multipliers.values())
    if temporal_total <= 0:
        raise ValueError("at least one hourly multiplier must be positive")

    calls_per_day = monthly_calls / days_in_month
    return {
        hour: {
            village: calls_per_day * (weight / spatial_total) * (multipliers[hour] / temporal_total)
            for village, weight in spatial.items()
        }
        for hour in range(24)
    }


def normalize_profile_counts(counts: Mapping[str, float]) -> dict[str, float]:
    """Convert non-negative patient-type counts to probabilities."""
    if not counts:
        raise ValueError("profile counts must not be empty")
    clean: dict[str, float] = {}
    for profile, raw_count in counts.items():
        count = float(raw_count)
        if not math.isfinite(count) or count < 0:
            raise ValueError(f"count for {profile!r} must be finite and non-negative")
        clean[profile] = count
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("at least one profile count must be positive")
    return {profile: count / total for profile, count in clean.items()}
