from __future__ import annotations

from copy import deepcopy
import math
import random
from typing import Mapping

from .model import AmbulanceStatus, Scenario


def randomized_initial_locations(
    scenario: Scenario,
    location_weights: Mapping[str, float],
    *,
    seed: int,
    fixed_ambulance_ids: frozenset[str] = frozenset(),
) -> Scenario:
    """Return a copied scenario with selected idle ambulances randomly located.

    `fixed_ambulance_ids` is intended for the candidate ambulance whose placement
    is being optimized; background-fleet uncertainty can be sampled without
    randomizing the decision variable itself.
    """
    if not location_weights:
        raise ValueError("location_weights must not be empty")
    nodes = set(scenario.network.edges)
    nodes.update(target for neighbors in scenario.network.edges.values() for target in neighbors)
    locations: list[str] = []
    weights: list[float] = []
    for location, raw_weight in location_weights.items():
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"weight for {location!r} must be finite and non-negative")
        if nodes and location not in nodes:
            raise ValueError(f"initial location {location!r} is not in the road network")
        if weight > 0:
            locations.append(location)
            weights.append(weight)
    if not locations:
        raise ValueError("at least one initial location must have positive weight")

    copied = deepcopy(scenario)
    rng = random.Random(seed)
    for ambulance in copied.ambulances.values():
        if ambulance.id in fixed_ambulance_ids:
            continue
        if ambulance.status is not AmbulanceStatus.IDLE:
            continue
        ambulance.location = rng.choices(locations, weights=weights, k=1)[0]
    return copied
