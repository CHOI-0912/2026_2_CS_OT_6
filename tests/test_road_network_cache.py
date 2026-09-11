"""The per-source shortest-path cache must be invisible except for speed."""

from __future__ import annotations

import heapq
import math

from ambulance_sim.model import RoadNetwork


def _dijkstra(edges: dict[str, dict[str, float]], source: str, target: str, multiplier: float) -> float:
    frontier = [(0.0, source)]
    best = {source: 0.0}
    while frontier:
        cost, node = heapq.heappop(frontier)
        if node == target:
            return cost
        if cost != best[node]:
            continue
        for nxt, duration in edges.get(node, {}).items():
            candidate = cost + duration * multiplier
            if candidate < best.get(nxt, math.inf):
                best[nxt] = candidate
                heapq.heappush(frontier, (candidate, nxt))
    return math.inf


EDGES = {
    "a": {"b": 4.0, "c": 10.0},
    "b": {"c": 3.0, "d": 12.0},
    "c": {"d": 2.0, "a": 10.0},
    "d": {"a": 1.0},
    "e": {},  # unreachable island
}


def test_cached_travel_time_matches_fresh_dijkstra_for_every_pair_and_hour():
    network = RoadNetwork(edges=EDGES, time_multipliers={8: 1.4, 18: 1.6})
    nodes = ["a", "b", "c", "d", "e"]
    for hour in range(24):
        multiplier = network.time_multipliers.get(hour, 1.0)
        for source in nodes:
            for target in nodes:
                expected = 0.0 if source == target else _dijkstra(EDGES, source, target, multiplier)
                actual = network.travel_time(source, target, departure_time=hour * 60.0 + 5)
                # Scaling the summed base path differs from summing scaled edges only by
                # floating-point rounding (about 1e-15), never by route choice.
                assert math.isclose(actual, expected, rel_tol=1e-12) or (math.isinf(actual) and math.isinf(expected)), (source, target, hour)


def test_cache_is_invalidated_when_edges_change_after_first_lookup():
    edges = {"a": {"b": 5.0}, "b": {"a": 5.0}}
    network = RoadNetwork(edges=edges)
    assert network.travel_time("a", "b") == 5.0
    edges["a"]["c"] = 1.0
    edges["c"] = {"b": 1.0}
    assert network.travel_time("a", "b") == 2.0


def test_cache_does_not_leak_into_serialisation_or_equality():
    left = RoadNetwork(edges={"a": {"b": 1.0}})
    right = RoadNetwork(edges={"a": {"b": 1.0}})
    left.travel_time("a", "b")
    assert left == right
    assert "_base_paths" not in repr(left)
