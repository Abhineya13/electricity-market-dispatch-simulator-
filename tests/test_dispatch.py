"""Run: python -m unittest discover -s tests -v"""
import math
import unittest

import numpy as np

from dispatch import Battery, auction, gas_bid, read_workbook, schedule_battery, simulate, validate_inputs


def sample_data(demand=12, wind_factor=1, solar_factor=0):
    return {
        'windplants': [{'name': 'wind1', 'capacity': 10}],
        'wind_loadfactors': [{'hour': 1, 'wind1': wind_factor}],
        'solarplants': [{'name': 'solar1', 'capacity': 10}],
        'solar_loadfactors': [{'hour': 1, 'solar1': solar_factor}],
        'gasplants': [{'name': 'gas1', 'capacity': 10, 'efficiency': 0.5}],
        'demand': [{'hour': 1, 'demand': demand}],
        'gas_prices': [{'hour': 1, 'price': 100}],
    }


class AuctionTests(unittest.TestCase):
    def test_gas_bid_unit_conversion(self):
        self.assertAlmostEqual(gas_bid(100, 0.5), 68.242)

    def test_wind_then_gas_marginal(self):
        r = auction(sample_data(), 0, 12)
        self.assertAlmostEqual(r['wind_mwh'], 10)
        self.assertAlmostEqual(r['gas_mwh'], 2)
        self.assertAlmostEqual(r['price_gbp_mwh'], 68.242)
        self.assertAlmostEqual(r['unserved_mwh'], 0)

    def test_renewables_only_clears_at_zero(self):
        r = auction(sample_data(demand=8), 0, 8)
        self.assertEqual(r['price_gbp_mwh'], 0)
        self.assertAlmostEqual(r['renewable_curtailed_mwh'], 2)

    def test_equal_price_green_pro_rata(self):
        r = auction(sample_data(demand=10, solar_factor=1), 0, 10)
        self.assertAlmostEqual(r['wind_mwh'], 5)
        self.assertAlmostEqual(r['solar_mwh'], 5)

    def test_shortage_price_undefined(self):
        r = auction(sample_data(demand=30), 0, 30)
        self.assertIsNone(r['price_gbp_mwh'])
        self.assertAlmostEqual(r['unserved_mwh'], 10)

    def test_zero_demand(self):
        r = auction(sample_data(demand=0), 0, 0)
        self.assertEqual(r['price_gbp_mwh'], 0)
        self.assertAlmostEqual(r['gas_mwh'], 0)

    def test_invalid_load_factor(self):
        tables = sample_data()
        tables['wind_loadfactors'][0]['wind1'] = 1.2
        with self.assertRaises(ValueError):
            validate_inputs(tables)


class BatteryTests(unittest.TestCase):
    def test_flat_prices_do_not_cycle(self):
        c, d, soc = schedule_battery(np.array([50., 50., 50., 50.]), Battery())
        self.assertAlmostEqual(float(c.sum()), 0, places=5)
        self.assertAlmostEqual(float(d.sum()), 0, places=5)
        self.assertAlmostEqual(soc[-1], 4000, places=5)

    def test_arbitrage_preserves_energy_balance(self):
        b = Battery(power_mw=2, energy_mwh=5)
        c, d, soc = schedule_battery(np.array([10., 100., 10., 100.]), b)
        self.assertGreater(float(d.sum()), 0)
        self.assertTrue(np.all(soc >= -1e-6))
        self.assertTrue(np.all(soc <= b.energy_mwh + 1e-6))
        self.assertTrue(np.all(c <= b.power_mw + 1e-6))
        self.assertTrue(np.all(d <= b.power_mw + 1e-6))
        self.assertTrue(np.all((c < 1e-6) | (d < 1e-6)))
        self.assertAlmostEqual(float(soc[-1]), b.energy_mwh * b.initial_soc_fraction, places=5)
        for t in range(len(soc)):
            prev = soc[t - 1] if t else b.energy_mwh * b.initial_soc_fraction
            self.assertAlmostEqual(soc[t], prev + b.charge_efficiency * c[t] - d[t] / b.discharge_efficiency, places=5)

    def test_complete_uploaded_dataset_balances_every_hour(self):
        from pathlib import Path
        source = Path(__file__).resolve().parents[1] / 'data.xlsx'
        tables = read_workbook(source)
        baseline, storage, base_detail, detail = simulate(tables, Battery())
        self.assertEqual(len(baseline), 168)
        self.assertEqual(len(storage), 168)
        self.assertEqual(len(base_detail), 168)
        self.assertEqual(len(detail), 168)
        for b, s in zip(baseline, storage):
            self.assertAlmostEqual(b['wind_mwh'] + b['solar_mwh'] + b['gas_mwh'] + b['unserved_mwh'], b['demand_mwh'], places=4)
            self.assertAlmostEqual(s['wind_mwh'] + s['solar_mwh'] + s['gas_mwh'] + s['battery_discharge_mwh'] + s['unserved_mwh'], s['demand_mwh'] + s['battery_charge_mwh'], places=4)


if __name__ == '__main__':
    unittest.main()
