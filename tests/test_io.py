import json
import tempfile
import unittest
from pathlib import Path

from ambulance_sim.engine import build_default_scenario
from ambulance_sim.io import (
    ScenarioValidationError,
    load_scenario,
    save_scenario,
    scenario_from_dict,
    scenario_to_dict,
)


class ScenarioIOTests(unittest.TestCase):
    def test_synthetic_example_loads_and_runs(self):
        path = Path(__file__).parents[1] / "examples" / "synthetic_scenario.json"
        scenario = load_scenario(path)
        self.assertEqual(set(scenario.hospitals), {"central", "regional", "trauma"})
        self.assertEqual(len(scenario.profiles), 4)
        self.assertEqual(scenario.horizon_minutes, 1440.0)
        from ambulance_sim.engine import Simulation
        result = Simulation(scenario, seed=4).run()
        self.assertIn("expected_saved", result)

    def test_json_round_trip_preserves_model(self):
        original = build_default_scenario(hours=3, ambulances=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            save_scenario(original, path)
            loaded = load_scenario(path)
        self.assertEqual(scenario_to_dict(original), scenario_to_dict(loaded))

    def test_optional_defaults_and_unknown_fields(self):
        payload = {
            "future_model_field": {"version": 99},
            "network": {"edges": {"v": {"h": 3}, "h": {"v": 3}}},
            "villages": {"v": 0},
            "hospitals": {"h1": {"location": "h", "capabilities": ["x"], "success_when_available": 0.8}},
            "ambulances": {"a1": {"location": "v"}},
            "profiles": [{"name": "x"}],
        }
        scenario = scenario_from_dict(payload)
        self.assertEqual(scenario.horizon_minutes, 1440.0)
        self.assertEqual(scenario.max_transfers, 2)
        self.assertEqual(scenario.hospitals["h1"].transfer_delay_minutes, 5.0)
        self.assertEqual(scenario.profiles[0].scene_minutes, 0.0)
        self.assertEqual(scenario.standby_nodes, ("h",))

    def test_core_extension_fields_survive_round_trip(self):
        payload = {
            "network": {
                "edges": {"v": {"h": 10}, "h": {"v": 10}},
                "time_multipliers": {"0": 2.0, "1": 0.5},
                "positions": {"v": [10, 20], "h": [80, 70]},
            },
            "villages": {"v": 1},
            "hourly_village_rates": {"1": {"v": 3}},
            "hourly_profile_probabilities": {"1": {"x": 1}},
            "hospitals": {
                "h1": {
                    "location": "h",
                    "capabilities": ["x"],
                    "success_when_available": 0.8,
                    "capacity": 4,
                    "occupied": 1,
                    "treatment_minutes": 6,
                    "success_by_profile": {"x": 0.9},
                    "unavailable_success_by_profile": {"x": 0.2},
                }
            },
            "ambulances": {"a1": {"location": "v"}},
            "profiles": [{"name": "x"}],
            "standby_nodes": ["v"],
        }
        scenario = scenario_from_dict(payload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extended.json"
            save_scenario(scenario, path)
            loaded = load_scenario(path)
        self.assertEqual(loaded.network.time_multipliers, {0: 2.0, 1: 0.5})
        self.assertEqual(loaded.network.positions, {"v": (10.0, 20.0), "h": (80.0, 70.0)})
        self.assertEqual(loaded.hourly_village_rates, {1: {"v": 3.0}})
        self.assertEqual(loaded.hourly_profile_probabilities, {1: {"x": 1.0}})
        hospital = loaded.hospitals["h1"]
        self.assertEqual((hospital.capacity, hospital.occupied, hospital.treatment_minutes), (4, 1, 6.0))
        self.assertEqual(hospital.success_by_profile, {"x": 0.9})
        self.assertEqual(hospital.unavailable_success_by_profile, {"x": 0.2})

    def test_errors_identify_invalid_json_path(self):
        payload = {
            "network": {"edges": {"v": {"h": 3}, "h": {"v": 3}}},
            "villages": {"v": 0},
            "hospitals": {"h1": {"location": "h", "success_when_available": 1.2}},
        }
        with self.assertRaisesRegex(ScenarioValidationError, r"hospitals\.h1\.success_when_available"):
            scenario_from_dict(payload)

    def test_invalid_json_file_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"network": ', encoding="utf-8")
            with self.assertRaisesRegex(ScenarioValidationError, r"invalid JSON at line 1"):
                load_scenario(path)

    def test_unavailable_initial_ambulance_round_trip(self):
        payload = {
            "network": {"edges": {"v": {}}},
            "ambulances": {"a": {
                "location": "v", "status": "unavailable", "initial_available_after": 17
            }},
        }
        scenario = scenario_from_dict(payload)
        self.assertEqual(scenario.ambulances["a"].initial_available_after, 17)
        self.assertEqual(scenario_to_dict(scenario)["ambulances"]["a"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
