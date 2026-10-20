import type { CSSProperties } from 'react';
import type {
  AggregateDepotLoad,
  CarbonAwareSchedule,
  CarbonIntensityInterval,
  Scenario,
} from '../api/client';

const VEHICLE_COLOURS = ['#76d9bb', '#f2c673', '#87aaf2', '#dc91bd', '#e88f75', '#b4a0eb'];
const PLOT_WIDTH = 840;

function formatNumber(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits }).format(value);
}

function epoch(value: string): number {
  return Date.parse(value);
}

export function CarbonProfileChart({ profile }: { profile: CarbonIntensityInterval[] }) {
  const maximum = Math.max(1, ...profile.map((row) => row.intensity_value));
  const points = profile.map((row, index) => {
    const x = ((index + 0.5) / profile.length) * PLOT_WIDTH;
    const y = 92 - (row.intensity_value / maximum) * 70;
    return `${x},${y}`;
  });
  const path = points.length > 0 ? `M ${points.join(' L ')}` : '';
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
            <path d={path} className="intensity-line" />
            {profile.map((row, index) => {
              const x = ((index + 0.5) / profile.length) * PLOT_WIDTH;
              const y = 92 - (row.intensity_value / maximum) * 70;
              return <circle key={`${row.interval_start}-${index}`} cx={x} cy={y} r="3.5" className="intensity-dot"><title>{`Interval ${index + 1}: ${formatNumber(row.intensity_value)} ${unit}`}</title></circle>;
            })}
            <text x="4" y="15" className="chart-label">{formatNumber(maximum)} max</text>
            <text x="4" y="113" className="chart-label">{profile[0]?.interval_start.slice(11, 16)} UTC</text>
            <text x="836" y="113" textAnchor="end" className="chart-label">{profile.at(-1)?.interval_end.slice(11, 16)} UTC</text>
          </svg>
          <details className="data-table-details">
            <summary>View interval values</summary>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Interval</th><th>Start (UTC)</th><th>Intensity ({unit})</th></tr></thead>
                <tbody>{profile.map((row, index) => <tr key={`${row.interval_start}-${index}`}><td>{index + 1}</td><td>{row.interval_start}</td><td>{formatNumber(row.intensity_value)}</td></tr>)}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </figure>
  );
}

export function ScheduleChart({ schedule, scenario, label }: {
  schedule: CarbonAwareSchedule;
  scenario: Scenario;
  label: string;
}) {
  const start = epoch(scenario.start_at);
  const end = start + scenario.interval_minutes * scenario.intervals * 60_000;
  const duration = Math.max(1, end - start);
  const vehicleIds = scenario.vehicles.map((vehicle) => vehicle.vehicle_id);
  const rowHeight = 38;
  const height = Math.max(112, 60 + vehicleIds.length * rowHeight);

  return (
    <figure className="chart-figure schedule-figure">
      <figcaption className="chart-heading">
        <span>Vehicle charging windows</span>
        <span className="chart-unit">Segment allocations · kWh</span>
      </figcaption>
      {schedule.allocations.length === 0 ? (
        <p className="chart-empty">No charging allocations were produced.</p>
      ) : (
        <>
          <svg className="data-chart schedule-chart" viewBox={`0 0 ${PLOT_WIDTH} ${height}`} role="img" aria-label={`${label} segment allocations by vehicle and UTC time`}>
            <title>{label} charging allocations</title>
            {scenario.intervals > 0 && Array.from({ length: scenario.intervals + 1 }, (_, index) => {
              const x = 110 + (index / scenario.intervals) * (PLOT_WIDTH - 110);
              return <g key={index}><line x1={x} y1="24" x2={x} y2={height - 22} className="chart-gridline" />{index < scenario.intervals && <text x={x + 4} y="15" className="chart-label">{new Date(start + index * scenario.interval_minutes * 60_000).toISOString().slice(11, 16)}</text>}</g>;
            })}
            {vehicleIds.map((vehicleId, index) => {
              const y = 32 + index * rowHeight;
              return <g key={vehicleId}><text x="3" y={y + 15} className="chart-label">{vehicleId}</text><line x1="110" y1={y + 21} x2={PLOT_WIDTH} y2={y + 21} className="chart-row-line" /></g>;
            })}
            {schedule.allocations.map((allocation, index) => {
              const row = vehicleIds.indexOf(allocation.vehicle_id);
              if (row < 0) return null;
              const x = 110 + ((epoch(allocation.segment_start) - start) / duration) * (PLOT_WIDTH - 110);
              const width = Math.max(2, ((epoch(allocation.segment_end) - epoch(allocation.segment_start)) / duration) * (PLOT_WIDTH - 110));
              const style = { '--allocation-colour': VEHICLE_COLOURS[row % VEHICLE_COLOURS.length] } as CSSProperties;
              return <rect key={`${allocation.vehicle_id}-${allocation.segment_start}-${index}`} x={x} y={34 + row * rowHeight} width={Math.min(width, PLOT_WIDTH - x)} height="14" rx="4" className="allocation-bar" style={style}><title>{`${allocation.vehicle_id}: ${formatNumber(allocation.energy_kwh, 4)} kWh, ${formatNumber(allocation.charging_power_kw)} kW segment average, ${allocation.segment_start} to ${allocation.segment_end}`}</title></rect>;
            })}
            <line x1="110" y1={height - 21} x2={PLOT_WIDTH} y2={height - 21} className="chart-axis" />
            <text x="110" y={height - 5} className="chart-label">{scenario.start_at.slice(11, 16)} UTC</text>
            <text x={PLOT_WIDTH} y={height - 5} textAnchor="end" className="chart-label">{new Date(end).toISOString().slice(11, 16)} UTC</text>
          </svg>
          <div className="vehicle-legend">{vehicleIds.map((vehicleId, index) => <span key={vehicleId}><i style={{ backgroundColor: VEHICLE_COLOURS[index % VEHICLE_COLOURS.length] }} />{vehicleId}</span>)}</div>
          <details className="data-table-details">
            <summary>View allocation data</summary>
            <div className="table-scroll">
              <table>
                <thead><tr><th>Vehicle</th><th>Segment (UTC)</th><th>Energy (kWh)</th><th>Segment average (kW)</th><th>Intensity</th></tr></thead>
                <tbody>{schedule.allocations.map((allocation, index) => <tr key={`${allocation.vehicle_id}-${index}`}><td>{allocation.vehicle_id}</td><td>{allocation.segment_start} – {allocation.segment_end}</td><td>{formatNumber(allocation.energy_kwh, 4)}</td><td>{formatNumber(allocation.charging_power_kw, 4)}</td><td>{formatNumber(allocation.carbon_intensity_value)} {allocation.intensity_unit}</td></tr>)}</tbody>
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
  const maximum = Math.max(depotPowerKw, 1, ...rows.map((row) => row.peak_power_kw));
  const yFor = (power: number) => 94 - (power / maximum) * 70;
  const series = (key: 'average_power_kw' | 'peak_power_kw') => rows.map((row, index) => {
    const x = ((index + 0.5) / Math.max(rows.length, 1)) * PLOT_WIDTH;
    return `${x},${yFor(row[key])}`;
  });
  const averagePath = series('average_power_kw');
  const peakPath = series('peak_power_kw');
  const capY = yFor(depotPowerKw);

  return (
    <figure className="chart-figure">
      <figcaption className="chart-heading"><span>Depot load</span><span className="chart-unit">Average and peak power · kW</span></figcaption>
      {rows.length === 0 ? <p className="chart-empty">No load values are available.</p> : <>
        <svg className="data-chart" viewBox="0 0 840 120" role="img" aria-label={`${label} interval average and peak depot power in kilowatts against ${formatNumber(depotPowerKw)} kilowatt capacity`}>
          <title>{label} aggregate depot load; average and peak are distinct</title>
          <line x1="0" y1={capY} x2="840" y2={capY} className="capacity-line" />
          <line x1="0" y1="94" x2="840" y2="94" className="chart-axis" />
          <polyline points={peakPath.join(' ')} className="load-peak-line" />
          <polyline points={averagePath.join(' ')} className="load-average-line" />
          <text x="4" y={Math.max(12, capY - 4)} className="capacity-label">Capacity {formatNumber(depotPowerKw)} kW</text>
          <text x="4" y="113" className="chart-label">Interval 1</text>
          <text x="836" y="113" textAnchor="end" className="chart-label">Interval {rows.length}</text>
        </svg>
        <div className="chart-key"><span><i className="key-average" />Interval average</span><span><i className="key-peak" />Segment peak</span><span><i className="key-capacity" />Depot limit</span></div>
        <details className="data-table-details">
          <summary>View load data</summary>
          <div className="table-scroll">
            <table>
              <thead><tr><th>Interval</th><th>Energy (kWh)</th><th>Average power (kW)</th><th>Peak power (kW)</th><th>Depot limit (kW)</th></tr></thead>
              <tbody>{rows.map((row) => <tr key={row.interval_index}><td>{row.interval_index + 1}</td><td>{formatNumber(row.delivered_energy_kwh, 4)}</td><td>{formatNumber(row.average_power_kw, 4)}</td><td>{formatNumber(row.peak_power_kw, 4)}</td><td>{formatNumber(depotPowerKw)}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
      </>}
    </figure>
  );
}
