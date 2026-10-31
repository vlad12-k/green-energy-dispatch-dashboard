import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  CarbonIntensityResponse,
  FieldErrors,
  ForecastScenario,
  ForecastSchedulingResult,
  Scenario,
  SimulationResult,
} from './api/client';
import {
  ForecastApiError,
  SimulationApiError,
  evaluateForecastScenario,
  evaluateScenario,
} from './api/client';
import { ForecastResultsPanel } from './components/ForecastResultsPanel';
import { ForecastScenarioEditor } from './components/ForecastScenarioEditor';
import { ForecastTimelinePanel } from './components/ForecastTimelinePanel';
import { ResultsDetailsPanel, ResultsPanel } from './components/ResultsPanel';
import { ScenarioEditor } from './components/ScenarioEditor';
import { LiveCarbonIntensity } from './components/LiveCarbonIntensity';
import { createDemoScenario, scenarioSignature } from './data/scenario';
import {
  FORECAST_MAX_VEHICLES,
  createDefaultForecastScenario,
  forecastScenarioSignature,
  validateForecastScenario,
} from './data/forecastScenario';

interface EvaluationSnapshot {
  revision: number;
  result: SimulationResult;
}

type EvaluationStatus = 'edited' | 'running' | 'complete' | 'error';
type EvaluationMode = 'synthetic' | 'forecast';

interface ForecastEvaluationSnapshot {
  signature: string;
  result: ForecastSchedulingResult;
}

interface VerifiedRegion {
  id: number;
  name: string;
}

function isScenarioShape(value: unknown): value is Scenario {
  if (typeof value !== 'object' || value === null) return false;
  const record = value as Record<string, unknown>;
  if (
    typeof record.scenario_id !== 'string'
    || typeof record.start_at !== 'string'
    || typeof record.interval_minutes !== 'number'
    || typeof record.intervals !== 'number'
    || typeof record.depot_power_kw !== 'number'
    || record.geography_code !== 'simulation-only'
    || !Array.isArray(record.vehicles)
    || record.vehicles.length < 1
    || record.vehicles.length > 50
    || !Array.isArray(record.carbon_intensity_profile)
    || record.carbon_intensity_profile.length < 1
    || record.carbon_intensity_profile.length > 96
  ) return false;

  const vehiclesValid = record.vehicles.every((item: unknown) => {
    if (typeof item !== 'object' || item === null) return false;
    const vehicle = item as Record<string, unknown>;
    return typeof vehicle.vehicle_id === 'string'
      && typeof vehicle.availability_start === 'string'
      && typeof vehicle.availability_end === 'string'
      && typeof vehicle.required_energy_kwh === 'number'
      && typeof vehicle.charging_power_kw === 'number';
  });
  const profileValid = record.carbon_intensity_profile.every((item: unknown) => {
    if (typeof item !== 'object' || item === null) return false;
    const row = item as Record<string, unknown>;
    return typeof row.interval_start === 'string'
      && typeof row.interval_end === 'string'
      && typeof row.intensity_value === 'number'
      && (row.intensity_unit === 'gCO2/kWh' || row.intensity_unit === 'gCO2e/kWh')
      && row.source_classification === 'synthetic_scenario'
      && row.source_id === 'synthetic-scenario'
      && row.geography_code === 'simulation-only'
      && typeof row.provenance_label === 'string'
      && row.provenance_label.startsWith('synthetic:');
  });
  return vehiclesValid && profileValid;
}

export default function App() {
  const [scenario, setScenario] = useState<Scenario>(createDemoScenario);
  const scenarioRef = useRef(scenario);
  const revisionRef = useRef(0);
  const [revision, setRevision] = useState(0);
  const [evaluation, setEvaluation] = useState<EvaluationSnapshot | null>(null);
  const [status, setStatus] = useState<EvaluationStatus>('edited');
  const [statusMessage, setStatusMessage] = useState('Synthetic example loaded. Run a simulation to calculate schedule results.');
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<EvaluationMode>('synthetic');
  const [forecastScenario, setForecastScenario] = useState<ForecastScenario>(() => createDefaultForecastScenario());
  const forecastScenarioRef = useRef(forecastScenario);
  const [forecastStatus, setForecastStatus] = useState<EvaluationStatus>('edited');
  const [forecastStatusMessage, setForecastStatusMessage] = useState('Forecast mode is advisory. The API will retrieve and validate its own regional forecast.');
  const [forecastFieldErrors, setForecastFieldErrors] = useState<FieldErrors>({});
  const [forecastApiError, setForecastApiError] = useState<string | null>(null);
  const [forecastEvaluation, setForecastEvaluation] = useState<ForecastEvaluationSnapshot | null>(null);
  const forecastRequestSequence = useRef(0);
  const forecastActiveRequest = useRef<AbortController | null>(null);
  const forecastUserEdited = useRef(false);
  const serverTimeInitialized = useRef(false);
  const forecastServerNowRef = useRef<string | null>(null);
  const [forecastServerNow, setForecastServerNow] = useState<string | null>(null);
  const [verifiedRegion, setVerifiedRegion] = useState<VerifiedRegion | null>(null);
  const isCurrentResult = evaluation !== null && evaluation.revision === revision;
  const result = isCurrentResult ? evaluation.result : null;
  const forecastSignature = forecastScenarioSignature(forecastScenario);
  const isCurrentForecastResult = forecastEvaluation?.signature === forecastSignature;
  const forecastResult = isCurrentForecastResult ? forecastEvaluation.result : null;
  const displayStatus = mode === 'synthetic' ? status : forecastStatus;
  const updateScenario = (next: Scenario) => {
    activeRequest.current?.abort();
    requestSequence.current += 1;
    revisionRef.current += 1;
    setRevision(revisionRef.current);
    scenarioRef.current = next;
    setScenario(next);
    setFieldErrors({});
    setApiError(null);
    setStatus('edited');
    setStatusMessage('Scenario edited. Previous results are hidden until this version is evaluated.');
  };

  const handleServerTime = useCallback((serverTime: string) => {
    if (!Number.isFinite(Date.parse(serverTime))) return;
    forecastServerNowRef.current = serverTime;
    setForecastServerNow(serverTime);
    if (serverTimeInitialized.current) return;
    serverTimeInitialized.current = true;
    if (!forecastUserEdited.current) {
      const initialScenario = createDefaultForecastScenario(serverTime);
      if (forecastScenarioSignature(initialScenario) === forecastScenarioSignature(forecastScenarioRef.current)) return;
      forecastActiveRequest.current?.abort();
      forecastRequestSequence.current += 1;
      forecastScenarioRef.current = initialScenario;
      setForecastScenario(initialScenario);
      setForecastEvaluation(null);
      setForecastFieldErrors({});
      setForecastApiError(null);
      setForecastStatus('edited');
      setForecastStatusMessage('API server time updated the initial forecast scenario. Evaluate this version again; earlier results are hidden.');
    }
  }, []);

  const handleVerifiedReading = useCallback((reading: CarbonIntensityResponse) => {
    setVerifiedRegion({ id: reading.region_id, name: reading.region_name });
  }, []);

  const updateForecastScenario = (next: ForecastScenario) => {
    forecastUserEdited.current = true;
    forecastActiveRequest.current?.abort();
    forecastRequestSequence.current += 1;
    setForecastEvaluation(null);
    forecastScenarioRef.current = next;
    setForecastScenario(next);
    setForecastFieldErrors({});
    setForecastApiError(null);
    setForecastStatus('edited');
    setForecastStatusMessage('Scenario edited. Earlier forecast results are hidden until this exact version is evaluated.');
  };

  const addForecastVehicle = () => {
    if (forecastScenarioRef.current.vehicles.length >= FORECAST_MAX_VEHICLES) return;
    const existingIds = new Set(forecastScenarioRef.current.vehicles.map((vehicle) => vehicle.vehicle_id));
    let nextId = forecastScenarioRef.current.vehicles.length + 1;
    while (existingIds.has(`vehicle-${nextId}`)) nextId += 1;
    const startAt = forecastScenarioRef.current.start_at;
    const startTime = Date.parse(startAt);
    const horizonEnd = Number.isFinite(startTime)
      ? new Date(startTime + forecastScenarioRef.current.intervals * 30 * 60_000).toISOString()
      : '';
    const powerLimit = Number.isFinite(forecastScenarioRef.current.depot_power_kw)
      ? Math.min(7, Math.max(0.1, forecastScenarioRef.current.depot_power_kw))
      : 7;
    updateForecastScenario({
      ...forecastScenarioRef.current,
      vehicles: [...forecastScenarioRef.current.vehicles, {
        vehicle_id: `vehicle-${nextId}`,
        availability_start: startAt,
        availability_end: horizonEnd,
        required_energy_kwh: 5,
        charging_power_kw: powerLimit,
      }],
    });
  };

  const removeForecastVehicle = (index: number) => {
    if (forecastScenarioRef.current.vehicles.length <= 1) return;
    updateForecastScenario({
      ...forecastScenarioRef.current,
      vehicles: forecastScenarioRef.current.vehicles.filter((_, vehicleIndex) => vehicleIndex !== index),
    });
  };

  const runForecastEvaluation = async () => {
    forecastActiveRequest.current?.abort();
    const controller = new AbortController();
    forecastActiveRequest.current = controller;
    const requestId = ++forecastRequestSequence.current;
    const requestScenario = structuredClone(forecastScenarioRef.current);
    const requestSignature = forecastScenarioSignature(requestScenario);
    const validationErrors = validateForecastScenario(requestScenario, {
      serverNow: forecastServerNowRef.current,
    });
    setForecastFieldErrors(validationErrors);
    setForecastApiError(null);
    setForecastEvaluation(null);
    if (Object.keys(validationErrors).length > 0) {
      setForecastStatus('error');
      setForecastStatusMessage('Forecast scenario has invalid or out-of-bounds inputs. Correct the highlighted fields before evaluation.');
      return;
    }

    setForecastStatus('running');
    setForecastStatusMessage('Forecast evaluation in progress. The server is retrieving and validating its configured NESO forecast.');
    try {
      const response = await evaluateForecastScenario(
        requestScenario,
        controller.signal,
        verifiedRegion ? { region_id: verifiedRegion.id, region_name: verifiedRegion.name } : undefined,
      );
      if (controller.signal.aborted || requestId !== forecastRequestSequence.current) return;
      if (requestSignature !== forecastScenarioSignature(forecastScenarioRef.current)) return;
      setForecastEvaluation({ signature: requestSignature, result: response });
      setForecastStatus('complete');
      setForecastStatusMessage(response.comparison_eligible
        ? 'Evaluation returned. Both strategies completed the requested service; the carbon proxy remains modelled.'
        : `Evaluation returned, but the equal-service carbon-proxy comparison is unavailable: ${response.comparison_unavailable_reason}`);
    } catch (error) {
      if (controller.signal.aborted || requestId !== forecastRequestSequence.current) return;
      if (error instanceof ForecastApiError) {
        setForecastFieldErrors(error.fieldErrors);
        if (error.result) setForecastEvaluation({ signature: requestSignature, result: error.result });
        setForecastApiError(error.message);
        setForecastStatus('error');
        setForecastStatusMessage(error.status === 422
          ? 'The API rejected this forecast scenario. Review the highlighted inputs.'
          : error.message);
      } else {
        setForecastApiError('An unexpected error prevented forecast evaluation. No synthetic fallback was attempted.');
        setForecastStatus('error');
        setForecastStatusMessage('Forecast evaluation failed. Review the message and retry only after checking the scenario.');
      }
    }
  };

  const selectMode = (nextMode: EvaluationMode) => {
    if (nextMode === mode) return;
    if (mode === 'synthetic') {
      activeRequest.current?.abort();
      requestSequence.current += 1;
      if (status === 'running') {
        setStatus('edited');
        setStatusMessage('Synthetic evaluation was cancelled when the operating mode changed.');
      }
    } else {
      forecastActiveRequest.current?.abort();
      forecastRequestSequence.current += 1;
      if (forecastStatus === 'running') {
        setForecastStatus('edited');
        setForecastStatusMessage('Forecast evaluation was cancelled when the operating mode changed.');
      }
    }
    setMode(nextMode);
  };

  useEffect(() => () => {
    activeRequest.current?.abort();
    forecastActiveRequest.current?.abort();
    requestSequence.current += 1;
    forecastRequestSequence.current += 1;
  }, []);

  const loadExample = () => {
    updateScenario(createDemoScenario());
    setImportMessage('Synthetic example loaded. Values are labelled as simulation-only.');
  };

  const importScenario = async (file: File) => {
    setImportMessage(null);
    if (file.size > 256 * 1024) {
      setImportMessage('Import rejected: JSON file must be 256 KiB or smaller.');
      return;
    }
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isScenarioShape(parsed)) {
        throw new Error('The file must match the EV depot scenario contract and use synthetic_scenario inputs with simulation-only geography.');
      }
      updateScenario(parsed);
      setImportMessage(`Imported ${file.name}; server validation will run on evaluation.`);
    } catch (error) {
      setImportMessage(error instanceof Error ? `Import failed: ${error.message}` : 'Import failed: invalid JSON.');
    }
  };

  const exportScenario = () => {
    const json = `${JSON.stringify(scenario, null, 2)}\n`;
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${scenario.scenario_id.replace(/[^a-z0-9-_]+/gi, '-').replace(/^-|-$/g, '') || 'ev-depot-scenario'}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setImportMessage('Current scenario exported as JSON.');
  };

  const runSimulation = async () => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const requestId = ++requestSequence.current;
    const requestRevision = revisionRef.current;
    const requestScenario = structuredClone(scenarioRef.current);
    const requestSignature = scenarioSignature(requestScenario);
    setFieldErrors({});
    setApiError(null);
    setStatus('running');
    setStatusMessage('Simulation in progress. The backend is evaluating both existing strategies.');

    try {
      const response = await evaluateScenario(requestScenario, controller.signal);
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      if (requestSignature !== scenarioSignature(scenarioRef.current)) return;
      setEvaluation({ revision: requestRevision, result: response });
      setStatus('complete');
      setStatusMessage('Simulation complete. Results below belong to the current scenario version.');
    } catch (error) {
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      if (error instanceof SimulationApiError) {
        setFieldErrors(error.fieldErrors);
        setApiError(error.message);
      } else if (error instanceof Error && error.name !== 'AbortError') {
        setApiError('An unexpected error prevented the simulation. Please try again.');
      }
      setStatus('error');
      setStatusMessage('Simulation failed. Review the message and highlighted fields.');
    }
  };

  return (
    <div className="app-shell">
      <aside className="side-rail" aria-label="Application rail">
        <a className="brand" href="#main-content" aria-label="Green Energy Dispatch, Dispatch Workbench">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand-copy"><strong>GED / LAB</strong><small>DISPATCH SYSTEMS</small></span>
        </a>
        <span className="rail-label">WORKSPACES</span>
        <nav className="rail-nav" aria-label="Workspace sections">
          <a aria-label="Scenario" href={mode === 'synthetic' ? '#scenario-heading' : '#forecast-scenario-title'}><span className="rail-index">01</span><span>Scenario</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          <a aria-label="Analysis" href="#results-pane"><span className="rail-index">02</span><span>Analysis</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          {(mode === 'synthetic' ? result : forecastResult) && <a aria-label="Schedules" href={mode === 'synthetic' ? '#schedule-details-heading' : '#forecast-results'}><span className="rail-index">03</span><span>Schedules</span><span className="rail-arrow" aria-hidden="true">↗</span></a>}
          <a aria-label="Live data" href="#live-carbon-intensity"><span className="rail-index">04</span><span>Live data</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          <a aria-label="Data basis" href="#source-basis"><span className="rail-index">05</span><span>Data basis</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
        </nav>
        <div className="rail-spacer" />
        <div className="rail-boundary"><span className="rail-boundary-mark" aria-hidden="true">{mode === 'synthetic' ? 'S' : 'A'}</span><div><strong>{mode === 'synthetic' ? 'SIMULATION ONLY' : 'ADVISORY ONLY'}</strong><small>NO PHYSICAL ACTUATION</small></div></div>
        <span className="rail-version">EV DEPOT · {mode === 'synthetic' ? 'SYNTHETIC INPUT' : 'NESO FORECAST'}</span>
      </aside>

      <div className="app-frame">
        <header className="topbar">
          <div className="topbar-context">
            <span>EV DEPOT / {mode === 'synthetic' ? 'SYNTHETIC' : 'FORECAST-BACKED'} SCENARIO</span>
            <strong title={mode === 'synthetic' ? scenario.scenario_id : forecastScenario.scenario_id}>
              {(mode === 'synthetic' ? scenario.scenario_id : forecastScenario.scenario_id) || 'Unnamed scenario'}
            </strong>
          </div>
          <div className="topbar-right">
            <span className={`status-chip status-${displayStatus}`} aria-hidden="true">
              <i />
              <span className="status-chip-label-full">{displayStatus === 'running' ? 'EVALUATING' : displayStatus === 'complete' ? 'EVALUATION COMPLETE' : displayStatus === 'error' ? 'RUN FAILED' : 'INPUTS EDITED'}</span>
              <span className="status-chip-label-compact">{displayStatus === 'running' ? 'RUNNING' : displayStatus === 'complete' ? 'COMPLETE' : displayStatus === 'error' ? 'FAILED' : 'EDITED'}</span>
            </span>
            {mode === 'synthetic' && <button className="button button-primary topbar-run-button" type="button" onClick={runSimulation} disabled={status === 'running'}>
              {status === 'running' ? <><span className="spinner" aria-hidden="true" /> Evaluating…</> : 'Run simulation'}
            </button>}
          </div>
        </header>

        <main id="main-content" className="main-content">
          <section className="workspace-heading" aria-labelledby="workspace-title">
            <div>
              <p className="eyebrow">CONTROL ROOM / DEPOT DISPATCH</p>
              <h1 id="workspace-title">Dispatch workbench</h1>
            </div>
            <p>{mode === 'synthetic'
              ? 'Configure a synthetic scenario and inspect the API-returned schedules; live NESO data remains separate.'
              : 'Configure depot requirements for a server-validated NESO forecast; no charger control is performed.'}</p>
          </section>

          <fieldset className="mode-selector" aria-label="Scheduling mode">
            <legend>Scheduling mode</legend>
            <label className={mode === 'synthetic' ? 'mode-option mode-option-selected' : 'mode-option'}>
              <input type="radio" name="scheduling-mode" value="synthetic" checked={mode === 'synthetic'} onChange={() => selectMode('synthetic')} />
              <span><strong>Synthetic scenario</strong><small>Existing simulation endpoint · manually configured synthetic inputs</small></span>
            </label>
            <label className={mode === 'forecast' ? 'mode-option mode-option-selected' : 'mode-option'}>
              <input type="radio" name="scheduling-mode" value="forecast" checked={mode === 'forecast'} onChange={() => selectMode('forecast')} />
              <span><strong>NESO forecast-backed scenario</strong><small>Server-owned regional forecast · advisory scheduling only</small></span>
            </label>
          </fieldset>

          <div className={`run-status run-status-${displayStatus}`} role="status" aria-live="polite" aria-atomic="true">
            <span className="run-indicator" aria-hidden="true">{displayStatus === 'running' ? '◌' : displayStatus === 'complete' ? '✓' : displayStatus === 'error' ? '!' : '·'}</span>
            <span>{mode === 'synthetic' ? statusMessage : forecastStatusMessage}</span>
          </div>
          {mode === 'synthetic' && apiError && <div className="error-banner" role="alert"><strong>Simulation could not be completed</strong><span>{apiError}</span></div>}
          {mode === 'forecast' && forecastApiError && <div className="error-banner" role="alert"><strong>Forecast evaluation could not be completed</strong><span>{forecastApiError}</span></div>}

          <div className={`workbench-grid ${mode === 'forecast' ? 'workbench-grid-forecast' : ''}`}>
            <aside className="scenario-dock" aria-label="Scenario setup">
              {mode === 'synthetic'
                ? <ScenarioEditor
                    scenario={scenario}
                    errors={fieldErrors}
                    importMessage={importMessage}
                    onChange={updateScenario}
                    onLoadExample={loadExample}
                    onImport={importScenario}
                    onExport={exportScenario}
                  />
                : <ForecastScenarioEditor
                    scenario={forecastScenario}
                    errors={forecastFieldErrors}
                    status={forecastStatus}
                    serverNow={forecastServerNow}
                    onChange={updateForecastScenario}
                    onAddVehicle={addForecastVehicle}
                    onRemoveVehicle={removeForecastVehicle}
                    onEvaluate={() => void runForecastEvaluation()}
                  />}
            </aside>
            <section id="results-pane" className="analysis-column" aria-label={mode === 'synthetic' ? 'Simulation output' : 'Forecast scheduling output'}>
              {mode === 'synthetic'
                ? !result ? (
                    <div className="panel empty-results">
                      <div className="empty-results-top"><span>ANALYSIS / AWAITING EVALUATION</span><span>NO RESULT</span></div>
                      <div className="empty-graphic" aria-hidden="true"><span>01</span><i /><span>02</span></div>
                      <p className="eyebrow">RUN CURRENT SCENARIO</p>
                      <h2>{evaluation && !isCurrentResult ? 'Results are out of date' : 'Ready to evaluate'}</h2>
                      <p>{evaluation && !isCurrentResult ? 'The inputs have changed since the last run. Earlier results are withheld to prevent misattribution.' : 'Send this synthetic scenario to the FastAPI simulation endpoint to calculate both existing schedules.'}</p>
                      <div className="empty-step-grid"><div><span>01 / INPUT</span><strong>Scenario configuration</strong></div><div><span>02 / EVALUATION</span><strong>Two scheduling strategies</strong></div><div><span>03 / OUTPUT</span><strong>Service and proxy results</strong></div></div>
                      <button className="button button-primary" type="button" onClick={runSimulation} disabled={status === 'running'}>{status === 'running' ? 'Evaluating…' : 'Evaluate this scenario'}</button>
                    </div>
                  ) : <ResultsPanel result={result} scenario={scenario} />
                : forecastResult
                  ? <ForecastResultsPanel result={forecastResult} scenario={forecastScenario} />
                  : <div className="panel empty-results forecast-empty-results">
                      <div className="empty-results-top"><span>FORECAST SCHEDULING / AWAITING EVALUATION</span><span>NO RESULT</span></div>
                      <p className="eyebrow">SERVER-VALIDATED FORECAST / ADVISORY ONLY</p>
                      <h2>{forecastStatus === 'running'
                        ? 'Forecast evaluation in progress'
                        : forecastStatus === 'error'
                          ? 'No current forecast result'
                          : forecastEvaluation
                            ? 'Forecast results are out of date'
                            : 'Ready for forecast evaluation'}</h2>
                      <p>{forecastStatus === 'running'
                        ? 'Previous results are hidden until this exact evaluation completes.'
                        : forecastStatus === 'error'
                          ? 'No result is available for this evaluation. Resolve the reported error before relying on scheduling or carbon-proxy outputs.'
                          : forecastEvaluation
                            ? 'The forecast scenario changed after the previous evaluation. Its results are withheld until this exact version is evaluated.'
                            : 'Submit only the depot scenario. The API retrieves and validates NESO forecast evidence independently; it does not control physical chargers.'}</p>
                    </div>}
            </section>
          </div>
          {mode === 'synthetic' && result && <div className="schedule-workspace"><ResultsDetailsPanel result={result} scenario={scenario} /></div>}

          {mode === 'forecast' && <ForecastTimelinePanel
            region={forecastResult?.input_provenance
              ? { id: forecastResult.input_provenance.region_id, name: forecastResult.input_provenance.region_name }
              : verifiedRegion}
          />}

          <LiveCarbonIntensity onServerTime={handleServerTime} onVerifiedReading={handleVerifiedReading} />

          <section id="source-basis" className="signal-note panel" aria-labelledby="signal-note-heading">
            <span className="signal-index">DATA / 01</span>
            <div><p className="eyebrow">SOURCE BOUNDARY</p><h2 id="signal-note-heading">{mode === 'synthetic' ? 'Synthetic schedule inputs — live display isolated' : 'Forecast-backed schedule — server-owned evidence'}</h2><p>{mode === 'synthetic'
              ? 'Schedule evaluation continues to use manually configured synthetic intensity values only. The read-only NESO display does not feed, modify, or influence either scheduling strategy.'
              : 'The forecast evaluation submits only vehicle, timing and depot constraints. The API retrieves and validates its own configured regional forecast; this live display and optional timeline preview remain separate. Carbon outcomes are modelled proxies, not measured emissions.'}</p></div>
            <span className="signal-status">{mode === 'synthetic' ? 'SYNTHETIC INPUT' : 'ADVISORY ONLY'}</span>
          </section>

          <footer className="page-footer"><span>GREEN ENERGY DISPATCH <b>·</b> LOCAL ADVISORY TOOL</span><span>NO PHYSICAL ACTUATION <b>·</b> NO ELECTRICITY TRADING</span></footer>
        </main>
      </div>
    </div>
  );
}
