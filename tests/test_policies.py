import unittest

from ambulance_sim.engine import Simulation
from ambulance_sim.model import Hospital, Patient, PatientProfile, RoadNetwork, Scenario
from ambulance_sim.policy import GreedySurvivalPolicy, NearestHospitalPolicy, NoRepositionPolicy


class PolicyTests(unittest.TestCase):
    def test_nearest_and_survival_policy_are_distinct(self):
        profile = PatientProfile("x", golden_minutes=100, decay_rate=0, scene_minutes=0)
        near = Hospital("near", "near", {"x"}, 0.1)
        far = Hospital("far", "far", {"x"}, 0.9)
        scenario = Scenario(
            network=RoadNetwork({"v": {"near": 1, "far": 5}, "near": {"v": 1}, "far": {"v": 5}}),
            villages={}, hospitals={"near": near, "far": far}, ambulances={}, profiles=(profile,),
            standby_nodes=("v",), max_transfers=0,
        )
        patient = Patient("p", "v", profile, 0, last_survival_time=0)
        sim = Simulation(scenario)
        self.assertEqual(NearestHospitalPolicy().choose_hospital(sim, patient, "v", 0).id, "near")
        self.assertEqual(GreedySurvivalPolicy().choose_hospital(sim, patient, "v", 0).id, "far")

    def test_no_reposition_policy_stays_at_restock_location(self):
        profile = PatientProfile("x", 1, 1, 0)
        scenario = Scenario(
            RoadNetwork({"a": {"b": 1}, "b": {"a": 1}}), {}, {}, {}, (profile,), ("a", "b")
        )
        ambulance = __import__("ambulance_sim.model", fromlist=["Ambulance"]).Ambulance("a1", "b")
        self.assertEqual(NoRepositionPolicy().choose_standby_location(Simulation(scenario), ambulance), "b")


if __name__ == "__main__":
    unittest.main()
