from __future__ import annotations

from collections import Counter
import heapq
import math
import random

from .io import validate_scenario
from .model import (
    Ambulance, AmbulanceStatus, Event, EventKind, Hospital, Patient, PatientProfile,
    PatientStatus, RoadNetwork, Scenario,
)
from .policy import GreedySurvivalPolicy


class Simulation:
    mass_epsilon = 1e-9

    def __init__(
        self,
        scenario: Scenario,
        policy: GreedySurvivalPolicy | None = None,
        seed: int = 0,
        *,
        trace: bool = False,
    ):
        self.scenario = scenario
        self.policy = policy or GreedySurvivalPolicy()
        self.rng = random.Random(seed)
        self.now = 0.0
        self.events: list[Event] = []
        self._sequence = 0
        self.patients: dict[str, Patient] = {}
        self.waiting: list[str] = []
        self.saved = 0.0
        self.lost = 0.0
        self.event_counts: Counter[str] = Counter()
        self.trace_enabled = trace
        self.trace: list[dict[str, object]] = []
        # Travel time depends only on source, target and departure hour in the
        # current road model. Hospital-route lookahead reuses these values many
        # times in municipal scenarios.
        self._travel_cache: dict[tuple[str, str, int], float] = {}

    def travel(self, source: str, target: str, departure_time: float | None = None) -> float:
        when = self.now if departure_time is None else departure_time
        key = (source, target, int(math.floor(when / 60.0)) % 24)
        if key not in self._travel_cache:
            self._travel_cache[key] = self.scenario.network.travel_time(source, target, when)
        return self._travel_cache[key]

    def schedule(self, time: float, kind: EventKind, **ids: str | None) -> None:
        self._sequence += 1
        heapq.heappush(self.events, Event(time, self._sequence, kind, **ids))

    def _schedule_demand(self) -> None:
        """Piecewise-constant (hourly) Poisson streams using this simulation RNG."""
        villages = set(self.scenario.villages)
        for rates in self.scenario.hourly_village_rates.values():
            villages.update(rates)
        arrival_horizon = self.scenario.arrival_horizon
        for village in sorted(villages):
            interval_start = 0.0
            while interval_start < arrival_horizon:
                interval_end = min(interval_start + 60.0, arrival_horizon)
                hourly_rate = self.scenario.demand_rate(village, interval_start)
                if hourly_rate > 0:
                    current = interval_start
                    rate_per_minute = hourly_rate / 60.0
                    while True:
                        current += self.rng.expovariate(rate_per_minute)
                        if current >= interval_end:
                            break
                        self.schedule(current, EventKind.PATIENT_ARRIVAL, patient_id=village)
                interval_start = interval_end

    def _schedule_initial_availability(self) -> None:
        for ambulance in self.scenario.ambulances.values():
            if ambulance.status is AmbulanceStatus.UNAVAILABLE:
                self.schedule(
                    max(0.0, ambulance.initial_available_after),
                    EventKind.INITIAL_AVAILABLE,
                    ambulance_id=ambulance.id,
                )

    def _schedule_shift_changes(self) -> None:
        """Standby-window boundaries; only a policy that publishes a schedule has them."""
        schedule_hours = getattr(self.policy, "schedule_hours", None)
        if schedule_hours is None:
            return
        hours = sorted({int(hour) % 24 for hour in schedule_hours()})
        for day in range(int(self.scenario.horizon_minutes // 1440.0) + 1):
            for hour in hours:
                time = day * 1440.0 + hour * 60.0
                # Time zero needs no shift: every ambulance starts at its own station.
                if 0 < time <= self.scenario.horizon_minutes:
                    self.schedule(time, EventKind.SHIFT_CHANGE)

    def run(self) -> dict[str, object]:
        if self.trace_enabled:
            self._record_trace("initial")
        self._schedule_initial_availability()
        self._schedule_shift_changes()
        self._schedule_demand()
        while self.events:
            event = heapq.heappop(self.events)
            if event.time > self.scenario.horizon_minutes:
                break
            self.now = event.time
            self.event_counts[event.kind.value] += 1
            self._handle(event)
            if self.trace_enabled:
                self._record_trace(event.kind.value, event)
        # Patients still waiting at the finite horizon have no terminal reward yet.
        for patient in self.patients.values():
            if patient.status not in {PatientStatus.COMPLETE, PatientStatus.LOST}:
                self._advance(patient, self.scenario.horizon_minutes)
                self._lose(patient)
        if self.trace_enabled:
            self.now = self.scenario.horizon_minutes
            self._record_trace("horizon_end")
        return self.summary()

    def _handle(self, event: Event) -> None:
        handlers = {
            EventKind.INITIAL_AVAILABLE: self._initial_available,
            EventKind.PATIENT_ARRIVAL: self._patient_arrival,
            EventKind.ARRIVE_SCENE: self._arrive_scene,
            EventKind.SCENE_COMPLETE: self._scene_complete,
            EventKind.ARRIVE_HOSPITAL: self._arrive_hospital,
            EventKind.ARRIVE_MORTUARY: self._arrive_mortuary,
            EventKind.HOSPITAL_RELEASE: self._hospital_release,
            EventKind.TRANSFER_READY: self._transfer_ready,
            EventKind.RESTOCK_COMPLETE: self._restock_complete,
            EventKind.REPOSITION_COMPLETE: self._reposition_complete,
            EventKind.SHIFT_CHANGE: self._shift_change,
        }
        handlers[event.kind](event)

    def _initial_available(self, event: Event) -> None:
        ambulance = self.scenario.ambulances[event.ambulance_id]
        ambulance.status = AmbulanceStatus.IDLE
        ambulance.patient_id = None
        self._finish_movement(ambulance, ambulance.location)
        self._dispatch_waiting()

    def _patient_arrival(self, event: Event) -> None:
        village = event.patient_id
        patient_id = f"P{len(self.patients) + 1:04d}"
        profile = self._choose_profile(self.now)
        patient = Patient(patient_id, village, profile, self.now, last_survival_time=self.now)
        self.patients[patient_id] = patient
        self.waiting.append(patient_id)
        self._dispatch_waiting()

    def _dispatch_waiting(self) -> None:
        # Decay is booked before the comparison, so a patient that has waited
        # longer is never given the stale value it had at arrival.  ``now`` is
        # fixed for the whole trigger, so one pass is enough.
        live_waiting: list[str] = []
        for patient_id in self.waiting:
            patient = self.patients[patient_id]
            self._advance(patient, self.now)
            # At this scale sending an ambulance creates no meaningful reward;
            # book the residual probability as lost and leave the queue instead.
            if patient.remaining_mass <= self.mass_epsilon:
                self._lose(patient)
            else:
                live_waiting.append(patient_id)
        self.waiting = live_waiting
        # Highest current unresolved expected survivor mass is dispatched first.
        # A custom policy may still return an ambulance that cannot reach the
        # patient.  Only that (patient, ambulance) pair is skipped; a patient the
        # policy cannot serve in this trigger is deferred so the other waiting
        # patients and idle ambulances are still considered.  Every iteration
        # dispatches, defers, or records a new pair, so the loop terminates.
        unreachable: set[tuple[str, str]] = set()
        deferred: set[str] = set()
        while True:
            candidates = [self.patients[p] for p in self.waiting if p not in deferred]
            if not candidates:
                return
            patient = max(candidates, key=lambda p: p.remaining_mass)
            ambulance = self.policy.choose_ambulance(self, patient)
            if ambulance is None or (patient.id, ambulance.id) in unreachable:
                deferred.add(patient.id)
                continue
            duration = self.travel(ambulance.location, patient.location)
            if not math.isfinite(duration):
                unreachable.add((patient.id, ambulance.id))
                continue
            self.waiting.remove(patient.id)
            patient.status = PatientStatus.ASSIGNED
            patient.assigned_ambulance = ambulance.id
            ambulance.status, ambulance.patient_id = AmbulanceStatus.TO_SCENE, patient.id
            self._start_movement(ambulance, patient.location, duration)
            self.schedule(self.now + duration, EventKind.ARRIVE_SCENE, ambulance_id=ambulance.id, patient_id=patient.id)

    def _arrive_scene(self, event: Event) -> None:
        ambulance, patient = self._records(event)
        self._finish_movement(ambulance, patient.location)
        ambulance.status = AmbulanceStatus.ON_SCENE
        patient.status = PatientStatus.ON_SCENE
        self._advance(patient, self.now)
        patient.scene_arrival_time = self.now
        patient.response_minutes = max(0.0, self.now - patient.onset_time)
        self.schedule(self.now + patient.profile.scene_minutes, EventKind.SCENE_COMPLETE, ambulance_id=ambulance.id, patient_id=patient.id)

    def _scene_complete(self, event: Event) -> None:
        ambulance, patient = self._records(event)
        alive = self._advance(patient, self.now)
        if patient.scene_arrival_time is not None:
            patient.scene_time_minutes += max(0.0, self.now - patient.scene_arrival_time)
        if alive <= self.mass_epsilon:
            self._lose(patient)
            self._transport_deceased(ambulance, patient)
            return
        field_success = patient.profile.field_success
        if field_success:
            self._save(patient, alive * field_success)
            patient.remaining_mass *= 1 - field_success
        if patient.remaining_mass <= self.mass_epsilon:
            patient.status = PatientStatus.COMPLETE
            self._begin_restock(ambulance)
            return
        hospital = self.policy.choose_hospital(self, patient, ambulance.location, self.now)
        if hospital is None:
            self._lose(patient)
            self._transport_deceased(ambulance, patient)
            return
        self._transport(ambulance, patient, hospital)

    def _transport(self, ambulance: Ambulance, patient: Patient, hospital: Hospital) -> None:
        patient.status, ambulance.status = PatientStatus.TRANSPORTING, AmbulanceStatus.TO_HOSPITAL
        duration = self.travel(ambulance.location, hospital.location)
        if not math.isfinite(duration):
            self._lose(patient)
            self._transport_deceased(ambulance, patient)
            return
        patient.transport_departure_time = self.now
        self._start_movement(ambulance, hospital.location, duration)
        self.schedule(self.now + duration, EventKind.ARRIVE_HOSPITAL, ambulance_id=ambulance.id, patient_id=patient.id, hospital_id=hospital.id)

    def _transport_deceased(self, ambulance: Ambulance, patient: Patient) -> None:
        """Occupy the ambulance for post-mortem transport without adding reward."""
        choices = [
            hospital for hospital in self.scenario.hospitals.values()
            if math.isfinite(self.travel(ambulance.location, hospital.location))
        ]
        if not choices:
            self._begin_restock(ambulance)
            return
        hospital = min(
            choices,
            key=lambda item: (self.travel(ambulance.location, item.location), item.id),
        )
        duration = self.travel(ambulance.location, hospital.location)
        ambulance.status = AmbulanceStatus.TO_HOSPITAL
        patient.transport_departure_time = self.now
        self._start_movement(ambulance, hospital.location, duration)
        self.schedule(
            self.now + duration,
            EventKind.ARRIVE_MORTUARY,
            ambulance_id=ambulance.id,
            patient_id=patient.id,
            hospital_id=hospital.id,
        )

    def _arrive_mortuary(self, event: Event) -> None:
        ambulance, patient = self._records(event)
        hospital = self.scenario.hospitals[event.hospital_id]
        self._finish_movement(ambulance, hospital.location)
        if patient.transport_departure_time is not None:
            patient.transport_minutes += max(0.0, self.now - patient.transport_departure_time)
            patient.transport_legs += 1
            patient.transport_departure_time = None
        self._begin_restock(ambulance)

    def _arrive_hospital(self, event: Event) -> None:
        ambulance, patient = self._records(event)
        hospital = self.scenario.hospitals[event.hospital_id]
        self._finish_movement(ambulance, hospital.location)
        alive = self._advance(patient, self.now)
        if patient.transport_departure_time is not None:
            patient.transport_minutes += max(0.0, self.now - patient.transport_departure_time)
            patient.transport_legs += 1
            patient.transport_departure_time = None
        patient.visited_hospitals.add(hospital.id)
        success = hospital.treatment_success(patient)
        # Capacity is reserved only after the arrival's service quality has been
        # evaluated.  Thus the final open bed is usable by this patient.
        treated = patient.profile.name in hospital.capabilities and hospital.reserve_bed()
        if treated:
            self.schedule(self.now + max(0.0, hospital.treatment_minutes), EventKind.HOSPITAL_RELEASE, hospital_id=hospital.id)
        self._save(patient, alive * success)
        patient.remaining_mass *= 1 - success
        # A patient who was actually admitted and treated has a final outcome: the
        # unsuccessful share is lost, never re-treated elsewhere.  Transfers exist only
        # for patients the hospital could not admit (no bed / no capability).
        if patient.remaining_mass <= self.mass_epsilon:
            patient.status = PatientStatus.COMPLETE
            self._begin_restock(ambulance)
            return
        if treated or patient.transfers >= self.scenario.max_transfers:
            self._lose(patient)
            self._begin_restock(ambulance)
            return
        patient.transfers += 1
        # The ambulance remains committed during clinical handoff/transfer preparation.
        # This is deliberately a distinct event from scene treatment: transfer must
        # not apply field-care success a second time.  The receiving-hospital action
        # is made at transfer readiness, after the delay's survival decay is known.
        self.schedule(self.now + hospital.transfer_delay_minutes, EventKind.TRANSFER_READY, ambulance_id=ambulance.id, patient_id=patient.id)

    def _hospital_release(self, event: Event) -> None:
        hospital_id = event.hospital_id
        if hospital_id is not None:
            self.scenario.hospitals[hospital_id].release_bed()

    def _transfer_ready(self, event: Event) -> None:
        ambulance, patient = self._records(event)
        self._advance(patient, self.now)
        if patient.remaining_mass <= self.mass_epsilon:
            self._lose(patient)
            self._begin_restock(ambulance)
            return
        hospital = self.policy.choose_hospital(self, patient, ambulance.location, self.now)
        if hospital is None:
            self._lose(patient)
            self._begin_restock(ambulance)
            return
        self._transport(ambulance, patient, hospital)

    def _begin_restock(self, ambulance: Ambulance) -> None:
        ambulance.patient_id = None
        target = self.policy.choose_restock_location(self, ambulance)
        ambulance.status = AmbulanceStatus.RESTOCKING
        duration = self.travel(ambulance.location, target)
        if not math.isfinite(duration):
            duration, target = 0.0, ambulance.location
        self._start_movement(ambulance, target, duration)
        self.schedule(self.now + duration + ambulance.restock_minutes, EventKind.RESTOCK_COMPLETE, ambulance_id=ambulance.id, patient_id=target)

    def _restock_complete(self, event: Event) -> None:
        ambulance = self.scenario.ambulances[event.ambulance_id]
        self._finish_movement(ambulance, event.patient_id)
        target = self.policy.choose_standby_location(self, ambulance)
        ambulance.status = AmbulanceStatus.REPOSITIONING
        duration = self.travel(ambulance.location, target)
        if not math.isfinite(duration):
            duration, target = 0.0, ambulance.location
        self._start_movement(ambulance, target, duration)
        self.schedule(self.now + duration, EventKind.REPOSITION_COMPLETE, ambulance_id=ambulance.id, patient_id=target)

    def _reposition_complete(self, event: Event) -> None:
        ambulance = self.scenario.ambulances[event.ambulance_id]
        self._finish_movement(ambulance, event.patient_id)
        ambulance.status = AmbulanceStatus.IDLE
        self._dispatch_waiting()

    def _shift_change(self, event: Event) -> None:
        """Send idle ambulances to the standby node of the new window.

        Patient work always wins: an ambulance on a call, transporting, or
        restocking is left untouched, and any patient still waiting is offered
        the fleet again after the moves.  A repositioning ambulance is not
        dispatchable in this engine, so the vehicle is unavailable while it
        drives to the new post; that cost is part of the evaluated policy.
        """
        for ambulance in self.scenario.ambulances.values():
            if ambulance.status is not AmbulanceStatus.IDLE:
                continue
            target = self.policy.choose_standby_location(self, ambulance)
            if target == ambulance.location:
                continue
            duration = self.travel(ambulance.location, target)
            if not math.isfinite(duration):
                continue
            ambulance.status = AmbulanceStatus.REPOSITIONING
            self._start_movement(ambulance, target, duration)
            self.schedule(self.now + duration, EventKind.REPOSITION_COMPLETE, ambulance_id=ambulance.id, patient_id=target)
        if self.waiting:
            self._dispatch_waiting()

    def _start_movement(self, ambulance: Ambulance, destination: str, duration: float) -> None:
        ambulance.destination = destination
        ambulance.movement_started_at = self.now
        ambulance.movement_ends_at = self.now + max(0.0, duration)

    @staticmethod
    def _finish_movement(ambulance: Ambulance, location: str) -> None:
        ambulance.location = location
        ambulance.destination = None
        ambulance.movement_started_at = None
        ambulance.movement_ends_at = None

    def _record_trace(self, label: str, event: Event | None = None) -> None:
        self.trace.append({
            "time": self.now,
            "event": label,
            "subject": {
                "ambulance_id": event.ambulance_id if event else None,
                "patient_id": event.patient_id if event else None,
                "hospital_id": event.hospital_id if event else None,
            },
            "ambulances": {
                ident: {
                    "location": ambulance.location,
                    "status": ambulance.status.value,
                    "patient_id": ambulance.patient_id,
                    "destination": ambulance.destination,
                    "movement_started_at": ambulance.movement_started_at,
                    "movement_ends_at": ambulance.movement_ends_at,
                }
                for ident, ambulance in self.scenario.ambulances.items()
            },
            "patients": {
                ident: {
                    "location": patient.location,
                    "profile": patient.profile.name,
                    "status": patient.status.value,
                    "remaining_mass": patient.remaining_mass,
                    "saved_mass": patient.saved_mass,
                    "lost_mass": patient.lost_mass,
                    "ambulance_id": patient.assigned_ambulance,
                }
                for ident, patient in self.patients.items()
            },
            "hospitals": {
                ident: {
                    "occupied": hospital.occupied,
                    "capacity": hospital.capacity,
                    "available": hospital.available,
                }
                for ident, hospital in self.scenario.hospitals.items()
            },
            "totals": {
                "saved": self.saved,
                "lost": self.lost,
                "waiting": len(self.waiting),
            },
        })

    def trace_payload(self) -> dict[str, object]:
        if not self.trace_enabled:
            raise ValueError("trace was not enabled for this simulation")
        villages = set(self.scenario.villages)
        for rates in self.scenario.hourly_village_rates.values():
            villages.update(rates)
        return {
            "network": {
                "edges": self.scenario.network.edges,
                "positions": {
                    node: list(position) for node, position in self.scenario.network.positions.items()
                },
            },
            "villages": sorted(villages),
            "hospitals": {
                ident: {"location": hospital.location, "capacity": hospital.capacity}
                for ident, hospital in self.scenario.hospitals.items()
            },
            "horizon_minutes": self.scenario.horizon_minutes,
            "summary": self.summary(),
            "frames": self.trace,
        }

    def _records(self, event: Event) -> tuple[Ambulance, Patient]:
        return self.scenario.ambulances[event.ambulance_id], self.patients[event.patient_id]

    def _save(self, patient: Patient, amount: float) -> None:
        amount = max(0.0, min(amount, patient.remaining_mass))
        patient.saved_mass += amount
        self.saved += amount

    def _advance(self, patient: Patient, now: float) -> float:
        """Advance survival and book deaths in the new time interval once."""
        before = patient.remaining_mass
        alive = patient.advance_survival(now)
        decayed = before - alive
        if decayed > 0:
            patient.lost_mass += decayed
            self.lost += decayed
        return alive

    def _lose(self, patient: Patient) -> None:
        if patient.status in {PatientStatus.COMPLETE, PatientStatus.LOST}:
            return
        patient.lost_mass += patient.remaining_mass
        self.lost += patient.remaining_mass
        patient.remaining_mass = 0.0
        patient.status = PatientStatus.LOST

    def _choose_profile(self, now: float) -> PatientProfile:
        weights = self.scenario.profile_weights(now)
        candidates = [(profile, max(0.0, weights.get(profile.name, 0.0))) for profile in self.scenario.profiles]
        total = sum(weight for _, weight in candidates)
        if total <= 0:
            return self.rng.choice(self.scenario.profiles)
        draw = self.rng.random() * total
        cumulative = 0.0
        for profile, weight in candidates:
            cumulative += weight
            if draw < cumulative:
                return profile
        return candidates[-1][0]

    def summary(self) -> dict[str, object]:
        def percentile(values: list[float], probability: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            position = (len(ordered) - 1) * probability
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                return ordered[lower]
            fraction = position - lower
            return ordered[lower] * (1 - fraction) + ordered[upper] * fraction

        terminal = sum(p.status in {PatientStatus.COMPLETE, PatientStatus.LOST} for p in self.patients.values())
        type_metrics: dict[str, dict[str, float | int | None]] = {}
        for patient in self.patients.values():
            metric = type_metrics.setdefault(patient.profile.name, {
                "patients": 0, "expected_saved": 0.0, "expected_lost": 0.0,
                "response_count": 0, "response_minutes": 0.0,
                "scene_count": 0, "scene_minutes": 0.0,
                "transport_count": 0, "transport_minutes": 0.0,
            })
            metric["patients"] += 1
            metric["expected_saved"] += patient.saved_mass
            metric["expected_lost"] += patient.lost_mass
            if patient.response_minutes is not None:
                metric["response_count"] += 1
                metric["response_minutes"] += patient.response_minutes
            if patient.scene_arrival_time is not None:
                metric["scene_count"] += 1
                metric["scene_minutes"] += patient.scene_time_minutes
            if patient.transport_legs:
                metric["transport_count"] += 1
                metric["transport_minutes"] += patient.transport_minutes
        for profile_name, metric in type_metrics.items():
            for name in ("response", "scene", "transport"):
                count = metric[f"{name}_count"]
                metric[f"mean_{name}_minutes"] = (metric[f"{name}_minutes"] / count) if count else None
            patient_responses = [
                patient.response_minutes for patient in self.patients.values()
                if patient.profile.name == profile_name and patient.response_minutes is not None
            ]
            metric["p90_response_minutes"] = percentile(patient_responses, 0.9)
        response = [p.response_minutes for p in self.patients.values() if p.response_minutes is not None]
        scenes = [p.scene_time_minutes for p in self.patients.values() if p.scene_arrival_time is not None]
        transports = [p.transport_minutes for p in self.patients.values() if p.transport_legs]
        return {
            "seeded_patients": len(self.patients),
            "terminal_patients": terminal,
            "expected_saved": round(self.saved, 6),
            "expected_lost": round(self.lost, 6),
            "expected_saved_per_patient": (self.saved / len(self.patients)) if self.patients else None,
            "reached_patients": len(response),
            "unreached_patients": len(self.patients) - len(response),
            "response_rate": (len(response) / len(self.patients)) if self.patients else None,
            "events": dict(self.event_counts),
            "mean_response_minutes": (sum(response) / len(response)) if response else None,
            "p90_response_minutes": percentile(response, 0.9),
            "mean_scene_minutes": (sum(scenes) / len(scenes)) if scenes else None,
            "mean_transport_minutes": (sum(transports) / len(transports)) if transports else None,
            "by_type": type_metrics,
        }


def build_default_scenario(hours: float = 24, ambulances: int = 3) -> Scenario:
    edges = {
        "Station North": {"Village A": 8, "Central ER": 12},
        "Station South": {"Village B": 7, "Village C": 10, "Regional ER": 11},
        "Village A": {"Village B": 9, "Central ER": 16},
        "Village B": {"Village C": 8, "Regional ER": 14},
        "Village C": {"Trauma Center": 15},
        "Central ER": {"Regional ER": 9, "Trauma Center": 17},
        "Regional ER": {"Trauma Center": 12},
    }
    undirected: dict[str, dict[str, float]] = {}
    for source, neighbors in edges.items():
        for target, minutes in neighbors.items():
            undirected.setdefault(source, {})[target] = minutes
            undirected.setdefault(target, {})[source] = minutes
    profiles = (
        PatientProfile("cardiac", golden_minutes=8, decay_rate=0.045, scene_minutes=12),
        PatientProfile("stroke", golden_minutes=20, decay_rate=0.018, scene_minutes=14),
        PatientProfile("trauma", golden_minutes=15, decay_rate=0.028, scene_minutes=16),
        PatientProfile("minor", golden_minutes=45, decay_rate=0.006, scene_minutes=10, field_success=0.35),
    )
    # Explicit bed capacity and treatment time make the capacity model bind in
    # the demo; with ``capacity=None`` the unavailable success rates were dead.
    hospitals = {
        "central": Hospital("central", "Central ER", {"cardiac", "stroke", "minor"}, 0.86, 0.45, capacity=2, treatment_minutes=60.0),
        "regional": Hospital("regional", "Regional ER", {"cardiac", "stroke", "minor"}, 0.78, 0.35, capacity=2, treatment_minutes=45.0),
        "trauma": Hospital("trauma", "Trauma Center", {"trauma", "cardiac", "minor"}, 0.93, 0.50, capacity=2, treatment_minutes=75.0),
    }
    starts = ("Station North", "Station South", "Central ER", "Regional ER")
    fleet = {f"A{i + 1}": Ambulance(f"A{i + 1}", starts[i % len(starts)]) for i in range(ambulances)}
    scenario = Scenario(
        network=RoadNetwork(undirected), villages={"Village A": 0.8, "Village B": 0.65, "Village C": 0.45},
        hospitals=hospitals, ambulances=fleet, profiles=profiles,
        standby_nodes=("Station North", "Station South", "Central ER", "Regional ER"),
        horizon_minutes=hours * 60,
    )
    validate_scenario(scenario, source="build_default_scenario")
    return scenario
