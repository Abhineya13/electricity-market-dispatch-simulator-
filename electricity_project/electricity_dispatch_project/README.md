# Electricity Dispatch Auction & Battery Storage

A reproducible Python simulation of the simplified UK hourly pay-as-clear electricity auction described in the take-home assessment. It uses **only the provided `data.xlsx`** and produces hourly market prices, hourly generation by technology and plant, and a storage extension.

## Quick start

Requires Python 3.10+.

```bash
python -m pip install -r requirements.txt
python dispatch.py --input data.xlsx --output outputs
python -m unittest discover -s tests -v
```

Outputs in `outputs/`:

| File | Contents |
| --- | --- |
| `baseline_hourly.csv` | Hour, demand, clearing price, wind/solar/gas generation, unmet demand, renewable curtailment |
| `storage_hourly.csv` | Same hourly results after storage, plus battery charge/discharge and state of charge |
| `plant_dispatch_baseline.csv` | Generation per individual plant, baseline |
| `plant_dispatch_with_storage.csv` | Generation per individual plant, storage case |
| `price_comparison.png` | Clearing prices in the two scenarios |
| `generation_mix.png` | Hourly wind/solar/gas production after storage |
| `battery_operation.png` | Charge/discharge and state of charge |
| `summary.txt` | Printed headline figures |

## 1. Baseline auction

For each hour, available wind and solar generation is capacity (MW) multiplied by the hour-specific load factor. A one-hour period means that one MW of output equals one MWh of energy over the interval.

All wind and solar submit **£0/MWh** offers. Gas offers equal variable fuel cost:

\[
\text{gas bid (£/MWh electricity)} = \frac{\text{gas price (pence/therm)}\times34.121}{100\times\text{efficiency}}.
\]

Example: 100 pence/therm and efficiency 0.50 gives **£68.242/MWh**.

The auction orders all offers from cheapest to most expensive and dispatches until demand is met or all supply is used. For multiple generators at the *same* price, dispatch is shared **pro rata to available capacity** to avoid arbitrary preference for wind over solar. The last partially or fully accepted price is the uniform clearing price paid to all dispatched generators. Dispatchable gas can be partially loaded; minimum stable load, startup, ramping and network constraints are deliberately omitted because they are not specified in the data.

**Scarcity:** The provided data has hours where all generation combined cannot meet demand. No offer price satisfies the exact definition of a marginal clearing price in those hours. They appear as **blank / empty `price_gbp_mwh`** in the CSV, and the shortfall is reported in `unserved_mwh`. These values are **not** zero-price hours.

## 2. Storage extension

Illustrative parameters (in the `Battery` dataclass):

| Parameter | Value |
| --- | ---: |
| Power rating | 2,000 MW (max charging or discharging each hour) |
| Energy capacity | 8,000 MWh (4-hour battery at rated power) |
| Round-trip efficiency | 90% |
| Charge efficiency | sqrt(0.90) |
| Discharge efficiency | sqrt(0.90) |
| Initial charge | 50% (4,000 MWh) |
| Terminal charge | 50% (4,000 MWh), no end-of-week energy subsidy |
| Illustrative throughput cost | £2 per MWh charged and £2 per MWh discharged |
| Hour length | 1 hour |

**Participation choice:** The storage asset is a **price-taking, committed day-ahead schedule**, not a separately ranked generator offer. First, the model calculates the baseline auction. Second, it solves a linear programme to choose charging (a positive addition to demand) and discharging (a committed positive injection) to maximise anticipated arbitrage using baseline prices, subject to SoC and efficiency constraints. Finally, it **re-runs the merit-order auction** after adding battery charging load and subtracting committed battery output from the residual demand. This produces a market price that can differ from the baseline forecast. The battery does not revise its planned schedule after seeing the updated price.

For battery inventory:

\[
SoC_t=SoC_{t-1}+\eta_c Charge_t-Discharge_t/\eta_d
\]

For market balance (including shortages):

\[
Wind_t+Solar_t+Gas_t+BatteryDischarge_t+Unserved_t=Demand_t+BatteryCharge_t
\]

**Scarcity scheduling proxy:** In baseline hours without any defined clearing price, the battery scheduler uses **£500/MWh** as an *illustrative value of avoiding unserved demand*. This is a forecast signal in the storage optimisation **only**. It is **not** recorded or asserted to be an actual electricity clearing price. This explicit modelling choice encourages discharge in scarcity hours.

The optimisation takes the entire 168-hour sequence as known (**perfect foresight**). This is an *illustrative retrospective study*, not a claim that market participants would know future realised prices. The efficiency loss plus positive throughput cost discourages simultaneous charging and discharging; the implementation checks and rejects any such schedule. No grid export limit, battery degradation model beyond the stated proxy, reserve services, demand response, penalties for outages, or strategic bidding are represented.

## 3. Important interpretation of results

With the default battery settings, storage reduces electricity shortage but does **not** necessarily reduce the arithmetic average of the *defined* hourly clearing prices. There is no single legitimate price series for all 168 hours under the assessment's literal definition because some hours are unserved. Mean price is therefore calculated only for clearing hours and is subject to selection effects when storage changes the set of clearing hours. Avoid concluding that battery storage always reduces prices.

Total wind and solar generation is an **output** of dispatch, not the sum of capacity times load factor: some available zero-bid renewables may be curtailed. Tied zero-bid offers are shared pro rata.

**Simplifications:** This is the stylised system described in the assessment, not a reproduction of the actual UK balancing mechanism or settlement process. No network congestion, transmission losses, carbon pricing, gas non-fuel variable O&M, thermal minimum outputs, ramp limits, or generator commitment constraints are assumed. One interval is precisely one hour; only the supplied seven worksheets matter.

## 4. Code structure

- `read_workbook()` — reads the seven data sheets from the supplied XLSX (standard-library-only XLSX reader).
- `validate_inputs()` — checks sheet names, hours, capacities, efficiencies and load factors.
- `gas_bid()` — converts pence/therm to £/MWh electrical.
- `auction()` — clears a single hour, including shortages and tied bids.
- `schedule_battery()` — optimises charge/discharge and state of charge with `scipy.optimize.linprog`.
- `simulate()` — compares the baseline and committed-battery scenarios.
- `make_plots()` — generates legible figures for the report.
- `tests/test_dispatch.py` — boundary cases, unit conversion, storage constraints, and conservation on the real dataset.

## 5. Further improvements

A more ambitious extension would co-optimise storage and generation using bids and scarcity penalties, or model a separate battery discharge offer curve rather than a fixed price-taking committed schedule. The current approach is deliberately simpler, transparent, and reproducible using the assessment's inputs.
