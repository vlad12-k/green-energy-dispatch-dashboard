import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DispatchFlow } from './DispatchFlow';
import { createDemoScenario } from '../data/scenario';
import { incompleteSimulationResult, simulationResult } from '../test/fixtures';

describe('DispatchFlow', () => {
  it('maps scenario inputs to the two actual schedule results without inventing grid telemetry', () => {
    const scenario = createDemoScenario();
    const result = simulationResult();

    const { container } = render(<DispatchFlow result={result} scenario={scenario} />);

    expect(screen.getByRole('heading', { name: 'Scenario-to-schedule flow' })).toBeInTheDocument();

    expect(container.querySelector('.flow-input-energy')).toHaveTextContent('8 kWh');
    expect(container.querySelector('.flow-input-intervals')).toHaveTextContent('2 intervals');
    expect(screen.getByText('Immediate baseline')).toBeInTheDocument();
    expect(screen.getByText('Carbon-aware heuristic')).toBeInTheDocument();
    expect(Array.from(container.querySelectorAll('.flow-output-delivered'))).toHaveLength(2);
    expect(Array.from(container.querySelectorAll('.flow-output-delivered')).every((node) => node.textContent?.includes('8 kWh'))).toBe(true);
    expect(Array.from(container.querySelectorAll('.flow-output-peak')).every((node) => node.textContent?.includes('7 kW'))).toBe(true);
    expect(screen.getByRole('group', { name: 'Simulation evaluation stages' })).toBeInTheDocument();
  });

  it('labels incomplete schedules from the API response without asserting infeasibility', () => {
    render(<DispatchFlow result={incompleteSimulationResult()} scenario={createDemoScenario()} />);

    expect(screen.getAllByText('Requirements not demonstrated')).toHaveLength(2);
    expect(screen.queryByText(/No feasible schedule exists/)).not.toBeInTheDocument();
  });
});
