import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from ambulance_sim.__main__ import main


class CliTests(unittest.TestCase):
    def test_json_scenario_single_run(self):
        scenario = Path(__file__).parents[1] / "examples" / "synthetic_scenario.json"
        stream = StringIO()
        with redirect_stdout(stream):
            code = main(["--scenario", str(scenario), "--hours", "1", "--seed", "9", "--json"])
        result = json.loads(stream.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result["seeded_patients"], result["terminal_patients"])

    def test_comparison_writes_reproducible_artifacts(self):
        with TemporaryDirectory() as directory:
            stream = StringIO()
            with redirect_stdout(stream):
                code = main([
                    "--compare", "--episodes", "3", "--hours", "1",
                    "--ambulances", "2", "--seed", "4", "--output-dir", directory,
                ])
            self.assertEqual(code, 0)
            self.assertTrue((Path(directory) / "experiment_summary.json").is_file())
            self.assertTrue((Path(directory) / "episode_results.csv").is_file())
        self.assertIn("greedy:", stream.getvalue())

    def test_aggregate_overview_requires_explicit_legacy_flag(self):
        with self.assertRaises(SystemExit):
            main(["--national", "--json"])

    def test_standby_placement_cli(self):
        stream = StringIO()
        with redirect_stdout(stream):
            code = main([
                "--placement-ambulance", "A1", "--candidate", "Station North",
                "--candidate", "Station South", "--episodes", "2", "--hours", "1",
            ])
        self.assertEqual(code, 0)
        self.assertIn("Station North:", stream.getvalue())
        self.assertIn("Station South_minus_Station North", stream.getvalue())

    def test_visualization_cli_writes_html(self):
        scenario = Path(__file__).parents[1] / "examples" / "synthetic_scenario.json"
        with TemporaryDirectory() as directory:
            output = Path(directory) / "view.html"
            stream = StringIO()
            with redirect_stdout(stream):
                code = main([
                    "--scenario", str(scenario), "--hours", "1", "--seed", "42",
                    "--visualize", str(output), "--json",
                ])
            result = json.loads(stream.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(result["visualization"], str(output))


if __name__ == "__main__":
    unittest.main()
