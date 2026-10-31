import { useCallback, useEffect, useRef, useState } from 'react';
import { ForecastApiError, getCarbonIntensityForecast } from '../api/client';
import type { ForecastTimeline } from '../api/client';
import { formatValue } from '../utils/number-format';

interface VerifiedRegion {
  id: number;
  name: string;
}

function formatUtc(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
    timeZoneName: 'short',
  }).format(new Date(value));
}

export function ForecastTimelinePanel({
  region,
}: {
  region: VerifiedRegion | null;
}) {
  const [timeline, setTimeline] = useState<ForecastTimeline | null>(null);
  const [status, setStatus] = useState<'empty' | 'loading' | 'available' | 'error'>('empty');
  const [requestError, setRequestError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const activeRequest = useRef<AbortController | null>(null);
  const lastRegionId = useRef<number | null>(region?.id ?? null);
  const currentnessUnverified = timeline !== null && requestError !== null;

  const loadTimeline = useCallback(async () => {
    if (!region) return;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    const requestId = ++requestSequence.current;
    setStatus('loading');
    setRequestError(null);
    try {
      const result = await getCarbonIntensityForecast(region.id, controller.signal);
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      if (result.region_id !== region.id) throw new ForecastApiError('The forecast region did not match the validated application region.', 200);
      setTimeline(result);
      setStatus('available');
    } catch (error) {
      if (controller.signal.aborted || requestId !== requestSequence.current) return;
      const message = error instanceof ForecastApiError
        ? error.message
        : 'The regional forecast preview could not be loaded.';
      setRequestError(message);
      setStatus('error');
    }
  }, [region]);

  useEffect(() => {
    if (lastRegionId.current === (region?.id ?? null)) return;
    activeRequest.current?.abort();
    requestSequence.current += 1;
    lastRegionId.current = region?.id ?? null;
    setTimeline(null);
    setStatus('empty');
    setRequestError(null);
  }, [region?.id]);

  useEffect(() => () => {
    activeRequest.current?.abort();
    requestSequence.current += 1;
  }, []);

  const intensityMaximum = timeline
    ? Math.max(1, ...timeline.intervals.map((interval) => interval.intensity_gco2_per_kwh))
    : 1;
  const slotWidth = timeline ? 840 / timeline.intervals.length : 0;
  const barWidth = Math.max(2, slotWidth * 0.68);

  return (
    <section id="forecast-timeline" className="panel forecast-timeline" aria-labelledby="forecast-timeline-heading" aria-busy={status === 'loading'}>
      <header className="forecast-panel-heading">
        <div>
          <p className="eyebrow">READ-ONLY PREVIEW / PUBLIC NESO API</p>
          <h2 id="forecast-timeline-heading">Grid carbon forecast</h2>
          <p>{region ? `Server-bound region: ${region.name} · region ${region.id}.` : 'Waiting for a verified region from the live application API.'}</p>
        </div>
        <div className="forecast-panel-actions">
          <span className={`forecast-provenance-pill ${currentnessUnverified ? 'forecast-unverified' : timeline ? 'forecast-preview' : 'forecast-pending'}`}>
            {currentnessUnverified ? 'CURRENTNESS UNVERIFIED' : timeline ? 'FORECAST SNAPSHOT' : 'NO PREVIEW'}
          </span>
          <button className="button button-secondary" type="button" onClick={() => void loadTimeline()} disabled={!region || status === 'loading'}>
            {status === 'loading' ? 'Loading…' : timeline ? 'Refresh forecast preview' : 'Load regional forecast preview'}
          </button>
        </div>
      </header>

      {!region && <p className="forecast-empty-state" role="status">The browser does not choose a NESO region. A validated server response must first provide the configured region identifier.</p>}
      {requestError && <p className={timeline ? 'forecast-warning' : 'forecast-error'} role={timeline ? 'status' : 'alert'}>{requestError}{timeline ? ' The previous preview is retained; its currentness is not verified. Each scheduling evaluation retrieves and validates its own server-side forecast.' : ''}</p>}
      {!timeline && !requestError && status === 'empty' && <p className="forecast-empty-state">Load a read-only preview on operator action. The forecast-backed evaluation independently retrieves its own server-side evidence.</p>}

      {timeline && (
        <>

          <div className="forecast-timeline-summary">
            <div><span>Region</span><strong>{timeline.region_name} · {timeline.region_id}</strong></div>
            <div><span>Provenance</span><strong>FORECAST · {timeline.units}</strong></div>
            <div><span>Intervals</span><strong>{timeline.intervals.length} × 30 minutes</strong></div>
            <div><span>Valid through</span><strong>{formatUtc(timeline.valid_until)}</strong></div>
          </div>
          <figure className="forecast-chart-figure">
            <figcaption><span>Regional carbon-intensity forecast</span><span>{timeline.units} · half-hour UTC intervals</span></figcaption>
            <div className="forecast-chart-viewport" role="region" aria-label="Regional forecast chart; horizontally scrollable" tabIndex={0}>
              <svg className="forecast-timeline-chart" viewBox="0 0 840 150" role="img" aria-label={`Forecast carbon intensity for ${timeline.region_name}, region ${timeline.region_id}, in ${timeline.units}`}>
                <title>{timeline.region_name} regional forecast timeline</title>
                <line x1="0" y1="120" x2="840" y2="120" className="chart-axis" />
                {timeline.intervals.map((interval, index) => {
                  const height = interval.intensity_gco2_per_kwh / intensityMaximum * 92;
                  const x = index * slotWidth + (slotWidth - barWidth) / 2;
                  const y = 118 - height;
                  return (
                    <rect
                      key={interval.observed_at}
                      className="forecast-interval-bar"
                      data-forecast-interval={index}
                      x={x}
                      y={y}
                      width={barWidth}
                      height={height}
                      rx="2"
                    >
                      <title>{`${formatUtc(interval.observed_at)}: ${formatValue(interval.intensity_gco2_per_kwh)} ${timeline.units} forecast`}</title>
                    </rect>
                  );
                })}
                {timeline.intervals.filter((_, index) => index % 8 === 0).map((interval) => {
                  const index = timeline.intervals.indexOf(interval);
                  const x = index * slotWidth + 3;
                  return <text key={interval.observed_at} x={x} y="142" className="forecast-chart-label">{interval.observed_at.slice(11, 16)} UTC</text>;
                })}
              </svg>
            </div>
            <div className="forecast-chart-key"><span><i aria-hidden="true" /> Validated forecast intensity</span><span>Discrete upstream intervals; no interpolation</span></div>
          </figure>
          <details className="data-table-details">
            <summary>View forecast interval values</summary>
            <div className="table-scroll forecast-table-scroll" role="region" aria-label="Regional carbon-intensity forecast table; horizontally scrollable" tabIndex={0}>
              <table className="forecast-interval-table" aria-label="Regional carbon-intensity forecast by UTC interval">
                <thead><tr><th>Interval</th><th>Start (UTC)</th><th>End (UTC)</th><th>Forecast (gCO2/kWh)</th><th>Index</th><th>Provenance</th></tr></thead>
                <tbody>{timeline.intervals.map((interval, index) => (
                  <tr key={interval.observed_at}>
                    <td>{index + 1}</td><td>{formatUtc(interval.observed_at)}</td><td>{formatUtc(interval.valid_until)}</td>
                    <td>{formatValue(interval.intensity_gco2_per_kwh)}</td><td>{interval.intensity_index}</td><td>FORECAST</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </details>
          <div className="forecast-provenance-details">
            <div><span>Forecast retrieval time</span><strong>{formatUtc(timeline.retrieved_at)}</strong></div>
            <div><span>Forecast valid from</span><strong>{formatUtc(timeline.requested_from)}</strong></div>
            <div><span>Forecast validity horizon</span><strong>{formatUtc(timeline.requested_from)} – {formatUtc(timeline.valid_until)}</strong></div>
            <div><span>Source</span><a href={timeline.source_url} target="_blank" rel="noreferrer">NESO Carbon Intensity API regional forecast</a></div>
          </div>
          <p className="forecast-provenance-note">Retrieval time is the timestamp supplied by the application API. The interface does not claim an original NESO issue time. This read-only preview is a separate request; each evaluation obtains and validates its own server-side forecast.</p>
        </>
      )}
    </section>
  );
}
