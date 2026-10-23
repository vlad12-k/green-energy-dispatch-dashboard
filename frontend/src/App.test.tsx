import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { createDemoScenario } from './data/scenario';
import { jsonResponse, incompleteSimulationResult, simulationResult } from './test/fixtures';

afterEach(() => vi.unstubAllGlobals());

describe('Operator dashboard', () => {
  it('organises controls and analysis in the new workbench shell', () => {
    render(<App />);

    expect(screen.getByRole('navigation', { name: 'Workspace sections' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dispatch workbench' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Scenario configuration' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Simulation output' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run simulation' })).toBeVisible();
  });

  it('loads the synthetic example, edits it and renders backend response values', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(simulationResult())));
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
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(simulationResult()))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolvePending = resolve; }));
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
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.clear(energy);
    await user.type(energy, '10');
    await act(async () => resolvePending?.(jsonResponse(simulationResult())));

    expect(screen.queryByRole('heading', { name: 'Schedule comparison' })).not.toBeInTheDocument();
    expect(screen.getByText(/Results are out of date/)).toBeInTheDocument();
  });

  it('announces validation failures and shows server errors next to fields', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      detail: [{
        type: 'greater_than',
        loc: ['body', 'vehicles', 0, 'required_energy_kwh'],
        msg: 'Input should be greater than 0',
      }],
    }, 422)));
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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(incompleteSimulationResult())));
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
    const imported = JSON.parse(JSON.stringify(createDemoScenario())) as {
      vehicles: Array<{ required_energy_kwh: number }>;
      [key: string]: unknown;
    };
    imported.vehicles[0]!.required_energy_kwh = 6.25;
    const file = new File([JSON.stringify(imported)], 'scenario.json', { type: 'application/json' });

    await user.upload(screen.getByLabelText('Import scenario JSON'), file);

    expect(screen.getByLabelText('Required energy (kWh)')).toHaveValue(6.25);
    expect(screen.getByText(/Imported scenario\.json/)).toBeInTheDocument();
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
});
