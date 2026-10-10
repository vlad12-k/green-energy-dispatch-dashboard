# EV depot advisory simulation assumptions

This foundation is a deterministic, price-free simulation. It does not fetch electricity prices, make market recommendations, communicate with chargers, or issue control commands.

## Contract

- Scenario timestamps must be timezone-aware UTC timestamps. All windows must be positive and contained in the scenario horizon.
- Interval length is an integer number of minutes that divides evenly into an hour or is a whole number of hours. This keeps the regular schedule grid predictable.
- Vehicle IDs are unique; required energy and charging power are positive and finite. Each vehicle's charging limit cannot exceed the depot limit.
- No battery capacity, charger connector count, charging efficiency, minimum state of charge, or vehicle-specific charging curve is modelled.

## Baseline

`immediate_charge_in_vehicle_id_order` processes each interval from earliest to latest, assigning available depot capacity in ascending vehicle ID order. Charging is limited by vehicle power, depot power, remaining requested energy, and the exact overlap between availability and the interval. Output interval power is average kW across the full interval. Remaining energy above numerical tolerance is reported as unmet and marks the scenario infeasible; the simulator does not reject such scenarios at validation because reporting infeasibility is useful advisory output.

This is a transparent reference baseline, not a cost/carbon optimizer or operational recommendation. It has no electricity-price input and uses no real prices.
