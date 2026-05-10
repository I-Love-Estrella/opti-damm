'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { api, SIM_API_BASE } from '@/lib/api';
import StopTimeline from '@/components/StopTimeline';
import StepPlayer from '@/components/StepPlayer';
import ActionLog from '@/components/ActionLog';
import ValidationPanel from '@/components/ValidationPanel';
import {
  cargoStateAt,
  flattenStages,
  buildLiftReplaceMap,
  computeCenterOfMass,
  COM_LATERAL_WARN_M,
  COM_LATERAL_ERROR_M,
  COM_LONGITUDINAL_WARN_M,
  COM_HIGH_WARN_M,
} from '@/lib/cargoState';
import './algorithms.css';

const RouteMap = dynamic(() => import('@/components/RouteMap'), { ssr: false });
const Truck3D = dynamic(() => import('@/components/Truck3D'), { ssr: false });

function comTone(value, warn, err) {
  const a = Math.abs(value);
  if (a > err) return { color: '#ff5050', label: 'ROLLOVER RISK' };
  if (a > warn) return { color: '#fc0', label: 'WATCH TURNS' };
  return { color: '#3aff80', label: 'OK' };
}

function CenterOfMassPanel({ com }) {
  const lateral = comTone(com.lateral_z, COM_LATERAL_WARN_M, COM_LATERAL_ERROR_M);
  // longitudinal is warning-only (axle imbalance)
  const longi = comTone(
    com.longitudinal_x,
    COM_LONGITUDINAL_WARN_M,
    COM_LONGITUDINAL_WARN_M * 1.6,
  );
  // vertical is height above floor — high = top-heavy
  const vert =
    com.vertical_y > COM_HIGH_WARN_M
      ? { color: '#fc0', label: 'TOP-HEAVY' }
      : { color: '#3aff80', label: 'OK' };
  // Visual bar: lateral_z, range ±0.50m, with warn/error tick marks.
  const RANGE_M = 0.50;
  const barWidthPct = Math.min(100, (Math.abs(com.lateral_z) / RANGE_M) * 100);
  const barLeftPct = com.lateral_z < 0 ? 50 - barWidthPct / 2 : 50;
  // Actually compute precise position: mid of bar = 0, edge = ±RANGE_M
  const lateralMidPct = 50 + (com.lateral_z / RANGE_M) * 50;

  return (
    <div
      style={{
        width: 240,
        flexShrink: 0,
        background: '#0c0c0c',
        border: '1px solid #1f1f1f',
        padding: '12px 14px',
        fontFamily: 'var(--mono)',
        fontSize: 11,
        color: '#cfcfcf',
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
      }}
    >
      <div style={{ color: '#888', letterSpacing: '0.08em' }}>
        CENTER OF MASS
      </div>

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
          <span>Lateral (L↔R)</span>
          <span style={{ color: lateral.color, fontWeight: 600 }}>
            {com.lateral_z >= 0 ? '+' : ''}
            {com.lateral_z.toFixed(2)} m
          </span>
        </div>
        <div
          style={{
            position: 'relative',
            height: 8,
            background: '#1a1a1a',
            border: '1px solid #2a2a2a',
            borderRadius: 1,
          }}
        >
          {/* Warn band ±0.20m */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: `${50 - (COM_LATERAL_WARN_M / RANGE_M) * 50}%`,
              width: `${(2 * COM_LATERAL_WARN_M / RANGE_M) * 50}%`,
              background: 'rgba(58, 255, 128, 0.10)',
            }}
          />
          {/* Error band ±0.30m..0.50m (warn band) */}
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: `${50 - (COM_LATERAL_ERROR_M / RANGE_M) * 50}%`,
              width: `${((COM_LATERAL_ERROR_M - COM_LATERAL_WARN_M) / RANGE_M) * 50}%`,
              background: 'rgba(255, 204, 0, 0.15)',
            }}
          />
          <div
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              right: `${50 - (COM_LATERAL_ERROR_M / RANGE_M) * 50}%`,
              width: `${((COM_LATERAL_ERROR_M - COM_LATERAL_WARN_M) / RANGE_M) * 50}%`,
              background: 'rgba(255, 204, 0, 0.15)',
            }}
          />
          {/* Centerline */}
          <div
            style={{
              position: 'absolute',
              left: '50%',
              top: -2,
              bottom: -2,
              width: 1,
              background: '#444',
            }}
          />
          {/* Pointer */}
          <div
            style={{
              position: 'absolute',
              left: `${Math.max(0, Math.min(100, lateralMidPct))}%`,
              top: -3,
              bottom: -3,
              width: 3,
              background: lateral.color,
              transform: 'translateX(-50%)',
              boxShadow: `0 0 4px ${lateral.color}`,
            }}
          />
        </div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            color: '#666',
            fontSize: 9,
            marginTop: 2,
          }}
        >
          <span>L 0.50m</span>
          <span style={{ color: lateral.color }}>{lateral.label}</span>
          <span>R 0.50m</span>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>Longitudinal (F↔B)</span>
        <span style={{ color: longi.color, fontWeight: 600 }}>
          {com.longitudinal_x >= 0 ? '+' : ''}
          {com.longitudinal_x.toFixed(2)} m
        </span>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>Vertical (height)</span>
        <span style={{ color: vert.color, fontWeight: 600 }}>
          {com.vertical_y.toFixed(2)} m
        </span>
      </div>

      <div
        style={{
          marginTop: 'auto',
          paddingTop: 8,
          borderTop: '1px solid #1f1f1f',
          color: '#888',
          fontSize: 10,
          lineHeight: 1.4,
        }}
      >
        <div>Total mass: <b style={{ color: '#cfcfcf' }}>{com.total_kg.toFixed(0)} kg</b></div>
        <div style={{ marginTop: 4 }}>Lateral cap: warn 0.20 m / err 0.30 m</div>
      </div>
    </div>
  );
}

function Playback3D({ run }) {
  const stops = run?.data?.stops || [];
  const initialCargo = run?.data?.initial_cargo || [];
  const truck = run?.data?.truck;
  const violations = run?.data?.physics_violations || [];

  const flatStages = useMemo(() => flattenStages(stops), [stops]);
  const liftMaps = useMemo(() => buildLiftReplaceMap(flatStages), [flatStages]);
  const [idx, setIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const rootRef = useRef(null);

  // Map a violation seq to the closest stage index in flatStages so the
  // operator can jump straight to "this is where the truck broke".
  const violationStops = useMemo(() => {
    return violations.map((v) => {
      const seq = v.seq ?? 0;
      let idxAtOrAfter = flatStages.findIndex((s) => (s.seq ?? 0) >= seq);
      if (idxAtOrAfter < 0) idxAtOrAfter = flatStages.length;
      return { ...v, stage_idx: idxAtOrAfter + 1 };
    });
  }, [violations, flatStages]);

  // Auto-pause when playback reaches the first violation.
  const firstViolationIdx = violationStops[0]?.stage_idx ?? null;
  useEffect(() => {
    if (
      isPlaying &&
      firstViolationIdx !== null &&
      idx >= firstViolationIdx
    ) {
      setIsPlaying(false);
    }
  }, [idx, isPlaying, firstViolationIdx]);

  const state = useMemo(
    () => cargoStateAt(initialCargo, flatStages, idx),
    [initialCargo, flatStages, idx],
  );

  // Recompute COG every step.
  const com = useMemo(() => computeCenterOfMass(state.boxes), [state.boxes]);

  if (!truck || flatStages.length === 0) return null;

  const atViolation = violationStops.some((v) => idx >= v.stage_idx);

  return (
    <div className={`playback3d ${atViolation ? 'playback-broken' : ''}`} ref={rootRef}>
      {violationStops.length > 0 && (
        <div className="physics-banner">
          <div className="physics-banner-title">
            ⚠ {violationStops.length} PHYSICS VIOLATION
            {violationStops.length > 1 ? 'S' : ''} — truck cannot be driven
          </div>
          <ul className="physics-banner-list">
            {violationStops.slice(0, 50).map((v, i) => (
              <li key={i}>
                <span className={`pv-tag pv-${(v.code || '').toLowerCase()}`}>
                  {v.code}
                </span>
                <span className="pv-where">
                  step #{v.stage_idx} · {v.where || ''}
                </span>
                <span className="pv-msg">{v.message}</span>
                <button
                  className="btn-jump"
                  onClick={() => {
                    setIsPlaying(false);
                    setIdx(v.stage_idx);
                  }}
                >
                  Jump to step ▸
                </button>
              </li>
            ))}
            {violationStops.length > 50 && (
              <li className="pv-more">
                + {violationStops.length - 50} more violations
              </li>
            )}
          </ul>
        </div>
      )}
      <div style={{ display: 'flex', gap: 12, alignItems: 'stretch' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Truck3D
            truck={truck}
            palletsBySlot={state.palletsBySlot}
            boxes={state.boxes}
            highlightSeq={idx > 0 ? idx - 1 : undefined}
            height={480}
          />
        </div>
        <CenterOfMassPanel com={com} />
      </div>
      <StepPlayer
        stages={flatStages}
        idx={idx}
        onIdxChange={(updater) => {
          if (typeof updater === 'function') setIdx((c) => updater(c));
          else setIdx(updater);
        }}
        isPlaying={isPlaying}
        onPlayingChange={setIsPlaying}
        liftMaps={liftMaps}
        speed={speed}
        onSpeedChange={setSpeed}
        rootRef={rootRef}
      />
      <ActionLog
        stages={flatStages}
        idx={idx}
        onSelectIdx={setIdx}
      />
    </div>
  );
}

const KPI_CARDS = [
  { key: 'total_minutes',    label: 'Total minutes',    fmt: (v) => v.toFixed(1),     unit: 'min',   lowerBetter: true },
  { key: 'driver_minutes',   label: 'Driver shift',     fmt: (v) => `${v.toFixed(1)} (${(v / 60).toFixed(1)}h)`, unit: 'min', lowerBetter: true, highlight: true },
  { key: 'drive_minutes',    label: 'Drive (driver)',   fmt: (v) => v.toFixed(1),     unit: 'min',   lowerBetter: true, group: 'time' },
  { key: 'service_minutes',  label: 'Client service (driver)', fmt: (v) => v.toFixed(1), unit: 'min', lowerBetter: true, group: 'time' },
  { key: 'depot_minutes',    label: 'Depot loading (warehouse)', fmt: (v) => v.toFixed(1), unit: 'min', lowerBetter: true, group: 'time' },
  { key: 'total_km',         label: 'Distance',         fmt: (v) => v.toFixed(1),     unit: 'km',    lowerBetter: true },
  { key: 'search_moves',     label: 'Search moves',     fmt: (v) => Math.round(v),    unit: 'units', lowerBetter: true, highlight: true },
  { key: 'driver_labor_eur', label: 'Driver labor',     fmt: (v) => v.toFixed(2),     unit: '€',     lowerBetter: true, group: 'cost' },
  { key: 'depot_labor_eur',  label: 'Loader labor',     fmt: (v) => v.toFixed(2),     unit: '€',     lowerBetter: true, group: 'cost' },
  { key: 'total_cost_eur',   label: 'Total cost',       fmt: (v) => v.toFixed(0),     unit: '€',     lowerBetter: true },
  { key: 'co2_kg',           label: 'CO₂',              fmt: (v) => v.toFixed(1),     unit: 'kg',    lowerBetter: true },
  { key: 'fill_rate',        label: 'Fill rate',        fmt: (v) => `${(v * 100).toFixed(1)}%`, unit: '',  lowerBetter: false },
  { key: 'pallets_loaded',   label: 'Pallets',          fmt: (v) => Math.round(v),    unit: '',      lowerBetter: false },
  { key: 'capacity_violations', label: 'Capacity violations', fmt: (v) => Math.round(v), unit: '', lowerBetter: true },
];

function StatusPill({ status }) {
  const map = {
    idle:    { bg: '#1f1f1f', txt: '#999', label: 'IDLE' },
    loading: { bg: '#3a3000', txt: '#fc0', label: 'RUNNING' },
    ok:      { bg: '#003c1a', txt: '#3aff80', label: 'OK' },
    error:   { bg: '#3c0000', txt: '#ff5050', label: 'ERROR' },
  };
  const s = map[status] || map.idle;
  return (
    <span style={{ background: s.bg, color: s.txt, padding: '2px 8px', borderRadius: 2, fontSize: 11, fontFamily: 'var(--mono)', letterSpacing: '0.06em' }}>
      {s.label}
    </span>
  );
}

function KpiCard({ label, value, unit, baselineValue, lowerBetter, highlight }) {
  let delta = null;
  if (baselineValue !== undefined && baselineValue !== null && Number.isFinite(baselineValue) && baselineValue !== 0) {
    const num = typeof value === 'string' ? parseFloat(value) : value;
    const pct = ((num - baselineValue) / baselineValue) * 100;
    if (Number.isFinite(pct)) {
      const better = lowerBetter ? pct < 0 : pct > 0;
      delta = { pct, better };
    }
  }
  return (
    <div className={`kpi-card${highlight ? ' kpi-card-highlight' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      {delta && (
        <div className={`kpi-delta ${delta.better ? 'good' : 'bad'}`}>
          {delta.pct >= 0 ? '+' : ''}
          {delta.pct.toFixed(1)}% vs {KpiCard.baselineLabel || 'baseline'}
        </div>
      )}
    </div>
  );
}

function AlgorithmSection({ algo, run, baselineKpis, onRun }) {
  const status = run?.status || 'idle';
  const kpis = run?.data?.kpis;
  const truck = run?.data?.truck;
  const validation = run?.data?.validation;
  const planInvalid = validation && !validation.summary?.is_valid;
  const physicsViolations = run?.data?.physics_violations || [];
  const hasPhysicsViolations = physicsViolations.length > 0;
  const fit = run?.data?.fit;
  const fitFails = fit && fit.fits === false;

  return (
    <section className={`algo-section${planInvalid ? ' algo-section-invalid' : ''}`}>
      <header className="algo-header">
        <div className="algo-headline">
          <span className="algo-tag">ALGO</span>
          <h2 className="algo-name">{algo.name}</h2>
          <StatusPill status={status} />
          {planInvalid && (
            <span className="algo-invalid-badge">
              ✕ INVALID — {validation.summary.errors} error(s)
            </span>
          )}
          {hasPhysicsViolations && (
            <span className="algo-physics-badge">
              ⚠ {physicsViolations.length} PHYSICS
            </span>
          )}
          {fitFails && (
            <span className="algo-invalid-badge">
              ⚠ TRUCK TOO SMALL
            </span>
          )}
          {truck?.manual_override && (
            <span className="algo-physics-badge" title="Truck manually overridden">
              ⚙ MANUAL TRUCK · {truck.code}
            </span>
          )}
        </div>
        <button className="btn-primary" onClick={() => onRun(algo.name)} disabled={status === 'loading'}>
          {status === 'loading' ? 'Running…' : 'Run on selected day ▸'}
        </button>
      </header>

      <p className="algo-desc">{algo.description}</p>

      {status === 'error' && (
        <div className="algo-error">Error: {run?.error || 'unknown'}</div>
      )}

      {kpis && (
        <>
          {fitFails && (
            <div className="algo-error" style={{ marginBottom: 12 }}>
              <b>Truck capacity exceeded:</b>
              <ul style={{ margin: '6px 0 0 18px' }}>
                {fit.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}
          {run?.data?.validation && (
            <ValidationPanel validation={run.data.validation} />
          )}

          <div className="algo-meta">
            <span><b>Truck:</b> {truck?.code} · {truck?.pallet_capacity} plt · {truck?.max_weight_kg} kg</span>
            <span><b>Clients:</b> {kpis.n_clients_visited}/{kpis.n_clients_planned}</span>
            <span><b>Pallets loaded:</b> {kpis.pallets_loaded}</span>
            <span><b>Drops:</b> {kpis.drops}</span>
          </div>

          <div className="kpi-grid">
            {KPI_CARDS.map(c => (
              <KpiCard
                key={c.key}
                label={c.label}
                value={c.fmt(kpis[c.key] || 0)}
                unit={c.unit}
                baselineValue={baselineKpis?.[c.key]}
                lowerBetter={c.lowerBetter}
                highlight={c.highlight}
              />
            ))}
          </div>

          {run?.data?.rationale?.length > 0 && (
            <div className="algo-rationale">
              <h4>Strategy</h4>
              <ul>
                {run.data.rationale.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}

          {run?.data?.route?.length > 0 && (
            <div className="algo-route">
              <h4>Visit order</h4>
              <div className="route-chain">
                <span className="route-chip depot">DEPOT</span>
                {run.data.route.map((c, i) => (
                  <span key={`${c}-${i}`} className="route-chip">{c}</span>
                ))}
                <span className="route-chip depot">DEPOT</span>
              </div>
            </div>
          )}

          {run?.data?.stops?.length > 0 && run?.data?.depot && (
            <div className="viz-section">
              <h4>Route map</h4>
              <RouteMap
                depot={run.data.depot}
                stops={run.data.stops}
                legs={run.data.legs || []}
                height={340}
              />
            </div>
          )}

          {run?.data?.stops?.some((s) => (s.stages || []).length > 0) && (
            <details className="viz-section viz-collapsible">
              <summary className="viz-summary">
                <h4>Per-stop unload timeline (each box is a stage)</h4>
                <span className="viz-toggle" />
              </summary>
              <StopTimeline
                stops={run.data.stops}
                clientNames={Object.fromEntries((run.data.stops || []).map((s) => [s.client_id, s.name]))}
              />
            </details>
          )}

          {run?.data?.stops?.some((s) => (s.stages || []).length > 0) && run?.data?.truck && (
            <div className="viz-section">
              <h4>3D playback — step through every box that moves</h4>
              <Playback3D run={run} />
            </div>
          )}
        </>
      )}

      {!kpis && status !== 'loading' && (
        <div className="algo-placeholder">
          Press <b>Run</b> to execute the algorithm on the selected day and see KPIs here.
        </div>
      )}
    </section>
  );
}

// Picks the headline metrics worth showing first in the aggregate table.
const BATCH_KPI_KEYS = [
  'total_minutes',
  'drive_minutes',
  'service_minutes',
  'total_km',
  'search_moves',
  'total_cost_eur',
  'co2_kg',
  'fill_rate',
  'returnables_picked_units',
  'placement_rejections',
  'lost_units',
  'wall_clock_sec',
];

function fmt(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Number(n).toFixed(digits);
}

function BatchPanel({
  algo, days, trucks,
  truckCode, onTruckChange,
  mode, onModeChange,
  n, onNChange,
  seed, onSeedChange,
  selected, onTogglePick,
  status, error, data,
  onRun,
}) {
  const stats = data;
  return (
    <section className="batch-panel">
      <header className="batch-header">
        <div>
          <span className="algo-tag">BATCH</span>
          <h3 style={{ display: 'inline-block', margin: '0 0 0 8px' }}>
            Run <code>{algo || '—'}</code> on multiple datasets
          </h3>
        </div>
        <StatusPill status={status} />
      </header>

      <div className="batch-controls">
        <div className="control-group control-group-wide">
          <label title="Force a specific truck for every case in this run. Default: each case gets the smallest truck that fits its cargo.">
            Truck
          </label>
          <TruckPicker
            trucks={trucks}
            value={truckCode}
            onChange={onTruckChange}
            idPrefix="batch-"
          />
        </div>
        <div className="control-group">
          <label>Mode</label>
          <select value={mode} onChange={(e) => onModeChange(e.target.value)}>
            <option value="first">first N (sorted)</option>
            <option value="random">random N (seeded)</option>
            <option value="all">all available</option>
            <option value="selected">manual pick</option>
          </select>
        </div>
        {mode !== 'all' && mode !== 'selected' && (
          <div className="control-group">
            <label>N cases</label>
            <input
              type="number" min={1} max={200} value={n}
              onChange={(e) => onNChange(Math.max(1, parseInt(e.target.value, 10) || 1))}
              style={{ width: 80 }}
            />
          </div>
        )}
        {mode === 'random' && (
          <div className="control-group">
            <label>Seed</label>
            <input
              type="number" value={seed}
              onChange={(e) => onSeedChange(parseInt(e.target.value, 10) || 0)}
              style={{ width: 80 }}
            />
          </div>
        )}
        <button
          className="btn-primary big"
          onClick={onRun}
          disabled={status === 'loading' || !algo || (mode === 'selected' && !selected.length)}
        >
          {status === 'loading' ? 'Running…' : `▶ Run on ${mode === 'selected' ? `${selected.length} picked` : mode === 'all' ? 'all' : `${n}`}`}
        </button>
      </div>

      {mode === 'selected' && (
        <details className="batch-picker" open={selected.length === 0}>
          <summary>Pick datasets ({selected.length} selected)</summary>
          <div className="batch-picker-list">
            {days.map((d) => {
              const isPicked = selected.some((p) => p.date === d.date && p.ruta === d.ruta);
              return (
                <label key={`${d.date}-${d.ruta}`} className={`batch-pick-row${isPicked ? ' picked' : ''}`}>
                  <input
                    type="checkbox"
                    checked={isPicked}
                    onChange={() => onTogglePick(d)}
                  />
                  <span className="bp-date">{d.date}</span>
                  <span className="bp-ruta">{d.ruta}</span>
                  <span className="bp-meta">{d.clients} clients · {d.lines} lines</span>
                </label>
              );
            })}
          </div>
        </details>
      )}

      {error && <div className="algo-error" style={{ marginTop: 12 }}>Error: {error}</div>}

      {stats && status === 'ok' && (
        <div className="batch-results">
          <div className="batch-summary-grid">
            <SummaryCard label="Cases" value={stats.n_cases} />
            <SummaryCard label="Success" value={`${stats.n_success}/${stats.n_cases}`} good={stats.n_success === stats.n_cases} />
            <SummaryCard label="Failed (sim)" value={stats.n_failed} bad={stats.n_failed > 0} />
            <SummaryCard label="Invalid plan" value={stats.n_invalid_plan} bad={stats.n_invalid_plan > 0} />
            <SummaryCard label="Physics-V cases" value={stats.n_with_physics} bad={stats.n_with_physics > 0} />
            <SummaryCard label="Physics-V events" value={stats.total_physics_violations} bad={stats.total_physics_violations > 0} />
            <SummaryCard label="Total drops" value={stats.total_drops} bad={stats.total_drops > 0} />
            <SummaryCard label="Capacity-V" value={stats.total_capacity_violations} bad={stats.total_capacity_violations > 0} />
            <SummaryCard label="Validation errors" value={stats.total_validation_errors} bad={stats.total_validation_errors > 0} />
            <SummaryCard label="Validation warnings" value={stats.total_validation_warnings} />
            <SummaryCard label="Clean rate" value={`${(stats.clean_rate * 100).toFixed(1)}%`} good={stats.clean_rate >= 0.9} />
            <SummaryCard label="Wall time" value={`${stats.duration_sec.toFixed(1)}s`} />
          </div>

          <h4 className="batch-h">Distribution per KPI</h4>
          <div className="batch-table-wrap">
            <table className="batch-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>n</th><th>sum</th><th>mean</th>
                  <th>median</th><th>stdev</th>
                  <th>min</th><th>max</th><th>p95</th>
                </tr>
              </thead>
              <tbody>
                {BATCH_KPI_KEYS.map((k) => {
                  const a = stats.aggregates?.[k];
                  if (!a) return null;
                  return (
                    <tr key={k}>
                      <td className="metric">{k}</td>
                      <td>{a.n}</td>
                      <td>{fmt(a.sum)}</td>
                      <td>{fmt(a.mean, 3)}</td>
                      <td>{fmt(a.median, 3)}</td>
                      <td>{fmt(a.stdev, 3)}</td>
                      <td>{fmt(a.min, 3)}</td>
                      <td>{fmt(a.max, 3)}</td>
                      <td>{fmt(a.p95, 3)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h4 className="batch-h">Per-case results ({stats.cases.length})</h4>
          <div className="batch-table-wrap">
            <table className="batch-table batch-cases">
              <thead>
                <tr>
                  <th>Date</th><th>Ruta</th><th>OK</th>
                  <th>Time (min)</th><th>Km</th><th>Search</th>
                  <th>Cost (€)</th><th>Fill %</th><th>Drops</th>
                  <th>Cap-V</th><th>Phys-V</th><th>Val-E</th>
                  <th title="Items the simulator refused to place (overlap/float/oob)">
                    Lost
                  </th>
                  <th>Wall (s)</th>
                </tr>
              </thead>
              <tbody>
                {stats.cases.map((c) => {
                  const k = c.kpis || {};
                  const lost = k.lost_units ?? 0;
                  const bad =
                    !c.success ||
                    c.physics_violations > 0 ||
                    c.validation_errors > 0 ||
                    lost > 0;
                  return (
                    <tr key={`${c.date}-${c.ruta}`} className={bad ? 'row-bad' : ''}>
                      <td>{c.date}</td>
                      <td><code>{c.ruta}</code></td>
                      <td>{c.success ? '✓' : '✕'}</td>
                      <td>{fmt(k.total_minutes, 1)}</td>
                      <td>{fmt(k.total_km, 1)}</td>
                      <td>{fmt(k.search_moves, 0)}</td>
                      <td>{fmt(k.total_cost_eur, 0)}</td>
                      <td>{fmt((k.fill_rate ?? 0) * 100, 1)}</td>
                      <td>{fmt(k.drops, 0)}</td>
                      <td>{fmt(k.capacity_violations, 0)}</td>
                      <td>{c.physics_violations}</td>
                      <td>{c.validation_errors}</td>
                      <td>{fmt(lost, 0)}</td>
                      <td>{fmt(c.elapsed_sec, 2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {stats.failures?.length > 0 && (
            <details className="batch-failures">
              <summary>Failures ({stats.failures.length})</summary>
              <ul>
                {stats.failures.map((f, i) => (
                  <li key={i}><code>{f.date} {f.ruta}</code> — {f.error}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function SummaryCard({ label, value, good, bad }) {
  const cls = `batch-card${good ? ' good' : ''}${bad ? ' bad' : ''}`;
  return (
    <div className={cls}>
      <div className="bc-label">{label}</div>
      <div className="bc-value">{value}</div>
    </div>
  );
}

// Segmented pill selector for the truck choice. Four options:
// `auto` (let the builder pick the smallest fit per case) and the three
// fleet types — T6 (6-pallet, the everyday workhorse), T8 (8-pallet,
// for heavy days), V3 (3-pallet van, B-side only, for tight urban runs).
function TruckPicker({ trucks, value, onChange, idPrefix = '' }) {
  const opts = [
    { code: '', label: 'auto', sub: 'smallest fit / case' },
    ...(trucks || []).map((t) => ({
      code: t.code,
      label: t.code,
      sub: `${t.pallet_capacity} plt · ${t.max_weight_kg} kg`,
    })),
  ];
  return (
    <div className="truck-picker" role="radiogroup" aria-label="Truck">
      {opts.map((o) => {
        const active = (value || '') === o.code;
        return (
          <button
            key={o.code || 'auto'}
            id={`${idPrefix}truck-${o.code || 'auto'}`}
            role="radio"
            aria-checked={active}
            className={`tp-btn${active ? ' active' : ''}`}
            onClick={() => onChange(o.code || null)}
          >
            <span className="tp-label">{o.label}</span>
            <span className="tp-sub">{o.sub}</span>
          </button>
        );
      })}
    </div>
  );
}

// === Compare panel ====================================================
//
// Head-to-head comparison: run TWO algorithms on the SAME N cases, then
// surface paired deltas (algoB − algoA) per metric. The "paired" framing
// means route difficulty washes out — even on a small sample we get a
// clean A-vs-B effect.

const COMPARE_KPI_KEYS = [
  'total_minutes',
  'drive_minutes',
  'service_minutes',
  'depot_minutes',
  'total_km',
  'search_moves',
  'total_cost_eur',
  'driver_labor_eur',
  'depot_labor_eur',
  'co2_kg',
  'fill_rate',
  'returnables_picked_units',
  'placement_rejections',
  'lost_units',
];

function deltaCellClass(deltaPct, lowerBetter) {
  if (Math.abs(deltaPct) < 0.05) return '';
  const better = lowerBetter ? deltaPct < 0 : deltaPct > 0;
  return better ? 'cmp-good' : 'cmp-bad';
}

function ComparePanel({
  algorithms, days, trucks,
  algoA, onAlgoAChange,
  algoB, onAlgoBChange,
  truckCode, onTruckChange,
  mode, onModeChange,
  n, onNChange,
  seed, onSeedChange,
  selected, onTogglePick,
  status, error, data,
  onRun,
}) {
  const filteredMetrics = (data?.metrics || []).filter((m) =>
    COMPARE_KPI_KEYS.includes(m.metric),
  );
  const headlineMetrics = ['total_cost_eur', 'total_minutes', 'total_km', 'search_moves'];

  return (
    <section className="batch-panel cmp-panel">
      <header className="batch-header">
        <div>
          <span className="algo-tag">COMPARE</span>
          <h3 style={{ display: 'inline-block', margin: '0 0 0 8px' }}>
            Head-to-head: <code>{algoA || '—'}</code> vs <code>{algoB || '—'}</code>
          </h3>
        </div>
        <StatusPill status={status} />
      </header>

      <div className="batch-controls">
        <div className="control-group">
          <label>Algorithm A (baseline)</label>
          <select value={algoA || ''} onChange={(e) => onAlgoAChange(e.target.value)}>
            {algorithms.map((a) => (
              <option key={a.name} value={a.name}>{a.name}</option>
            ))}
          </select>
        </div>
        <div className="control-group">
          <label>Algorithm B (challenger)</label>
          <select value={algoB || ''} onChange={(e) => onAlgoBChange(e.target.value)}>
            {algorithms.map((a) => (
              <option key={a.name} value={a.name}>{a.name}</option>
            ))}
          </select>
        </div>
        <div className="control-group control-group-wide">
          <label title="Force a specific truck for every case in this comparison. Default: each case gets the smallest truck that fits its cargo.">
            Truck
          </label>
          <TruckPicker
            trucks={trucks}
            value={truckCode}
            onChange={onTruckChange}
            idPrefix="cmp-"
          />
        </div>
        <div className="control-group">
          <label>Mode</label>
          <select value={mode} onChange={(e) => onModeChange(e.target.value)}>
            <option value="first">first N</option>
            <option value="random">random N</option>
            <option value="all">all</option>
            <option value="selected">manual pick</option>
          </select>
        </div>
        {mode !== 'all' && mode !== 'selected' && (
          <div className="control-group">
            <label>N cases</label>
            <input
              type="number" min={1} max={200} value={n}
              onChange={(e) => onNChange(Math.max(1, parseInt(e.target.value, 10) || 1))}
              style={{ width: 80 }}
            />
          </div>
        )}
        {mode === 'random' && (
          <div className="control-group">
            <label>Seed</label>
            <input
              type="number" value={seed}
              onChange={(e) => onSeedChange(parseInt(e.target.value, 10) || 0)}
              style={{ width: 80 }}
            />
          </div>
        )}
        <button
          className="btn-primary big"
          onClick={onRun}
          disabled={
            status === 'loading'
            || !algoA || !algoB || algoA === algoB
            || (mode === 'selected' && !selected.length)
          }
        >
          {status === 'loading'
            ? 'Running…'
            : `▶ Compare on ${mode === 'selected' ? `${selected.length} picked` : mode === 'all' ? 'all' : `${n}`}`}
        </button>
      </div>

      {algoA && algoB && algoA === algoB && (
        <div className="algo-error" style={{ marginTop: 8 }}>
          Algorithm A and B must differ — pick two different algorithms.
        </div>
      )}

      {mode === 'selected' && (
        <details className="batch-picker" open={selected.length === 0}>
          <summary>Pick datasets ({selected.length} selected)</summary>
          <div className="batch-picker-list">
            {days.map((d) => {
              const isPicked = selected.some((p) => p.date === d.date && p.ruta === d.ruta);
              return (
                <label key={`${d.date}-${d.ruta}`} className={`batch-pick-row${isPicked ? ' picked' : ''}`}>
                  <input
                    type="checkbox"
                    checked={isPicked}
                    onChange={() => onTogglePick(d)}
                  />
                  <span className="bp-date">{d.date}</span>
                  <span className="bp-ruta">{d.ruta}</span>
                  <span className="bp-meta">{d.clients} clients · {d.lines} lines</span>
                </label>
              );
            })}
          </div>
        </details>
      )}

      {error && <div className="algo-error" style={{ marginTop: 12 }}>Error: {error}</div>}

      {data && status === 'ok' && (
        <div className="batch-results">
          <div className="cmp-headline-grid">
            <div className="cmp-headline-block">
              <div className="cmp-headline-label">A · {data.algo_a}</div>
              <div className="cmp-headline-stat">
                {data.a_stats.n_success}/{data.a_stats.n_cases} OK · {data.a_stats.total_physics_violations} phys-V
              </div>
              <div className="cmp-headline-stat dim">
                €{Math.round(
                  (data.a_stats.aggregates?.total_cost_eur?.mean || 0),
                )} avg · {(data.a_stats.aggregates?.total_km?.mean || 0).toFixed(1)} km avg
              </div>
            </div>
            <div className="cmp-headline-vs">vs</div>
            <div className="cmp-headline-block">
              <div className="cmp-headline-label">B · {data.algo_b}</div>
              <div className="cmp-headline-stat">
                {data.b_stats.n_success}/{data.b_stats.n_cases} OK · {data.b_stats.total_physics_violations} phys-V
              </div>
              <div className="cmp-headline-stat dim">
                €{Math.round(
                  (data.b_stats.aggregates?.total_cost_eur?.mean || 0),
                )} avg · {(data.b_stats.aggregates?.total_km?.mean || 0).toFixed(1)} km avg
              </div>
            </div>
          </div>

          <div className="batch-summary-grid" style={{ marginTop: 14 }}>
            <SummaryCard label="Cases compared" value={data.n_cases} />
            <SummaryCard label="Paired (both ran)" value={data.n_paired} />
            <SummaryCard label="A-only success" value={data.a_only_success} bad={data.a_only_success > 0} />
            <SummaryCard label="B-only success" value={data.b_only_success} bad={data.b_only_success > 0} />
            <SummaryCard label="Both failed" value={data.both_failed} bad={data.both_failed > 0} />
            <SummaryCard label="Wall time" value={`${data.duration_sec.toFixed(1)}s`} />
          </div>

          <h4 className="batch-h">Headline (B − A)</h4>
          <div className="cmp-headline-cards">
            {filteredMetrics
              .filter((m) => headlineMetrics.includes(m.metric))
              .sort((a, b) => headlineMetrics.indexOf(a.metric) - headlineMetrics.indexOf(b.metric))
              .map((m) => {
                const cls = deltaCellClass(m.delta_pct_mean, m.lower_better);
                const winner = m.b_wins > m.a_wins ? 'B' : m.a_wins > m.b_wins ? 'A' : '=';
                return (
                  <div key={m.metric} className={`cmp-card ${cls}`}>
                    <div className="cmp-card-label">{m.metric}</div>
                    <div className="cmp-card-delta">
                      {m.delta_pct_mean >= 0 ? '+' : ''}
                      {m.delta_pct_mean.toFixed(1)}%
                    </div>
                    <div className="cmp-card-sub">
                      A {m.a_mean.toFixed(1)} → B {m.b_mean.toFixed(1)}
                    </div>
                    <div className="cmp-card-wins">
                      A {m.a_wins} · B {m.b_wins} · = {m.ties} ({winner === '=' ? 'tie' : `${winner} wins`})
                    </div>
                  </div>
                );
              })}
          </div>

          <h4 className="batch-h">Paired metric table</h4>
          <div className="batch-table-wrap">
            <table className="batch-table">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>n</th>
                  <th>A mean</th><th>B mean</th>
                  <th>Δ mean</th><th>Δ %</th>
                  <th>Δ median</th>
                  <th>A wins</th><th>B wins</th><th>ties</th>
                </tr>
              </thead>
              <tbody>
                {filteredMetrics.map((m) => {
                  const cls = deltaCellClass(m.delta_pct_mean, m.lower_better);
                  return (
                    <tr key={m.metric}>
                      <td className="metric">
                        {m.metric}
                        {m.lower_better ? <span className="cmp-hint" title="lower is better"> ↓</span> : <span className="cmp-hint" title="higher is better"> ↑</span>}
                      </td>
                      <td>{m.n_paired}</td>
                      <td>{fmt(m.a_mean, 3)}</td>
                      <td>{fmt(m.b_mean, 3)}</td>
                      <td className={cls}>{fmt(m.delta_mean, 3)}</td>
                      <td className={cls}>
                        {m.delta_pct_mean >= 0 ? '+' : ''}
                        {fmt(m.delta_pct_mean, 1)}%
                      </td>
                      <td>{fmt(m.delta_median, 3)}</td>
                      <td>{m.a_wins}</td>
                      <td>{m.b_wins}</td>
                      <td>{m.ties}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <h4 className="batch-h">Per-case (B − A)</h4>
          <div className="batch-table-wrap">
            <table className="batch-table batch-cases">
              <thead>
                <tr>
                  <th>Date</th><th>Ruta</th>
                  <th title="Truck used by A on this case">A trk</th>
                  <th title="Truck used by B on this case">B trk</th>
                  <th>A cost</th><th>B cost</th><th>Δ %</th>
                  <th>A km</th><th>B km</th>
                  <th>A search</th><th>B search</th>
                  <th>A phys</th><th>B phys</th>
                  <th>A lost</th><th>B lost</th>
                </tr>
              </thead>
              <tbody>
                {data.cases.map((c) => {
                  const ak = c.a?.kpis || {};
                  const bk = c.b?.kpis || {};
                  const aCost = ak.total_cost_eur ?? 0;
                  const bCost = bk.total_cost_eur ?? 0;
                  const dPct = aCost > 0 ? ((bCost - aCost) / aCost) * 100 : 0;
                  const aTrk = c.a?.truck || '—';
                  const bTrk = c.b?.truck || '—';
                  const trucksDiffer = aTrk !== bTrk;
                  return (
                    <tr key={`${c.date}-${c.ruta}`}>
                      <td>{c.date}</td>
                      <td><code>{c.ruta}</code></td>
                      <td className={trucksDiffer ? 'cmp-truck-diff' : ''}>{aTrk}</td>
                      <td className={trucksDiffer ? 'cmp-truck-diff' : ''}>{bTrk}</td>
                      <td>{fmt(aCost, 0)}</td>
                      <td>{fmt(bCost, 0)}</td>
                      <td className={deltaCellClass(dPct, true)}>
                        {dPct >= 0 ? '+' : ''}{fmt(dPct, 1)}%
                      </td>
                      <td>{fmt(ak.total_km, 1)}</td>
                      <td>{fmt(bk.total_km, 1)}</td>
                      <td>{fmt(ak.search_moves, 0)}</td>
                      <td>{fmt(bk.search_moves, 0)}</td>
                      <td>{c.a?.physics_violations ?? 0}</td>
                      <td>{c.b?.physics_violations ?? 0}</td>
                      <td>{fmt(ak.lost_units, 0)}</td>
                      <td>{fmt(bk.lost_units, 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="cmp-issues-grid">
            <IssuesByAlgo
              label={`A · ${data.algo_a}`}
              cases={data.a_stats.cases}
            />
            <IssuesByAlgo
              label={`B · ${data.algo_b}`}
              cases={data.b_stats.cases}
            />
          </div>
        </div>
      )}
    </section>
  );
}

// Per-algorithm validation-issue feed: every issue surfaced by the
// validator on every dataset, grouped by (date, ruta), severity-coloured.
// Lets the user see — at a glance — *which* errors hit *which* dataset
// in a given comparison sweep.
function IssuesByAlgo({ label, cases }) {
  const [filter, setFilter] = useState('all'); // all | error | warning | info
  const totals = { error: 0, warning: 0, info: 0 };
  for (const c of cases || []) {
    for (const i of c.issues || []) {
      if (i.severity === 'error') totals.error += 1;
      else if (i.severity === 'warning') totals.warning += 1;
      else totals.info += 1;
    }
  }
  const sevRank = { error: 0, warning: 1, info: 2 };
  const passes = (sev) => filter === 'all' || sev === filter;

  // Cases that have at least one issue passing the filter.
  const visibleCases = (cases || [])
    .map((c) => ({
      ...c,
      _shownIssues: (c.issues || [])
        .filter((i) => passes(i.severity))
        .sort((a, b) => sevRank[a.severity] - sevRank[b.severity]),
    }))
    .filter((c) => c._shownIssues.length > 0);

  return (
    <div className="cmp-issues-block">
      <div className="cmp-issues-header">
        <div className="cmp-issues-label">{label}</div>
        <div className="cmp-issues-totals">
          <span className="ci-pill ci-pill-error">{totals.error} errors</span>
          <span className="ci-pill ci-pill-warning">{totals.warning} warnings</span>
          <span className="ci-pill ci-pill-info">{totals.info} info</span>
        </div>
      </div>
      <div className="cmp-issues-filter">
        {['all', 'error', 'warning', 'info'].map((opt) => (
          <button
            key={opt}
            className={`ci-filter${filter === opt ? ' active' : ''}`}
            onClick={() => setFilter(opt)}
          >
            {opt}
          </button>
        ))}
      </div>
      {visibleCases.length === 0 ? (
        <div className="cmp-issues-empty">
          No {filter === 'all' ? '' : filter + ' '}issues across {cases?.length || 0} cases.
        </div>
      ) : (
        <ul className="cmp-issues-list">
          {visibleCases.map((c) => (
            <li key={`${c.date}-${c.ruta}`} className="cmp-issue-case">
              <div className="ci-case-header">
                <span className="ci-case-date">{c.date}</span>
                <span className="ci-case-ruta">{c.ruta}</span>
                <span className="ci-case-counts">
                  {c.validation_errors > 0 && (
                    <span className="ci-mini ci-mini-error">
                      {c.validation_errors}E
                    </span>
                  )}
                  {c.validation_warnings > 0 && (
                    <span className="ci-mini ci-mini-warning">
                      {c.validation_warnings}W
                    </span>
                  )}
                  {c.physics_violations > 0 && (
                    <span className="ci-mini ci-mini-error">
                      {c.physics_violations} phys-V
                    </span>
                  )}
                </span>
              </div>
              <ul className="ci-issue-rows">
                {c._shownIssues.map((i, idx) => (
                  <li key={idx} className={`ci-issue ci-issue-${i.severity}`}>
                    <span className="ci-sev">{i.severity}</span>
                    <code className="ci-code">{i.code}</code>
                    <span className="ci-msg">{i.message}</span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AlgorithmsPage() {
  const [algorithms, setAlgorithms] = useState([]);
  const [trucks, setTrucks] = useState([]);
  const [days, setDays] = useState([]);
  const [selected, setSelected] = useState(null); // {date, ruta}
  const [selectedAlgo, setSelectedAlgo] = useState(null);
  // null = let the backend auto-pick the smallest fitting truck.
  const [selectedTruck, setSelectedTruck] = useState(null);
  const [runs, setRuns] = useState({});           // { algoName: {status, data, error} }
  const [bootError, setBootError] = useState(null);
  const [loadingBoot, setLoadingBoot] = useState(true);

  // Batch mode (run one algorithm on many datasets, get aggregate stats).
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchMode, setBatchMode] = useState('first');   // first | random | all | selected
  const [batchN, setBatchN] = useState(10);
  const [batchSeed, setBatchSeed] = useState(42);
  const [batchSelected, setBatchSelected] = useState([]); // [{date, ruta}]
  const [batchStatus, setBatchStatus] = useState('idle'); // idle | loading | ok | error
  const [batchError, setBatchError] = useState(null);
  const [batchData, setBatchData] = useState(null);

  // Compare mode (head-to-head: TWO algos on SAME N cases).
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareAlgoA, setCompareAlgoA] = useState(null);
  const [compareAlgoB, setCompareAlgoB] = useState(null);
  const [compareMode, setCompareMode] = useState('first');
  const [compareN, setCompareN] = useState(10);
  const [compareSeed, setCompareSeed] = useState(42);
  const [compareSelected, setCompareSelected] = useState([]);
  const [compareStatus, setCompareStatus] = useState('idle');
  const [compareError, setCompareError] = useState(null);
  const [compareData, setCompareData] = useState(null);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [a, t, d] = await Promise.all([
          api.algorithms(),
          api.trucks(),
          api.days({ minClients: 5, head: 50 }),
        ]);
        if (dead) return;
        const algos = a.algorithms || [];
        setAlgorithms(algos);
        setTrucks(t.trucks || []);
        setDays(d.items || []);
        if (d.items?.length > 0) {
          setSelected({ date: d.items[0].date, ruta: d.items[0].ruta });
        }
        if (algos.length > 0) {
          setSelectedAlgo(algos[0].name);
          setCompareAlgoA(algos[0].name);
          setCompareAlgoB(algos.length > 1 ? algos[1].name : algos[0].name);
        }
      } catch (e) {
        if (dead) return;
        setBootError(e.message || String(e));
      } finally {
        if (!dead) setLoadingBoot(false);
      }
    })();
    return () => { dead = true; };
  }, []);

  KpiCard.baselineLabel = 'baseline';

  const runOne = useCallback(async (algoName) => {
    if (!selected) return;
    setRuns(prev => ({ ...prev, [algoName]: { status: 'loading' } }));
    try {
      const data = await api.run({
        date: selected.date,
        ruta: selected.ruta,
        algo: algoName,
        truckCode: selectedTruck || undefined,
      });
      setRuns(prev => ({ ...prev, [algoName]: { status: 'ok', data } }));
    } catch (e) {
      setRuns(prev => ({ ...prev, [algoName]: { status: 'error', error: e.message || String(e) } }));
    }
  }, [selected, selectedTruck]);

  const handleDayChange = (e) => {
    const idx = parseInt(e.target.value, 10);
    const d = days[idx];
    if (d) {
      setSelected({ date: d.date, ruta: d.ruta });
      setRuns({});
    }
  };

  const handleAlgoChange = (e) => {
    setSelectedAlgo(e.target.value);
  };

  const handleTruckChange = (e) => {
    const v = e.target.value;
    setSelectedTruck(v === '' ? null : v);
    setRuns({});
  };

  const runBatch = useCallback(async () => {
    if (!selectedAlgo) return;
    setBatchStatus('loading');
    setBatchError(null);
    try {
      const payload = {
        algo: selectedAlgo,
        truckCode: selectedTruck || undefined,
        seed: batchSeed,
      };
      if (batchMode === 'selected') {
        if (!batchSelected.length) {
          throw new Error('Pick at least one dataset');
        }
        payload.cases = batchSelected;
        payload.mode = 'explicit';
      } else {
        payload.mode = batchMode;
        payload.n = batchN;
      }
      const data = await api.multiRun(payload);
      setBatchData(data);
      setBatchStatus('ok');
    } catch (e) {
      setBatchError(e.message || String(e));
      setBatchStatus('error');
    }
  }, [selectedAlgo, selectedTruck, batchMode, batchN, batchSeed, batchSelected]);

  const toggleBatchPick = (d) => {
    setBatchSelected((prev) => {
      const has = prev.some((p) => p.date === d.date && p.ruta === d.ruta);
      if (has) return prev.filter((p) => !(p.date === d.date && p.ruta === d.ruta));
      return [...prev, { date: d.date, ruta: d.ruta }];
    });
  };

  const runCompare = useCallback(async () => {
    if (!compareAlgoA || !compareAlgoB) return;
    if (compareAlgoA === compareAlgoB) {
      setCompareError('Pick two different algorithms');
      setCompareStatus('error');
      return;
    }
    setCompareStatus('loading');
    setCompareError(null);
    try {
      const payload = {
        algoA: compareAlgoA,
        algoB: compareAlgoB,
        truckCode: selectedTruck || undefined,
        seed: compareSeed,
      };
      if (compareMode === 'selected') {
        if (!compareSelected.length) {
          throw new Error('Pick at least one dataset');
        }
        payload.cases = compareSelected;
        payload.mode = 'explicit';
      } else {
        payload.mode = compareMode;
        payload.n = compareN;
      }
      const data = await api.multiCompare(payload);
      setCompareData(data);
      setCompareStatus('ok');
    } catch (e) {
      setCompareError(e.message || String(e));
      setCompareStatus('error');
    }
  }, [
    compareAlgoA, compareAlgoB, compareMode, compareN, compareSeed,
    compareSelected, selectedTruck,
  ]);

  const toggleComparePick = (d) => {
    setCompareSelected((prev) => {
      const has = prev.some((p) => p.date === d.date && p.ruta === d.ruta);
      if (has) return prev.filter((p) => !(p.date === d.date && p.ruta === d.ruta));
      return [...prev, { date: d.date, ruta: d.ruta }];
    });
  };

  const activeAlgo = algorithms.find((a) => a.name === selectedAlgo);

  return (
    <div className="algo-page">
      <div className="classification">
        <div className="cl-l">
          <span className="chip red">BEERANTIR · INTERNAL</span>
          <span className="chip">ALGORITHMS LAB</span>
          <span className="sep">/</span>
          <span>SIM API · {SIM_API_BASE}</span>
        </div>
        <div className="cl-r">
          <span>STATUS · {bootError ? 'API OFFLINE' : loadingBoot ? 'BOOTING' : 'OPERATIONAL'}</span>
          <span className="sep">·</span>
          <span>{algorithms.length} ALGOS · {days.length} CASES</span>
        </div>
      </div>

      <header className="algo-page-header">
        <div className="brand">
          <Link href="/">← Beer<span className="ant">antir</span></Link>
          <span className="page-title">Algorithms · head-to-head on real Damm days</span>
        </div>
        <div className="header-actions">
          <Link href="/console" className="btn-ghost">Console ↗</Link>
        </div>
      </header>

      <div className="controls">
        <div className="control-group">
          <label>Day</label>
          <select onChange={handleDayChange} disabled={!days.length}>
            {days.map((d, i) => (
              <option key={`${d.date}-${d.ruta}`} value={i}>
                {d.date} · {d.ruta} · {d.clients} clients · {d.lines} lines
              </option>
            ))}
          </select>
        </div>
        <div className="control-group">
          <label>Algorithm</label>
          <select onChange={handleAlgoChange} value={selectedAlgo || ''} disabled={!algorithms.length}>
            {algorithms.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <div className="control-group">
          <label>Truck</label>
          <select onChange={handleTruckChange} value={selectedTruck || ''} disabled={!trucks.length}>
            <option value="">auto (smallest fit)</option>
            {trucks.map((t) => (
              <option key={t.code} value={t.code}>
                {t.code} — {t.pallet_capacity} plt · {t.max_weight_kg} kg
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn-primary big"
          onClick={() => selectedAlgo && runOne(selectedAlgo)}
          disabled={!selected || !selectedAlgo || runs[selectedAlgo]?.status === 'loading'}
        >
          {runs[selectedAlgo]?.status === 'loading' ? 'Running…' : '▶ Run algorithm'}
        </button>
      </div>

      {bootError && (
        <div className="boot-error">
          <b>API unreachable.</b> Tried <code>{SIM_API_BASE}</code> — error: {bootError}
          <br />
          Start it with: <code>python3 -m simulator.api --port 8000</code>
        </div>
      )}

      <div className="batch-toggle-wrap">
        <button
          className="btn-ghost"
          onClick={() => setBatchOpen((v) => !v)}
        >
          {batchOpen ? '▾ Hide batch mode' : '▸ Batch mode — run on multiple datasets'}
        </button>
        <button
          className="btn-ghost"
          onClick={() => setCompareOpen((v) => !v)}
          style={{ marginLeft: 8 }}
        >
          {compareOpen ? '▾ Hide compare mode' : '▸ Compare mode — head-to-head on N datasets'}
        </button>
      </div>
      {batchOpen && (
        <BatchPanel
          algo={selectedAlgo}
          days={days}
          trucks={trucks}
          truckCode={selectedTruck}
          onTruckChange={setSelectedTruck}
          mode={batchMode}
          onModeChange={setBatchMode}
          n={batchN}
          onNChange={setBatchN}
          seed={batchSeed}
          onSeedChange={setBatchSeed}
          selected={batchSelected}
          onTogglePick={toggleBatchPick}
          status={batchStatus}
          error={batchError}
          data={batchData}
          onRun={runBatch}
        />
      )}
      {compareOpen && (
        <ComparePanel
          algorithms={algorithms}
          days={days}
          trucks={trucks}
          algoA={compareAlgoA}
          onAlgoAChange={setCompareAlgoA}
          algoB={compareAlgoB}
          onAlgoBChange={setCompareAlgoB}
          truckCode={selectedTruck}
          onTruckChange={setSelectedTruck}
          mode={compareMode}
          onModeChange={setCompareMode}
          n={compareN}
          onNChange={setCompareN}
          seed={compareSeed}
          onSeedChange={setCompareSeed}
          selected={compareSelected}
          onTogglePick={toggleComparePick}
          status={compareStatus}
          error={compareError}
          data={compareData}
          onRun={runCompare}
        />
      )}

      <main className="algo-grid">
        {activeAlgo && (
          <AlgorithmSection
            key={activeAlgo.name}
            algo={activeAlgo}
            run={runs[activeAlgo.name]}
            baselineKpis={undefined}
            onRun={runOne}
          />
        )}
      </main>

      <footer className="algo-footer">
        <span>BEERANTIR · ALGORITHMS LAB · v0.1 · BUILD 2026.05.09</span>
        <span>PICK A DAY AND ALGORITHM · RUN TO SEE KPIS</span>
      </footer>
    </div>
  );
}
