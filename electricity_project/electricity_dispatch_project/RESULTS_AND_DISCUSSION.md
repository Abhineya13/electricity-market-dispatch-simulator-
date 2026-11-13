# Results and Discussion

## Objective

Simulate the hourly pay-as-clear market for the 168 hourly intervals supplied in `data.xlsx`. Calculate the marginal price and the dispatched generation by wind, solar and gas. Extend the model with an illustrative battery and compare market outcomes.

## Core method

For each hour, compute renewable availability from nameplate capacity and load factors. Gas offer prices equal the per-MWh fuel cost, converting gas prices from pence/therm to pounds/MWh and dividing by each plant's efficiency. Sort offers from cheapest to most expensive, accept supply until hourly demand is met and use the last accepted bid as the market price. Where same-price renewable offers are only partly dispatched, allocate proportionally to their availability.

## Observed dataset results

| Metric | Without storage | With storage |
| --- | ---: | ---: |
| Total end-user demand (MWh) | 4,050,186.5 | 4,050,186.5 |
| Wind dispatch (MWh) | 1,421,258.7 | 1,425,643.1 |
| Solar dispatch (MWh) | 785,464.2 | 790,119.6 |
| Gas dispatch (MWh) | 1,783,137.3 | 1,797,087.4 |
| Unserved demand (MWh) | 60,326.3 | 43,442.3 |
| Renewable curtailment (MWh) | 35,968.7 | 26,928.9 |
| Hours with valid marginal prices | 139 | 146 |
| Mean of valid hourly prices (£/MWh) | 62.09 | 64.48 |

The illustrative battery charges **61,058.4 MWh** and discharges **54,952.6 MWh** over the week, with the difference representing round-trip losses. Battery state of charge starts and ends at 4,000 MWh. The shortfall in demand falls by **16,884.0 MWh**, or about **28.0%**. Renewable curtailment also falls by approximately 9,040 MWh.

### Interpretation

Storage shifts generation requirements across time. During charging hours, the battery adds demand; during discharge hours, it offsets some end-user demand. In the dataset, this is particularly valuable in hours where even all conventional generation cannot supply full demand.

Crucially, **the dataset has 29 baseline hours where the total available generation cannot meet demand**. With the battery, 22 hours still lack sufficient supply. Under the assessment's explicit marginal-price definition, those intervals have **no valid market-clearing price** and are excluded from the numerical average. The simulation reports the shortfall instead of assigning a fictitious price.

The mean of *defined* prices rises from £62.09/MWh to £64.48/MWh. This should not be presented as an overall cost increase or decrease for consumers: the set of hours with a price changes, creating a selection effect, and the test does not specify prices during scarcity. A full cost or market-benefit calculation would need a consistent, explicitly defined scarcity-pricing and settlement framework.

### Illustrative battery assumptions

The battery has 2,000 MW maximum charging/discharging power and 8,000 MWh usable capacity, 90% round-trip efficiency, a 50% starting and ending SoC, and a £2/MWh throughput penalty per MWh in and out. Its 168-hour schedule is optimised using **baseline** prices as a perfect-foresight proxy. If supply is insufficient to clear an hour, **£500/MWh** is used *only inside the battery planning objective* as an illustrative avoided-shortfall value, **not** as a market-clearing price. The final market is cleared again with battery charging added to demand and battery discharging subtracted from demand. The storage plan does not update in response to the resulting prices.

### Limitations

This is a deliberately stylised auction as described in the assessment. It omits network constraints, fuel transport costs, plant operating constraints, losses on the grid, generator startup costs, carbon pricing, and strategic generator bidding. The battery is a price-taking committed schedule rather than an independently bid marginal unit; it uses future baseline prices known in hindsight. Battery outputs show what this transparent modelling choice implies, not a forecast of actual UK electricity prices.

## Reproducibility and tests

Run `python dispatch.py` to recreate the hourly CSVs and figures, then run `python -m unittest discover -s tests -v`. Ten automated tests verify unit conversion, merit order, tied bids, unmet demand, battery state-of-charge constraints, and hourly energy conservation on the real input workbook.
