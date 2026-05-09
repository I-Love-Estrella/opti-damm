'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { api, SIM_API_BASE } from '@/lib/api';
import StopTimeline from '@/components/StopTimeline';
import StepPlayer from '@/components/StepPlayer';
import ActionLog from '@/components/ActionLog';
import ValidationPanel from '@/components/ValidationPanel';
import { cargoStateAt, flattenStages, buildLiftReplaceMap } from '@/lib/cargoState';
import './algorithms.css';

const RouteMap = dynamic(() => import('@/components/RouteMap'), { ssr: false });
const Truck3D = dynamic(() => import('@/components/Truck3D'), { ssr: false });

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
      <Truck3D
        truck={truck}
        palletsBySlot={state.palletsBySlot}
        boxes={state.boxes}
        highlightSeq={idx > 0 ? idx - 1 : undefined}
        height={480}
      />
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
  { key: 'drive_minutes',    label: 'Drive minutes',    fmt: (v) => v.toFixed(1),     unit: 'min',   lowerBetter: true },
  { key: 'service_minutes',  label: 'Service minutes',  fmt: (v) => v.toFixed(1),     unit: 'min',   lowerBetter: true },
  { key: 'total_km',         label: 'Distance',         fmt: (v) => v.toFixed(1),     unit: 'km',    lowerBetter: true },
  { key: 'search_moves',     label: 'Search moves',     fmt: (v) => Math.round(v),    unit: 'units', lowerBetter: true, highlight: true },
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
