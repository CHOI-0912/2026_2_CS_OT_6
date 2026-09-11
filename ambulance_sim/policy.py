from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterable, Mapping

from .model import Ambulance, Hospital, Patient

if TYPE_CHECKING:
    from .engine import Simulation


class GreedySurvivalPolicy:
    """A transparent centralized policy; actions are selected from global state."""

    def choose_ambulance(self, sim: "Simulation", patient: Patient) -> Ambulance | None:
        idle = [a for a in sim.scenario.ambulances.values() if a.status.value == "idle"]
        reachable = [a for a in idle if math.isfinite(sim.travel(a.location, patient.location))]
        return min(reachable, key=lambda a: sim.travel(a.location, patient.location), default=None)

    def choose_hospital(self, sim: "Simulation", patient: Patient, location: str, now: float) -> Hospital | None:
        choices = [
            h for h in sim.scenario.hospitals.values()
            if h.id not in patient.visited_hospitals
            and h.treatment_success(patient) > 0
            and math.isfinite(sim.travel(location, h.location, now))
        ]
        if not choices:
            return None
        # Value includes the best permitted fallback, so an initial transfer is not ignored.
        return max(choices, key=lambda h: self._route_value(sim, patient, location, now, h.id, set(patient.visited_hospitals), patient.transfers))

    def _route_value(
        self, sim: "Simulation", patient: Patient, location: str, now: float,
        hospital_id: str, visited: set[str], transfers: int,
    ) -> float:
        hospital = sim.scenario.hospitals[hospital_id]
        travel = sim.travel(location, hospital.location, now)
        if math.isinf(travel):
            return -math.inf
        baseline = patient.profile.survival(now - patient.onset_time)
        ratio = patient.profile.survival(now + travel - patient.onset_time) / baseline if baseline else 0.0
        arrived = patient.remaining_mass * ratio
        success = hospital.treatment_success(patient)
        value = arrived * success
        remainder = arrived * (1 - success)
        used = visited | {hospital_id}
        # An admitted patient's outcome is final (engine rule): no downstream value
        # from a later transfer.  Only a refusal (no bed / no capability) continues.
        if patient.profile.name in hospital.capabilities and hospital.has_open_bed():
            return value
        if remainder <= sim.mass_epsilon or transfers >= sim.scenario.max_transfers:
            return value
        next_candidates = [
            h for h in sim.scenario.hospitals.values()
            if h.id not in used
            and h.treatment_success(patient) > 0
            and math.isfinite(sim.travel(hospital.location, h.location, now + travel + hospital.transfer_delay_minutes))
        ]
        if not next_candidates:
            return value
        after_delay = now + travel + hospital.transfer_delay_minutes
        # Recurse with a copy of the unresolved probability at this arrival time.
        original_mass, original_last = patient.remaining_mass, patient.last_survival_time
        arrival_time = now + travel
        survival_at_arrival = patient.profile.survival(arrival_time - patient.onset_time)
        delay_ratio = (
            patient.profile.survival(after_delay - patient.onset_time) / survival_at_arrival
            if survival_at_arrival else 0.0
        )
        patient.remaining_mass, patient.last_survival_time = remainder * delay_ratio, after_delay
        try:
            downstream = max(
                self._route_value(sim, patient, hospital.location, after_delay, nxt.id, used, transfers + 1)
                for nxt in next_candidates
            )
        finally:
            patient.remaining_mass, patient.last_survival_time = original_mass, original_last
        return value + downstream

    def choose_restock_location(self, sim: "Simulation", ambulance: Ambulance) -> str:
        candidates = [h.location for h in sim.scenario.hospitals.values() if h.restock_capable]
        candidates.extend(sim.scenario.standby_nodes)
        reachable = [node for node in candidates if math.isfinite(sim.travel(ambulance.location, node))]
        return min(reachable, key=lambda node: sim.travel(ambulance.location, node), default=ambulance.location)

    def choose_standby_location(self, sim: "Simulation", ambulance: Ambulance) -> str:
        # Demand-rate coverage heuristic.  It remains an explicit centralized action.
        villages = set(sim.scenario.villages)
        for rates in sim.scenario.hourly_village_rates.values():
            villages.update(rates)

        def value(node: str) -> float:
            return sum(
                rate / (1.0 + travel)
                for village in villages
                for rate in (sim.scenario.demand_rate(village, sim.now),)
                if math.isfinite(travel := sim.travel(node, village))
            )
        candidates = [node for node in sim.scenario.standby_nodes if math.isfinite(sim.travel(ambulance.location, node))]
        return max(candidates, key=value, default=ambulance.location)


class NoRepositionPolicy(GreedySurvivalPolicy):
    """Ablation policy: retain survival-aware dispatch/hospital choice but do not reposition."""

    def choose_standby_location(self, sim: "Simulation", ambulance: Ambulance) -> str:
        return ambulance.location


class NearestHospitalPolicy(NoRepositionPolicy):
    """Transparent operational baseline using the nearest usable hospital."""

    def choose_hospital(self, sim: "Simulation", patient: Patient, location: str, now: float) -> Hospital | None:
        choices = [
            hospital for hospital in sim.scenario.hospitals.values()
            if hospital.id not in patient.visited_hospitals
            and hospital.treatment_success(patient) > 0
            and math.isfinite(sim.travel(location, hospital.location, now))
        ]
        return min(
            choices,
            key=lambda hospital: (sim.travel(location, hospital.location, now), hospital.id),
            default=None,
        )


class ScheduledStandbyPolicy(GreedySurvivalPolicy):
    """Deterministic standby posts with a night-time home-station window.

    Dispatch and hospital choice are inherited unchanged, so repositioning stays
    the lowest-priority decision.  During ``home_hours`` an ambulance returns to
    its own station; during the free hours it stands by at the post assigned to
    it, and at its home station when it has no assigned post.  The demand
    coverage heuristic is deliberately not used: the posts are the decision
    variables of the optimizer and must stay reproducible.
    """

    def __init__(self, free_hours: Iterable[int], home_hours: Iterable[int]):
        self.free_hours = self._hours(free_hours, "free_hours")
        self.home_hours = self._hours(home_hours, "home_hours")
        if self.free_hours & self.home_hours:
            raise ValueError(f"free_hours and home_hours overlap: {sorted(self.free_hours & self.home_hours)}")
        if self.free_hours | self.home_hours != frozenset(range(24)):
            raise ValueError("free_hours and home_hours together must cover every hour from 0 to 23")

    @staticmethod
    def _hours(values: Iterable[int], label: str) -> frozenset[int]:
        hours = frozenset(int(hour) for hour in values)
        if not hours or any(not 0 <= hour <= 23 for hour in hours):
            raise ValueError(f"{label} must be a non-empty set of hours from 0 to 23")
        return hours

    def at_home_hour(self, time: float) -> bool:
        return int(math.floor(time / 60.0)) % 24 in self.home_hours

    def schedule_hours(self) -> tuple[int, ...]:
        """Hour boundaries where the window changes; the engine shifts there."""
        return tuple(
            hour for hour in range(24)
            if (hour in self.home_hours) != ((hour - 1) % 24 in self.home_hours)
        )

    def choose_standby_location(self, sim: "Simulation", ambulance: Ambulance) -> str:
        home = ambulance.home_base or ambulance.location
        target = home if self.at_home_hour(sim.now) else (ambulance.assigned_post or home)
        if target != ambulance.location and not math.isfinite(sim.travel(ambulance.location, target)):
            return ambulance.location
        return target


class FixedPlacementPolicy(GreedySurvivalPolicy):
    """Keep selected ambulances assigned to explicit permanent standby nodes."""

    def __init__(self, standby_by_ambulance: Mapping[str, str]):
        self.standby_by_ambulance = dict(standby_by_ambulance)

    def choose_standby_location(self, sim: "Simulation", ambulance: Ambulance) -> str:
        target = self.standby_by_ambulance.get(ambulance.id)
        if target is None:
            return ambulance.location
        if not math.isfinite(sim.travel(ambulance.location, target)):
            return ambulance.location
        return target
