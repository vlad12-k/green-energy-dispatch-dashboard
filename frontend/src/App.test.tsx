import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createDemoScenario } from './data/scenario';
import type { ForecastScenario, ForecastTimeline } from './api/client';
import {
  jsonResponse,
  incompleteSimulationResult,
  simulationResult,
  forecastSchedulingResultFixture,
  unavailableForecastSchedulingResult,
} from './test/fixtures';

afterEach(() => vi.unstubAllGlobals());

const retrievedAt = new Date();
const intervalStart = new Date(retrievedAt.getTime() - 5 * 60 * 1000);
const intervalEnd = new Date(intervalStart.getTime() + 30 * 60 * 1000);

const liveNesoReading = {
  intensity_gco2_per_kwh: 79,
  measurement_type: 'actual',
  units: 'gCO2/kWh',
  region_id: 13,
  region_name: 'London',
  intensity_index: 'moderate',
  observed_at: intervalStart.toISOString(),
  valid_until: intervalEnd.toISOString(),
  retrieved_at: retrievedAt.toISOString(),
  interval_status: 'in_progress',
  interval_lag_seconds: 0,
  is_fresh: true,
  age_seconds: 300,
  fresh_until: new Date(retrievedAt.getTime() + 55 * 60 * 1000).toISOString(),
  source_api: 'neso-carbon-intensity-api',
  source_url: 'https://api.carbonintensity.org.uk/regional/regionid/13',
};

function forecastTimelineFixture(requestedFrom: string, retrievedAt: string): ForecastTimeline {
  const start = Date.parse(requestedFrom);
  return {
    status: 'available',
    measurement_type: 'forecast',
    source_api: 'neso-carbon-intensity-api',
    source_url: `https://api.carbonintensity.org.uk/regional/intensity/${requestedFrom.slice(0, 16)}Z/fw24h/regionid/13`,
    region_id: 13,
    region_name: 'London',
    requested_from: requestedFrom,
    retrieved_at: retrievedAt,
    valid_until: new Date(start + 48 * 30 * 60_000).toISOString(),
    units: 'gCO2/kWh',
    intervals: Array.from({ length: 48 }, (_, index) => {
      const intervalStart = new Date(start + index * 30 * 60_000).toISOString();
      return {
        observed_at: intervalStart,
        valid_until: new Date(Date.parse(intervalStart) + 30 * 60_000).toISOString(),
        intensity_gco2_per_kwh: index < 2 ? 400 : 50,
        intensity_index: index < 2 ? 'high' : 'low',
        measurement_type: 'forecast',
      };
    }),
  };
}

describe('Operator dashboard', () => {
  it('organises controls and analysis in the new workbench shell', () => {
    render(<App />);

    expect(screen.getByRole('navigation', { name: 'Workspace sections' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dispatch workbench' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Scenario configuration' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Simulation output' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run simulation' })).toBeVisible();
  });

  it('shows the live feed without sending it to the synthetic scheduler', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL) => (
      String(input) === '/api/v1/energy/carbon-intensity'
        ? Promise.resolve(jsonResponse(liveNesoReading))
        : Promise.resolve(jsonResponse(simulationResult()))
    ));
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    expect(await screen.findByRole('heading', { name: 'London' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByRole('heading', { name: 'Schedule comparison' })).toBeInTheDocument();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/v1/energy/carbon-intensity',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/v1/simulations/ev-depot/evaluate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(createDemoScenario()),
      }),
    );
  });

  it('loads the synthetic example, edits it and renders backend response values', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(simulationResult()))));
    render(<App />);

    expect(screen.getByRole('heading', { name: 'Dispatch workbench' })).toBeInTheDocument();
    expect(screen.getByLabelText('Required energy (kWh)')).toHaveValue(8);
    await user.click(screen.getByRole('button', { name: 'Load synthetic example' }));
    const energy = screen.getByLabelText('Required energy (kWh)');
    await user.clear(energy);
    await user.type(energy, '8.5');
    const intensity = document.querySelector<HTMLInputElement>('#carbon_intensity_profile\\.1\\.intensity_value');
    expect(intensity).not.toBeNull();
    await user.clear(intensity!);
    await user.type(intensity!, '50');

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));

    expect(await screen.findByRole('heading', { name: 'Schedule comparison' })).toBeInTheDocument();
    expect(screen.getByText('2,900')).toBeInTheDocument();
    expect(screen.getByText('1,100')).toBeInTheDocument();
    expect(screen.getAllByText('Complete schedule demonstrated')).toHaveLength(2);
    expect(screen.getByText(/Synthetic carbon-intensity-weighted energy proxy only/)).toBeInTheDocument();
  });

  it('keeps synthetic schedule details hidden while the forecast mode is selected', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/evaluate') return Promise.resolve(jsonResponse(simulationResult()));
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByRole('heading', { name: 'Interval and constraint detail' })).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));

    expect(screen.queryByRole('heading', { name: 'Interval and constraint detail' })).not.toBeInTheDocument();
  });

  it('does not show a schedule link when the selected mode has no schedule target', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/evaluate') return Promise.resolve(jsonResponse(simulationResult()));
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    const scheduleLink = await screen.findByRole('link', { name: 'Schedules' });
    expect(scheduleLink).toHaveAttribute('href', '#schedule-details-heading');
    expect(document.querySelector('#schedule-details-heading')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));

    expect(screen.queryByRole('link', { name: 'Schedules' })).not.toBeInTheDocument();
  });

  it('provides workspace rail anchors for scenario, analysis and source basis', () => {
    render(<App />);

    expect(screen.getByRole('navigation', { name: 'Workspace sections' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Scenario' })).toHaveAttribute('href', '#scenario-heading');
    expect(screen.getByRole('link', { name: 'Analysis' })).toHaveAttribute('href', '#results-pane');
    expect(screen.getByRole('link', { name: 'Data basis' })).toHaveAttribute('href', '#source-basis');
    expect(screen.getByRole('complementary', { name: 'Application rail' })).toBeInTheDocument();
  });

  it('withholds stale results and ignores an in-flight response after editing', async () => {
    const user = userEvent.setup();
    let resolvePending: ((response: Response) => void) | undefined;
    let simulationCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/v1/energy/carbon-intensity') {
        return Promise.resolve(jsonResponse(liveNesoReading));
      }
      simulationCalls += 1;
      if (simulationCalls === 1) return Promise.resolve(jsonResponse(simulationResult()));
      return new Promise<Response>((resolve) => { resolvePending = resolve; });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByRole('heading', { name: 'Schedule comparison' })).toBeInTheDocument();
    const energy = screen.getByLabelText('Required energy (kWh)');
    await user.clear(energy);
    await user.type(energy, '9');
    expect(screen.queryByRole('heading', { name: 'Schedule comparison' })).not.toBeInTheDocument();
    expect(screen.getByText(/Previous results are hidden until this version is evaluated/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    await waitFor(() => expect(simulationCalls).toBe(2));
    await user.clear(energy);
    await user.type(energy, '10');
    await act(async () => resolvePending?.(jsonResponse(simulationResult())));

    expect(screen.queryByRole('heading', { name: 'Schedule comparison' })).not.toBeInTheDocument();
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
  });

  it('announces validation failures and shows server errors next to fields', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({
      detail: [{
        type: 'greater_than',
        loc: ['body', 'vehicles', 0, 'required_energy_kwh'],
        msg: 'Input should be greater than 0',
      }],
    }, 422))));
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));

    expect(await screen.findByText('Simulation could not be completed')).toBeInTheDocument();
    const energyInput = screen.getByLabelText('Required energy (kWh)');
    expect(energyInput).toHaveAttribute('aria-invalid', 'true');
    expect(energyInput).toHaveAttribute('aria-describedby', 'vehicles-0-required_energy_kwh-error');
    expect(document.getElementById('vehicles-0-required_energy_kwh-error')).toHaveTextContent('Input should be greater than 0');
    expect(screen.getByRole('status')).toHaveTextContent('Simulation failed');
  });

  it('explains incomplete schedules and withholds equal-service comparison results', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(incompleteSimulationResult()))));
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Run simulation' }));

    expect(await screen.findByText('Not comparable')).toBeInTheDocument();
    expect(screen.getAllByText('Complete schedule not demonstrated')).toHaveLength(2);
    expect(screen.getAllByText(/This heuristic did not demonstrate complete service/)).toHaveLength(2);
    expect(screen.getByText(/At least one strategy did not demonstrate complete service/)).toBeInTheDocument();
    expect(screen.getByText('Not defined for an unavailable equal-service comparison.')).toBeInTheDocument();
  });

  it('adds and removes vehicle rows within the scenario editor', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole('button', { name: 'Add vehicle' }));
    expect(screen.getAllByLabelText('Vehicle ID')).toHaveLength(2);
    expect(screen.getAllByLabelText('Vehicle ID')[1]).toHaveValue('vehicle-2');
    await user.click(screen.getByRole('button', { name: 'Remove vehicle vehicle-2' }));
    expect(screen.getAllByLabelText('Vehicle ID')).toHaveLength(1);
  });

  it('imports a local scenario JSON file', async () => {
    const user = userEvent.setup();
    render(<App />);
    const imported = createDemoScenario();
    imported.vehicles[0]!.required_energy_kwh = 6.25;
    imported.carbon_intensity_profile.forEach((row) => {
      row.provenance_label = 'synthetic:imported-fixture-v1';
    });
    const file = new File([JSON.stringify(imported)], 'scenario.json', { type: 'application/json' });

    await user.upload(screen.getByLabelText('Import scenario JSON'), file);

    expect(screen.getByLabelText('Required energy (kWh)')).toHaveValue(6.25);
    expect(screen.getByText('synthetic:imported-fixture-v1')).toBeInTheDocument();
    expect(screen.getByText(/Imported scenario\.json/)).toBeInTheDocument();
  });

  it('describes each carbon intensity input with its unit', () => {
    render(<App />);

    expect(screen.getByRole('spinbutton', { name: /Interval 01/ }))
      .toHaveAccessibleDescription('gCO2/kWh');
  });

  it('rejects imported non-synthetic profiles without relabelling them', async () => {
    const user = userEvent.setup();
    render(<App />);
    const imported = createDemoScenario();
    imported.carbon_intensity_profile[0]!.source_classification = 'observed_actual';
    const file = new File([JSON.stringify(imported)], 'actual.json', { type: 'application/json' });

    await user.upload(screen.getByLabelText('Import scenario JSON'), file);

    expect(screen.getByLabelText('Required energy (kWh)')).toHaveValue(8);
    expect(screen.getByText(/Import failed: The file must match the EV depot scenario contract and use synthetic_scenario inputs/)).toBeInTheDocument();
  });

  it('resets the scenario through the working example action', async () => {
    const user = userEvent.setup();
    render(<App />);
    const energy = screen.getByLabelText('Required energy (kWh)');
    fireEvent.change(energy, { target: { value: '9.25' } });
    expect(energy).toHaveValue(9.25);

    await user.click(screen.getByRole('button', { name: 'Load synthetic example' }));

    expect(screen.getByLabelText('Required energy (kWh)')).toHaveValue(8);
    expect(screen.getByText(/Synthetic example loaded\. Values are labelled as simulation-only\./)).toBeInTheDocument();
  });

  it('submits a bounded forecast scenario only to the explicit forecast endpoint', async () => {
    const user = userEvent.setup();
    const requests: Array<{ path: string; body?: BodyInit | null }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ path: url, body: init?.body });
      if (url === '/api/v1/energy/carbon-intensity') {
        return Promise.resolve(jsonResponse(liveNesoReading));
      }
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        return Promise.resolve(jsonResponse({ detail: 'upstream unavailable' }, 503));
      }
      return Promise.resolve(jsonResponse(simulationResult()));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    const syntheticMode = screen.getByRole('radio', { name: /synthetic scenario/i });
    const forecastMode = screen.getByRole('radio', { name: /neso forecast-backed scenario/i });
    expect(syntheticMode).toBeChecked();
    await user.click(forecastMode);
    expect(forecastMode).toBeChecked();
    expect(await screen.findByRole('heading', { name: 'London' })).toBeInTheDocument();

    expect(screen.getByRole('heading', { name: 'Forecast scenario' })).toBeInTheDocument();
    const startInput = screen.getByLabelText('Scheduling start (UTC)');
    const startValue = (startInput as HTMLInputElement).value;
    expect(startValue).toMatch(/T\d{2}:(?:00|30)$/);
    expect(Date.parse(`${startValue}:00Z`)).toBeGreaterThan(Date.parse(liveNesoReading.retrieved_at) + 30 * 60_000);
    expect(screen.getByLabelText('Scheduling intervals')).toHaveValue(4);
    expect(screen.getByText(/forecast values remain server-owned/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/simulations/ev-depot/forecast-evaluate',
      expect.objectContaining({ method: 'POST' }),
    ));

    const request = requests.find(({ path }) => (
      path === '/api/v1/simulations/ev-depot/forecast-evaluate'
    ));
    expect(request).toBeDefined();
    const payload = JSON.parse(String(request?.body)) as Record<string, unknown>;
    expect(Object.keys(payload)).toEqual(['scenario']);
    expect(payload.scenario).toMatchObject({ interval_minutes: 30, intervals: 4 });
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/v1/simulations/ev-depot/evaluate',
      expect.anything(),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(/forecast evaluation is temporarily unavailable/i);
  });

  it('loads a regional forecast timeline only after a server response binds the region', async () => {
    const user = userEvent.setup();
    const retrievedAt = new Date().toISOString();
    const requestedFrom = new Date(Math.ceil(Date.now() / (30 * 60_000)) * (30 * 60_000)).toISOString();
    const timeline = forecastTimelineFixture(requestedFrom, retrievedAt);
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/energy/carbon-intensity/forecast?region_id=13') {
        const response = jsonResponse(timeline);
        response.headers.set('Date', new Date(retrievedAt).toUTCString());
        return Promise.resolve(response);
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    expect(await screen.findByRole('heading', { name: 'London' })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('/carbon-intensity/forecast?'),
      expect.anything(),
    );
    await user.click(screen.getByRole('button', { name: 'Load regional forecast preview' }));

    expect(await screen.findByRole('heading', { name: 'Grid carbon forecast' })).toBeInTheDocument();
    expect(screen.getByText('FORECAST SNAPSHOT', { selector: '.forecast-provenance-pill' })).toBeInTheDocument();
    expect(screen.getByText(/FORECAST · gCO2\/kWh/)).toBeInTheDocument();
    expect(screen.getByText(/forecast retrieval time/i)).toBeInTheDocument();
    expect(document.querySelectorAll('[data-forecast-interval]')).toHaveLength(48);
    await user.click(screen.getByText('View forecast interval values'));
    const forecastTable = screen.getByRole('table', { name: /regional carbon-intensity forecast/i });
    expect(forecastTable.querySelectorAll('tbody tr')).toHaveLength(48);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/energy/carbon-intensity/forecast?region_id=13',
      expect.objectContaining({ method: 'GET', cache: 'no-store' }),
    );
  });

  it('renders paired forecast strategies with shared time, provenance and numeric alternatives', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        const payload = JSON.parse(String(init?.body)) as { scenario: ForecastScenario };
        return Promise.resolve(jsonResponse(forecastSchedulingResultFixture(payload.scenario)));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    expect(await screen.findByRole('heading', { name: 'Charging strategy comparison' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Immediate Baseline' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Forecast-aware Heuristic' })).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Forecast-weighted carbon proxy comparison' }))
      .getByText(/forecast-weighted carbon proxy/i)).toBeInTheDocument();
    expect(screen.getByText('Forecast provenance')).toBeInTheDocument();
    expect(screen.getByText(/regional_forecast/i)).toBeInTheDocument();
    expect(screen.getByText(/modelled forecast-weighted carbon proxy only/i)).toBeInTheDocument();
    expect(document.querySelector('[data-comparison-power-chart]')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /forecast strategy power by utc interval/i })).toBeInTheDocument();
  });

  it('hides a prior result during reevaluation and rejects inconsistent proxy metrics', async () => {
    const user = userEvent.setup();
    let evaluationCount = 0;
    let resolveInvalidResponse: ((response: Response) => void) | undefined;
    let invalidResponsePayload: ReturnType<typeof forecastSchedulingResultFixture> | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        const payload = JSON.parse(String(init?.body)) as { scenario: ForecastScenario };
        evaluationCount += 1;
        const result = forecastSchedulingResultFixture(payload.scenario);
        if (evaluationCount === 2) {
          result.absolute_carbon_proxy_reduction = (result.absolute_carbon_proxy_reduction ?? 0) + 1;
          invalidResponsePayload = result;
          return new Promise<Response>((resolve) => { resolveInvalidResponse = resolve; });
        }
        return Promise.resolve(jsonResponse(result));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    expect(await screen.findByRole('heading', { name: 'Charging strategy comparison' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    await waitFor(() => expect(resolveInvalidResponse).toBeDefined());
    const hiddenWhileRunning = screen.queryByRole('heading', { name: 'Charging strategy comparison' }) === null;
    await act(async () => resolveInvalidResponse?.(jsonResponse(
      invalidResponsePayload!,
    )));

    expect(hiddenWhileRunning).toBe(true);
    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or unavailable forecast result/i);
    expect(screen.queryByRole('heading', { name: 'Charging strategy comparison' })).not.toBeInTheDocument();
  });

  it('withholds carbon-proxy differences when either returned strategy is incomplete', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        const payload = JSON.parse(String(init?.body)) as { scenario: ForecastScenario };
        return Promise.resolve(jsonResponse(forecastSchedulingResultFixture(payload.scenario, false)));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    expect(await screen.findByText('Not comparable', { selector: '.forecast-comparison-status strong' })).toBeInTheDocument();
    expect(screen.getByText(/equivalent-service comparison is unavailable because at least one strategy did not complete every vehicle requirement/i, {
      selector: '.forecast-comparison-status p',
    })).toBeInTheDocument();
    expect(screen.queryByText(/carbon proxy reduction/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/complete schedule not demonstrated/i)).toHaveLength(1);
    expect(screen.getByText(/lower delivered energy is not an emissions benefit/i)).toBeInTheDocument();
  });

  it('maps a forecast 422 response to the relevant vehicle field without switching endpoints', async () => {
    const user = userEvent.setup();
    const paths: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      paths.push(url);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        return Promise.resolve(jsonResponse({ detail: [{
          type: 'greater_than',
          loc: ['body', 'scenario', 'vehicles', 0, 'required_energy_kwh'],
          msg: 'Input should be greater than 0',
        }] }, 422));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    expect(await screen.findByText('Forecast evaluation could not be completed')).toBeInTheDocument();
    expect(document.getElementById('forecast-vehicle-0-energy')).toHaveAttribute('aria-invalid', 'true');
    expect(document.getElementById('forecast-vehicles-0-required_energy_kwh-error'))
      .toHaveTextContent('Input should be greater than 0');
    expect(paths.filter((path) => path.includes('/simulations/ev-depot/')))
      .toEqual(['/api/v1/simulations/ev-depot/forecast-evaluate']);
  });

  it('announces forecast rate limiting and does not fall back to synthetic evaluation', async () => {
    const user = userEvent.setup();
    const paths: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      paths.push(url);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        return Promise.resolve(jsonResponse({ detail: 'rate limited' }, 429));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The forecast service rate-limited this request. Wait before trying again.');
    expect(paths.filter((path) => path.includes('/simulations/ev-depot/')))
      .toEqual(['/api/v1/simulations/ev-depot/forecast-evaluate']);
  });

  it('announces forecast network failures without attempting a synthetic fallback', async () => {
    const user = userEvent.setup();
    const paths: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      paths.push(url);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        return Promise.reject(new TypeError('private network detail'));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Forecast evaluation could not reach the API. Check the local service and try again.');
    expect(paths.filter((path) => path.includes('/simulations/ev-depot/')))
      .toEqual(['/api/v1/simulations/ev-depot/forecast-evaluate']);
  });

  it('renders a typed forecast 503 as unavailable without presenting a schedule', async () => {
    const user = userEvent.setup();
    const paths: string[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      paths.push(url);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        const payload = JSON.parse(String(init?.body)) as { scenario: ForecastScenario };
        return Promise.resolve(jsonResponse(unavailableForecastSchedulingResult(payload.scenario), 503));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));

    expect(await screen.findByRole('heading', { name: 'Forecast evaluation unavailable' })).toBeInTheDocument();
    expect(screen.getByText('forecast_unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Charging strategy comparison' })).not.toBeInTheDocument();
    expect(paths.filter((path) => path.includes('/simulations/ev-depot/')))
      .toEqual(['/api/v1/simulations/ev-depot/forecast-evaluate']);
  });

  it('cancels a forecast request and ignores its response after the scenario changes', async () => {
    const user = userEvent.setup();
    let resolvePending: (() => void) | undefined;
    const requestState: { signal: AbortSignal | null } = { signal: null };
    let requestedScenario: ForecastScenario | null = null;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        requestState.signal = init?.signal instanceof AbortSignal ? init.signal : null;
        requestedScenario = (JSON.parse(String(init?.body)) as { scenario: ForecastScenario }).scenario;
        return new Promise<Response>((resolve) => {
          resolvePending = () => resolve(jsonResponse(forecastSchedulingResultFixture(requestedScenario!)));
        });
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    await waitFor(() => expect(resolvePending).toBeDefined());
    const scenarioId = screen.getByLabelText('Scenario identifier');
    await user.clear(scenarioId);
    await user.type(scenarioId, 'edited-while-running');
    expect(requestState.signal).not.toBeNull();
    expect(requestState.signal?.aborted).toBe(true);

    await act(async () => resolvePending?.());

    expect(screen.queryByRole('heading', { name: 'Charging strategy comparison' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Earlier forecast results are hidden until this exact version is evaluated.');
  });

  it('does not restore an old forecast result after editing away and back to the same scenario', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') return Promise.resolve(jsonResponse(liveNesoReading));
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        const payload = JSON.parse(String(init?.body)) as { scenario: ForecastScenario };
        return Promise.resolve(jsonResponse(forecastSchedulingResultFixture(payload.scenario)));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    expect(await screen.findByRole('heading', { name: 'Charging strategy comparison' })).toBeInTheDocument();

    const scenarioId = screen.getByLabelText('Scenario identifier') as HTMLInputElement;
    const originalScenarioId = scenarioId.value;
    await user.clear(scenarioId);
    await user.type(scenarioId, 'temporary-scenario');
    await user.clear(scenarioId);
    await user.type(scenarioId, originalScenarioId);

    expect(screen.queryByRole('heading', { name: 'Charging strategy comparison' })).not.toBeInTheDocument();
  });

  it('uses the API server clock for the initial forecast default and preserves it after edits', async () => {
    const user = userEvent.setup();
    const firstServerTime = new Date(Date.now() + 10 * 60_000);
    let liveRequests = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input) === '/api/v1/energy/carbon-intensity') {
        liveRequests += 1;
        const serverTime = liveRequests === 1 ? firstServerTime : new Date(firstServerTime.getTime() + 2 * 60 * 60_000);
        return Promise.resolve(new Response(JSON.stringify(liveNesoReading), {
          headers: { 'Content-Type': 'application/json', Date: serverTime.toUTCString() },
        }));
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    await screen.findByRole('heading', { name: 'London' });
    const startInput = screen.getByLabelText('Scheduling start (UTC)') as HTMLInputElement;
    const initialStart = startInput.value;
    expect(Date.parse(`${initialStart}:00Z`)).toBeGreaterThan(firstServerTime.getTime() + 30 * 60_000);

    const energyInput = screen.getAllByLabelText('Required energy (kWh)')[0]!;
    await user.clear(energyInput);
    await user.type(energyInput, '9');
    await user.click(screen.getByRole('button', { name: 'Refresh live carbon intensity' }));
    await waitFor(() => expect(liveRequests).toBe(2));

    expect(startInput).toHaveValue(initialStart);
    expect(energyInput).toHaveValue(9);
  });

  it('cancels a forecast evaluation if the server clock updates its initial default in flight', async () => {
    const user = userEvent.setup();
    const serverTime = new Date(Date.now() + 3 * 60 * 60_000);
    let resolveLiveResponse: ((response: Response) => void) | undefined;
    let resolveEvaluation: ((response: Response) => void) | undefined;
    const requestState: { signal: AbortSignal | null; scenario: ForecastScenario | null } = {
      signal: null,
      scenario: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/energy/carbon-intensity') {
        return new Promise<Response>((resolve) => { resolveLiveResponse = resolve; });
      }
      if (url === '/api/v1/simulations/ev-depot/forecast-evaluate') {
        requestState.signal = init?.signal instanceof AbortSignal ? init.signal : null;
        requestState.scenario = (JSON.parse(String(init?.body)) as { scenario: ForecastScenario }).scenario;
        return new Promise<Response>((resolve) => { resolveEvaluation = resolve; });
      }
      return Promise.resolve(jsonResponse({ detail: 'unexpected request' }, 404));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);

    await waitFor(() => expect(resolveLiveResponse).toBeDefined());
    await user.click(screen.getByRole('radio', { name: /neso forecast-backed scenario/i }));
    const startInput = screen.getByLabelText('Scheduling start (UTC)') as HTMLInputElement;
    const initialStart = startInput.value;
    await user.click(screen.getByRole('button', { name: 'Evaluate forecast schedule' }));
    await waitFor(() => expect(resolveEvaluation).toBeDefined());
    expect(screen.getByRole('button', { name: /evaluating/i })).toBeDisabled();

    await act(async () => resolveLiveResponse?.(new Response(JSON.stringify(liveNesoReading), {
      headers: { 'Content-Type': 'application/json', Date: serverTime.toUTCString() },
    })));

    await waitFor(() => expect(startInput.value).not.toBe(initialStart));
    expect(requestState.signal?.aborted).toBe(true);
    expect(screen.getByRole('button', { name: 'Evaluate forecast schedule' })).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent(/server time updated the initial forecast scenario/i);

    await act(async () => resolveEvaluation?.(jsonResponse(forecastSchedulingResultFixture(requestState.scenario!))));

    expect(screen.queryByRole('heading', { name: 'Charging strategy comparison' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/evaluate this version again/i);
    expect(screen.getByRole('button', { name: 'Evaluate forecast schedule' })).toBeEnabled();
  });
});
