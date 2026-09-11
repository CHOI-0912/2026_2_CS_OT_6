from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import heapq
import math


class AmbulanceStatus(str, Enum):
    IDLE = "idle"
    UNAVAILABLE = "unavailable"
    TO_SCENE = "to_scene"
    ON_SCENE = "on_scene"
    TO_HOSPITAL = "to_hospital"
    RESTOCKING = "restocking"
    REPOSITIONING = "repositioning"


class PatientStatus(str, Enum):
    WAITING = "waiting"
    ASSIGNED = "assigned"
    ON_SCENE = "on_scene"
    TRANSPORTING = "transporting"
    COMPLETE = "complete"
    LOST = "lost"


class EventKind(str, Enum):
    INITIAL_AVAILABLE = "initial_available"
    PATIENT_ARRIVAL = "patient_arrival"
    ARRIVE_SCENE = "arrive_scene"
    SCENE_COMPLETE = "scene_complete"
    ARRIVE_HOSPITAL = "arrive_hospital"
    ARRIVE_MORTUARY = "arrive_mortuary"
    HOSPITAL_RELEASE = "hospital_release"
    TRANSFER_READY = "transfer_ready"
    RESTOCK_COMPLETE = "restock_complete"
    REPOSITION_COMPLETE = "reposition_complete"
    SHIFT_CHANGE = "shift_change"


@dataclass(order=True)
class Event:
    time: float
    sequence: int
    kind: EventKind = field(compare=False)
    ambulance_id: str | None = field(default=None, compare=False)
    patient_id: str | None = field(default=None, compare=False)
    hospital_id: str | None = field(default=None, compare=False)


@dataclass
class RoadNetwork:
    """Road graph whose directed edge weights are travel minutes."""

    edges: dict[str, dict[str, float]]
    # Multiplier by hour-of-day (0--23).  An omitted hour has normal travel time.
    # The multiplier is deliberately selected at departure: route choices therefore
    # see the road state that exists when an ambulance starts moving.
    time_multipliers: dict[int, float] = field(default_factory=dict)
    positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    # Single-source shortest paths at multiplier 1.0, built lazily per source.
    # The hourly multiplier scales every edge equally, so shortest paths do not
    # change with the hour and only the total is rescaled.  The signature guards
    # against edges being edited after the first lookup.
    _base_paths: dict[str, dict[str, float]] = field(default_factory=dict, init=False, repr=False, compare=False)
    _base_signature: tuple[int, int] | None = field(default=None, init=False, repr=False, compare=False)

    def _edge_signature(self) -> tuple[int, int]:
        return len(self.edges), sum(len(neighbors) for neighbors in self.edges.values())

    def _shortest_paths_from(self, source: str) -> dict[str, float]:
        signature = self._edge_signature()
        if signature != self._base_signature:
            self._base_paths.clear()
            self._base_signature = signature
        cached = self._base_paths.get(source)
        if cached is not None:
            return cached
        frontier: list[tuple[float, str]] = [(0.0, source)]
        best = {source: 0.0}
        while frontier:
            cost, node = heapq.heappop(frontier)
            if cost != best[node]:
                continue
            for nxt, duration in self.edges.get(node, {}).items():
                if not math.isfinite(duration) or duration < 0:
                    continue
                candidate = cost + duration
                if candidate < best.get(nxt, math.inf):
                    best[nxt] = candidate
                    heapq.heappush(frontier, (candidate, nxt))
        self._base_paths[source] = best
        return best

    def travel_time(self, source: str, target: str, departure_time: float = 0.0) -> float:
        if source == target:
            return 0.0
        hour = int(math.floor(departure_time / 60.0)) % 24
        multiplier = self.time_multipliers.get(hour, 1.0)
        # Invalid input must not create negative Dijkstra edges or a divide-by-zero.
        if not math.isfinite(multiplier) or multiplier <= 0:
            multiplier = 1.0
        base = self._shortest_paths_from(source).get(target, math.inf)
        return base * multiplier if math.isfinite(base) else math.inf


@dataclass(frozen=True)
class PatientProfile:
    name: str
    golden_minutes: float
    decay_rate: float
    scene_minutes: float
    field_success: float = 0.0

    def survival(self, elapsed_minutes: float) -> float:
        """Survival probability since onset; 1 through golden time then exponential."""
        after_golden = max(0.0, elapsed_minutes - self.golden_minutes)
        return math.exp(-self.decay_rate * after_golden)


@dataclass
class Patient:
    id: str
    location: str
    profile: PatientProfile
    onset_time: float
    status: PatientStatus = PatientStatus.WAITING
    remaining_mass: float = 1.0
    last_survival_time: float = 0.0
    assigned_ambulance: str | None = None
    visited_hospitals: set[str] = field(default_factory=set)
    transfers: int = 0
    saved_mass: float = 0.0
    lost_mass: float = 0.0
    scene_arrival_time: float | None = None
    response_minutes: float | None = None
    scene_time_minutes: float = 0.0
    transport_minutes: float = 0.0
    transport_legs: int = 0
    transport_departure_time: float | None = None

    def advance_survival(self, now: float) -> float:
        """Apply only the new interval's decay, never the whole curve twice."""
        prior_elapsed = max(0.0, self.last_survival_time - self.onset_time)
        current_elapsed = max(0.0, now - self.onset_time)
        prior = self.profile.survival(prior_elapsed)
        current = self.profile.survival(current_elapsed)
        self.remaining_mass *= current / prior if prior else 0.0
        self.last_survival_time = now
        return self.remaining_mass


@dataclass
class Hospital:
    id: str
    location: str
    capabilities: set[str]
    success_when_available: float
    success_when_unavailable: float = 0.0
    available: bool = True
    restock_capable: bool = True
    transfer_delay_minutes: float = 5.0
    # ``None`` means no capacity limit, preserving the original Hospital API.
    capacity: int | None = None
    occupied: int = 0
    treatment_minutes: float = 0.0
    success_by_profile: dict[str, float] = field(default_factory=dict)
    unavailable_success_by_profile: dict[str, float] = field(default_factory=dict)

    def has_open_bed(self) -> bool:
        return self.available and (self.capacity is None or self.occupied < self.capacity)

    def reserve_bed(self) -> bool:
        if not self.has_open_bed():
            return False
        self.occupied += 1
        return True

    def release_bed(self) -> None:
        if self.occupied > 0:
            self.occupied -= 1

    def treatment_success(self, patient: Patient) -> float:
        if patient.profile.name not in self.capabilities:
            return 0.0
        if self.has_open_bed():
            return self.success_by_profile.get(patient.profile.name, self.success_when_available)
        return self.unavailable_success_by_profile.get(patient.profile.name, self.success_when_unavailable)


@dataclass
class Ambulance:
    id: str
    location: str
    status: AmbulanceStatus = AmbulanceStatus.IDLE
    patient_id: str | None = None
    restock_minutes: float = 8.0
    initial_available_after: float = 0.0
    destination: str | None = None
    movement_started_at: float | None = None
    movement_ends_at: float | None = None
    # Home station of the vehicle; the night-time schedule returns it here.
    home_base: str | None = None
    # Standby post the repositioning decision assigns for the free window.
    assigned_post: str | None = None

    def __post_init__(self) -> None:
        # An ambulance without an explicit home station belongs to the node it
        # starts the episode at, so the default never depends on later movement.
        if self.home_base is None:
            self.home_base = self.location


@dataclass
class Scenario:
    network: RoadNetwork
    villages: dict[str, float]  # mean arrivals per hour
    hospitals: dict[str, Hospital]
    ambulances: dict[str, Ambulance]
    profiles: tuple[PatientProfile, ...]
    standby_nodes: tuple[str, ...]
    horizon_minutes: float = 24 * 60
    max_transfers: int = 2
    # Optional non-homogeneous Poisson inputs.  Values are arrivals/hour and are
    # keyed by hour-of-day then village.  ``villages`` remains the all-hours default.
    hourly_village_rates: dict[int, dict[str, float]] = field(default_factory=dict)
    # Optional profile-name weights/probabilities keyed by hour-of-day.  Missing or
    # invalid rows fall back to the original uniform profile draw.
    hourly_profile_probabilities: dict[int, dict[str, float]] = field(default_factory=dict)
    # New patients arrive only before this cut-off; the episode itself keeps running
    # until ``horizon_minutes`` so patients already in the system can be resolved
    # instead of being written off at the boundary.  None means "same as horizon".
    arrival_cutoff_minutes: float | None = None

    @property
    def arrival_horizon(self) -> float:
        return self.horizon_minutes if self.arrival_cutoff_minutes is None else self.arrival_cutoff_minutes

    def demand_rate(self, village: str, time: float) -> float:
        hour = int(math.floor(time / 60.0)) % 24
        return max(0.0, self.hourly_village_rates.get(hour, {}).get(village, self.villages.get(village, 0.0)))

    def profile_weights(self, time: float) -> dict[str, float]:
        hour = int(math.floor(time / 60.0)) % 24
        return self.hourly_profile_probabilities.get(hour, {})
