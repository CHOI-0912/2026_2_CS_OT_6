import unittest

from ambulance_sim.calibration import allocate_hourly_demand, normalize_profile_counts


class CalibrationTests(unittest.TestCase):
    def test_disaggregation_preserves_monthly_total(self):
        rates = allocate_hourly_demand(
            monthly_calls=310,
            days_in_month=31,
            population={"A": 1000, "B": 1000},
            elderly_share={"A": 0.1, "B": 0.4},
            elderly_effect=1.0,
            hourly_multipliers={8: 2.0, 9: 2.0},
        )
        recovered = 31 * sum(sum(row.values()) for row in rates.values())
        self.assertAlmostEqual(recovered, 310)
        self.assertGreater(rates[8]["B"], rates[8]["A"])
        self.assertEqual(rates[8]["A"], 2 * rates[7]["A"])

    def test_patient_counts_normalize(self):
        probabilities = normalize_profile_counts({"cardiac": 2, "minor": 6})
        self.assertEqual(probabilities, {"cardiac": 0.25, "minor": 0.75})


if __name__ == "__main__":
    unittest.main()
