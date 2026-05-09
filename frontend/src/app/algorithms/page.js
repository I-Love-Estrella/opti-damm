'use client';

import { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { api, SIM_API_BASE } from '@/lib/api';
import TruckLoadView from '@/components/TruckLoadView';
import StopTimeline from '@/components/StopTimeline';
import StepPlayer from '@/components/StepPlayer';
import ActionLog from '@/components/ActionLog';
import { cargoStateAt, flattenStages, buildLiftReplaceMap } from '@/lib/cargoState';
import './algorithms.css';

const RouteMap = dynamic(() => import('@/components/RouteMap'), { ssr: false });
const Truck3D = dynamic(() => import('@/components/Truck3D'), { ssr: false });

function Playback3D({ run }) {
  const stops = run?.data?.stops || [];
  const initialCargo = run?.data?.initial_cargo || [];
  const truck = run?.data?.truck;

  const flatStages = useMemo(() => flattenStages(stops), [stops]);
  const liftMaps = useMemo(() => buildLiftReplaceMap(flatStages), [flatStages]);
  const [idx, setIdx] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const rootRef = useRef(null);

  const state = useMemo(
    () => cargoStateAt(initialCargo, flatStages, idx),
    [initialCargo, flatStages, idx],
  );

  if (!truck || flatStages.length === 0) return null;

  return (
    <div className="playback3d" ref={rootRef}>
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

  return (
    <section className="algo-section">
      <header className="algo-header">
        <div className="algo-headline">
          <span className="algo-tag">ALGO</span>
          <h2 className="algo-name">{algo.name}</h2>
          <StatusPill status={status} />
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

          {run?.data?.initial_cargo?.length > 0 && run?.data?.truck && (
            <div className="viz-section">
              <h4>Initial truck loading (after depot, before driving)</h4>
              <TruckLoadView
                truck={run.data.truck}
                cargo={run.data.initial_cargo}
                clientNames={Object.fromEntries((run.data.stops || []).map((s) => [s.client_id, s.name]))}
              />
            </div>
          )}

          {run?.data?.stops?.some((s) => (s.stages || []).length > 0) && (
            <div className="viz-section">
              <h4>Per-stop unload timeline (each box is a stage)</h4>
              <StopTimeline
                stops={run.data.stops}
                clientNames={Object.fromEntries((run.data.stops || []).map((s) => [s.client_id, s.name]))}
              />
            </div>
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
  const [days, setDays] = useState([]);
  const [selected, setSelected] = useState(null); // {date, ruta}
  const [runs, setRuns] = useState({});           // { algoName: {status, data, error} }
  const [bootError, setBootError] = useState(null);
  const [loadingBoot, setLoadingBoot] = useState(true);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [a, d] = await Promise.all([
          api.algorithms(),
          api.days({ minClients: 5, head: 50 }),
        ]);
        if (dead) return;
        setAlgorithms(a.algorithms || []);
        setDays(d.items || []);
        if (d.items?.length > 0) {
          setSelected({ date: d.items[0].date, ruta: d.items[0].ruta });
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

  const baselineName = algorithms[0]?.name;
  const baselineKpis = useMemo(
    () => (baselineName ? runs[baselineName]?.data?.kpis : undefined),
    [baselineName, runs]
  );

  KpiCard.baselineLabel = baselineName || 'baseline';

  const runOne = useCallback(async (algoName) => {
    if (!selected) return;
    setRuns(prev => ({ ...prev, [algoName]: { status: 'loading' } }));
    try {
      const data = await api.run({ date: selected.date, ruta: selected.ruta, algo: algoName });
      setRuns(prev => ({ ...prev, [algoName]: { status: 'ok', data } }));
    } catch (e) {
      setRuns(prev => ({ ...prev, [algoName]: { status: 'error', error: e.message || String(e) } }));
    }
  }, [selected]);

  const runAll = useCallback(async () => {
    if (!selected || algorithms.length === 0) return;
    for (const a of algorithms) {
      // sequential — keep API ordering predictable
      await runOne(a.name);
    }
  }, [selected, algorithms, runOne]);

  const handleDayChange = (e) => {
    const idx = parseInt(e.target.value, 10);
    const d = days[idx];
    if (d) {
      setSelected({ date: d.date, ruta: d.ruta });
      setRuns({});
    }
  };

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
        <button className="btn-primary big" onClick={runAll} disabled={!selected || algorithms.length === 0}>
          ▶ Run all algorithms
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
        {algorithms.map(algo => (
          <AlgorithmSection
            key={algo.name}
            algo={algo}
            run={runs[algo.name]}
            baselineKpis={algo.name === baselineName ? undefined : baselineKpis}
            onRun={runOne}
          />
        ))}
      </main>

      <footer className="algo-footer">
        <span>BEERANTIR · ALGORITHMS LAB · v0.1 · BUILD 2026.05.09</span>
        <span>FIRST ALGO IS THE BASELINE · DELTAS COMPUTED VS IT</span>
      </footer>
    </div>
  );
}
