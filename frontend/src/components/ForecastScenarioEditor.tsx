import type { FieldErrors, ForecastScenario, ForecastVehicle } from '../api/client';
import { FORECAST_MAX_INTERVALS, FORECAST_MAX_VEHICLES, toUtcDateTimeLocal, fromUtcDateTimeLocal } from '../data/forecastScenario';

type ForecastStatus = 'edited' | 'running' | 'complete' | 'error';

export function ForecastScenarioEditor({
  scenario,
  errors,
  status,
  serverNow,
  onChange,
  onAddVehicle,
  onRemoveVehicle,
  onEvaluate,
}: {
  scenario: ForecastScenario;
  errors: FieldErrors;
  status: ForecastStatus;
  serverNow: string | null;
  onChange: (scenario: ForecastScenario) => void;
  onAddVehicle: () => void;
  onRemoveVehicle: (index: number) => void;
  onEvaluate: () => void;
}) {
  const updateVehicle = (index: number, change: Partial<ForecastVehicle>) => {
    onChange({
      ...scenario,
      vehicles: scenario.vehicles.map((vehicle, vehicleIndex) => (
        vehicleIndex === index ? { ...vehicle, ...change } : vehicle
      )),
    });
  };
  const fieldError = (field: string) => errors[field];
  const errorId = (field: string) => `forecast-${field.replaceAll('.', '-')}-error`;
  const describedBy = (field: string) => fieldError(field) ? errorId(field) : undefined;

  return (
    <section id="forecast-scenario-heading" className="panel forecast-editor" aria-labelledby="forecast-scenario-title">
      <header className="forecast-editor-heading">
        <div>
          <p className="eyebrow">SCENARIO INPUT / FORECAST-BACKED</p>
          <h2 id="forecast-scenario-title">Forecast scenario</h2>
          <p>Forecast values remain server-owned; provenance is server-validated. The browser submits only depot requirements for API-side retrieval and validation.</p>
        </div>
        <span className="forecast-mode-tag">30-MINUTE UTC</span>
      </header>

      <p className="forecast-clock-note">
        {serverNow
          ? `Default timing uses the API server Date header (${serverNow}). The API rechecks timing at evaluation.`
          : 'Waiting for a validated server timestamp; the initial default uses the browser clock and is revalidated by the API.'}
      </p>
      {Object.keys(errors).length > 0 && (
        <div className="error-banner forecast-error-summary" role="alert">
          <strong>Review forecast scenario inputs</strong>
          <span>{Object.values(errors).join(' ')}</span>
        </div>
      )}

      <div className="forecast-form-grid">
        <label className="field-control forecast-field-wide" htmlFor="forecast-scenario-id">
          <span>Scenario identifier</span>
          <input
            id="forecast-scenario-id"
            value={scenario.scenario_id}
            maxLength={120}
            aria-invalid={Boolean(fieldError('scenario_id'))}
            aria-describedby={describedBy('scenario_id')}
            onChange={(event) => onChange({ ...scenario, scenario_id: event.currentTarget.value })}
          />
          {fieldError('scenario_id') && <small id={errorId('scenario_id')} className="field-error">{fieldError('scenario_id')}</small>}
        </label>
        <label className="field-control" htmlFor="forecast-start-at">
          <span>Scheduling start (UTC)</span>
          <input
            id="forecast-start-at"
            aria-label="Scheduling start (UTC)"
            type="datetime-local"
            step={1800}
            value={toUtcDateTimeLocal(scenario.start_at)}
            aria-invalid={Boolean(fieldError('start_at'))}
            aria-describedby={fieldError('start_at') ? describedBy('start_at') : 'forecast-start-at-hint'}
            onChange={(event) => onChange({ ...scenario, start_at: fromUtcDateTimeLocal(event.currentTarget.value) })}
          />
          {fieldError('start_at')
            ? <small id={errorId('start_at')} className="field-error">{fieldError('start_at')}</small>
            : <small id="forecast-start-at-hint" className="field-hint">Enter the displayed wall time as UTC, not local time.</small>}
        </label>
        <label className="field-control" htmlFor="forecast-intervals">
          <span>Scheduling intervals</span>
          <input
            id="forecast-intervals"
            type="number"
            min={1}
            max={FORECAST_MAX_INTERVALS}
            step={1}
            value={scenario.intervals}
            aria-invalid={Boolean(fieldError('intervals'))}
            aria-describedby={describedBy('intervals')}
            onChange={(event) => onChange({ ...scenario, intervals: Number(event.currentTarget.value) })}
          />
          {fieldError('intervals') && <small id={errorId('intervals')} className="field-error">{fieldError('intervals')}</small>}
        </label>
        <label className="field-control" htmlFor="forecast-depot-power">
          <span>Depot power limit (kW)</span>
          <input
            id="forecast-depot-power"
            type="number"
            min="0.001"
            step="any"
            value={scenario.depot_power_kw}
            aria-invalid={Boolean(fieldError('depot_power_kw'))}
            aria-describedby={describedBy('depot_power_kw')}
            onChange={(event) => onChange({ ...scenario, depot_power_kw: Number(event.currentTarget.value) })}
          />
          {fieldError('depot_power_kw') && <small id={errorId('depot_power_kw')} className="field-error">{fieldError('depot_power_kw')}</small>}
        </label>
        <div className="field-control interval-fixed-field">
          <span>Interval length</span>
          <strong>30 minutes</strong>
          <small>Fixed by the forecast scheduling contract.</small>
        </div>
      </div>

      <section className="forecast-vehicle-section" aria-labelledby="forecast-vehicles-title">
        <div className="forecast-subsection-heading">
          <div><p className="eyebrow">FLEET / BOUNDED INPUT</p><h3 id="forecast-vehicles-title">Vehicle availability windows</h3></div>
          <span>{scenario.vehicles.length} / {FORECAST_MAX_VEHICLES}</span>
        </div>
        {fieldError('vehicles') && <p className="field-error" role="alert">{fieldError('vehicles')}</p>}
        <div className="forecast-vehicle-list">
          {scenario.vehicles.map((vehicle, index) => {
            const prefix = `vehicles.${index}`;
            return (
              <fieldset className="forecast-vehicle-card" key={`${index}-${vehicle.vehicle_id}`}>
                <legend>Vehicle {String(index + 1).padStart(2, '0')} · {vehicle.vehicle_id || 'unnamed'}</legend>
                <label className="field-control" htmlFor={`forecast-vehicle-${index}-id`}>
                  <span>Vehicle ID</span>
                  <input
                    id={`forecast-vehicle-${index}-id`}
                    value={vehicle.vehicle_id}
                    maxLength={120}
                    aria-invalid={Boolean(fieldError(`${prefix}.vehicle_id`))}
                    aria-describedby={describedBy(`${prefix}.vehicle_id`)}
                    onChange={(event) => updateVehicle(index, { vehicle_id: event.currentTarget.value })}
                  />
                  {fieldError(`${prefix}.vehicle_id`) && <small id={errorId(`${prefix}.vehicle_id`)} className="field-error">{fieldError(`${prefix}.vehicle_id`)}</small>}
                </label>
                <label className="field-control" htmlFor={`forecast-vehicle-${index}-arrival`}>
                  <span>Availability starts (UTC)</span>
                  <input
                    id={`forecast-vehicle-${index}-arrival`}
                    type="datetime-local"
                    step={60}
                    value={toUtcDateTimeLocal(vehicle.availability_start)}
                    aria-invalid={Boolean(fieldError(`${prefix}.availability_start`))}
                    aria-describedby={describedBy(`${prefix}.availability_start`)}
                    onChange={(event) => updateVehicle(index, { availability_start: fromUtcDateTimeLocal(event.currentTarget.value) })}
                  />
                  {fieldError(`${prefix}.availability_start`) && <small id={errorId(`${prefix}.availability_start`)} className="field-error">{fieldError(`${prefix}.availability_start`)}</small>}
                </label>
                <label className="field-control" htmlFor={`forecast-vehicle-${index}-deadline`}>
                  <span>Availability deadline (UTC)</span>
                  <input
                    id={`forecast-vehicle-${index}-deadline`}
                    type="datetime-local"
                    step={60}
                    value={toUtcDateTimeLocal(vehicle.availability_end)}
                    aria-invalid={Boolean(fieldError(`${prefix}.availability_end`))}
                    aria-describedby={describedBy(`${prefix}.availability_end`)}
                    onChange={(event) => updateVehicle(index, { availability_end: fromUtcDateTimeLocal(event.currentTarget.value) })}
                  />
                  {fieldError(`${prefix}.availability_end`) && <small id={errorId(`${prefix}.availability_end`)} className="field-error">{fieldError(`${prefix}.availability_end`)}</small>}
                </label>
                <label className="field-control" htmlFor={`forecast-vehicle-${index}-energy`}>
                  <span>Required energy (kWh)</span>
                  <input
                    id={`forecast-vehicle-${index}-energy`}
                    type="number"
                    min="0.000000001"
                    step="any"
                    value={vehicle.required_energy_kwh}
                    aria-invalid={Boolean(fieldError(`${prefix}.required_energy_kwh`))}
                    aria-describedby={describedBy(`${prefix}.required_energy_kwh`)}
                    onChange={(event) => updateVehicle(index, { required_energy_kwh: Number(event.currentTarget.value) })}
                  />
                  {fieldError(`${prefix}.required_energy_kwh`) && <small id={errorId(`${prefix}.required_energy_kwh`)} className="field-error">{fieldError(`${prefix}.required_energy_kwh`)}</small>}
                </label>
                <label className="field-control" htmlFor={`forecast-vehicle-${index}-power`}>
                  <span>Vehicle charging power limit (kW)</span>
                  <input
                    id={`forecast-vehicle-${index}-power`}
                    type="number"
                    min="0.000000001"
                    step="any"
                    value={vehicle.charging_power_kw}
                    aria-invalid={Boolean(fieldError(`${prefix}.charging_power_kw`))}
                    aria-describedby={describedBy(`${prefix}.charging_power_kw`)}
                    onChange={(event) => updateVehicle(index, { charging_power_kw: Number(event.currentTarget.value) })}
                  />
                  {fieldError(`${prefix}.charging_power_kw`) && <small id={errorId(`${prefix}.charging_power_kw`)} className="field-error">{fieldError(`${prefix}.charging_power_kw`)}</small>}
                </label>
                <button
                  className="button button-quiet forecast-remove-vehicle"
                  type="button"
                  aria-label={`Remove vehicle ${vehicle.vehicle_id || index + 1}`}
                  disabled={scenario.vehicles.length <= 1}
                  onClick={() => onRemoveVehicle(index)}
                >Remove vehicle</button>
              </fieldset>
            );
          })}
        </div>
        <button className="button button-secondary" type="button" disabled={scenario.vehicles.length >= FORECAST_MAX_VEHICLES} onClick={onAddVehicle}>Add vehicle</button>
      </section>

      <div className="forecast-editor-actions">
        <span>Deadlines are never adjusted automatically. FastAPI remains authoritative.</span>
        <button className="button button-primary" type="button" onClick={onEvaluate} disabled={status === 'running'}>
          {status === 'running' ? <><span className="spinner" aria-hidden="true" /> Evaluating…</> : 'Evaluate forecast schedule'}
        </button>
      </div>
    </section>
  );
}
