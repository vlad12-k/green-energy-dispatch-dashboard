import { useRef, useState } from 'react';
import type { FieldErrors, Scenario, SimulationResult } from './api/client';
import { SimulationApiError, evaluateScenario } from './api/client';
import { ResultsPanel } from './components/ResultsPanel';
import { ScenarioEditor } from './components/ScenarioEditor';
import { createDemoScenario, scenarioSignature } from './data/scenario';

interface EvaluationSnapshot {
  revision: number;
  result: SimulationResult;
}

type EvaluationStatus = 'edited' | 'running' | 'complete' | 'error';

function formatEnergy(value: number): string {
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 }).format(value);
}

function getRequiredEnergy(scenario: Scenario): number {
  return scenario.vehicles.reduce((total, vehicle) => total + vehicle.required_energy_kwh, 0);
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
  const isCurrentResult = evaluation !== null && evaluation.revision === revision;
  const result = isCurrentResult ? evaluation.result : null;
  const requiredEnergy = getRequiredEnergy(scenario);

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
      <header className="topbar">
        <a className="brand" href="#main-content" aria-label="Green Energy Dispatch, Operator Dashboard">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand-copy"><strong>GREEN ENERGY</strong><small>DISPATCH SYSTEMS</small></span>
        </a>
        <div className="topbar-right"><span className="environment-label"><i /> LOCAL SIMULATION</span><span className="topbar-divider" /><span className="topbar-version">OPERATOR CONSOLE <b>01</b></span></div>
      </header>

      <main id="main-content" className="main-content">
        <section className="page-intro">
          <div>
            <p className="eyebrow"><span className="eyebrow-line" /> EV DEPOT / SCENARIO EVALUATION</p>
            <h1>Operator dashboard</h1>
            <p className="page-summary">Configure a bounded synthetic depot scenario, then compare schedules calculated by the Python simulation engine.</p>
          </div>
          <div className="simulation-boundary"><span className="boundary-icon" aria-hidden="true">⌁</span><div><strong>Simulation only</strong><span>No live control · no trading</span></div></div>
        </section>

        <section className="overview-grid" aria-label="Current scenario overview">
          <article className="overview-card overview-scenario"><span className="overview-label">SCENARIO</span><strong title={scenario.scenario_id}>{scenario.scenario_id || 'Unnamed scenario'}</strong><span className="overview-foot"><i className="synthetic-dot" /> synthetic_scenario · simulation-only</span></article>
          <article className="overview-card"><span className="overview-label">FLEET SIZE</span><strong>{scenario.vehicles.length}<small> vehicles</small></strong><span className="overview-foot">Availability windows defined per vehicle</span></article>
          <article className="overview-card"><span className="overview-label">REQUIRED ENERGY</span><strong>{formatEnergy(requiredEnergy)}<small> kWh</small></strong><span className="overview-foot">{result ? `Baseline delivered ${formatEnergy(Object.values(result.baseline_delivered_energy_kwh).reduce((a, b) => a + b, 0))} kWh` : 'Awaiting simulation response'}</span></article>
          <article className="overview-card"><span className="overview-label">DEPOT CAPACITY</span><strong>{formatEnergy(scenario.depot_power_kw)}<small> kW</small></strong><span className="overview-foot">Configured aggregate power limit</span></article>
          <article className="overview-card"><span className="overview-label">PEAK SCHEDULED LOAD</span><strong>{result ? `${formatEnergy(Math.max(result.baseline_peak_depot_power_kw, result.carbon_aware.peak_depot_power_kw))}` : '—'}<small>{result ? ' kW' : ' not evaluated'}</small></strong><span className="overview-foot">Highest reported segment peak across schedules</span></article>
        </section>

        <div className={`run-status run-status-${status}`} role="status" aria-live="polite" aria-atomic="true">
          <span className="run-indicator" aria-hidden="true">{status === 'running' ? '◌' : status === 'complete' ? '✓' : status === 'error' ? '!' : '·'}</span>
          <span>{statusMessage}</span>
          <button className="button button-primary run-button" type="button" onClick={runSimulation} disabled={status === 'running'}>
            {status === 'running' ? <><span className="spinner" aria-hidden="true" /> Evaluating…</> : 'Run simulation'}
          </button>
        </div>
        {apiError && <div className="error-banner" role="alert"><strong>Simulation could not be completed</strong><span>{apiError}</span></div>}

        <div className="workspace-grid">
          <div className="editor-column">
            <ScenarioEditor
              scenario={scenario}
              errors={fieldErrors}
              importMessage={importMessage}
              onChange={updateScenario}
              onLoadExample={loadExample}
              onImport={importScenario}
              onExport={exportScenario}
            />
          </div>
          <section className="analysis-column" aria-label="Simulation output">
            {!result ? (
              <div className="panel empty-results">
                <div className="empty-icon" aria-hidden="true"><span>↗</span><span>↘</span></div>
                <p className="eyebrow">RESULTS PANE</p>
                <h2>{evaluation && !isCurrentResult ? 'Results are out of date' : 'Ready to evaluate'}</h2>
                <p>{evaluation && !isCurrentResult ? 'The inputs have changed since the last run. Earlier results are withheld to prevent misattribution.' : 'Submit the current synthetic scenario to request both schedules from the FastAPI simulation endpoint.'}</p>
                <div className="empty-checklist"><span><i>01</i> Validate scenario inputs</span><span><i>02</i> Run Python scheduling strategies</span><span><i>03</i> Inspect service and proxy outputs</span></div>
                <button className="button button-primary" type="button" onClick={runSimulation} disabled={status === 'running'}>{status === 'running' ? 'Evaluating…' : 'Evaluate this scenario'}</button>
              </div>
            ) : <ResultsPanel result={result} scenario={scenario} />}
          </section>
        </div>

        <section className="signal-note panel" aria-labelledby="signal-note-heading">
          <div className="signal-icon" aria-hidden="true">i</div>
          <div><p className="eyebrow">SOURCE SEPARATION</p><h2 id="signal-note-heading">Synthetic profile — no live NESO signal used</h2><p>This dashboard evaluates manually configured synthetic intensity values only. The existing read-only NESO endpoint remains separate and is not connected to this scheduler.</p></div>
          <span className="signal-status">NOT CONNECTED</span>
        </section>

        <footer className="page-footer"><span>GREEN ENERGY DISPATCH <b>·</b> LOCAL ADVISORY TOOL</span><span>NO PHYSICAL ACTUATION <b>·</b> NO ELECTRICITY TRADING</span></footer>
      </main>
    </div>
  );
}
