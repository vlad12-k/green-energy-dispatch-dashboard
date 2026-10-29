import { useCallback, useEffect, useRef, useState } from 'react';
import { getCarbonIntensity } from '../api/client';
import type { CarbonIntensityResponse } from '../api/client';

const REFRESH_INTERVAL_MS = 60_000;
const AGE_UPDATE_INTERVAL_MS = 15_000;

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(new Date(value)) + ' UTC';
}

function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)} sec`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} hr ${Math.floor((seconds % 3600) / 60)} min`;
}

export function LiveCarbonIntensity() {
  const [reading, setReading] = useState<CarbonIntensityResponse | null>(null);
  const [requestError, setRequestError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState<number>(() => Date.now());
  const requestSequence = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const freshUntil = reading?.fresh_until;

  const refresh = useCallback(async () => {
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const requestId = ++requestSequence.current;
    setLoading(true);

    try {
      const result = await getCarbonIntensity(controller.signal);
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      setReading(result);
      setRequestError(false);
      setNow(Date.now());
    } catch {
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      setRequestError(true);
    } finally {
      if (requestId === requestSequence.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refresh(), 0);
    const refreshTimer = window.setInterval(() => void refresh(), REFRESH_INTERVAL_MS);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(refreshTimer);
      activeRequest.current?.abort();
      requestSequence.current += 1;
    };
  }, [refresh]);

  useEffect(() => {
    const ageTimer = window.setInterval(() => setNow(Date.now()), AGE_UPDATE_INTERVAL_MS);
    const freshnessDelay = freshUntil === undefined
      ? null
      : Math.max(0, Date.parse(freshUntil) - Date.now());
    const freshnessTimer = freshnessDelay === null
      ? null
      : window.setTimeout(() => setNow(Date.now()), freshnessDelay);

    return () => {
      window.clearInterval(ageTimer);
      if (freshnessTimer !== null) window.clearTimeout(freshnessTimer);
    };
  }, [freshUntil]);

  const hasExpired = reading !== null && now >= Date.parse(reading.fresh_until);
  const freshness = reading === null
    ? 'unavailable'
    : requestError || !reading.is_fresh || hasExpired
      ? 'stale'
      : 'fresh';
  const ageSeconds = reading === null
    ? 0
    : Math.max(0, reading.age_seconds + (now - Date.parse(reading.retrieved_at)) / 1000);

  return (
    <section
      id="live-carbon-intensity"
      className="live-carbon-intensity panel"
      aria-labelledby="live-carbon-title"
      aria-busy={loading}
    >
      <header className="live-carbon-header">
        <div>
          <p className="eyebrow">LIVE DISPLAY / PUBLIC NESO FEED</p>
          <h2 id="live-carbon-title">Live carbon intensity</h2>
          <p className="live-carbon-intro">A read-only view of the latest regional grid signal. It does not change schedule inputs.</p>
        </div>
        <div className="live-carbon-actions">
          <span className={`live-carbon-state live-carbon-state-${freshness}`}>
            <i aria-hidden="true" />
            {loading && reading === null ? 'LOADING' : freshness.toUpperCase()}
          </span>
          <button
            className="button button-secondary live-carbon-refresh"
            type="button"
            aria-label="Refresh live carbon intensity"
            onClick={() => void refresh()}
            disabled={loading}
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </header>

      {reading === null && loading ? (
        <p className="live-carbon-loading" role="status">Retrieving the latest NESO regional reading…</p>
      ) : reading === null ? (
        <p className="live-carbon-warning" role="alert">
          Live NESO data is temporarily unavailable. No value is being inferred or substituted.
        </p>
      ) : (
        <>
          <div className="live-carbon-summary">
            <div className="live-carbon-region">
              <span className="live-carbon-region-label">REGION {reading.region_id}</span>
              <h3>{reading.region_name}</h3>
              <span className="live-carbon-index">{reading.intensity_index} intensity</span>
            </div>
            <div className="live-carbon-value" aria-label={`${reading.intensity_gco2_per_kwh} ${reading.units}`}>
              <strong>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(reading.intensity_gco2_per_kwh)}</strong>
              <span>{reading.units}</span>
            </div>
          </div>

          <div className="live-carbon-details">
            <div><span>Provenance</span><strong>{reading.measurement_type === 'actual' ? 'Estimated actual' : 'Forecast'}</strong></div>
            <div><span>Freshness</span><strong>{freshness === 'fresh' ? 'Fresh' : 'Stale'} · {formatAge(ageSeconds)}</strong></div>
            <div><span>Observed interval</span><strong>{formatTimestamp(reading.observed_at)} – {formatTimestamp(reading.valid_until)}</strong></div>
            <div><span>Retrieved</span><strong>{formatTimestamp(reading.retrieved_at)}</strong></div>
          </div>

          {requestError ? (
            <p className="live-carbon-warning" role="status">
              Refresh failed; showing the last successful reading. Treat it as stale.
            </p>
          ) : freshness === 'stale' ? (
            <p className="live-carbon-warning" role="status">
              {reading.measurement_type === 'forecast' && reading.interval_status === 'completed'
                ? 'This forecast covers a completed interval and is not a current reading.'
                : 'This reading has passed its freshness deadline and is no longer current.'}
            </p>
          ) : null}

          <footer className="live-carbon-footer">
            <span>Source: NESO Carbon Intensity API · units as reported upstream</span>
            <a href="https://carbon-intensity.github.io/api-definitions/" target="_blank" rel="noreferrer">
              API specification <span aria-hidden="true">↗</span>
            </a>
          </footer>
        </>
      )}
    </section>
  );
}
