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

function buildComparison(routeDetail, all, simStops) {
  if (!routeDetail) return null;
  const orders = routeDetail.orders || [];
  const orderedVolM3 = routeDetail.total_volume_m3 ?? orders.reduce((s, o) => s + (o.total_volume_m3 || 0), 0);
  const orderedWeightKg = orders.reduce((s, o) => s + (o.total_weight_kg || 0), 0);
  const expectedReturns = orders.reduce((s, o) => s + (o.expected_returnable_units || 0), 0);
  const orgClients = routeDetail.n_clients ?? orders.length;

  // Visit-order alignment: how many of organizer's actual visit_seq match our planned seq.
  const orgSeqByClient = new Map(
    orders
      .filter(o => o.visit_seq != null)
      .map(o => [String(o.client_id), o.visit_seq])
  );
  const ourSeqByClient = new Map(
    (simStops || []).map(s => [String(s.client_id), s.visit_seq])
  );
  let matchedSeq = 0;
  let comparedSeq = 0;
  for (const [cid, orgSeq] of orgSeqByClient) {
    const ours = ourSeqByClient.get(cid);
    if (ours == null) continue;
    comparedSeq += 1;
    if (ours === orgSeq) matchedSeq += 1;
  }
  const seqMatchPct = comparedSeq > 0 ? (matchedSeq / comparedSeq) : null;

  return {
    rows: [
      { label: 'Clients',           org: orgClients,           sim: all.n_clients_visited ?? 0,         unit: '',   fmt: (v) => Math.round(v) },
      { label: 'Units (ordered → delivered)', org: all.ordered_units ?? 0, sim: all.delivered_units ?? 0, unit: 'u', fmt: (v) => Math.round(v) },
      { label: 'Volume planned',    org: orderedVolM3,         sim: null,                                unit: 'm³', fmt: (v) => v.toFixed(2),    simFmt: () => '—' },
      { label: 'Weight planned',    org: orderedWeightKg,      sim: null,                                unit: 'kg', fmt: (v) => Math.round(v),   simFmt: () => '—' },
      { label: 'Returnables',       org: expectedReturns,      sim: all.returnables_picked_units ?? 0,  unit: 'u',  fmt: (v) => Math.round(v) },
      { label: 'Visit-order match', org: null,                 sim: seqMatchPct,                         unit: '%',  fmt: (v) => v == null ? '—' : (v * 100).toFixed(0), orgFmt: () => `${comparedSeq}/${orgClients}` },
    ],
    meta: { repartidor: routeDetail.repartidor, transports: routeDetail.transports?.length || 0 },
  };
}

function ComparisonRow({ row }) {
  const orgDisplay = row.orgFmt ? row.orgFmt(row.org) : (row.org == null ? '—' : row.fmt(row.org));
  const simDisplay = row.simFmt ? row.simFmt(row.sim) : (row.sim == null ? '—' : row.fmt(row.sim));
  let delta = null;
  if (typeof row.org === 'number' && typeof row.sim === 'number' && row.org !== 0) {
    delta = ((row.sim - row.org) / row.org) * 100;
  }
  const deltaCls = delta == null ? '' : (Math.abs(delta) < 0.5 ? 'cmp-delta-zero' : delta > 0 ? 'cmp-delta-pos' : 'cmp-delta-neg');
  return (
    <div className="cmp-row">
      <span className="cmp-label">{row.label}</span>
      <span className="cmp-org">{orgDisplay}{row.unit && row.org != null ? <span className="cmp-unit"> {row.unit}</span> : null}</span>
      <span className="cmp-sim">{simDisplay}{row.unit && row.sim != null ? <span className="cmp-unit"> {row.unit}</span> : null}</span>
      <span className={`cmp-delta ${deltaCls}`}>
        {delta == null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(1)}%`}
      </span>
    </div>
  );
}

export default function MetricsBar({ kpis, fullscreen, routeDetail, simStops }) {
  const all = kpis?.all || {};
  const empty = !kpis;
  const comparison = buildComparison(routeDetail, all, simStops);

  if (fullscreen) {
    return (
      <div className="metrics metrics-full">
        <div className="metrics-full-head">
          {empty ? 'Awaiting simulation…' : 'All KPIs'}
        </div>
        {comparison && (
          <div className="metrics-full-group cmp-block">
            <div className="metrics-full-group-title">Organizer plan vs simulator</div>
            <div className="cmp-table">
              <div className="cmp-row cmp-head">
                <span className="cmp-label">Metric</span>
                <span className="cmp-org">Organizer</span>
                <span className="cmp-sim">Simulator</span>
                <span className="cmp-delta">Δ</span>
              </div>
              {comparison.rows.map((r) => (
                <ComparisonRow key={r.label} row={r} />
              ))}
            </div>
          </div>
        )}
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
        {comparison && (
          <div className="cmp-strip">
            <span className="cmp-strip-label">Plan vs sim</span>
            {comparison.rows.slice(0, 5).map((r) => {
              const orgDisplay = r.orgFmt ? r.orgFmt(r.org) : (r.org == null ? '—' : r.fmt(r.org));
              const simDisplay = r.simFmt ? r.simFmt(r.sim) : (r.sim == null ? '—' : r.fmt(r.sim));
              let delta = null;
              if (typeof r.org === 'number' && typeof r.sim === 'number' && r.org !== 0) {
                delta = ((r.sim - r.org) / r.org) * 100;
              }
              const dCls = delta == null ? '' : (Math.abs(delta) < 0.5 ? 'cmp-delta-zero' : delta > 0 ? 'cmp-delta-pos' : 'cmp-delta-neg');
              return (
                <span key={r.label} className="cmp-strip-cell">
                  <span className="cmp-strip-key">{r.label}</span>
                  <span className="cmp-strip-vals">
                    {orgDisplay} <span className="cmp-strip-arrow">→</span> {simDisplay}
                    {delta != null && (
                      <span className={`cmp-strip-delta ${dCls}`}>
                        {delta > 0 ? '+' : ''}{delta.toFixed(0)}%
                      </span>
                    )}
                  </span>
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
