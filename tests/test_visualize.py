import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ambulance_sim.engine import Simulation
from ambulance_sim.io import load_scenario
from ambulance_sim.visualize import write_visualization


class VisualizationTests(unittest.TestCase):
    def test_trace_payload_contains_initial_events_and_resources(self):
        scenario_path = Path(__file__).parents[1] / "examples" / "synthetic_scenario.json"
        simulation = Simulation(load_scenario(scenario_path), seed=42, trace=True)
        simulation.scenario.horizon_minutes = 120
        simulation.run()
        payload = simulation.trace_payload()
        self.assertEqual(payload["frames"][0]["event"], "initial")
        self.assertEqual(payload["frames"][-1]["event"], "horizon_end")
        self.assertIn("A1", payload["frames"][0]["ambulances"])
        self.assertTrue(payload["network"]["positions"])
        self.assertTrue(any(frame["event"] == "patient_arrival" for frame in payload["frames"]))
        self.assertTrue(any(
            ambulance["destination"] is not None
            for frame in payload["frames"]
            for ambulance in frame["ambulances"].values()
        ))

    def test_writes_self_contained_html(self):
        scenario_path = Path(__file__).parents[1] / "examples" / "synthetic_scenario.json"
        simulation = Simulation(load_scenario(scenario_path), seed=42, trace=True)
        simulation.scenario.horizon_minutes = 60
        simulation.run()
        with TemporaryDirectory() as directory:
            output = write_visualization(simulation.trace_payload(), Path(directory) / "simulation.html")
            text = output.read_text(encoding="utf-8")
            self.assertIn("구급차 SMDP 상황실", text)
            self.assertIn('id="simulation-data"', text)
            self.assertIn('id="timeline"', text)
            self.assertNotIn("__TRACE_JSON__", text)


if __name__ == "__main__":
    unittest.main()
