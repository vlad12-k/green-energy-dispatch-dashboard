import type { CarbonAwareSchedule, ForecastScenario, ForecastSchedulingResult } from '../api/client';
import { formatValue } from '../utils/number-format';

function utc(value: string): string {
  return new Date(value).toISOString().replace('T', ' ').replace(/\.000Z$/, ' UTC').replace(/Z$/, ' UTC');
}

function signed(value: number): string {
  if (value > 0) return `+${formatValue(value)}`;
  if (value < 0) return `−${formatValue(Math.abs(value))}`;
  return formatValue(value);
}

function constraintLabel(key: string): string {
  const labels: Record<string, string> = {
    scenario_bounds_respected: 'Scenario bounds',
    availability_windows_respected: 'Vehicle availability windows',
    vehicle_power_limits_respected: 'Vehicle power limits',
    depot_power_limit_respected: 'Depot power limit',
    energy_conservation_respected: 'Energy conservation',
  };
  return labels[key] ?? key.replaceAll('_', ' ');
}

function demonstratesCompleteService(schedule: CarbonAwareSchedule, scenario: ForecastScenario): boolean {
  return schedule.feasible
    && scenario.vehicles.every((vehicle) => schedule.unmet_energy_kwh[vehicle.vehicle_id] === 0);
}

function ForecastPowerChart({ baseline, forecastAware, scenario }: {
  baseline: CarbonAwareSchedule;
  forecastAware: CarbonAwareSchedule;
  scenario: ForecastScenario;
}) {
  const intervalCount = scenario.intervals;
  const start = Date.parse(scenario.start_at);
  const plotLeft = 112;
  const plotWidth = 808 - plotLeft;
  const chartTop = 30;
  const chartBottom = 136;
  const scaleMaximum = Math.max(
    scenario.depot_power_kw,
    1,
    ...baseline.aggregate_depot_load.map((row) => row.peak_power_kw),
    ...forecastAware.aggregate_depot_load.map((row) => row.peak_power_kw),
  );
  const slotWidth = plotWidth / Math.max(intervalCount, 1);
  const barWidth = Math.max(2, Math.min(10, slotWidth * 0.3));
  const yFor = (value: number) => chartBottom - value / scaleMaximum * (chartBottom - chartTop);
  const capacityY = yFor(scenario.depot_power_kw);
  const tickIndices = Array.from(new Set([0, Math.floor(intervalCount / 2), intervalCount]));

  return (
    <figure className="forecast-power-figure">
      <figcaption><span>Shared-scale charging power</span><span>Average kW and segment peak · common UTC axis</span></figcaption>
      <div className="forecast-chart-viewport forecast-power-viewport" role="region" aria-label="Charging strategy comparison chart; horizontally scrollable" tabIndex={0}>
        <svg
          className="forecast-power-chart"
          viewBox="0 0 920 190"
          role="img"
          aria-label={`Immediate baseline and forecast-aware heuristic average charging power on a shared scale with ${formatValue(scenario.depot_power_kw)} kilowatt depot limit`}
          data-comparison-power-chart
        >
          <title>Time-aligned power profiles on a shared scale</title>
          <line x1={plotLeft} y1={chartTop} x2={plotLeft} y2={chartBottom} className="chart-axis" />
          <line x1={plotLeft} y1={chartBottom} x2="920" y2={chartBottom} className="chart-axis" />
          <line x1={plotLeft} y1={capacityY} x2="920" y2={capacityY} className="capacity-line" />
          <text x="4" y="20" className="forecast-chart-label">kW · scale 0–{formatValue(scaleMaximum)}</text>
          {tickIndices.map((index) => {
            const x = plotLeft + index / intervalCount * plotWidth;
            const timestamp = new Date(start + index * scenario.interval_minutes * 60_000).toISOString();
            return <g key={index}>
              <line x1={x} y1={chartTop} x2={x} y2={chartBottom} className="chart-gridline" />
              <text x={index === intervalCount ? x - 3 : x + 3} y="157" textAnchor={index === intervalCount ? 'end' : 'start'} className="forecast-chart-label">{timestamp.slice(11, 16)} UTC</text>
            </g>;
          })}
          {Array.from({ length: intervalCount }, (_, index) => {
            const base = baseline.aggregate_depot_load[index];
            const forecast = forecastAware.aggregate_depot_load[index];
            if (!base || !forecast) return null;
            const center = plotLeft + (index + 0.5) * slotWidth;
            const baselineX = center - barWidth - 1;
            const forecastX = center + 1;
            const baselineY = yFor(base.average_power_kw);
            const forecastY = yFor(forecast.average_power_kw);
            return <g key={index}>
              <rect className="comparison-bar-baseline" data-comparison-strategy="baseline" x={baselineX} y={baselineY} width={barWidth} height={Math.max(0, chartBottom - baselineY)} rx="2">
                <title>{`Immediate baseline, ${utc(base.interval_start)}: average ${formatValue(base.average_power_kw)} kW; peak ${formatValue(base.peak_power_kw)} kW`}</title>
              </rect>
              <line className="comparison-peak-baseline" x1={baselineX} y1={yFor(base.peak_power_kw)} x2={baselineX + barWidth} y2={yFor(base.peak_power_kw)} />
              <rect className="comparison-bar-forecast" data-comparison-strategy="forecast-aware" x={forecastX} y={forecastY} width={barWidth} height={Math.max(0, chartBottom - forecastY)} rx="2">
                <title>{`Forecast-aware heuristic, ${utc(forecast.interval_start)}: average ${formatValue(forecast.average_power_kw)} kW; peak ${formatValue(forecast.peak_power_kw)} kW`}</title>
              </rect>
              <line className="comparison-peak-forecast" x1={forecastX} y1={yFor(forecast.peak_power_kw)} x2={forecastX + barWidth} y2={yFor(forecast.peak_power_kw)} />
            </g>;
          })}
          <text x={plotLeft + 5} y={Math.max(14, capacityY - 5)} className="capacity-label">Depot limit {formatValue(scenario.depot_power_kw)} kW</text>
        </svg>
      </div>
      <div className="forecast-chart-key">
        <span><i className="key-baseline" />Immediate Baseline</span>
        <span><i className="key-forecast" />Forecast-aware Heuristic</span>
        <span><i className="key-capacity" />Depot capacity</span>
        <span>Peak markers are distinct from interval-average bars.</span>
      </div>
      <details className="data-table-details">
        <summary>View time-aligned power values</summary>
        <div className="table-scroll forecast-table-scroll" role="region" aria-label="Forecast strategy power table; horizontally scrollable" tabIndex={0}>
          <table aria-label="Forecast strategy power by UTC interval">
            <thead><tr><th>Interval</th><th>Start (UTC)</th><th>End (UTC)</th><th>Baseline avg (kW)</th><th>Baseline peak (kW)</th><th>Heuristic avg (kW)</th><th>Heuristic peak (kW)</th><th>Depot limit (kW)</th></tr></thead>
            <tbody>{baseline.aggregate_depot_load.map((row, index) => {
              const heuristic = forecastAware.aggregate_depot_load[index];
              return heuristic ? <tr key={row.interval_index}>
                <td>{index + 1}</td><td>{utc(row.interval_start)}</td><td>{utc(row.interval_end)}</td>
                <td>{formatValue(row.average_power_kw)}</td><td>{formatValue(row.peak_power_kw)}</td>
                <td>{formatValue(heuristic.average_power_kw)}</td><td>{formatValue(heuristic.peak_power_kw)}</td><td>{formatValue(scenario.depot_power_kw)}</td>
              </tr> : null;
            })}</tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

function BindingSummary({ schedule, strategy }: { schedule: CarbonAwareSchedule; strategy: string }) {
  const bindings = schedule.binding_operational_constraints;
  const depotCount = bindings.depot_capacity_interval_indexes.length;
  const powerVehicles = Object.entries(bindings.vehicle_power_limit_segments).filter(([, indexes]) => indexes.length > 0);
  const deadlineVehicles = Object.entries(bindings.availability_window_end_used_segments).filter(([, indexes]) => indexes.length > 0);
  return (
    <div className="forecast-binding-summary">
      <h4>{strategy} · reported binding constraints</h4>
      <ul>
        <li>{depotCount > 0 ? `Depot capacity bound in ${depotCount} interval(s).` : 'No depot-capacity binding intervals were reported.'}</li>
        <li>{powerVehicles.length > 0
          ? `Vehicle power limits bound for ${powerVehicles.map(([id, indexes]) => `${id} (${indexes.length})`).join(', ')}.`
          : 'No vehicle power-limit binding segments were reported.'}</li>
        <li>{deadlineVehicles.length > 0
          ? `Availability-window ends were used for ${deadlineVehicles.map(([id, indexes]) => `${id} (${indexes.length})`).join(', ')}.`
          : 'No availability-window-end binding segments were reported.'}</li>
      </ul>
    </div>
  );
}

function ForecastStrategyCard({ title, schedule, scenario }: {
  title: string;
  schedule: CarbonAwareSchedule;
  scenario: ForecastScenario;
}) {
  const labels: Record<string, string> = {
    scenario_bounds_respected: 'Scenario bounds',
    availability_windows_respected: 'Availability windows',
    vehicle_power_limits_respected: 'Vehicle power limits',
    depot_power_limit_respected: 'Depot power limit',
    energy_conservation_respected: 'Energy conservation',
  };
  const complete = demonstratesCompleteService(schedule, scenario);
  const unmetTotal = Object.values(schedule.unmet_energy_kwh).reduce((sum, value) => sum + value, 0);
  return (
    <article className="panel forecast-strategy-card">
      <header>
        <div><p className="eyebrow">{schedule.strategy}</p><h3>{title}</h3></div>
        <span className={`status-pill ${complete ? 'status-complete' : 'status-incomplete'}`}>{complete ? 'Complete schedule demonstrated' : 'Complete schedule not demonstrated'}</span>
      </header>
      <div className="forecast-strategy-metrics">
        <div><span>Energy delivered</span><strong>{formatValue(schedule.total_delivered_energy_kwh)} <small>kWh</small></strong></div>
        <div><span>Energy unmet</span><strong>{formatValue(unmetTotal)} <small>kWh</small></strong></div>
        <div><span>Peak depot power</span><strong>{formatValue(schedule.peak_depot_power_kw)} <small>kW</small></strong></div>
        <div><span>Forecast-weighted carbon proxy</span><strong>{formatValue(schedule.carbon_intensity_weighted_energy_proxy)} <small>gCO2</small></strong></div>
      </div>
      {!complete && <p className="forecast-warning">This strategy did not complete all requested energy. Lower delivered energy is not an emissions benefit.</p>}
      <details className="forecast-strategy-detail">
        <summary>Vehicle energy delivery, deadlines and allocations</summary>
        <div className="table-scroll forecast-table-scroll" role="region" aria-label={`${title} vehicle energy and deadline table; horizontally scrollable`} tabIndex={0}>
          <table aria-label={`${title} vehicle energy delivery and deadlines`}>
            <thead><tr><th>Vehicle</th><th>Availability (UTC)</th><th>Deadline (UTC)</th><th>Required (kWh)</th><th>Delivered (kWh)</th><th>Unmet (kWh)</th><th>Service</th></tr></thead>
            <tbody>{scenario.vehicles.map((vehicle) => {
              const delivered = schedule.delivered_energy_kwh[vehicle.vehicle_id] ?? 0;
              const unmet = schedule.unmet_energy_kwh[vehicle.vehicle_id] ?? 0;
              return <tr key={vehicle.vehicle_id}>
                <td>{vehicle.vehicle_id}</td><td>{utc(vehicle.availability_start)}</td><td>{utc(vehicle.availability_end)}</td>
                <td>{formatValue(vehicle.required_energy_kwh)}</td><td>{formatValue(delivered)}</td><td>{formatValue(unmet)}</td><td>{unmet === 0 ? 'Complete' : 'Incomplete'}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        <div className="table-scroll forecast-table-scroll" role="region" aria-label={`${title} vehicle charging allocations; horizontally scrollable`} tabIndex={0}>
          <table aria-label={`${title} vehicle allocations by UTC segment`}>
            <thead><tr><th>Vehicle</th><th>Segment (UTC)</th><th>Energy (kWh)</th><th>Charging power (kW)</th><th>Forecast intensity (gCO2/kWh)</th></tr></thead>
            <tbody>{schedule.allocations.length === 0
              ? <tr><td colSpan={5}>No charging allocations returned.</td></tr>
              : schedule.allocations.map((allocation, index) => <tr key={`${allocation.vehicle_id}-${allocation.segment_start}-${index}`}>
                <td>{allocation.vehicle_id}</td><td>{utc(allocation.segment_start)} – {utc(allocation.segment_end)}</td>
                <td>{formatValue(allocation.energy_kwh)}</td><td>{formatValue(allocation.charging_power_kw)}</td><td>{formatValue(allocation.carbon_intensity_value)}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </details>
      <details className="constraint-details forecast-constraint-details">
        <summary>Operational constraint checks</summary>
        <ul className="constraint-list">
          {Object.entries(schedule.constraint_checks).map(([key, passed]) => <li key={key}>
            <span className={passed ? 'check-mark' : 'cross-mark'} aria-hidden="true">{passed ? '✓' : '!'}</span>
            <span>{labels[key] ?? constraintLabel(key)}</span><strong>{passed ? 'Pass' : 'Review'}</strong>
          </li>)}
        </ul>
      </details>
      <BindingSummary schedule={schedule} strategy={title} />
    </article>
  );
}

function allocationEnergyByInterval(schedule: CarbonAwareSchedule): Map<number, { energy: number; intensity: number }> {
  const totals = new Map<number, { energy: number; weightedIntensity: number }>();
  for (const allocation of schedule.allocations) {
    const current = totals.get(allocation.interval_index) ?? { energy: 0, weightedIntensity: 0 };
    current.energy += allocation.energy_kwh;
    current.weightedIntensity += allocation.energy_kwh * allocation.carbon_intensity_value;
    totals.set(allocation.interval_index, current);
  }
  return new Map([...totals].map(([index, value]) => [
    index,
    { energy: value.energy, intensity: value.energy > 0 ? value.weightedIntensity / value.energy : 0 },
  ]));
}

function describeAllocationShift(baseline: CarbonAwareSchedule, forecastAware: CarbonAwareSchedule, scenario: ForecastScenario): string {
  const baselineByInterval = allocationEnergyByInterval(baseline);
  const forecastByInterval = allocationEnergyByInterval(forecastAware);
  const reduced = [...baselineByInterval].flatMap(([index, baselineValue]) => {
    const forecastValue = forecastByInterval.get(index);
    const difference = baselineValue.energy - (forecastValue?.energy ?? 0);
    return difference > 1e-6 ? [{ index, remaining: difference, intensity: baselineValue.intensity }] : [];
  }).sort((left, right) => right.intensity - left.intensity);
  const increased = [...forecastByInterval].flatMap(([index, forecastValue]) => {
    const baselineValue = baselineByInterval.get(index);
    const difference = forecastValue.energy - (baselineValue?.energy ?? 0);
    return difference > 1e-6 ? [{ index, remaining: difference, intensity: forecastValue.intensity }] : [];
  }).sort((left, right) => left.intensity - right.intensity);

  for (const lower of increased) {
    for (const higher of reduced) {
      if (lower.intensity >= higher.intensity) continue;
      const matched = Math.min(lower.remaining, higher.remaining);
      if (matched <= 1e-6) continue;
      const start = new Date(Date.parse(scenario.start_at) + lower.index * scenario.interval_minutes * 60_000).toISOString();
      const reducedStart = new Date(Date.parse(scenario.start_at) + higher.index * scenario.interval_minutes * 60_000).toISOString();
      return `Relative to the immediate baseline, the heuristic allocated ${formatValue(matched)} kWh more in ${utc(start)} (${formatValue(lower.intensity)} gCO2/kWh) and ${formatValue(matched)} kWh less in ${utc(reducedStart)} (${formatValue(higher.intensity)} gCO2/kWh). This describes returned allocations; it is not a causal or optimisation guarantee.`;
    }
  }
  return 'The returned allocations do not establish a net shift into a lower-intensity interval; no causal explanation is inferred.';
}

function Explainability({ result, scenario }: { result: ForecastSchedulingResult; scenario: ForecastScenario }) {
  const baseline = result.immediate_baseline;
  const forecastAware = result.forecast_aware;
  if (!baseline || !forecastAware) return null;
  const baselineComplete = demonstratesCompleteService(baseline, scenario);
  const forecastAwareComplete = demonstratesCompleteService(forecastAware, scenario);
  const shift = result.comparison_eligible
    ? describeAllocationShift(baseline, forecastAware, scenario)
    : 'No carbon-proxy benefit is reported because equivalent complete service was not demonstrated.';
  return (
    <section className="panel forecast-explainability" aria-labelledby="forecast-explainability-heading">
      <p className="eyebrow">RESULT INTERPRETATION / API-DERIVED</p>
      <h3 id="forecast-explainability-heading">Scheduling explanation</h3>
      <ul>
        <li>Immediate Baseline: {baselineComplete ? 'all requested vehicle energy was delivered.' : 'complete service was not demonstrated.'}</li>
        <li>Forecast-aware Heuristic: {forecastAwareComplete ? 'all requested vehicle energy was delivered.' : 'complete service was not demonstrated.'}</li>
        <li>{shift}</li>
        <li>{result.comparison_eligible
          ? 'The API marks this comparison eligible because both strategies completed the requested energy.'
          : `Comparison unavailable: ${result.comparison_unavailable_reason}`}</li>
      </ul>
      <p>Constraint checks and reported binding limits are shown with each strategy. The API does not establish global optimality or measured emissions savings.</p>
    </section>
  );
}

function ForecastProvenance({ result }: { result: ForecastSchedulingResult }) {
  const provenance = result.input_provenance;
  if (!provenance) return null;
  return (
    <section className="panel forecast-provenance-card" aria-labelledby="forecast-provenance-heading">
      <p className="eyebrow">SERVER-VALIDATED INPUT / PROVENANCE</p>
      <h3 id="forecast-provenance-heading">Forecast provenance</h3>
      <dl>
        <div><dt>Region</dt><dd>{provenance.region_name} · {provenance.region_id}</dd></div>
        <div><dt>Classification</dt><dd>{provenance.source_classification.toUpperCase()}</dd></div>
        <div><dt>Measurement</dt><dd>{provenance.measurement_type.toUpperCase()} · {provenance.intensity_unit}</dd></div>
        <div><dt>Forecast retrieved at</dt><dd>{utc(provenance.forecast_retrieved_at)}</dd></div>
        <div><dt>Decision time</dt><dd>{utc(provenance.decision_time)} · age {formatValue(provenance.forecast_age_seconds)} sec</dd></div>
        <div><dt>Forecast validity</dt><dd>{utc(provenance.prediction_horizon_start)} – {utc(provenance.prediction_horizon_end)}</dd></div>
        <div><dt>Scheduling horizon</dt><dd>{utc(provenance.scheduling_horizon_start)} – {utc(provenance.scheduling_horizon_end)}</dd></div>
        <div><dt>Intervals validated</dt><dd>{provenance.interval_count} × 30 minutes</dd></div>
        <div><dt>Source API</dt><dd><a href={provenance.source_url} target="_blank" rel="noreferrer">NESO Carbon Intensity API</a></dd></div>
      </dl>
      <p>These are the server-reported retrieval and decision timestamps. No original NESO issue time is claimed.</p>
    </section>
  );
}

export function ForecastResultsPanel({ result, scenario }: {
  result: ForecastSchedulingResult;
  scenario: ForecastScenario;
}) {
  if (result.status === 'unavailable' || !result.immediate_baseline || !result.forecast_aware) {
    return (
      <section id="forecast-results" className="panel forecast-result-unavailable" aria-labelledby="forecast-results-heading">
        <p className="eyebrow">FORECAST-BACKED EVALUATION / UNAVAILABLE</p>
        <h2 id="forecast-results-heading">Forecast evaluation unavailable</h2>
        <p><strong>API reason:</strong> <code>{result.unavailable_reason ?? 'forecast_unavailable'}</code></p>
        <p>{result.comparison_unavailable_reason}</p>
        <p>No schedule or carbon-proxy difference is presented for an unavailable forecast.</p>
      </section>
    );
  }

  const baseline = result.immediate_baseline;
  const forecastAware = result.forecast_aware;
  return (
    <section id="forecast-results" className="forecast-results" aria-labelledby="forecast-results-heading">
      <header className="forecast-results-heading">
        <div><p className="eyebrow">FORECAST-BACKED EVALUATION / ADVISORY ONLY</p><h2 id="forecast-results-heading">Charging strategy comparison</h2></div>
        <span className="source-tag">NESO server-validated forecast · {result.input_provenance?.region_name ?? 'region unavailable'}</span>
      </header>
      <div className="forecast-comparison-board panel" role="group" aria-label="Forecast-weighted carbon proxy comparison">
        <p className="eyebrow">FORECAST-WEIGHTED CARBON PROXY / MODELLED</p>
        <div className={`forecast-comparison-status ${result.comparison_eligible ? 'comparison-eligible' : 'comparison-unavailable'}`}>
          <span>{result.comparison_eligible ? 'EQUAL-SERVICE COMPARISON' : 'COMPARISON UNAVAILABLE'}</span>
          <strong>{result.comparison_eligible ? 'Comparable' : 'Not comparable'}</strong>
          {!result.comparison_eligible && <p>{result.comparison_unavailable_reason}</p>}
        </div>
        <div className="forecast-comparison-values">
          <div><span>Immediate Baseline</span><strong>{formatValue(baseline.carbon_intensity_weighted_energy_proxy)} <small>gCO2 proxy</small></strong></div>
          <div><span>Forecast-aware Heuristic</span><strong>{formatValue(forecastAware.carbon_intensity_weighted_energy_proxy)} <small>gCO2 proxy</small></strong></div>
          <div className={result.comparison_eligible ? 'forecast-proxy-delta' : 'forecast-proxy-delta forecast-proxy-unavailable'}>
            <span>Signed proxy difference</span>
            <strong>{result.comparison_eligible && result.absolute_carbon_proxy_reduction !== null ? signed(result.absolute_carbon_proxy_reduction) : 'Not comparable'}
              {result.comparison_eligible && result.absolute_carbon_proxy_reduction !== null ? <small> gCO2 · baseline − heuristic</small> : null}
            </strong>
            <small>{result.comparison_eligible
              ? result.percentage_carbon_proxy_reduction === null
                ? result.percentage_difference_unavailable_reason ?? 'Percentage is undefined for this baseline proxy.'
                : `${signed(result.percentage_carbon_proxy_reduction)}%`
              : result.comparison_unavailable_reason}</small>
          </div>
        </div>
      </div>
      <p className="forecast-proxy-disclaimer">Modelled forecast-weighted carbon proxy only; not measured emissions or a real-world savings claim. The scheduling API is advisory and does not control physical chargers.</p>

      <ForecastPowerChart baseline={baseline} forecastAware={forecastAware} scenario={scenario} />
      <div className="forecast-strategy-grid">
        <ForecastStrategyCard title="Immediate Baseline" schedule={baseline} scenario={scenario} />
        <ForecastStrategyCard title="Forecast-aware Heuristic" schedule={forecastAware} scenario={scenario} />
      </div>
      <div className="forecast-result-support-grid">
        <ForecastProvenance result={result} />
        <Explainability result={result} scenario={scenario} />
      </div>
    </section>
  );
}
