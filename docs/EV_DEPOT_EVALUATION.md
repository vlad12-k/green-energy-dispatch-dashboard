# EV Depot Scenario Evaluation

## Purpose and limits

This offline evaluation compares the existing `immediate_charge_in_vehicle_id_order` baseline with `carbon_aware_edf_then_bounded_carbon_shift_v1`. It does not add or invoke a production scheduler. It evaluates only explicitly labelled synthetic profiles and makes no claim about real emissions, grid operation, hardware control, market activity, or global scheduling optimality.

The checked-in suite is `examples/ev_depot_evaluation_scenarios.json` (suite version `1.0`, provenance `synthetic:ev-depot-evaluation-v1`). Every profile row is classified as `synthetic_scenario`, uses `simulation-only` geography, and includes UTC interval bounds and its intensity unit. The loader rejects non-synthetic provenance, inconsistent units, invalid scenario data, duplicate IDs, more than 16 scenarios, and files larger than 512 KiB. The bundled scenarios use `gCO2/kWh`; a separately labelled `gCO2e/kWh` suite is supported, but the two units cannot be mixed within one suite or compared implicitly.

## Scenario selection

The 11 cases are deliberately small and deterministic. Their categories cover:

| Scenario | Coverage and reason for inclusion |
| --- | --- |
| `flexible-single-vehicle` | One vehicle, two intensities, flexible charging; exercises a positive equivalent-service proxy difference. |
| `identical-window-fleet` | Three equal-deadline vehicles with identical windows and depot competition; tests tie order and a known heuristic limitation. |
| `deadline-order-matters` | Different departure times; EDF demonstrates completion where the ID-ordered baseline fails. |
| `heavy-depot-competition` | Four competing vehicles with aggregate demand close to available capacity. |
| `partial-overlapping-windows` | Half-hour, partially overlapping availability boundaries inside hourly carbon intervals. |
| `constant-carbon-no-improvement` | Constant intensity means every feasible charging allocation has the same proxy; no shift can reduce the proxy. |
| `tight-but-feasible-demand` | Demand close to an individual charger's two-hour physical limit. |
| `individual-capacity-infeasible` | A vehicle's request exceeds its charger-times-availability upper bound, which is a direct infeasibility proof. |
| `tiny-energy-boundary` | A `1e-9 kWh` request with zero intensity. |
| `maximum-numeric-boundary` | Existing supported upper-bound numeric inputs. |
| `zero-and-repeated-carbon-values` | Zero and repeated intensity values, including equal proxy outcomes. |

All vehicle requests, charger ratings, depot capacity, and carbon values are synthetic fixture inputs. No NESO observations are used.

## Metrics and interpretation

For each scenario the JSON result records the scenario ID/version, categories, assumptions, input provenance, time horizon, interval count, vehicle count, and depot limit. For each existing strategy it records required, delivered and unmet energy; per-vehicle totals and completion; completion classification; all existing constraint checks; peak depot power; aggregate capacity utilisation; carbon proxy and unit; and binding operational constraints.

The proxy is the existing scheduler's `sum(charged_energy_kWh * synthetic_intensity_value)` and is labelled `gCO2` or `gCO2e` according to the input contract. It is not measured emissions. The signed difference is `baseline_proxy - carbon_aware_proxy`: positive means a lower carbon-aware proxy, zero means equal proxies, and negative means a higher carbon-aware proxy. Percentage difference uses the baseline proxy as denominator; when that proxy is zero, the percentage is unavailable with an explicit reason.

A proxy comparison is eligible only if **both** strategies meet every vehicle's request under the shared full-service criterion. Partial delivery is never described as an improvement. Unavailable comparisons contain a reason. The completion tolerance is inherited from PR #10: a positive shortfall is accepted only when it is no more than `min(2 * ulp(required_energy_kWh), required_energy_kWh * 1e-12)`. The evaluator uses that same predicate for per-vehicle completion and comparison eligibility.

## Feasibility semantics

- `feasible_schedule_demonstrated`: at least one of the two production strategies produced a complete schedule satisfying reported constraints.
- `feasibility_not_proven`: neither production strategy completed the request and the evaluator has no direct proof of impossibility. A heuristic failure is not an infeasibility claim.
- `scenario_proven_infeasible`: a vehicle's request exceeds its own maximum possible energy (`charger kW * full availability hours`). This is a sufficient proof for that case, not a general infeasibility solver.

A test-only discrete reference checker independently enumerates a narrowly bounded integer grid: at most four 60-minute slots, three vehicles, 1 kW/1 kWh integer quanta, individual/depot power at most 3 kW, energy request at most 8 kWh per vehicle, integer non-negative carbon intensity, and availability aligned to slot boundaries. It retains the minimum proxy for each remaining-energy state and has a 10,000-state limit. Its results apply only to that discretised grid and do not prove continuous-time feasibility or optimality. In particular, the identical-window case is grid-feasible in this checker even though both existing heuristics fail to complete it; the evaluator therefore correctly leaves its strategy-based classification at `feasibility_not_proven`.

## Actual deterministic fixture results

The following summary is asserted against the evaluated fixtures by `app/tests/test_ev_depot_evaluation.py`:

| Result | Count |
| --- | ---: |
| Scenarios evaluated | 11 |
| Complete schedule demonstrated by either existing strategy | 8 |
| Proven infeasible by the per-vehicle capacity bound | 1 |
| Feasibility not proven by the production strategies | 2 |
| Equivalent-service comparisons | 7 |
| Lower carbon-aware synthetic proxy | 3 |
| Equal synthetic proxy | 4 |
| Higher carbon-aware synthetic proxy | 0 |

The zero observed regressions are a result of this fixture set, not a requirement imposed on the heuristic. A separate unit test verifies that negative signed and percentage differences are retained if a candidate proxy is higher. No result is evidence of real-world emissions performance.

## Robustness coverage

Tests check deterministic JSON output, required categories, equal-deadline cases, vehicle-list reordering, changed vehicle IDs (including the legacy baseline's ID-order sensitivity), completion and comparison eligibility, energy conservation, all reported operational constraint checks, zero and repeated intensity values, a positive/zero/negative proxy difference, mixed-unit rejection, partial interval boundaries, tiny values, maximum supported values, and the PR #10 large-request false-completion counterexample. The latter confirms that a `1,000,000,000 kWh` request with `999,999,999.9995 kWh` delivered remains incomplete and comparison-ineligible.

## Complexity and bounded execution

Let `V` be vehicle count and `P` the actual number of temporal segments after carbon-interval and vehicle-availability boundaries are combined. Each of at most 50 vehicles contributes only its availability start and end once over the horizon; those endpoints can split at most two segments each. Thus, with 96 carbon intervals, `P <= 96 + 2V <= 196` (not `96 * 2V`). The existing greedy seed runs in `O(P * V^2)` worst-case time: each segment can process at most one completion event per vehicle, and each event rebuilds a rate allocation across at most `V` vehicles. The two carbon-shift passes examine at most `O(V * P^2)` source/target pairs, plus `O(V * P log P)` ordering work. Reporting scans the bounded interval/segment and vehicle data. With `P <= 196`, `V <= 50` and 96 intervals, these bounds are finite; no combinatorial search is used by the normal evaluator. The optional reference checker has explicit small-instance and state-count limits and is not called by the normal evaluator.

Run a local, non-CI timing probe with:

```bash
python -m app.benchmark_ev_depot_evaluation --iterations 3
```

It reports the Python/runtime platform, CPU metadata available from the host, interval/vehicle/segment counts, and measured elapsed-time distribution separately from deterministic scenario JSON. Timing depends on local hardware and load; there are no fragile wall-clock assertions in the test suite.

A recorded three-iteration probe on macOS 26.6.2, arm64 (`arm` processor metadata, 10 logical CPUs), Python 3.14.7, evaluated the 96-interval/50-vehicle scenario with 196 temporal segments. Observed elapsed times were 0.2854 s minimum, 0.2855 s median and 0.3236 s maximum. This is a local measurement only, not a CI threshold or a general performance guarantee.

## Reproduction

Run the bundled suite without network, database, cloud, market, or hardware dependencies:

```bash
python -m app.evaluate_ev_depot_scenarios
```

To evaluate a local JSON suite within the same validation and size limits:

```bash
python -m app.evaluate_ev_depot_scenarios --suite path/to/scenarios.json
```

The command writes deterministic JSON to standard output. Invalid, mixed-unit, oversized, or otherwise unsupported suites fail with a clear command-line validation error. The existing single-scenario CLI and its contract are unchanged.

## Known limitations

The carbon-aware scheduler is a deterministic heuristic with a bounded number of shift passes. It may fail to find a schedule even when one exists, and it is not guaranteed to minimise the synthetic proxy. The evaluator only proves infeasibility using the stated individual-capacity upper bound; broader infeasibility would require a separate proof method. The discrete reference checker is test-only and intentionally excludes fractional time/power/energy. The scenarios do not represent fleet distributions, real carbon observations, tariffs, charging hardware, or field operation.
