'use client';

import { useEffect, useState, useRef } from 'react';

function useCountUp(target, duration = 350) {
  const [val, setVal] = useState(target);
  const fromRef = useRef(target);
  const startRef = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    fromRef.current = val;
    startRef.current = performance.now();
    const from = val;
    const to = target;
    if (Math.abs(from - to) < 0.001) { setVal(to); return; }
    function tick(now) {
      const t = Math.min(1, (now - startRef.current) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setVal(from + (to - from) * eased);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target]);

  return val;
}

function Stat({ label, value, unit, format }) {
  const v = useCountUp(typeof value === 'number' ? value : 0);
  const display = format ? format(v) : Math.round(v);
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {display}
        {unit && <span className="unit">{unit}</span>}
      </div>
    </div>
  );
}

const FOCUSED = [
  { key: 'total_km',       label: 'Distance',     unit: 'km',  format: (v) => v.toFixed(1) },
  { key: 'total_minutes',  label: 'Time',         unit: 'min', format: (v) => Math.round(v) },
  { key: 'total_cost_eur', label: 'Cost',         unit: '€',   format: (v) => v.toFixed(0) },
  { key: 'co2_kg',         label: 'CO₂',          unit: 'kg',  format: (v) => v.toFixed(1) },
  { key: 'search_moves',   label: 'Search moves', unit: '',    format: (v) => Math.round(v) },
  { key: 'fill_rate',      label: 'Fill rate',    unit: '%',   format: (v) => (v * 100).toFixed(0) },
];

const GROUPS = [
  {
    title: 'Cost',
    items: [
      { key: 'total_cost_eur', label: 'Total cost', unit: '€', format: (v) => v.toFixed(2) },
      { key: 'fuel_eur',       label: 'Fuel',       unit: '€', format: (v) => v.toFixed(2) },
      { key: 'labor_eur',      label: 'Labor',      unit: '€', format: (v) => v.toFixed(2) },
      { key: 'wear_eur',       label: 'Wear',       unit: '€', format: (v) => v.toFixed(2) },
      { key: 'fuel_liters',    label: 'Fuel',       unit: 'L', format: (v) => v.toFixed(2) },
    ],
  },
  {
    title: 'Time',
    items: [
      { key: 'total_minutes',    label: 'Total',    unit: 'min', format: (v) => v.toFixed(1) },
      { key: 'drive_minutes',    label: 'Drive',    unit: 'min', format: (v) => v.toFixed(1) },
      { key: 'service_minutes',  label: 'Service',  unit: 'min', format: (v) => v.toFixed(1) },
      { key: 'overhead_minutes', label: 'Overhead', unit: 'min', format: (v) => v.toFixed(1) },
      { key: 'tw_violations_min',label: 'TW viol.', unit: 'min', format: (v) => v.toFixed(1) },
    ],
  },
  {
    title: 'Efficiency',
    items: [
      { key: 'total_km',                label: 'Distance',    unit: 'km', format: (v) => v.toFixed(1) },
      { key: 'fill_rate',               label: 'Fill rate',   unit: '%',  format: (v) => (v * 100).toFixed(1) },
      { key: 'pallet_volume_util',      label: 'Vol. util',   unit: '%',  format: (v) => (v * 100).toFixed(1) },
      { key: 'weight_util',             label: 'Wt. util',    unit: '%',  format: (v) => (v * 100).toFixed(1) },
      { key: 'pallets_loaded',          label: 'Pallets',     unit: '',   format: (v) => Math.round(v) },
      { key: 'search_moves',            label: 'Search moves',unit: '',   format: (v) => Math.round(v) },
      { key: 'delivered_units',         label: 'Delivered',   unit: 'u',  format: (v) => v.toFixed(0) },
      { key: 'ordered_units',           label: 'Ordered',     unit: 'u',  format: (v) => v.toFixed(0) },
      { key: 'returnables_picked_units',label: 'Returns',     unit: 'u',  format: (v) => v.toFixed(0) },
    ],
  },
  {
    title: 'Service',
    items: [
      { key: 'n_clients_planned',  label: 'Planned',    unit: '',  format: (v) => Math.round(v) },
      { key: 'n_clients_visited',  label: 'Visited',    unit: '',  format: (v) => Math.round(v) },
      { key: 'closed_visits',      label: 'Closed',     unit: '',  format: (v) => Math.round(v) },
      { key: 'capacity_violations',label: 'Cap. viol.', unit: '',  format: (v) => Math.round(v) },
      { key: 'drops',              label: 'Drops',      unit: '',  format: (v) => Math.round(v) },
    ],
  },
  {
    title: 'Environment',
    items: [
      { key: 'co2_kg', label: 'CO₂', unit: 'kg', format: (v) => v.toFixed(2) },
    ],
  },
];

export default function MetricsBar({ kpis, fullscreen }) {
  const all = kpis?.all || {};
  const empty = !kpis;

  if (fullscreen) {
    return (
      <div className="metrics metrics-full">
        <div className="metrics-full-head">
          {empty ? 'Awaiting simulation…' : 'All KPIs'}
        </div>
        <div className="metrics-full-grid">
          {GROUPS.map((g) => (
            <div key={g.title} className="metrics-full-group">
              <div className="metrics-full-group-title">{g.title}</div>
              <div className="metrics-full-stats">
                {g.items.map((it) => (
                  <Stat
                    key={it.key}
                    label={it.label}
                    value={all[it.key] ?? 0}
                    unit={it.unit}
                    format={it.format}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="metrics">
      <div className="stats" style={{ width: '100%' }}>
        <div className="stats-head">{empty ? 'Awaiting simulation…' : 'KPIs'}</div>
        <div className="stats-grid">
          {FOCUSED.map((it) => (
            <Stat
              key={it.key}
              label={it.label}
              value={all[it.key] ?? 0}
              unit={it.unit}
              format={it.format}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
