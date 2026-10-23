import type { ChangeEvent } from 'react';
import type { FieldErrors, Scenario, Vehicle } from '../api/client';
import { fromUtcInput, rebuildCarbonProfile, toUtcInput } from '../data/scenario';

interface ScenarioEditorProps {
  scenario: Scenario;
  errors: FieldErrors;
  importMessage: string | null;
  onChange: (scenario: Scenario) => void;
  onLoadExample: () => void;
  onImport: (file: File) => void;
  onExport: () => void;
}

function fieldErrorId(path: string): string {
  return `${path.replaceAll('.', '-')}-error`;
}

function FieldError({ errors, path }: { errors: FieldErrors; path: string }) {
  const message = errors[path];
  return message ? <p className="field-error" id={fieldErrorId(path)} role="alert">{message}</p> : null;
}

export function ScenarioEditor({
  scenario,
  errors,
  importMessage,
  onChange,
  onLoadExample,
  onImport,
  onExport,
}: ScenarioEditorProps) {
  const updateScenario = (changes: Partial<Scenario>) => onChange({ ...scenario, ...changes });

  const updateVehicle = (index: number, changes: Partial<Vehicle>) => {
    const vehicles = scenario.vehicles.map((vehicle, current) => current === index ? { ...vehicle, ...changes } : vehicle);
    updateScenario({ vehicles });
  };

  const updateProfileIntensity = (index: number, value: number) => {
    const carbon_intensity_profile = scenario.carbon_intensity_profile.map((row, current) => (
      current === index ? { ...row, intensity_value: value } : row
    ));
    updateScenario({ carbon_intensity_profile });
  };

  const changeStart = (value: string) => {
    const start_at = fromUtcInput(value);
    updateScenario({
      start_at,
      carbon_intensity_profile: rebuildCarbonProfile(scenario, start_at, scenario.interval_minutes, scenario.intervals),
    });
  };

  const changeInterval = (field: 'interval_minutes' | 'intervals', value: number) => {
    const interval_minutes = field === 'interval_minutes' ? value : scenario.interval_minutes;
    const intervals = field === 'intervals' ? value : scenario.intervals;
    updateScenario({
      [field]: value,
      carbon_intensity_profile: rebuildCarbonProfile(scenario, scenario.start_at, interval_minutes, intervals),
    });
  };

  const addVehicle = () => {
    if (scenario.vehicles.length >= 50) return;
    const existing = new Set(scenario.vehicles.map((vehicle) => vehicle.vehicle_id));
    let suffix = scenario.vehicles.length + 1;
    while (existing.has(`vehicle-${suffix}`)) suffix += 1;
    const end = new Date(Date.parse(scenario.start_at) + scenario.interval_minutes * scenario.intervals * 60_000).toISOString();
    const vehicle: Vehicle = {
      vehicle_id: `vehicle-${suffix}`,
      availability_start: scenario.start_at,
      availability_end: end,
      required_energy_kwh: 1,
      charging_power_kw: Math.min(7, scenario.depot_power_kw),
    };
    updateScenario({ vehicles: [...scenario.vehicles, vehicle] });
  };

  const importSelectedFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    if (file) onImport(file);
    event.currentTarget.value = '';
  };

  return (
    <section className="panel editor-panel" aria-labelledby="scenario-heading">
      <header className="editor-panel-heading">
        <div className="editor-title-row"><span className="section-stamp">01</span><div><p className="eyebrow">SCENARIO EDITOR / INPUTS</p><h2 id="scenario-heading">Scenario configuration</h2></div></div>
        <span className="limit-note">1–50 vehicles<br />1–96 intervals</span>
      </header>

      <div className="editor-actions" aria-label="Scenario file actions">
        <button className="button button-secondary" type="button" onClick={onLoadExample}>Load synthetic example</button>
        <label className="button button-quiet file-button">
          Import JSON
          <input aria-label="Import scenario JSON" type="file" accept="application/json,.json" onChange={importSelectedFile} />
        </label>
        <button className="button button-quiet" type="button" onClick={onExport}>Export JSON</button>
      </div>
      {importMessage && <p className="inline-notice" role="status">{importMessage}</p>}
      {errors.scenario && <p className="field-error" role="alert">{errors.scenario}</p>}

      <section className="editor-section" aria-labelledby="scenario-window-heading">
        <div className="subsection-heading editor-section-heading"><span className="section-stamp">A</span><div><h3 id="scenario-window-heading">Scenario envelope</h3><p>Timing, depot limit and source boundary.</p></div></div>
        <div className="form-grid scenario-fields">
          <div className="form-field">
            <label htmlFor="scenario_id">Scenario identifier</label>
            <input id="scenario_id" name="scenario_id" value={scenario.scenario_id} maxLength={120} onChange={(event) => updateScenario({ scenario_id: event.target.value })} aria-invalid={Boolean(errors.scenario_id)} aria-describedby={errors.scenario_id ? fieldErrorId('scenario_id') : undefined} />
            <FieldError errors={errors} path="scenario_id" />
          </div>
          <div className="form-field">
            <label htmlFor="start_at">Scenario start <span>(UTC)</span></label>
            <input id="start_at" name="start_at" type="datetime-local" step="60" value={toUtcInput(scenario.start_at)} onChange={(event) => changeStart(event.target.value)} aria-invalid={Boolean(errors.start_at)} aria-describedby={errors.start_at ? fieldErrorId('start_at') : undefined} />
            <FieldError errors={errors} path="start_at" />
          </div>
          <div className="form-field">
            <label htmlFor="interval_minutes">Interval length <span>(minutes)</span></label>
            <input id="interval_minutes" name="interval_minutes" type="number" min="1" max="1440" step="1" value={scenario.interval_minutes} onChange={(event) => changeInterval('interval_minutes', Number(event.target.value))} aria-invalid={Boolean(errors.interval_minutes)} aria-describedby={errors.interval_minutes ? fieldErrorId('interval_minutes') : undefined} />
            <FieldError errors={errors} path="interval_minutes" />
          </div>
          <div className="form-field">
            <label htmlFor="intervals">Number of intervals</label>
            <input id="intervals" name="intervals" type="number" min="1" max="96" step="1" value={scenario.intervals} onChange={(event) => changeInterval('intervals', Number(event.target.value))} aria-invalid={Boolean(errors.intervals)} aria-describedby={errors.intervals ? fieldErrorId('intervals') : undefined} />
            <FieldError errors={errors} path="intervals" />
          </div>
          <div className="form-field">
            <label htmlFor="depot_power_kw">Depot power limit <span>(kW)</span></label>
            <input id="depot_power_kw" name="depot_power_kw" type="number" min="0.001" step="any" value={scenario.depot_power_kw} onChange={(event) => updateScenario({ depot_power_kw: Number(event.target.value) })} aria-invalid={Boolean(errors.depot_power_kw)} aria-describedby={errors.depot_power_kw ? fieldErrorId('depot_power_kw') : undefined} />
            <FieldError errors={errors} path="depot_power_kw" />
          </div>
          <div className="form-field provenance-field">
            <span className="field-label">Input provenance</span>
            <strong>synthetic_scenario</strong>
            <small>Simulation-only geography · no NESO data</small>
          </div>
        </div>
      </section>

      <section className="editor-section fleet-section" aria-labelledby="vehicle-section-heading">
        <div className="subsection-heading editor-section-heading">
          <span className="section-stamp">B</span>
          <div><h3 id="vehicle-section-heading">Vehicles</h3><p>UTC availability windows · per-vehicle limits.</p></div>
          <button className="button button-small button-secondary" type="button" onClick={addVehicle} disabled={scenario.vehicles.length >= 50}>Add vehicle</button>
        </div>
        {errors.vehicles && <p className="field-error" role="alert">{errors.vehicles}</p>}
        <div className="vehicle-list">
          {scenario.vehicles.map((vehicle, index) => {
            const prefix = `vehicles.${index}`;
            return (
              <fieldset className="vehicle-card" key={`${index}-${vehicle.vehicle_id}`}>
                <legend><span className="vehicle-index">{String(index + 1).padStart(2, '0')}</span> Vehicle</legend>
                <button className="icon-button remove-vehicle" type="button" aria-label={`Remove vehicle ${vehicle.vehicle_id}`} disabled={scenario.vehicles.length <= 1} onClick={() => updateScenario({ vehicles: scenario.vehicles.filter((_, current) => current !== index) })}>×</button>
                <div className="form-grid vehicle-fields">
                  <div className="form-field">
                    <label htmlFor={`${prefix}.vehicle_id`}>Vehicle ID</label>
                    <input id={`${prefix}.vehicle_id`} value={vehicle.vehicle_id} maxLength={80} onChange={(event) => updateVehicle(index, { vehicle_id: event.target.value })} aria-invalid={Boolean(errors[`${prefix}.vehicle_id`])} aria-describedby={errors[`${prefix}.vehicle_id`] ? fieldErrorId(`${prefix}.vehicle_id`) : undefined} />
                    <FieldError errors={errors} path={`${prefix}.vehicle_id`} />
                  </div>
                  <div className="form-field">
                    <label htmlFor={`${prefix}.required_energy_kwh`}>Required energy <span>(kWh)</span></label>
                    <input id={`${prefix}.required_energy_kwh`} type="number" min="0.000000001" step="any" value={vehicle.required_energy_kwh} onChange={(event) => updateVehicle(index, { required_energy_kwh: Number(event.target.value) })} aria-invalid={Boolean(errors[`${prefix}.required_energy_kwh`])} aria-describedby={errors[`${prefix}.required_energy_kwh`] ? fieldErrorId(`${prefix}.required_energy_kwh`) : undefined} />
                    <FieldError errors={errors} path={`${prefix}.required_energy_kwh`} />
                  </div>
                  <div className="form-field">
                    <label htmlFor={`${prefix}.charging_power_kw`}>Charger limit <span>(kW)</span></label>
                    <input id={`${prefix}.charging_power_kw`} type="number" min="0.000000001" step="any" value={vehicle.charging_power_kw} onChange={(event) => updateVehicle(index, { charging_power_kw: Number(event.target.value) })} aria-invalid={Boolean(errors[`${prefix}.charging_power_kw`])} aria-describedby={errors[`${prefix}.charging_power_kw`] ? fieldErrorId(`${prefix}.charging_power_kw`) : undefined} />
                    <FieldError errors={errors} path={`${prefix}.charging_power_kw`} />
                  </div>
                  <div className="form-field">
                    <label htmlFor={`${prefix}.availability_start`}>Available from <span>(UTC)</span></label>
                    <input id={`${prefix}.availability_start`} type="datetime-local" step="60" value={toUtcInput(vehicle.availability_start)} onChange={(event) => updateVehicle(index, { availability_start: fromUtcInput(event.target.value) })} aria-invalid={Boolean(errors[`${prefix}.availability_start`])} aria-describedby={errors[`${prefix}.availability_start`] ? fieldErrorId(`${prefix}.availability_start`) : undefined} />
                    <FieldError errors={errors} path={`${prefix}.availability_start`} />
                  </div>
                  <div className="form-field">
                    <label htmlFor={`${prefix}.availability_end`}>Available until <span>(UTC)</span></label>
                    <input id={`${prefix}.availability_end`} type="datetime-local" step="60" value={toUtcInput(vehicle.availability_end)} onChange={(event) => updateVehicle(index, { availability_end: fromUtcInput(event.target.value) })} aria-invalid={Boolean(errors[`${prefix}.availability_end`])} aria-describedby={errors[`${prefix}.availability_end`] ? fieldErrorId(`${prefix}.availability_end`) : undefined} />
                    <FieldError errors={errors} path={`${prefix}.availability_end`} />
                  </div>
                </div>
              </fieldset>
            );
          })}
        </div>
      </section>

      <section className="editor-section profile-section" aria-labelledby="profile-section-heading">
        <div className="subsection-heading editor-section-heading profile-heading">
          <span className="section-stamp">C</span>
          <div><h3 id="profile-section-heading">Synthetic carbon profile</h3><p>One intensity value per interval; no external source is queried.</p></div>
          <label className="unit-select">
            <span>Intensity unit</span>
            <select aria-label="Carbon intensity unit" value={scenario.carbon_intensity_profile[0]?.intensity_unit ?? 'gCO2/kWh'} onChange={(event) => updateScenario({ carbon_intensity_profile: scenario.carbon_intensity_profile.map((row) => ({ ...row, intensity_unit: event.target.value as 'gCO2/kWh' | 'gCO2e/kWh' })) })}>
              <option value="gCO2/kWh">gCO2/kWh</option>
              <option value="gCO2e/kWh">gCO2e/kWh</option>
            </select>
          </label>
        </div>
        {errors.carbon_intensity_profile && <p className="field-error" role="alert">{errors.carbon_intensity_profile}</p>}
        <div className="intensity-list">
          {scenario.carbon_intensity_profile.map((row, index) => {
            const path = `carbon_intensity_profile.${index}.intensity_value`;
            return (
              <div className="intensity-row" key={`${row.interval_start}-${index}`}>
                <label htmlFor={path}>Interval {String(index + 1).padStart(2, '0')} <span>{row.interval_start.slice(11, 16)}–{row.interval_end.slice(11, 16)} UTC</span></label>
                <div className="intensity-input"><input id={path} type="number" min="0" max="1000000000" step="any" value={row.intensity_value} onChange={(event) => updateProfileIntensity(index, Number(event.target.value))} aria-invalid={Boolean(errors[path])} aria-describedby={errors[path] ? fieldErrorId(path) : undefined} /><span>{row.intensity_unit}</span></div>
                <FieldError errors={errors} path={path} />
              </div>
            );
          })}
        </div>
        <p className="provenance-note"><span aria-hidden="true">●</span> Profile provenance is locked to <code>synthetic:operator-dashboard</code>. Imported source labels are validated by the API.</p>
      </section>
    </section>
  );
}
