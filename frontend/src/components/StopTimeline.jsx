'use client';

import { useMemo, useState } from 'react';

const KIND_META = {
  ARRIVE:           { icon: '⮕', cls: 'st-arrive',  label: 'Arrive' },
  SERVICE_BASE:     { icon: '◷', cls: 'st-service', label: 'Service base' },
  BLOCKER_LIFT:     { icon: '↑', cls: 'st-lift',    label: 'Lift blocker' },
  TARGET_TAKE:      { icon: '★', cls: 'st-take',    label: 'Take target' },
  BLOCKER_REPLACE:  { icon: '↓', cls: 'st-replace', label: 'Replace blocker' },
  UNLOAD:           { icon: '✓', cls: 'st-unload',  label: 'Line complete' },
  DROP:             { icon: '✕', cls: 'st-drop',    label: 'Drop' },
  PICKUP_RETURN:    { icon: '⌫', cls: 'st-pickup',  label: 'Pickup empties' },
};

function clientColor(id) {
  if (!id) return '#3a3a3a';
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const hue = (h * 137.508) % 360;
  return `oklch(72% 0.16 ${hue.toFixed(1)})`;
}

function fmtMin(t) {
  if (t === undefined || t === null) return '—';
  const m = Math.floor(t);
  const s = Math.round((t - m) * 60);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

function StageRow({ stage, clientNames }) {
  const meta = KIND_META[stage.kind] || { icon: '·', cls: '', label: stage.kind };
  const blockerClient = stage.detail?.intended_client;
  const targetClient = stage.detail?.target_client;
  const swatchColor =
    stage.kind === 'BLOCKER_LIFT' || stage.kind === 'BLOCKER_REPLACE'
      ? clientColor(blockerClient)
      : stage.kind === 'TARGET_TAKE'
        ? clientColor(targetClient)
        : null;

  return (
    <div className={`stage-row ${meta.cls}`}>
      <span className="stage-icon">{meta.icon}</span>
      <span className="stage-time">{stage.t_min.toFixed(2)}m</span>
      <span className="stage-delta">+{fmtMin(stage.time_min)}</span>
      {swatchColor && <span className="stage-swatch" style={{ background: swatchColor }} />}
      <span className="stage-desc">{stage.description}</span>
      {stage.detail?.col_x !== undefined && (
        <span className="stage-pos">
          ({stage.detail.col_x},{stage.detail.col_y})
          {stage.detail.bottom_level !== undefined && ` · lvl ${stage.detail.bottom_level}`}
        </span>
      )}
    </div>
  );
}

function PhaseSummary({ stages }) {
  const counts = useMemo(() => {
    const out = { lift: 0, take: 0, replace: 0, drops: 0, pickups: 0 };
    let liftTime = 0;
    let takeTime = 0;
    let replaceTime = 0;
    for (const s of stages) {
      if (s.kind === 'BLOCKER_LIFT') {
        out.lift += s.detail?.qty || 0;
        liftTime += s.time_min || 0;
      } else if (s.kind === 'BLOCKER_REPLACE') {
        out.replace += s.detail?.qty || 0;
        replaceTime += s.time_min || 0;
      } else if (s.kind === 'TARGET_TAKE') {
        out.take += 1;
        takeTime += s.time_min || 0;
      } else if (s.kind === 'DROP') {
        out.drops += 1;
      } else if (s.kind === 'PICKUP_RETURN') {
        out.pickups += 1;
      }
    }
    return { ...out, liftTime, takeTime, replaceTime };
  }, [stages]);

  return (
    <div className="phase-summary">
      <span><b>Lifts</b> {counts.lift} ({fmtMin(counts.liftTime)})</span>
      <span><b>Takes</b> {counts.take} ({fmtMin(counts.takeTime)})</span>
      <span><b>Replaces</b> {counts.replace} ({fmtMin(counts.replaceTime)})</span>
      {counts.pickups > 0 && <span><b>Pickups</b> {counts.pickups}</span>}
      {counts.drops > 0 && <span className="bad"><b>Drops</b> {counts.drops}</span>}
    </div>
  );
}

export default function StopTimeline({ stops = [], clientNames = {} }) {
  const stopsWithTrace = stops.filter((s) => (s.stages || []).length > 0);
  const [activeIdx, setActiveIdx] = useState(0);

  if (stopsWithTrace.length === 0) {
    return <div className="timeline-empty">No per-stop trace available — re-run the algorithm.</div>;
  }

  const safeIdx = Math.min(activeIdx, stopsWithTrace.length - 1);
  const stop = stopsWithTrace[safeIdx];

  return (
    <div className="stop-timeline">
      <div className="stop-tabs">
        {stopsWithTrace.map((s, i) => (
          <button
            key={s.client_id}
            className={`stop-tab${i === safeIdx ? ' active' : ''}`}
            onClick={() => setActiveIdx(i)}
            title={`${s.name || s.client_id} · arrive ${s.arrive_t_min?.toFixed?.(1) ?? '?'} min`}
          >
            <span className="stop-tab-num">#{s.visit_seq}</span>
            <span className="stop-tab-name">{(s.name || s.client_id).slice(0, 18)}</span>
            <span className="stop-tab-dwell">{fmtMin(s.dwell_min)}</span>
          </button>
        ))}
      </div>

      <div className="stop-detail">
        <div className="stop-detail-head">
          <div>
            <div className="stop-detail-name">
              <span className="stop-detail-num">#{stop.visit_seq}</span>
              {stop.name || stop.client_id}
            </div>
            <div className="stop-detail-sub">
              {stop.client_id} · {stop.city || ''} {stop.cp || ''}
            </div>
          </div>
          <div className="stop-detail-times">
            <span><b>Arrive</b> {fmtMin(stop.arrive_t_min)}</span>
            <span><b>Depart</b> {fmtMin(stop.depart_t_min)}</span>
            <span><b>Dwell</b> {fmtMin(stop.dwell_min)}</span>
          </div>
        </div>

        <PhaseSummary stages={stop.stages} />

        <div className="stage-list">
          {stop.stages.map((stg, i) => (
            <StageRow key={`${stg.seq}-${i}`} stage={stg} clientNames={clientNames} />
          ))}
        </div>
      </div>
    </div>
  );
}
