import unittest
from tempfile import TemporaryDirectory

from ambulance_sim.engine import build_default_scenario
from ambulance_sim.experiment import evaluate_standby_candidates, run_experiment, write_experiment_outputs
from ambulance_sim.policy import GreedySurvivalPolicy


class ExperimentTests(unittest.TestCase):
    def test_paired_reproducible_experiment(self):
        factory = lambda: build_default_scenario(hours=2, ambulances=2)
        policies = {
            "baseline": GreedySurvivalPolicy,
            "candidate": GreedySurvivalPolicy,
        }
        first = run_experiment(factory, policies, episodes=4, base_seed=11)
        second = run_experiment(factory, policies, episodes=4, base_seed=11)
        self.assertEqual(first, second)
        difference = first["paired_expected_saved_differences"]["candidate_minus_baseline"]
        self.assertEqual(difference["mean"], 0.0)
        self.assertEqual(difference["ci95_low"], 0.0)
        self.assertEqual(difference["ci95_high"], 0.0)

    def test_rejects_invalid_episode_count(self):
        with self.assertRaisesRegex(ValueError, "episodes"):
            run_experiment(build_default_scenario, {"p": GreedySurvivalPolicy}, episodes=0)

    def test_writes_json_and_csv_outputs(self):
        result = run_experiment(
            lambda: build_default_scenario(hours=1, ambulances=1),
            {"greedy": GreedySurvivalPolicy},
            episodes=2,
            base_seed=3,
        )
        with TemporaryDirectory() as directory:
            json_path, csv_path = write_experiment_outputs(result, directory)
            self.assertTrue(json_path.is_file())
            self.assertTrue(csv_path.is_file())
            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("policy,episode,seed", text)
            self.assertEqual(len(text.splitlines()), 3)

    def test_evaluates_fixed_standby_candidates_with_paired_demand(self):
        result = evaluate_standby_candidates(
            lambda: build_default_scenario(hours=2, ambulances=2),
            "A1",
            ["Station North", "Station South"],
            episodes=3,
            base_seed=20,
            background_location_weights={"Central ER": 1.0},
        )
        self.assertEqual(result["experiment_type"], "standby_placement")
        self.assertEqual(result["target_ambulance_id"], "A1")
        self.assertEqual(result["policy_order"], ["Station North", "Station South"])
        for episode in range(3):
            self.assertEqual(
                result["runs"]["Station North"][episode]["seeded_patients"],
                result["runs"]["Station South"][episode]["seeded_patients"],
            )


if __name__ == "__main__":
    unittest.main()
