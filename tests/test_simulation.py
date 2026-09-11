import unittest

from ambulance_sim.engine import Simulation, build_default_scenario
from ambulance_sim.model import Hospital, Patient, PatientProfile, RoadNetwork, Scenario
from ambulance_sim.policy import GreedySurvivalPolicy


class SimulationTests(unittest.TestCase):
    def test_fixed_seed_is_reproducible(self):
        first = Simulation(build_default_scenario(hours=8), seed=19).run()
        second = Simulation(build_default_scenario(hours=8), seed=19).run()
        self.assertEqual(first, second)

    def test_episode_conserves_probability_mass(self):
        result = Simulation(build_default_scenario(hours=8), seed=7).run()
        self.assertAlmostEqual(result["expected_saved"] + result["expected_lost"], result["seeded_patients"], places=5)

    def _run_hospital_chain(self, h1: Hospital, h2: Hospital):
        profile = PatientProfile("x", golden_minutes=1000, decay_rate=0, scene_minutes=0)
        network = RoadNetwork({"v": {"h1": 0}, "h1": {"v": 0, "h2": 0}, "h2": {"h1": 0}})
        scenario = Scenario(
            network=network, villages={}, profiles=(profile,), ambulances={}, standby_nodes=("v",),
            hospitals={"h1": h1, "h2": h2}, max_transfers=2,
        )
        sim = Simulation(scenario, seed=1)
        patient = Patient("p", "v", profile, 0, last_survival_time=0)
        sim.patients[patient.id] = patient
        from ambulance_sim.model import Ambulance, AmbulanceStatus, EventKind
        ambulance = Ambulance("a", "v", AmbulanceStatus.TO_HOSPITAL, "p")
        sim.scenario.ambulances["a"] = ambulance
        sim._transport(ambulance, patient, scenario.hospitals["h1"])
        while sim.events:
            event = __import__("heapq").heappop(sim.events)
            sim.now = event.time
            if event.kind == EventKind.ARRIVE_HOSPITAL:
                sim._arrive_hospital(event)
            elif event.kind == EventKind.TRANSFER_READY:
                sim._transfer_ready(event)
        return sim, patient

    def test_treated_patient_has_a_final_outcome_and_is_not_retried(self):
        # Admitted at h1 with success .7: the remaining .3 is lost, never re-treated at h2.
        sim, patient = self._run_hospital_chain(
            Hospital("h1", "h1", {"x"}, 0.7, transfer_delay_minutes=0),
            Hospital("h2", "h2", {"x"}, 0.8, transfer_delay_minutes=0),
        )
        self.assertAlmostEqual(sim.saved, 0.7)
        self.assertAlmostEqual(sim.lost, 0.3)
        self.assertAlmostEqual(patient.saved_mass + patient.lost_mass, 1.0)
        self.assertEqual(patient.transfers, 0)

    def test_patient_refused_for_lack_of_beds_is_transferred_once(self):
        # h1 has no bed: unavailable success 0, so the whole mass moves to h2 (.8 saved).
        sim, patient = self._run_hospital_chain(
            Hospital("h1", "h1", {"x"}, 0.7, transfer_delay_minutes=0, capacity=0),
            Hospital("h2", "h2", {"x"}, 0.8, transfer_delay_minutes=0),
        )
        self.assertAlmostEqual(sim.saved, 0.8)
        self.assertAlmostEqual(sim.lost, 0.2)
        self.assertEqual(patient.transfers, 1)

    def test_survival_interval_is_not_applied_twice(self):
        profile = PatientProfile("x", golden_minutes=0, decay_rate=0.1, scene_minutes=0)
        patient = Patient("p", "v", profile, onset_time=0, last_survival_time=0)
        patient.advance_survival(5)
        patient.advance_survival(10)
        self.assertAlmostEqual(patient.remaining_mass, profile.survival(10))


if __name__ == "__main__":
    unittest.main()
