import heapq
import math
import unittest

from ambulance_sim.engine import Simulation
from ambulance_sim.model import (
    Ambulance, AmbulanceStatus, Event, EventKind, Hospital, Patient,
    PatientProfile, PatientStatus, RoadNetwork, Scenario,
)


def scenario_for(profile, *, network=None, hospitals=None, ambulances=None, horizon=120, **kwargs):
    return Scenario(
        network=network or RoadNetwork({"v": {"h": 0}, "h": {"v": 0}}),
        villages={}, profiles=(profile,), hospitals=hospitals or {}, ambulances=ambulances or {},
        standby_nodes=("v",), horizon_minutes=horizon, **kwargs,
    )


class CoreExtensionTests(unittest.TestCase):
    def test_hourly_demand_and_profile_probabilities_are_reproducible(self):
        alpha = PatientProfile("alpha", 100, 0, 0)
        beta = PatientProfile("beta", 100, 0, 0)
        kwargs = dict(
            network=RoadNetwork({}), villages={"v": 0}, profiles=(alpha, beta), hospitals={}, ambulances={},
            standby_nodes=("v",), horizon_minutes=120,
            hourly_village_rates={1: {"v": 300}},
            hourly_profile_probabilities={1: {"beta": 1.0}},
        )
        first = Simulation(Scenario(**kwargs), seed=31).run()
        second = Simulation(Scenario(**kwargs), seed=31).run()
        self.assertEqual(first, second)
        self.assertGreater(first["seeded_patients"], 0)
        self.assertEqual(set(first["by_type"]), {"beta"})
        self.assertAlmostEqual(first["expected_saved"] + first["expected_lost"], first["seeded_patients"], places=6)

    def test_departure_hour_changes_road_weight(self):
        roads = RoadNetwork({"a": {"b": 10}, "b": {"a": 10}}, time_multipliers={0: 2.0, 1: 0.5})
        self.assertEqual(roads.travel_time("a", "b", 10), 20)
        self.assertEqual(roads.travel_time("a", "b", 70), 5)
        self.assertTrue(math.isinf(roads.travel_time("a", "missing", 70)))

    def test_capacity_is_reserved_then_released_and_changes_success(self):
        profile = PatientProfile("x", 1000, 0, 0)
        hospital = Hospital("h", "h", {"x"}, 0.9, 0.1, capacity=1, treatment_minutes=5)
        ambulances = {
            "a1": Ambulance("a1", "h", AmbulanceStatus.TO_HOSPITAL, "p1"),
            "a2": Ambulance("a2", "h", AmbulanceStatus.TO_HOSPITAL, "p2"),
        }
        sim = Simulation(scenario_for(profile, hospitals={"h": hospital}, ambulances=ambulances), seed=1)
        for pid in ("p1", "p2"):
            sim.patients[pid] = Patient(pid, "v", profile, 0, last_survival_time=0)
        sim._arrive_hospital(Event(0, 1, EventKind.ARRIVE_HOSPITAL, "a1", "p1", "h"))
        sim._arrive_hospital(Event(0, 2, EventKind.ARRIVE_HOSPITAL, "a2", "p2", "h"))
        self.assertEqual(hospital.occupied, 1)
        self.assertAlmostEqual(sim.patients["p1"].saved_mass, 0.9)
        self.assertAlmostEqual(sim.patients["p2"].saved_mass, 0.1)
        releases = [event for event in sim.events if event.kind == EventKind.HOSPITAL_RELEASE]
        self.assertEqual(len(releases), 1)
        sim.now = 5
        sim._hospital_release(releases[0])
        self.assertEqual(hospital.occupied, 0)

    def test_waiting_priority_uses_mass_at_dispatch_time(self):
        profile = PatientProfile("x", 0, 0.2, 0)
        ambulance = Ambulance("a", "v")
        sim = Simulation(scenario_for(profile, ambulances={"a": ambulance}), seed=2)
        old = Patient("old", "v", profile, 0, last_survival_time=0)
        new = Patient("new", "v", profile, 10, last_survival_time=10)
        sim.patients = {"old": old, "new": new}
        sim.waiting = ["old", "new"]
        sim.now = 10
        sim._dispatch_waiting()
        self.assertEqual(ambulance.patient_id, "new")
        self.assertLess(old.remaining_mass, new.remaining_mass)

    def test_negligible_waiter_is_lost_without_occupying_an_ambulance(self):
        profile = PatientProfile("x", 0, 100, 0)
        ambulance = Ambulance("a", "v")
        sim = Simulation(scenario_for(profile, ambulances={"a": ambulance}), seed=2)
        patient = Patient("p", "v", profile, 0, last_survival_time=0)
        sim.patients["p"] = patient
        sim.waiting = ["p"]
        sim.now = 1
        sim._dispatch_waiting()
        self.assertEqual(patient.status, PatientStatus.LOST)
        self.assertEqual(sim.waiting, [])
        self.assertEqual(ambulance.status, AmbulanceStatus.IDLE)
        self.assertEqual(sim.events, [])

    def test_unreachable_dispatch_does_not_schedule_infinite_event(self):
        profile = PatientProfile("x", 0, 0.1, 0)
        sim = Simulation(
            scenario_for(profile, network=RoadNetwork({"station": {}, "v": {}}),
                         ambulances={"a": Ambulance("a", "station")}, horizon=10), seed=3
        )
        patient = Patient("p", "v", profile, 0, last_survival_time=0)
        sim.patients["p"] = patient
        sim.waiting.append("p")
        sim._dispatch_waiting()
        self.assertEqual(sim.events, [])
        self.assertEqual(patient.status, PatientStatus.WAITING)
        result = sim.run()
        self.assertEqual(result["terminal_patients"], 1)
        self.assertAlmostEqual(result["expected_saved"] + result["expected_lost"], 1.0)

    def test_summary_has_time_and_type_metrics(self):
        profile = PatientProfile("x", 1000, 0, 3)
        hospital = Hospital("h", "h", {"x"}, 1.0)
        ambulance = Ambulance("a", "station")
        network = RoadNetwork({"station": {"v": 2}, "v": {"station": 2, "h": 4}, "h": {"v": 4}})
        sim = Simulation(scenario_for(profile, network=network, hospitals={"h": hospital}, ambulances={"a": ambulance}), seed=4)
        sim.patients["p"] = Patient("p", "v", profile, 0, last_survival_time=0)
        sim.waiting.append("p")
        sim._dispatch_waiting()
        while sim.events:
            event = heapq.heappop(sim.events)
            sim.now = event.time
            sim._handle(event)
        result = sim.summary()
        self.assertEqual(result["mean_response_minutes"], 2)
        self.assertEqual(result["mean_scene_minutes"], 3)
        self.assertEqual(result["mean_transport_minutes"], 4)
        self.assertAlmostEqual(result["by_type"]["x"]["expected_saved"], 1.0)
        self.assertAlmostEqual(result["by_type"]["x"]["expected_lost"], 0.0)

    def test_death_at_scene_still_occupies_ambulance_for_hospital_transport(self):
        profile = PatientProfile("x", 0, 100, 1)
        hospital = Hospital("h", "h", set(), 0.0)
        ambulance = Ambulance("a", "v")
        network = RoadNetwork({"v": {"h": 7}, "h": {"v": 7}})
        sim = Simulation(scenario_for(
            profile, network=network, hospitals={"h": hospital},
            ambulances={"a": ambulance}, horizon=20,
        ))
        sim.patients["p"] = Patient("p", "v", profile, 0, last_survival_time=0)
        sim.waiting.append("p")
        sim._dispatch_waiting()
        result = sim.run()
        self.assertEqual(result["events"]["arrive_mortuary"], 1)
        self.assertEqual(sim.patients["p"].status, PatientStatus.LOST)
        self.assertEqual(sim.patients["p"].transport_minutes, 7)
        self.assertEqual(ambulance.location, "h")
        self.assertEqual(result["expected_saved"], 0.0)


if __name__ == "__main__":
    unittest.main()
