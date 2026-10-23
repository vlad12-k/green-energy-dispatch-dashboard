import type { Scenario, SimulationResult } from '../api/client';
import { formatValue } from '../utils/number-format';

function total(values: Record<string, number>): number {
  return Object.values(values).reduce((sum, value) => sum + value, 0);
}

function ScheduleOutput({
  name,
  delivered,
  unmet,
  peak,
  complete,
}: {
  name: string;
  delivered: number;
  unmet: number;
  peak: number;
  complete: boolean;
}) {
  return (
    <article className={`flow-output ${complete ? 'flow-output-complete' : 'flow-output-incomplete'}`}>
      <div className="flow-output-heading">
        <span className="flow-output-mark" aria-hidden="true" />
        <div>
          <span className="flow-stage">SCHEDULE OUTPUT</span>
          <h4>{name}</h4>
        </div>
      </div>
      <span className={`flow-output-status ${complete ? '' : 'flow-output-status-incomplete'}`}>
        {complete ? 'Requirements met' : 'Requirements not demonstrated'}
      </span>
      <dl className="flow-output-metrics">
        <div><dt>Delivered</dt><dd className="flow-output-delivered">{formatValue(delivered)} <small>kWh</small></dd></div>
        <div><dt>Unmet</dt><dd className={unmet > 0 ? 'flow-unmet' : ''}>{formatValue(unmet)} <small>kWh</small></dd></div>
        <div><dt>Peak load</dt><dd className="flow-output-peak">{formatValue(peak)} <small>kW</small></dd></div>
      </dl>
    </article>
  );
}

export function DispatchFlow({ result, scenario }: { result: SimulationResult; scenario: Scenario }) {
  const requiredEnergy = scenario.vehicles.reduce((sum, vehicle) => sum + vehicle.required_energy_kwh, 0);

  return (
    <section className="dispatch-flow panel" aria-labelledby="dispatch-flow-heading">
      <header className="dispatch-flow-heading">
        <div>
          <p className="eyebrow">MODEL TRACE / API BOUNDARY</p>
          <h3 id="dispatch-flow-heading">Scenario-to-schedule flow</h3>
          <p>Scenario inputs enter the existing evaluator; its response contains two schedules.</p>
        </div>
        <span className="flow-boundary-tag">NO LIVE CONTROL</span>
      </header>
      <div className="flow-map" role="group" aria-label="Simulation evaluation stages">
        <article className="flow-input">
          <span className="flow-stage">01 / SCENARIO INPUTS</span>
          <h4>Configured case</h4>
          <dl className="flow-input-metrics">
            <div><dt>Required energy</dt><dd className="flow-input-energy">{formatValue(requiredEnergy)} <small>kWh</small></dd></div>
            <div><dt>Depot power limit</dt><dd>{formatValue(scenario.depot_power_kw)} <small>kW</small></dd></div>
            <div><dt>Carbon profile</dt><dd className="flow-input-intervals">{scenario.carbon_intensity_profile.length} intervals</dd></div>
          </dl>
          <span className="flow-source-note">Synthetic scenario · {scenario.vehicles.length} {scenario.vehicles.length === 1 ? 'vehicle' : 'vehicles'}</span>
        </article>
        <div className="flow-connector" aria-hidden="true"><span>REQUEST</span><b>→</b></div>
        <article className="flow-engine">
          <span className="flow-stage">02 / API EVALUATION</span>
          <div className="flow-engine-mark" aria-hidden="true">
            <svg viewBox="0 0 32 32"><path d="M5 9h7l4 7 4-7h7M5 23h7l4-7 4 7h7" /></svg>
          </div>
          <h4>Schedule engine</h4>
          <p>Two existing strategies<br />evaluated by the API</p>
        </article>
        <div className="flow-connector flow-connector-response" aria-hidden="true"><span>RESPONSE</span><b>→</b></div>
        <div className="flow-outputs" role="group" aria-label="Schedules returned by the simulation API">
          <ScheduleOutput
            name="Immediate baseline"
            delivered={total(result.baseline_delivered_energy_kwh)}
            unmet={total(result.baseline_unmet_energy_kwh)}
            peak={result.baseline_peak_depot_power_kw}
            complete={result.baseline_requirements_met_for_comparison}
          />
          <ScheduleOutput
            name="Carbon-aware heuristic"
            delivered={total(result.carbon_aware.delivered_energy_kwh)}
            unmet={total(result.carbon_aware.unmet_energy_kwh)}
            peak={result.carbon_aware.peak_depot_power_kw}
            complete={result.carbon_aware_feasible}
          />
        </div>
      </div>
      <p className="flow-footnote">Values are from this API response and scenario input. “Not demonstrated” is not a proof of infeasibility.</p>
    </section>
  );
}
