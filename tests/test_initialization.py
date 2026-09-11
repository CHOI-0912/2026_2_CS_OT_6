import unittest

from ambulance_sim.engine import build_default_scenario
from ambulance_sim.initialization import randomized_initial_locations
from ambulance_sim.engine import Simulation
from ambulance_sim.model import Ambulance, AmbulanceStatus, Hospital, Patient, PatientProfile, RoadNetwork, Scenario


class InitializationTests(unittest.TestCase):
    def test_randomization_is_reproducible_and_does_not_mutate_source(self):
        original = build_default_scenario(hours=1, ambulances=3)
        before = {key: value.location for key, value in original.ambulances.items()}
        weights = {"Station North": 0.5, "Central ER": 0.5}
        first = randomized_initial_locations(original, weights, seed=8)
        second = randomized_initial_locations(original, weights, seed=8)
        self.assertEqual(
            [a.location for a in first.ambulances.values()],
            [a.location for a in second.ambulances.values()],
        )
        self.assertEqual(before, {key: value.location for key, value in original.ambulances.items()})

    def test_candidate_ambulance_can_be_fixed(self):
        original = build_default_scenario(hours=1, ambulances=2)
        candidate_location = original.ambulances["A1"].location
        randomized = randomized_initial_locations(
            original,
            {"Regional ER": 1.0},
            seed=2,
            fixed_ambulance_ids=frozenset({"A1"}),
        )
        self.assertEqual(randomized.ambulances["A1"].location, candidate_location)
        self.assertEqual(randomized.ambulances["A2"].location, "Regional ER")

    def test_initially_unavailable_ambulance_enters_service(self):
        profile = PatientProfile("x", 100, 0, 0)
        scenario = Scenario(
            RoadNetwork({"v": {"h": 0}, "h": {"v": 0}}),
            {},
            {"h": Hospital("h", "h", {"x"}, 1.0)},
            {"a": Ambulance("a", "v", AmbulanceStatus.UNAVAILABLE, initial_available_after=5)},
            (profile,),
            ("v",),
            horizon_minutes=20,
        )
        simulation = Simulation(scenario)
        simulation.patients["p"] = Patient("p", "v", profile, 0, last_survival_time=0)
        simulation.waiting.append("p")
        result = simulation.run()
        self.assertEqual(result["events"]["initial_available"], 1)
        self.assertAlmostEqual(result["expected_saved"], 1.0)


if __name__ == "__main__":
    unittest.main()
