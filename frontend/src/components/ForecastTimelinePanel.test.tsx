import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ForecastTimelinePanel } from './ForecastTimelinePanel';
import type { ForecastTimeline } from '../api/client';
import { jsonResponse } from '../test/fixtures';

const requestedFrom = '2026-10-30T12:00:00.000Z';
const region = { id: 13, name: 'London' };

function forecastTimeline(retrievedAt = '2026-10-30T11:30:00.000Z'): ForecastTimeline {
  const start = Date.parse(requestedFrom);
  const validUntil = new Date(start + 48 * 30 * 60_000).toISOString();
  return {
    status: 'available',
    region_id: region.id,
    region_name: region.name,
    measurement_type: 'forecast',
    units: 'gCO2/kWh',
    source_api: 'neso-carbon-intensity-api',
    source_url: 'https://api.carbonintensity.org.uk/regional/intensity/2026-10-30T12:00Z/fw24h/regionid/13',
    requested_from: requestedFrom,
    retrieved_at: retrievedAt,
    valid_until: validUntil,
    intervals: Array.from({ length: 48 }, (_, index) => {
      const observedAt = new Date(start + index * 30 * 60_000).toISOString();
      return {
        observed_at: observedAt,
        valid_until: new Date(start + (index + 1) * 30 * 60_000).toISOString(),
        intensity_gco2_per_kwh: 100 + index,
        intensity_index: 'moderate',
        measurement_type: 'forecast',
      };
    }),
  };
}

function forecastTimelineResponse(
  timeline: ForecastTimeline,
  serverTime = timeline.retrieved_at,
): Response {
  const response = jsonResponse(timeline);
  response.headers.set('Date', new Date(serverTime).toUTCString());
  return response;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('ForecastTimelinePanel', () => {
  it('waits for a server-verified region and makes no preview request without one', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<ForecastTimelinePanel region={null} />);

    expect(screen.getByRole('status')).toHaveTextContent('A validated server response must first provide the configured region identifier.');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Load regional forecast preview' })).toBeDisabled();
  });

  it('renders discrete half-hour values and provenance from the validated forecast response', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(forecastTimelineResponse(forecastTimeline()));
    vi.stubGlobal('fetch', fetchMock);
    render(<ForecastTimelinePanel region={region} />);

    await user.click(screen.getByRole('button', { name: 'Load regional forecast preview' }));

    expect(await screen.findByText('FORECAST · gCO2/kWh')).toBeInTheDocument();
    expect(screen.getByText('48 × 30 minutes')).toBeInTheDocument();
    expect(screen.getByText('Forecast retrieval time')).toBeInTheDocument();
    expect(screen.getByText(/does not claim an original NESO issue time/i)).toBeInTheDocument();
    expect(document.querySelectorAll('[data-forecast-interval]')).toHaveLength(48);
    await user.click(screen.getByText('View forecast interval values'));
    expect(screen.getByRole('table', { name: 'Regional carbon-intensity forecast by UTC interval' })).toBeInTheDocument();
    expect(screen.getAllByRole('cell', { name: '100' })).toHaveLength(1);
    expect(screen.getAllByRole('cell', { name: '147' })).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/energy/carbon-intensity/forecast?region_id=13',
      expect.objectContaining({ method: 'GET', cache: 'no-store' }),
    );
  });

  it('does not infer forecast staleness from browser time or an unconfigured age limit', async () => {
    const user = userEvent.setup();
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2030-01-01T00:00:00.000Z'));
    const timeline = forecastTimeline('2026-10-30T10:59:00.000Z');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      forecastTimelineResponse(timeline, '2026-10-30T12:00:00.000Z'),
    ));
    render(<ForecastTimelinePanel region={region} />);

    await user.click(screen.getByRole('button', { name: 'Load regional forecast preview' }));

    expect(await screen.findByText('FORECAST SNAPSHOT', { selector: '.forecast-provenance-pill' })).toBeInTheDocument();
    expect(screen.queryByText('STALE PREVIEW')).not.toBeInTheDocument();
    expect(screen.getByText('Forecast retrieval time')).toBeInTheDocument();
  });

  it('marks a retained preview currentness unverified after refresh fails', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(forecastTimelineResponse(forecastTimeline()))
      .mockRejectedValueOnce(new TypeError('network unavailable'));
    vi.stubGlobal('fetch', fetchMock);
    render(<ForecastTimelinePanel region={region} />);

    await user.click(screen.getByRole('button', { name: 'Load regional forecast preview' }));
    expect(await screen.findByText('FORECAST SNAPSHOT', { selector: '.forecast-provenance-pill' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh forecast preview' }));

    expect(await screen.findByText('CURRENTNESS UNVERIFIED')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('The previous preview is retained; its currentness is not verified.');
  });

  it('shows an unavailable response without inventing timeline values', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ detail: 'unavailable' }, 503)));
    render(<ForecastTimelinePanel region={region} />);

    await user.click(screen.getByRole('button', { name: 'Load regional forecast preview' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The forecast preview is unavailable from the server.');
    expect(document.querySelectorAll('[data-forecast-interval]')).toHaveLength(0);
    expect(screen.queryByRole('table', { name: 'Regional carbon-intensity forecast by UTC interval' })).not.toBeInTheDocument();
  });
});
