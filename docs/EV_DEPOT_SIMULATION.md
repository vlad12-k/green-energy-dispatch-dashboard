# Carbon-aware EV depot advisory simulation

This is a deterministic, price-free software simulation. It does not fetch live carbon data, make electricity-market recommendations, communicate with chargers, control hardware, or verify real-world emissions reductions.

## Existing immediate-charging baseline

`immediate_charge_in_vehicle_id_order` remains the reference baseline. It processes time chronologically, splits intervals at vehicle-availability boundaries, and allocates capacity in vehicle-ID order. It respects individual charger power, aggregate depot power, availability, scenario bounds and remaining energy. Capacity is re-used when a vehicle completes charging. The public `SimulationResult` fields and established baseline behaviour are preserved.

`baseline_feasible` (also exposed by the backwards-compatible `feasible` field) retains the existing baseline's 1e-9 kWh absolute completion tolerance. For carbon-proxy comparison, `baseline_requirements_met_for_comparison` separately checks full-precision per-vehicle delivery, allowing at most two ULPs of representational error capped at 1e-12 of the request. Comparison eligibility requires this stricter check and a complete carbon-aware schedule; when the representational tolerance is used, reported unmet energy is zero. Detailed comparison reports retain full-precision floating-point values, while the nested legacy `SimulationResult` retains its established nine-decimal rounding. This prevents presentation rounding or the legacy baseline tolerance from making unequal service appear comparable. The baseline's `feasible_alternative_schedule` remains `unknown`; a failed baseline alone does not establish that no other schedule is feasible.

## Carbon-aware scenario contract

A `CarbonAwareDepotScenario` extends the existing depot scenario with one carbon-intensity row for every depot interval. Each row requires UTC start/end timestamps, a finite non-negative value, an explicit intensity unit, source classification, source ID, simulation geography and provenance label. The profile must match the scenario's interval boundaries exactly, in order, with no duplicates or gaps; every row must use the same unit and source metadata.

The current scheduler deliberately accepts only synthetic inputs:

- `source_classification`: `synthetic_scenario`
- `source_id`: `synthetic-scenario`
- `geography_code`: `simulation-only`
- `provenance_label`: a label beginning with `synthetic:`

External observations, forecasts, actuals and estimates are not accepted. In particular, a retrospective NESO forecast cannot be represented as a real-world forecast that was available to the scheduler at a historical origin. Supporting external data requires a separately reviewed point-in-time provenance adapter; no such adapter or live data acquisition is part of this milestone.

Intensity units are limited to `gCO2/kWh` and `gCO2e/kWh`. All rows in a scenario must use one unit. The scheduler does not convert between them. The weighted-energy proxy is labelled `gCO2` or `gCO2e` to match the input unit.

For bounded local execution, a carbon-aware scenario is limited to 96 intervals and 50 vehicles; the CLI rejects JSON files larger than 256 KiB. To keep weighted-proxy and power arithmetic finite, each carbon intensity, vehicle energy request, vehicle charging limit and depot power limit is capped at 1,000,000,000 in its declared unit. This is a numerical safety bound, not an operational limit. Availability windows may start or end within an interval. The scheduler splits intervals at those boundaries and reports the resulting segment-level allocations.

## Carbon-aware scheduling strategy

The strategy is `carbon_aware_edf_then_bounded_carbon_shift_v1`:

1. **Serve energy first.** A chronological earliest-departure-first (EDF) greedy pass orders active vehicles by availability end, then vehicle ID. It allocates power up to individual charger and depot limits, reusing capacity as requests complete.
2. **Improve the carbon proxy only after a complete service schedule is found.** Up to two deterministic passes, in stable vehicle/time order, move energy from a higher-intensity segment to a lower-intensity segment only when the vehicle is available and both its charger and the depot have spare capacity. Each move preserves that vehicle's delivered energy. If EDF does not complete all requests, the scheduler does not make carbon-directed shifts.

This is a bounded heuristic, not a global optimizer. Its result is a demonstrated schedule when complete; a failure to find a complete schedule is reported as `complete_schedule_not_demonstrated`, not as proof that the scenario is infeasible. The scheduler does not claim global optimality.

`charging_power_kw` for an allocation is average power over that complete segment (`energy_kwh / segment_hours`). The segment boundaries make availability and carbon intensity constant within that segment. These are segment-average schedule allocations, not an event-by-event replay of the baseline's within-segment vehicle-ID dispatch; the existing public `SimulationResult` remains the canonical baseline output. Results include per-vehicle energy, segment allocations, interval-average and peak depot loads, and runtime checks for scenario bounds, availability, vehicle power, depot power and energy conservation. Binding-constraint fields identify observed capacity saturation and allocations reaching a vehicle availability end; they are descriptive, not a proof of a global bottleneck.

## Carbon-proxy comparison

For each strategy the proxy is:

`sum(segment_energy_kwh * segment_intensity_value)`

The baseline and carbon-aware proxies are reported separately. An equivalent-service comparison is eligible only when both strategies fulfil every vehicle's required energy. Otherwise, comparison differences are `null`; the per-strategy proxies still describe the energy that was delivered and must not be interpreted as equal-service savings. `comparison_unavailable_reason` explains why the equal-service comparison is unavailable; `percentage_difference_unavailable_reason` separately explains a missing percentage when the baseline proxy is zero.

`absolute_carbon_proxy_reduction` is the signed baseline-minus-carbon-aware difference: positive means a lower carbon-aware proxy, negative means a higher one. `percentage_carbon_proxy_reduction` uses the baseline proxy as its denominator and is `null` when that denominator is zero. Neither field represents measured or verified emissions reductions.

## Reproducible synthetic example

Run from the repository root, with no network or database configuration:

```bash
python -m app.simulate_carbon_aware_ev_depot examples/carbon_aware_ev_depot_demo.json
```

The example requests 8 kWh for one vehicle over two one-hour intervals. Its explicitly synthetic intensity profile is 400 then 100 `gCO2/kWh`; the vehicle charger is limited to 7 kW and the depot to 10 kW.

- Immediate baseline: 7 kWh in the first interval and 1 kWh in the second; proxy **2,900 gCO2**.
- Carbon-aware heuristic: 1 kWh in the first interval and 7 kWh in the second; proxy **1,100 gCO2**.
- Both schedules deliver the same required **8 kWh**; comparison is eligible. The simulated proxy difference is **1,800 gCO2 (62.07%)**.

These are deterministic calculations over synthetic inputs, not operational evidence, a measured carbon footprint, or a real-world emissions-reduction claim.

## Verification and future scope

Focused offline checks:

```bash
python -m pytest -q app/tests/test_ev_depot.py app/tests/test_carbon_aware_scheduler.py
python -m app.simulate_carbon_aware_ev_depot examples/carbon_aware_ev_depot_demo.json
```

Implemented: bounded synthetic profile validation, service-first EDF scheduling, bounded lower-intensity shifts, per-strategy feasibility and proxy reporting, deterministic JSON output, the local CLI example, and the stateless FastAPI simulation endpoint used by the operator dashboard.

Not implemented: external source ingestion or point-in-time verification, global optimisation or general infeasibility proofs, hardware control, market trading, or operational deployment. Supporting external data or actuation remains separate future work requiring its own requirements and safety review.
