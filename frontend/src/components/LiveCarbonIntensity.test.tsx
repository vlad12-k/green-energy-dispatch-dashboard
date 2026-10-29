import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { jsonResponse } from '../test/fixtures';
import { LiveCarbonIntensity } from './LiveCarbonIntensity';

const retrievedAt = new Date();
const intervalStart = new Date(retrievedAt.getTime() - 5 * 60 * 1000);
const intervalEnd = new Date(intervalStart.getTime() + 30 * 60 * 1000);

const freshActual = {
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

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('LiveCarbonIntensity', () => {
  it('shows the NESO region, measured value, units, provenance and retrieval freshness', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(freshActual));
    vi.stubGlobal('fetch', fetchMock);
    render(<LiveCarbonIntensity />);

    expect(await screen.findByRole('heading', { name: 'London' })).toBeInTheDocument();
    expect(screen.getByText('79')).toBeInTheDocument();
    expect(screen.getByText('gCO2/kWh')).toBeInTheDocument();
    expect(screen.getByText('Estimated actual')).toBeInTheDocument();
    expect(screen.getByText(/Retrieved/)).toBeInTheDocument();
    expect(screen.getByText(/fresh · \d+ min/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/energy/carbon-intensity',
      expect.objectContaining({ method: 'GET', headers: { Accept: 'application/json' } }),
    );
  });

  it('marks a completed forecast stale instead of presenting it as current actual data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      ...freshActual,
      measurement_type: 'forecast',
      interval_status: 'completed',
      is_fresh: false,
      valid_until: new Date(retrievedAt.getTime() - 60_000).toISOString(),
      fresh_until: new Date(retrievedAt.getTime() - 60_000).toISOString(),
    })));
    render(<LiveCarbonIntensity />);

    expect(await screen.findByText('Forecast')).toBeInTheDocument();
    expect(screen.getByText(/stale · \d+ min/i)).toBeInTheDocument();
    expect(screen.getByText(/not a current reading/i)).toBeInTheDocument();
  });

  it('marks a reading stale when its freshness deadline passes without a refresh', async () => {
    const retrievedAt = new Date('2026-10-29T12:00:00.000Z');
    const freshUntil = new Date(retrievedAt.getTime() + 2_000).toISOString();
    const reading = {
      ...freshActual,
      observed_at: retrievedAt.toISOString(),
      retrieved_at: retrievedAt.toISOString(),
      valid_until: new Date(retrievedAt.getTime() + 30 * 60_000).toISOString(),
      age_seconds: 0,
      fresh_until: freshUntil,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(reading));
    vi.stubGlobal('fetch', fetchMock);
    vi.useFakeTimers();
    vi.setSystemTime(retrievedAt);
    render(<LiveCarbonIntensity />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole('heading', { name: 'London' })).toBeInTheDocument();
    expect(screen.getByText(/fresh · 0 sec/i)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });

    expect(screen.getByText(/stale · 2 sec/i)).toBeInTheDocument();
    expect(screen.getByText(/passed its freshness deadline/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('explains when the live reading is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'upstream failure' }, 503)));
    render(<LiveCarbonIntensity />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/live NESO data is temporarily unavailable/i);
    expect(screen.queryByText('79')).not.toBeInTheDocument();
  });

  it('keeps the last reading but marks it stale after a refresh fails', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(freshActual))
      .mockResolvedValueOnce(jsonResponse({ detail: 'upstream failure' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    render(<LiveCarbonIntensity />);

    expect(await screen.findByRole('heading', { name: 'London' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh live carbon intensity' }));

    await waitFor(() => expect(screen.getByText(/stale · \d+ min/i)).toBeInTheDocument());
    expect(screen.getByText('79')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/showing the last successful reading/i);
  });
});
