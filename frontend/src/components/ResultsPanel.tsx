import type { CarbonAwareSchedule, Scenario, SimulationResult } from '../api/client';
import { CarbonProfileChart, DepotLoadChart, ScheduleChart } from './Charts';
import { DispatchFlow } from './DispatchFlow';
import { formatValue } from '../utils/number-format';

function StatusBadge({ complete }: { complete: boolean }) {
  return complete
    ? <span className="status-pill status-complete">Complete schedule demonstrated</span>
    : <span className="status-pill status-incomplete">Complete schedule not demonstrated</span>;
}

function ConstraintList({ checks, id }: {
  checks: CarbonAwareSchedule['constraint_checks'];
  id: string;
}) {
  const labels: Record<string, string> = {
    scenario_bounds_respected: 'Scenario bounds',
    availability_windows_respected: 'Availability windows',
    vehicle_power_limits_respected: 'Vehicle charger limits',
    depot_power_limit_respected: 'Depot power limit',
    energy_conservation_respected: 'Energy conservation',
  };
  return (
    <details className="constraint-details">
      <summary>Constraint checks</summary>
      <ul id={id} className="constraint-list">
        {Object.entries(checks).map(([key, passed]) => (
          <li key={key}>
            <span className={passed ? 'check-mark' : 'cross-mark'} aria-hidden="true">{passed ? '✓' : '!'}</span>
            <span>{labels[key] ?? key.replaceAll('_', ' ')}</span>
            <strong>{passed ? 'Pass' : 'Review'}</strong>
          </li>
        ))}
      </ul>
    </details>
  );
}

function ScheduleCard({
  title,
  strategy,
  complete,
  delivered,
  unmet,
  allocations,
  aggregateLoad,
  peakPower,
  constraintChecks,
  scenario,
}: {
  title: string;
  strategy: string;
  complete: boolean;
  delivered: Record<string, number>;
  unmet: Record<string, number>;
  allocations: CarbonAwareSchedule['allocations'];
  aggregateLoad: CarbonAwareSchedule['aggregate_depot_load'];
  peakPower: number;
  constraintChecks: CarbonAwareSchedule['constraint_checks'];
  scenario: Scenario;
}) {
  const deliveredTotal = Object.values(delivered).reduce((sum, value) => sum + value, 0);
  const unmetTotal = Object.values(unmet).reduce((sum, value) => sum + value, 0);

  return (
    <article className="panel schedule-card">
      <header className="schedule-card-heading">
        <div><p className="eyebrow">SCHEDULE OUTPUT</p><h3>{title}</h3><p className="strategy-id">{strategy}</p></div>
        <StatusBadge complete={complete} />
      </header>
      {!complete && <p className="caveat-note">This heuristic did not demonstrate complete service. That does not prove that no feasible schedule exists.</p>}
      <div className="schedule-metrics">
        <div><span>Delivered energy</span><strong>{formatValue(deliveredTotal)} <small>kWh</small></strong></div>
        <div><span>Unmet energy</span><strong className={unmetTotal > 0 ? 'metric-warning' : ''}>{formatValue(unmetTotal)} <small>kWh</small></strong></div>
        <div><span>Peak depot power</span><strong>{formatValue(peakPower)} <small>kW</small></strong></div>
      </div>
      <ScheduleChart schedule={{ allocations } as CarbonAwareSchedule} scenario={scenario} label={title} />
      <DepotLoadChart rows={aggregateLoad} depotPowerKw={scenario.depot_power_kw} label={title} />
      <ConstraintList checks={constraintChecks} id={`${strategy}-constraints`} />
      <details className="vehicle-results-details">
        <summary>Per-vehicle service</summary>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Vehicle</th><th>Required (kWh)</th><th>Delivered (kWh)</th><th>Unmet (kWh)</th><th>Completion</th></tr></thead>
            <tbody>{scenario.vehicles.map((vehicle) => {
              const vehicleUnmet = unmet[vehicle.vehicle_id] ?? 0;
              const vehicleComplete = vehicleUnmet === 0;
              return <tr key={vehicle.vehicle_id}><td>{vehicle.vehicle_id}</td><td>{formatValue(vehicle.required_energy_kwh)}</td><td>{formatValue(delivered[vehicle.vehicle_id] ?? 0)}</td><td>{formatValue(vehicleUnmet)}</td><td><span className={vehicleComplete ? 'table-status-ok' : 'table-status-warn'}>{vehicleComplete ? 'Complete' : 'Incomplete'}</span></td></tr>;
            })}</tbody>
          </table>
        </div>
      </details>
    </article>
  );
}

export function ResultsPanel({ result, scenario }: { result: SimulationResult; scenario: Scenario }) {
  const difference = result.absolute_carbon_proxy_reduction;
  const diffClass = difference === null ? '' : difference > 0 ? 'positive-difference' : difference < 0 ? 'negative-difference' : 'zero-difference';
  const signedDifference = difference === null ? null : `${difference > 0 ? '+' : ''}${formatValue(difference)}`;

  return (
    <section className="results-section" aria-labelledby="results-heading">
      <div className="results-section-heading">
        <div><p className="eyebrow">EVALUATION / TWO STRATEGIES</p><h2 id="results-heading">Schedule comparison</h2></div>
        <span className="source-tag"><span aria-hidden="true">●</span> Synthetic input · {result.input_provenance.intensity_unit}</span>
      </div>

      <div className="comparison-board panel" aria-label="Carbon proxy strategy comparison">
        <div className={`difference-value comparison-delta ${diffClass}`}>
          <span>DELTA / 01 − 02</span>
          <small>Baseline − carbon-aware</small>
          <strong>{signedDifference ?? '—'} <small>{result.comparison_eligible ? result.carbon_proxy_unit : ''}</small></strong>
          <span className="percentage-value">{result.percentage_carbon_proxy_reduction === null ? result.percentage_difference_unavailable_reason ?? 'Not comparable' : `${result.percentage_carbon_proxy_reduction > 0 ? '+' : ''}${formatValue(result.percentage_carbon_proxy_reduction)}%`}</span>
        </div>
        <div className="comparison-board-main">
          <div className="comparison-intro">
            <div><span className="eyebrow">EQUAL-SERVICE BASIS</span><p>{result.comparison_eligible ? 'Both strategies meet the full vehicle energy requirements.' : result.comparison_unavailable_reason}</p></div>
            <strong className={result.comparison_eligible ? 'comparison-eligible' : 'comparison-unavailable'}>{result.comparison_eligible ? 'Comparable' : 'Not comparable'}</strong>
          </div>
          <div className="strategy-lanes">
            <article className="strategy-proxy strategy-proxy-baseline">
              <span className="strategy-lane-index">01</span>
              <div className="strategy-proxy-heading"><span>IMMEDIATE BASELINE</span><h3>Baseline schedule</h3><small>{result.baseline.strategy}</small></div>
              <div className="proxy-reading"><span>CARBON PROXY</span><strong>{formatValue(result.baseline_carbon_proxy)} <small>{result.carbon_proxy_unit}</small></strong></div>
            </article>
            <article className="strategy-proxy strategy-proxy-carbon">
              <span className="strategy-lane-index">02</span>
              <div className="strategy-proxy-heading"><span>CARBON-AWARE HEURISTIC</span><h3>Heuristic schedule</h3><small>{result.carbon_aware.strategy}</small></div>
              <div className="proxy-reading"><span>CARBON PROXY</span><strong>{formatValue(result.carbon_aware_carbon_proxy)} <small>{result.carbon_proxy_unit}</small></strong></div>
            </article>
          </div>
        </div>
      </div>
      <p className="proxy-disclaimer">Synthetic carbon-intensity-weighted energy proxy only; not measured emissions, a forecast, or a real-world savings claim. A failed heuristic is not proof of infeasibility.</p>

      <div className="results-visual-grid">
        <div className="panel intensity-panel">
          <div className="visual-panel-index"><span>INPUT PROFILE / DISCRETE INTERVALS</span><span>{scenario.carbon_intensity_profile.length} INTERVAL{scenario.carbon_intensity_profile.length === 1 ? '' : 'S'}</span></div>
          <CarbonProfileChart profile={scenario.carbon_intensity_profile} />
        </div>
        <DispatchFlow result={result} scenario={scenario} />
      </div>
    </section>
  );
}

export function ResultsDetailsPanel({ result, scenario }: { result: SimulationResult; scenario: Scenario }) {
  const baselineComplete = result.baseline_requirements_met_for_comparison;
  const carbonComplete = result.carbon_aware_feasible;

  return (
    <section className="schedule-details" aria-labelledby="schedule-details-heading">
      <div className="schedule-details-heading">
        <div><p className="eyebrow">SCHEDULE OUTPUTS / PER STRATEGY</p><h2 id="schedule-details-heading">Interval and constraint detail</h2></div>
      </div>
      <div className="schedule-grid">
        <ScheduleCard
          title="Immediate baseline"
          strategy={result.baseline.strategy}
          complete={baselineComplete}
          delivered={result.baseline_delivered_energy_kwh}
          unmet={result.baseline_unmet_energy_kwh}
          allocations={result.baseline_allocations}
          aggregateLoad={result.baseline_aggregate_depot_load}
          peakPower={result.baseline_peak_depot_power_kw}
          constraintChecks={result.baseline_constraint_checks}
          scenario={scenario}
        />
        <ScheduleCard
          title="Carbon-aware heuristic"
          strategy={result.carbon_aware.strategy}
          complete={carbonComplete}
          delivered={result.carbon_aware.delivered_energy_kwh}
          unmet={result.carbon_aware.unmet_energy_kwh}
          allocations={result.carbon_aware.allocations}
          aggregateLoad={result.carbon_aware.aggregate_depot_load}
          peakPower={result.carbon_aware.peak_depot_power_kw}
          constraintChecks={result.carbon_aware.constraint_checks}
          scenario={scenario}
        />
      </div>
    </section>
  );
}
