import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type {
  AggregateDepotLoad,
  CarbonAwareSchedule,
  CarbonIntensityInterval,
  Scenario,
} from '../api/client';

import { formatValue } from '../utils/number-format';

const VEHICLE_COLOURS = ['#DCF36B', '#6FD7C9', '#8FB5FF', '#B39AF7', '#FF9A83', '#F2BD77'];
const PLOT_WIDTH = 840;

function epoch(value: string): number {
  return Date.parse(value);
}

export function CarbonProfileChart({ profile }: { profile: CarbonIntensityInterval[] }) {
  const maximum = Math.max(1, ...profile.map((row) => row.intensity_value));
  const slotWidth = PLOT_WIDTH / Math.max(profile.length, 1);
  const barWidth = Math.max(1, slotWidth * 0.68);
  const unit = profile[0]?.intensity_unit ?? 'gCO2/kWh';

  return (
    <figure className="chart-figure">
      <figcaption className="chart-heading">
        <span>Carbon intensity profile</span>
        <span className="chart-unit">Synthetic · {unit}</span>
      </figcaption>
      {profile.length === 0 ? (
        <p className="chart-empty">Add at least one interval to plot the profile.</p>
      ) : (
        <>
          <svg className="data-chart" viewBox="0 0 840 120" role="img" aria-label={`Synthetic carbon intensity profile in ${unit}`}>
            <title>Carbon intensity by scenario interval</title>
            <line x1="0" y1="94" x2="840" y2="94" className="chart-axis" />
            <line x1="0" y1="22" x2="840" y2="22" className="chart-gridline" />
            {profile.map((row, index) => {
              const height = (row.intensity_value / maximum) * 70;
              const x = index * slotWidth + (slotWidth - barWidth) / 2;
              const y = 92 - height;
              return <rect key={`${row.interval_start}-${index}`} className="carbon-interval-bar" data-interval-bar={index} x={x} y={y} width={barWidth} height={height} rx={Math.min(4, barWidth / 4)}><title>{`Interval ${index + 1}, ${row.interval_start} to ${row.interval_end}: ${formatValue(row.intensity_value)} ${unit}`}</title></rect>;
            })}
            <text x="4" y="15" className="chart-label">{formatValue(maximum)} max</text>
            <text x="4" y="113" className="chart-label">{profile[0]?.interval_start.slice(11, 16)} UTC</text>
            <text x="836" y="113" textAnchor="end" className="chart-label">{profile.at(-1)?.interval_end.slice(11, 16)} UTC</text>
          </svg>
          <details className="data-table-details">
            <summary>View interval values</summary>
            <div className="table-scroll" role="region" aria-label="Carbon intensity profile data table; horizontally scrollable" tabIndex={0}>
              <table className="interval-data-table" aria-label="Synthetic carbon intensity profile by interval">
                <thead><tr><th>Interval</th><th>Start (UTC)</th><th>End (UTC)</th><th>Intensity ({unit})</th></tr></thead>
                <tbody>{profile.map((row, index) => <tr key={`${row.interval_start}-${index}`}><td>{index + 1}</td><td>{row.interval_start}</td><td>{row.interval_end}</td><td>{formatValue(row.intensity_value)}</td></tr>)}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </figure>
  );
}

const PLOT_START_X = 150;
const MAX_TIME_TICKS = 5;
const MIN_CHART_WIDTH = 560;
const CHART_ROW_HEIGHT = 30;

function axisTimeLabel(milliseconds: number, includeDate: boolean): string {
  const timestamp = new Date(milliseconds).toISOString();
  return includeDate ? timestamp.slice(0, 16).replace('T', ' ') : timestamp.slice(11, 16);
}

function selectTimeTickIndices(intervalCount: number, viewportWidth: number, includeDate: boolean): number[] {
  if (intervalCount <= 0) return [0];
  const effectiveWidth = Math.max(MIN_CHART_WIDTH, viewportWidth);
  const minimumLabelSpacing = includeDate ? 128 : 72;
  const maximumTicks = Math.max(2, Math.min(
    MAX_TIME_TICKS,
    Math.floor((effectiveWidth - PLOT_START_X) / minimumLabelSpacing) + 1,
  ));
  const tickCount = Math.min(intervalCount + 1, maximumTicks);
  if (tickCount === intervalCount + 1) {
    return Array.from({ length: tickCount }, (_, index) => index);
  }
  return Array.from({ length: tickCount }, (_, index) => Math.round(index * intervalCount / (tickCount - 1)));
}

export function ScheduleChart({ schedule, scenario, label }: {
  schedule: CarbonAwareSchedule;
  scenario: Scenario;
  label: string;
}) {
  const [viewportWidth, setViewportWidth] = useState(MIN_CHART_WIDTH);
  const viewportRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const measuredWidth = entries[0]?.contentRect.width;
      if (measuredWidth !== undefined) setViewportWidth(measuredWidth);
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  const start = epoch(scenario.start_at);
  const end = start + scenario.interval_minutes * scenario.intervals * 60_000;
  const duration = Math.max(1, end - start);
  const vehicleIds = scenario.vehicles.map((vehicle) => vehicle.vehicle_id);
  const height = Math.max(120, 64 + vehicleIds.length * CHART_ROW_HEIGHT);
  const includeDate = scenario.interval_minutes * scenario.intervals >= 24 * 60;
  const plotWidth = PLOT_WIDTH - PLOT_START_X;
  const timeTickIndices = selectTimeTickIndices(scenario.intervals, viewportWidth, includeDate);

  return (
    <figure className="chart-figure schedule-figure">
      <figcaption className="chart-heading">
        <span>Vehicle charging windows</span>
        <span className="chart-unit">Segment allocations · kWh · UTC</span>
      </figcaption>
      {schedule.allocations.length === 0 ? (
        <p className="chart-empty">No charging allocations were produced.</p>
      ) : (
        <>
          <div ref={viewportRef} className="schedule-chart-viewport" role="region" aria-label={`${label} schedule chart; scroll to inspect all vehicles`} tabIndex={0}>
            <svg className="data-chart schedule-chart" viewBox={`0 0 ${PLOT_WIDTH} ${height}`} role="img" aria-label={`${label} segment allocations by vehicle and UTC time`}>
              <title>{label} charging allocations</title>
              {timeTickIndices.map((intervalIndex) => {
                const x = PLOT_START_X + (intervalIndex / scenario.intervals) * plotWidth;
                const timestamp = start + intervalIndex * scenario.interval_minutes * 60_000;
                const isFirst = intervalIndex === 0;
                const isLast = intervalIndex === scenario.intervals;
                return (
                  <g key={intervalIndex}>
                    <line x1={x} y1="30" x2={x} y2={height - 22} className="chart-gridline" />
                    <text
                      data-time-tick={intervalIndex}
                      x={isFirst ? x + 4 : isLast ? x - 4 : x}
                      y="20"
                      textAnchor={isFirst ? 'start' : isLast ? 'end' : 'middle'}
                      className="chart-label schedule-time-tick"
                    >
                      {axisTimeLabel(timestamp, includeDate)}
                    </text>
                  </g>
                );
              })}
              {vehicleIds.map((vehicleId, index) => {
                const rowTop = 34 + index * CHART_ROW_HEIGHT;
                const visibleId = vehicleId.length > 20 ? `${vehicleId.slice(0, 19)}…` : vehicleId;
                return (
                  <g key={vehicleId}>
                    <text x="4" y={rowTop + 18} className="chart-label schedule-vehicle-label">
                      <title>{vehicleId}</title>{visibleId}
                    </text>
                    <line x1={PLOT_START_X} y1={rowTop + 24} x2={PLOT_WIDTH} y2={rowTop + 24} className="chart-row-line" />
                  </g>
                );
              })}
              {schedule.allocations.map((allocation, index) => {
                const row = vehicleIds.indexOf(allocation.vehicle_id);
                if (row < 0) return null;
                const x = PLOT_START_X + ((epoch(allocation.segment_start) - start) / duration) * plotWidth;
                const width = Math.max(2, ((epoch(allocation.segment_end) - epoch(allocation.segment_start)) / duration) * plotWidth);
                const style = { '--allocation-colour': VEHICLE_COLOURS[row % VEHICLE_COLOURS.length] } as CSSProperties;
                const y = 40 + row * CHART_ROW_HEIGHT;
                return <rect key={`${allocation.vehicle_id}-${allocation.segment_start}-${index}`} x={x} y={y} width={Math.min(width, PLOT_WIDTH - x)} height="14" rx="4" className="allocation-bar" style={style}><title>{`${allocation.vehicle_id}: ${formatValue(allocation.energy_kwh)} kWh, ${formatValue(allocation.charging_power_kw)} kW segment average, ${allocation.segment_start} to ${allocation.segment_end}`}</title></rect>;
              })}
            </svg>
          </div>
          {vehicleIds.length <= 12 && <div className="vehicle-legend">{vehicleIds.map((vehicleId, index) => <span key={vehicleId}><i style={{ backgroundColor: VEHICLE_COLOURS[index % VEHICLE_COLOURS.length] }} />{vehicleId}</span>)}</div>}
          <details className="data-table-details">
            <summary>View allocation data</summary>
            <div className="table-scroll" role="region" aria-label={`${label} charging allocation data table; horizontally scrollable`} tabIndex={0}>
              <table className="allocation-data-table" aria-label={`${label} charging allocations by segment`}>
                <thead><tr><th>Vehicle</th><th>Segment (UTC)</th><th>Energy (kWh)</th><th>Segment average (kW)</th><th>Intensity</th></tr></thead>
                <tbody>{schedule.allocations.map((allocation, index) => <tr key={`${allocation.vehicle_id}-${index}`}><td>{allocation.vehicle_id}</td><td>{allocation.segment_start} – {allocation.segment_end}</td><td>{formatValue(allocation.energy_kwh)}</td><td>{formatValue(allocation.charging_power_kw)}</td><td>{formatValue(allocation.carbon_intensity_value)} {allocation.intensity_unit}</td></tr>)}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </figure>
  );
}

export function DepotLoadChart({ rows, depotPowerKw, label }: {
  rows: AggregateDepotLoad[];
  depotPowerKw: number;
  label: string;
}) {
  const maximum = Math.max(depotPowerKw, 1, ...rows.map((row) => Math.max(row.average_power_kw, row.peak_power_kw)));
  const yFor = (power: number) => 94 - (power / maximum) * 70;
  const slotWidth = PLOT_WIDTH / Math.max(rows.length, 1);
  const barWidth = Math.max(1, slotWidth * 0.58);
  const capY = yFor(depotPowerKw);

  return (
    <figure className="chart-figure">
      <figcaption className="chart-heading"><span>Depot load</span><span className="chart-unit">Average and peak power · kW</span></figcaption>
      {rows.length === 0 ? <p className="chart-empty">No load values are available.</p> : <>
        <svg className="data-chart" viewBox="0 0 840 120" role="img" aria-label={`${label} interval average and peak depot power in kilowatts against ${formatValue(depotPowerKw)} kilowatt capacity`}>
          <title>{label} aggregate depot load; average and peak are distinct</title>
          <line x1="0" y1={capY} x2="840" y2={capY} className="capacity-line" />
          <line x1="0" y1="94" x2="840" y2="94" className="chart-axis" />
          {rows.map((row, index) => {
            const x = index * slotWidth + (slotWidth - barWidth) / 2;
            const averageY = yFor(row.average_power_kw);
            const peakY = yFor(row.peak_power_kw);
            return (
              <g key={row.interval_index}>
                <rect className="load-average-bar" data-load-interval={row.interval_index} x={x} y={averageY} width={barWidth} height={Math.max(0, 94 - averageY)} rx={Math.min(4, barWidth / 4)}>
                  <title>{`Interval ${row.interval_index + 1}: average ${formatValue(row.average_power_kw)} kW; delivered ${formatValue(row.delivered_energy_kwh)} kWh`}</title>
                </rect>
                <line className="load-peak-marker" x1={x} y1={peakY} x2={x + barWidth} y2={peakY}>
                  <title>{`Interval ${row.interval_index + 1}: segment peak ${formatValue(row.peak_power_kw)} kW`}</title>
                </line>
              </g>
            );
          })}
          <text x="4" y={Math.max(12, capY - 4)} className="capacity-label">Capacity {formatValue(depotPowerKw)} kW</text>
          <text x="4" y="113" className="chart-label">Interval 1</text>
          <text x="836" y="113" textAnchor="end" className="chart-label">Interval {rows.length}</text>
        </svg>
        <div className="chart-key"><span><i className="key-average" />Interval average</span><span><i className="key-peak" />Segment peak</span><span><i className="key-capacity" />Depot limit</span></div>
        <details className="data-table-details">
          <summary>View load data</summary>
          <div className="table-scroll" role="region" aria-label={`${label} depot load data table; horizontally scrollable`} tabIndex={0}>
            <table className="load-data-table" aria-label={`${label} depot load by interval`}>
              <thead><tr><th>Interval</th><th>Start (UTC)</th><th>End (UTC)</th><th>Energy (kWh)</th><th>Average power (kW)</th><th>Peak power (kW)</th><th>Depot limit (kW)</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.interval_index}><td>{row.interval_index + 1}</td><td>{row.interval_start}</td><td>{row.interval_end}</td><td>{formatValue(row.delivered_energy_kwh)}</td><td>{formatValue(row.average_power_kw)}</td><td>{formatValue(row.peak_power_kw)}</td><td>{formatValue(depotPowerKw)}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
      </>}
    </figure>
  );
}
