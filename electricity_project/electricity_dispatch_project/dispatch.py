"""Simplified hourly UK electricity pay-as-clear auction with illustrative battery.

Run: python dispatch.py --input data.xlsx --output outputs

Only the supplied seven worksheets are used; no external market datasets.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
from scipy.optimize import linprog

THERMS_PER_MWH = 34.121
PENCE_PER_POUND = 100.0
EPS = 1e-8
SCARCITY_SIGNAL_GBP_MWH = 500.0  # scheduling proxy only; not a clearing price


@dataclass(frozen=True)
class Battery:
    power_mw: float = 2000.0
    energy_mwh: float = 8000.0
    round_trip_efficiency: float = 0.90
    initial_soc_fraction: float = 0.50
    throughput_cost_gbp_mwh: float = 2.0  # illustrative cycling penalty per MWh in or out

    @property
    def charge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5

    @property
    def discharge_efficiency(self) -> float:
        return self.round_trip_efficiency ** 0.5

    def validate(self) -> None:
        if self.power_mw <= 0 or self.energy_mwh <= 0:
            raise ValueError("Battery power and energy must be positive")
        if not 0 < self.round_trip_efficiency <= 1:
            raise ValueError("Battery round-trip efficiency must be in (0,1]")
        if not 0 <= self.initial_soc_fraction <= 1:
            raise ValueError("Initial state of charge must be in [0,1]")
        if self.throughput_cost_gbp_mwh < 0:
            raise ValueError("Throughput cost must not be negative")


def _cell_column(reference: str) -> int:
    letters = ''.join(c for c in reference if c.isalpha())
    n = 0
    for letter in letters:
        n = 26 * n + ord(letter.upper()) - ord('A') + 1
    return n - 1


def read_workbook(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Read simple tabular XLSX worksheets with Python's standard library.

    Handles numbers, shared strings and inline strings. Excel calculations should be
    saved as values in the source workbook; this assessment uses numeric inputs.
    """
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
          'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
          'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}
    with ZipFile(path) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [''.join(t.text or '' for t in item.findall('.//m:t', ns))
                      for item in root.findall('m:si', ns)]
        wb = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'] for r in rels}
        result = {}
        for sheet in wb.findall('m:sheets/m:sheet', ns):
            name = sheet.attrib['name']
            target = targets[sheet.attrib[f'{{{ns["r"]}}}id']]
            sheet_path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            root = ET.fromstring(archive.read(sheet_path))
            matrix = []
            for row in root.findall('m:sheetData/m:row', ns):
                cells = {}
                for cell in row.findall('m:c', ns):
                    col = _cell_column(cell.attrib['r'])
                    raw = cell.find('m:v', ns)
                    typ = cell.attrib.get('t')
                    if typ == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//m:t', ns))
                    elif raw is None or raw.text is None:
                        value = None
                    elif typ == 's':
                        value = shared[int(raw.text)]
                    elif typ in ('str', 'e'):
                        value = raw.text
                    else:
                        number = float(raw.text)
                        value = int(number) if number.is_integer() else number
                    cells[col] = value
                if cells:
                    matrix.append([cells.get(i) for i in range(max(cells) + 1)])
            if not matrix:
                raise ValueError(f'Sheet {name} is empty')
            headers = [str(h) for h in matrix[0]]
            result[name] = [dict(zip(headers, values)) for values in matrix[1:]]
    return result


def validate_inputs(tables: dict[str, list[dict]]) -> list[int]:
    required = {'windplants', 'wind_loadfactors', 'solarplants', 'solar_loadfactors',
                'gasplants', 'demand', 'gas_prices'}
    if set(tables) != required:
        raise ValueError(f'Worksheet mismatch; missing={required - set(tables)}, extra={set(tables) - required}')
    hours = [int(x['hour']) for x in tables['demand']]
    if not hours or hours != list(range(1, len(hours) + 1)):
        raise ValueError('Demand hours must be consecutive and start at 1')
    for tab in ('wind_loadfactors', 'solar_loadfactors', 'gas_prices'):
        if [int(r['hour']) for r in tables[tab]] != hours:
            raise ValueError(f'Hours in {tab} do not match demand')
    for technology in ('wind', 'solar'):
        names = {p['name'] for p in tables[f'{technology}plants']}
        expected = set(tables[f'{technology}_loadfactors'][0]) - {'hour'}
        if names != expected:
            raise ValueError(f'{technology} plant names differ from load factor columns')
        for plant in tables[f'{technology}plants']:
            if float(plant['capacity']) <= 0:
                raise ValueError('Plant capacity must be positive')
        for record in tables[f'{technology}_loadfactors']:
            if any(not 0 <= float(record[name]) <= 1 for name in names):
                raise ValueError(f'Invalid {technology} load factor in hour {record["hour"]}')
    for p in tables['gasplants']:
        if float(p['capacity']) <= 0 or not 0 < float(p['efficiency']) <= 1:
            raise ValueError('Invalid gas plant capacity or efficiency')
    if any(float(r['demand']) < 0 for r in tables['demand']):
        raise ValueError('Demand must be non-negative')
    if any(float(r['price']) < 0 for r in tables['gas_prices']):
        raise ValueError('Gas prices must be non-negative')
    return hours


def gas_bid(gas_pence_per_therm: float, efficiency: float) -> float:
    """£/electrical MWh = (GBp/therm × therm/electrical MWh) / 100."""
    return (gas_pence_per_therm * THERMS_PER_MWH / PENCE_PER_POUND) / efficiency


def auction(tables: dict, idx: int, demand_mwh: float) -> dict:
    """Dispatch cheapest generators first; the last used bid sets market price.

    Each hour lasts one hour, so MW capacity produces the same number of MWh.
    Equal-priced offers are allocated pro rata by available capacity;
    all wind and solar have the same zero price.
    """
    if demand_mwh < -EPS:
        raise ValueError('Demand cannot be negative')
    bids = []
    for technology in ('wind', 'solar'):
        lf = tables[f'{technology}_loadfactors'][idx]
        for plant in tables[f'{technology}plants']:
            available = float(plant['capacity']) * float(lf[plant['name']])
            bids.append((0.0, plant['name'], technology, available))
    gas_price = float(tables['gas_prices'][idx]['price'])
    for plant in tables['gasplants']:
        bids.append((gas_bid(gas_price, float(plant['efficiency'])),
                     plant['name'], 'gas', float(plant['capacity'])))
    bids.sort(key=lambda x: (x[0], x[1]))
    remaining = max(0.0, demand_mwh)
    mix = {'wind': 0.0, 'solar': 0.0, 'gas': 0.0}
    generation = {name: 0.0 for _, name, _, _ in bids}
    marginal_price = 0.0 if remaining <= EPS else None
    # Dispatch bids in ascending price groups. For equal-cost renewable bids,
    # allocate pro rata, avoiding arbitrary favouring of wind or solar.
    cursor = 0
    while cursor < len(bids) and remaining > EPS:
        bid = bids[cursor][0]
        end = cursor + 1
        while end < len(bids) and abs(bids[end][0] - bid) < EPS:
            end += 1
        group = bids[cursor:end]
        group_capacity = sum(available for _, _, _, available in group)
        group_output = min(remaining, group_capacity)
        for _, name, technology, available in group:
            dispatched = group_output * available / group_capacity if group_capacity > EPS else 0.0
            generation[name] = dispatched
            mix[technology] += dispatched
        remaining -= group_output
        if group_output > EPS:
            marginal_price = bid
        cursor = end
    if remaining <= EPS:
        remaining = 0.0
    else:
        marginal_price = None  # no accepted bid can clear this demand
    renewable_available = sum(v for _, _, tech, v in bids if tech != 'gas')
    return {'price_gbp_mwh': marginal_price, 'wind_mwh': mix['wind'],
            'solar_mwh': mix['solar'], 'gas_mwh': mix['gas'],
            'unserved_mwh': remaining,
            'renewable_curtailed_mwh': max(0.0, renewable_available - mix['wind'] - mix['solar']),
            'plant_dispatch': generation}


def schedule_battery(baseline_prices: np.ndarray, battery: Battery) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Perfect-foresight illustrative arbitrage against baseline (not realised) prices.

    Linear program selects grid-side MWh charged/discharged each hour, with an
    energy balance, hourly power limits, and terminal SoC = initial SoC.
    There are NO simultaneous charge/discharge constraints, but strict efficiency
    losses and positive throughput cost make simultaneous cycling suboptimal.
    """
    battery.validate()
    n = len(baseline_prices)
    if not np.isfinite(baseline_prices).all():
        raise ValueError('Battery scheduling prices must be finite')
    # Variables: [charge[0:n], discharge[0:n], soc[0:n]]
    c = np.concatenate([baseline_prices + battery.throughput_cost_gbp_mwh,
                        -baseline_prices + battery.throughput_cost_gbp_mwh,
                        np.zeros(n)])
    Aeq = np.zeros((n + 1, 3 * n))
    beq = np.zeros(n + 1)
    eta_c, eta_d = battery.charge_efficiency, battery.discharge_efficiency
    initial = battery.energy_mwh * battery.initial_soc_fraction
    for t in range(n):
        Aeq[t, t] = -eta_c
        Aeq[t, n + t] = 1 / eta_d
        Aeq[t, 2 * n + t] = 1
        if t:
            Aeq[t, 2 * n + t - 1] = -1
        else:
            beq[t] = initial
    Aeq[n, 3 * n - 1] = 1
    beq[n] = initial
    bounds = ([(0, battery.power_mw)] * (2 * n)
              + [(0, battery.energy_mwh)] * n)
    result = linprog(c, A_eq=Aeq, b_eq=beq, bounds=bounds, method='highs')
    if not result.success:
        raise RuntimeError(f'Battery optimisation failed: {result.message}')
    charge, discharge, soc = np.split(np.maximum(result.x, 0.0), 3)
    charge[np.abs(charge) < 1e-6] = 0
    discharge[np.abs(discharge) < 1e-6] = 0
    if np.any((charge > 1e-6) & (discharge > 1e-6)):
        raise RuntimeError('Unexpected simultaneous battery charge/discharge')
    return charge, discharge, soc


def simulate(tables: dict, battery: Battery) -> tuple[list[dict], list[dict], list[dict]]:
    hours = validate_inputs(tables)
    baseline = []
    baseline_detail = []
    for idx, hour in enumerate(hours):
        demand = float(tables['demand'][idx]['demand'])
        result = auction(tables, idx, demand)
        baseline.append({'hour': hour, 'demand_mwh': demand, **{k: v for k, v in result.items() if k != 'plant_dispatch'}})
        baseline_detail.append({'hour': hour, 'scenario': 'baseline', **result['plant_dispatch']})
    prices = np.array([np.nan if r['price_gbp_mwh'] is None else r['price_gbp_mwh'] for r in baseline])
    # If demand exceeds available generation, no marginal bid clears the market.
    # For battery scheduling ONLY, use an explicit scarcity-value proxy.
    planning_prices = np.where(np.isfinite(prices), prices, SCARCITY_SIGNAL_GBP_MWH)
    charge, discharge, soc = schedule_battery(planning_prices, battery)
    with_storage = []
    dispatch_detail = []
    for idx, hour in enumerate(hours):
        demand = float(tables['demand'][idx]['demand'])
        # Battery is committed, price-taking net injection / withdrawal. All remaining
        # generation is re-dispatched at its submitted bid against residual load.
        effective_demand = demand + float(charge[idx]) - float(discharge[idx])
        if effective_demand < -EPS:
            raise RuntimeError('Battery discharge exceeds demand plus charge')
        result = auction(tables, idx, max(effective_demand, 0.0))
        with_storage.append({'hour': hour, 'demand_mwh': demand,
                             'price_gbp_mwh': result['price_gbp_mwh'],
                             'wind_mwh': result['wind_mwh'], 'solar_mwh': result['solar_mwh'],
                             'gas_mwh': result['gas_mwh'], 'battery_charge_mwh': float(charge[idx]),
                             'battery_discharge_mwh': float(discharge[idx]),
                             'battery_soc_mwh': float(soc[idx]),
                             'unserved_mwh': result['unserved_mwh'],
                             'renewable_curtailed_mwh': result['renewable_curtailed_mwh']})
        detail = {'hour': hour, 'scenario': 'with_storage', **result['plant_dispatch']}
        dispatch_detail.append(detail)
    return baseline, with_storage, baseline_detail, dispatch_detail


def save_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_plots(baseline: list[dict], storage: list[dict], output: Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    hours = [r['hour'] for r in baseline]
    plt.figure(figsize=(12, 4.5))
    plt.plot(hours, [r['price_gbp_mwh'] for r in baseline], label='Without storage', lw=1.8)
    plt.plot(hours, [r['price_gbp_mwh'] for r in storage], label='With storage', lw=1.4)
    plt.xlabel('Hour'); plt.ylabel('Marginal electricity price (£/MWh)')
    plt.title('Hourly pay-as-clear auction prices'); plt.legend(); plt.grid(alpha=0.2)
    plt.tight_layout(); plt.savefig(output / 'price_comparison.png', dpi=170); plt.close()

    fig, ax = plt.subplots(figsize=(12, 5))
    series = [np.array([r[f'{tech}_mwh'] for r in storage]) for tech in ('wind', 'solar', 'gas')]
    ax.stackplot(hours, *series, labels=['Wind', 'Solar', 'Gas'], alpha=0.85)
    ax.plot(hours, [r['demand_mwh'] for r in storage], color='black', lw=1, label='End-user demand')
    ax.set(xlabel='Hour', ylabel='Generation (MWh)', title='Generation mix with storage')
    ax.legend(loc='upper right', ncol=4); ax.grid(alpha=0.15)
    fig.tight_layout(); fig.savefig(output / 'generation_mix.png', dpi=170); plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.bar(hours, [r['battery_discharge_mwh'] - r['battery_charge_mwh'] for r in storage],
           width=1.0, label='Net battery output (positive = discharge)')
    ax.set(xlabel='Hour', ylabel='Battery net output (MWh)', title='Battery schedule and state of charge')
    ax2 = ax.twinx()
    ax2.plot(hours, [r['battery_soc_mwh'] for r in storage], color='black', lw=1.5, label='SoC')
    ax2.set_ylabel('State of charge (MWh)')
    fig.tight_layout(); fig.savefig(output / 'battery_operation.png', dpi=170); plt.close(fig)


def print_summary(baseline: list[dict], storage: list[dict]) -> str:
    def total(rows, key):
        return sum(r[key] for r in rows)
    def average(rows):
        vals = [r['price_gbp_mwh'] for r in rows if r['price_gbp_mwh'] is not None]
        return sum(vals) / len(vals) if vals else float('nan')
    return '\n'.join([
        f'Hours: {len(baseline)}',
        f'Demand: {total(baseline, "demand_mwh"):,.1f} MWh',
        f'Price without / with storage: £{average(baseline):.2f} / £{average(storage):.2f} per MWh (simple hourly mean)',
        f'Gas generation without / with storage: {total(baseline, "gas_mwh"):,.1f} / {total(storage, "gas_mwh"):,.1f} MWh',
        f'Wind generation without / with storage: {total(baseline, "wind_mwh"):,.1f} / {total(storage, "wind_mwh"):,.1f} MWh',
        f'Solar generation without / with storage: {total(baseline, "solar_mwh"):,.1f} / {total(storage, "solar_mwh"):,.1f} MWh',
        f'Battery charging / discharging: {total(storage, "battery_charge_mwh"):,.1f} / {total(storage, "battery_discharge_mwh"):,.1f} MWh',
        f'Unserved electricity without / with storage: {total(baseline, "unserved_mwh"):,.1f} / {total(storage, "unserved_mwh"):,.1f} MWh',
        f'Hours without clearing price (insufficient supply), baseline / storage: {sum(r["price_gbp_mwh"] is None for r in baseline)} / {sum(r["price_gbp_mwh"] is None for r in storage)}',
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data.xlsx'))
    parser.add_argument('--output', type=Path, default=Path('outputs'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tables = read_workbook(args.input)
    battery = Battery()
    baseline, storage, baseline_detail, detail = simulate(tables, battery)
    save_csv(args.output / 'baseline_hourly.csv', baseline)
    save_csv(args.output / 'storage_hourly.csv', storage)
    save_csv(args.output / 'plant_dispatch_baseline.csv', baseline_detail)
    save_csv(args.output / 'plant_dispatch_with_storage.csv', detail)
    make_plots(baseline, storage, args.output)
    summary = print_summary(baseline, storage)
    (args.output / 'summary.txt').write_text(summary + '\n', encoding='utf-8')
    print(summary)


if __name__ == '__main__':
    main()
