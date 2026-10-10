# EV depot advisory simulation assumptions

This foundation is a deterministic, price-free simulation. It does not fetch electricity prices, make market recommendations, communicate with chargers, or issue control commands.

## Contract

- Scenario timestamps must be timezone-aware UTC timestamps. All windows must be positive and contained in the scenario horizon.
- Interval length is an integer number of minutes that divides evenly into an hour or is a whole number of hours. This keeps the regular schedule grid predictable.
- Vehicle IDs are unique; required energy and charging power are positive and finite. Each vehicle's charging limit cannot exceed the depot limit.
- `baseline_feasible` (also exposed through the backwards-compatible `feasible` field) means only that the named greedy baseline meets every energy request. A false value does not establish that no alternative schedule could work; `feasible_alternative_schedule` is explicitly `unknown` because this foundation does not search alternative schedules.

## Baseline

`immediate_charge_in_vehicle_id_order` processes each interval from earliest to latest, splitting it at availability boundaries. For each segment, available depot capacity is allocated in ascending vehicle ID order and resets for the next segment. Charging is limited by vehicle power, depot power, remaining requested energy, and the segment duration. This allows unused capacity to be reused immediately when a vehicle completes charging and prevents a vehicle's average interval power from being incorrectly treated as instantaneous depot load. Output interval power is average kW across the full interval. Remaining energy above numerical tolerance is reported as unmet and marks the baseline infeasible; this does not prove that no alternative schedule could satisfy demand.

This is a transparent reference baseline, not a cost/carbon optimizer or operational recommendation. It has no electricity-price input and uses no real prices.
