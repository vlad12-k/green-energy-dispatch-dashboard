import { useRef, useState } from 'react';
import type { FieldErrors, Scenario, SimulationResult } from './api/client';
import { SimulationApiError, evaluateScenario } from './api/client';
import { ResultsDetailsPanel, ResultsPanel } from './components/ResultsPanel';
import { ScenarioEditor } from './components/ScenarioEditor';
import { LiveCarbonIntensity } from './components/LiveCarbonIntensity';
import { createDemoScenario, scenarioSignature } from './data/scenario';

interface EvaluationSnapshot {
  revision: number;
  result: SimulationResult;
}

type EvaluationStatus = 'edited' | 'running' | 'complete' | 'error';

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
      <aside className="side-rail" aria-label="Application rail">
        <a className="brand" href="#main-content" aria-label="Green Energy Dispatch, Dispatch Workbench">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span className="brand-copy"><strong>GED / LAB</strong><small>DISPATCH SYSTEMS</small></span>
        </a>
        <span className="rail-label">WORKSPACES</span>
        <nav className="rail-nav" aria-label="Workspace sections">
          <a aria-label="Scenario" href="#scenario-heading"><span className="rail-index">01</span><span>Scenario</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          <a aria-label="Analysis" href="#results-pane"><span className="rail-index">02</span><span>Analysis</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          {result && <a aria-label="Schedules" href="#schedule-details-heading"><span className="rail-index">03</span><span>Schedules</span><span className="rail-arrow" aria-hidden="true">↗</span></a>}
          <a aria-label="Live data" href="#live-carbon-intensity"><span className="rail-index">04</span><span>Live data</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
          <a aria-label="Data basis" href="#source-basis"><span className="rail-index">05</span><span>Data basis</span><span className="rail-arrow" aria-hidden="true">↗</span></a>
        </nav>
        <div className="rail-spacer" />
        <div className="rail-boundary"><span className="rail-boundary-mark" aria-hidden="true">S</span><div><strong>SIMULATION ONLY</strong><small>LOCAL / NO ACTUATION</small></div></div>
        <span className="rail-version">EV DEPOT · SYNTHETIC INPUT</span>
      </aside>

      <div className="app-frame">
        <header className="topbar">
          <div className="topbar-context">
            <span>EV DEPOT / ACTIVE SCENARIO</span>
            <strong title={scenario.scenario_id}>{scenario.scenario_id || 'Unnamed scenario'}</strong>
          </div>
          <div className="topbar-right">
            <span className={`status-chip status-${status}`} aria-hidden="true">
              <i />
              <span className="status-chip-label-full">{status === 'running' ? 'EVALUATING' : status === 'complete' ? 'EVALUATION COMPLETE' : status === 'error' ? 'RUN FAILED' : 'INPUTS EDITED'}</span>
              <span className="status-chip-label-compact">{status === 'running' ? 'RUNNING' : status === 'complete' ? 'COMPLETE' : status === 'error' ? 'FAILED' : 'EDITED'}</span>
            </span>
            <button className="button button-primary topbar-run-button" type="button" onClick={runSimulation} disabled={status === 'running'}>
              {status === 'running' ? <><span className="spinner" aria-hidden="true" /> Evaluating…</> : 'Run simulation'}
            </button>
          </div>
        </header>

        <main id="main-content" className="main-content">
          <section className="workspace-heading" aria-labelledby="workspace-title">
            <div>
              <p className="eyebrow">CONTROL ROOM / DEPOT DISPATCH</p>
              <h1 id="workspace-title">Dispatch workbench</h1>
            </div>
            <p>Configure one synthetic scenario, then inspect both API-returned schedules.</p>
          </section>

          <div className={`run-status run-status-${status}`} role="status" aria-live="polite" aria-atomic="true">
            <span className="run-indicator" aria-hidden="true">{status === 'running' ? '◌' : status === 'complete' ? '✓' : status === 'error' ? '!' : '·'}</span>
            <span>{statusMessage}</span>
          </div>
          {apiError && <div className="error-banner" role="alert"><strong>Simulation could not be completed</strong><span>{apiError}</span></div>}

          <div className="workbench-grid">
            <aside className="scenario-dock" aria-label="Scenario setup">
              <ScenarioEditor
                scenario={scenario}
                errors={fieldErrors}
                importMessage={importMessage}
                onChange={updateScenario}
                onLoadExample={loadExample}
                onImport={importScenario}
                onExport={exportScenario}
              />
            </aside>
            <section id="results-pane" className="analysis-column" aria-label="Simulation output">
              {!result ? (
                <div className="panel empty-results">
                  <div className="empty-results-top"><span>ANALYSIS / AWAITING EVALUATION</span><span>NO RESULT</span></div>
                  <div className="empty-graphic" aria-hidden="true"><span>01</span><i /><span>02</span></div>
                  <p className="eyebrow">RUN CURRENT SCENARIO</p>
                  <h2>{evaluation && !isCurrentResult ? 'Results are out of date' : 'Ready to evaluate'}</h2>
                  <p>{evaluation && !isCurrentResult ? 'The inputs have changed since the last run. Earlier results are withheld to prevent misattribution.' : 'Send this synthetic scenario to the FastAPI simulation endpoint to calculate both existing schedules.'}</p>
                  <div className="empty-step-grid"><div><span>01 / INPUT</span><strong>Scenario configuration</strong></div><div><span>02 / EVALUATION</span><strong>Two scheduling strategies</strong></div><div><span>03 / OUTPUT</span><strong>Service and proxy results</strong></div></div>
                  <button className="button button-primary" type="button" onClick={runSimulation} disabled={status === 'running'}>{status === 'running' ? 'Evaluating…' : 'Evaluate this scenario'}</button>
                </div>
              ) : <ResultsPanel result={result} scenario={scenario} />}
            </section>
          </div>
          {result && <div className="schedule-workspace"><ResultsDetailsPanel result={result} scenario={scenario} /></div>}

          <LiveCarbonIntensity />

          <section id="source-basis" className="signal-note panel" aria-labelledby="signal-note-heading">
            <span className="signal-index">DATA / 01</span>
            <div><p className="eyebrow">SOURCE BOUNDARY</p><h2 id="signal-note-heading">Synthetic schedule inputs — live display isolated</h2><p>Schedule evaluation continues to use manually configured synthetic intensity values only. The read-only NESO display does not feed, modify, or influence either scheduling strategy.</p></div>
            <span className="signal-status">NOT CONNECTED</span>
          </section>

          <footer className="page-footer"><span>GREEN ENERGY DISPATCH <b>·</b> LOCAL ADVISORY TOOL</span><span>NO PHYSICAL ACTUATION <b>·</b> NO ELECTRICITY TRADING</span></footer>
        </main>
      </div>
    </div>
  );
}
