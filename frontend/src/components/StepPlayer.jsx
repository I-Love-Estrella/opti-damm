'use client';

import { useEffect, useMemo, useRef } from 'react';

const KIND_META = {
  ARRIVE:           { icon: '⮕', label: 'Arrive',           tone: 'info'    },
  SERVICE_BASE:     { icon: '◷', label: 'Service base',     tone: 'neutral' },
  BLOCKER_LIFT:     { icon: '↑', label: 'Lift blocker',     tone: 'warn'    },
  TARGET_TAKE:      { icon: '★', label: 'Take target',      tone: 'good'    },
  BLOCKER_REPLACE:  { icon: '↓', label: 'Replace blocker',  tone: 'orange'  },
  UNLOAD:           { icon: '✓', label: 'Line complete',    tone: 'good'    },
  DROP:             { icon: '✕', label: 'Drop',             tone: 'bad'     },
  PICKUP_RETURN:    { icon: '⌫', label: 'Pickup empties',   tone: 'purple'  },
};

function fmtMin(t) {
  if (t === undefined || t === null) return '—';
  const m = Math.floor(t);
  const s = Math.round((t - m) * 60);
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

export default function StepPlayer({
  stages = [],
  idx = 0,
  onIdxChange,
  isPlaying = false,
  onPlayingChange,
  liftMaps = { replaceMap: new Map(), deliveryMap: new Map() },
  speed = 1.0,
  onSpeedChange,
  rootRef,
}) {
  const total = stages.length;
  const safeIdx = Math.min(Math.max(idx, 0), total);
  const current = safeIdx > 0 ? stages[safeIdx - 1] : null;
  const next = safeIdx < total ? stages[safeIdx] : null;
  const playInterval = useRef(null);

  const meta = current ? KIND_META[current.kind] || { icon: '·', label: current.kind, tone: 'neutral' } : null;

  useEffect(() => {
    if (!isPlaying) {
      if (playInterval.current) {
        clearInterval(playInterval.current);
        playInterval.current = null;
      }
      return;
    }
    const tickMs = Math.max(50, 350 / speed);
    playInterval.current = setInterval(() => {
      onIdxChange((curr) => {
        if (curr >= total) {
          onPlayingChange(false);
          return curr;
        }
        return curr + 1;
      });
    }, tickMs);
    return () => {
      if (playInterval.current) clearInterval(playInterval.current);
    };
  }, [isPlaying, speed, total, onIdxChange, onPlayingChange]);

  useEffect(() => {
    function onKey(e) {
      const target = e.target;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (rootRef?.current && !rootRef.current.contains(document.activeElement) &&
          !rootRef.current.matches(':hover') && !rootRef.current.contains(e.target)) {
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        onIdxChange((c) => Math.min(total, c + 1));
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        onIdxChange((c) => Math.max(0, c - 1));
      } else if (e.key === ' ') {
        e.preventDefault();
        onPlayingChange(!isPlaying);
      } else if (e.key === 'Home') {
        e.preventDefault();
        onIdxChange(0);
      } else if (e.key === 'End') {
        e.preventDefault();
        onIdxChange(total);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [total, isPlaying, onIdxChange, onPlayingChange, rootRef]);

  const futureInfo = useMemo(() => {
    if (!current || current.kind !== 'BLOCKER_LIFT') return null;
    const replaceMap = liftMaps?.replaceMap || new Map();
    const deliveryMap = liftMaps?.deliveryMap || new Map();
    const replaceSeq = replaceMap.get(current.seq);
    const deliverySeq = deliveryMap.get(current.seq);
    if (replaceSeq !== undefined) {
      const idx = stages.findIndex((s) => s.seq === replaceSeq);
      if (idx === -1) return null;
      return { kind: 'replace', targetIdx: idx + 1, stagesUntil: idx - (safeIdx - 1) };
    }
    if (deliverySeq !== undefined) {
      const idx = stages.findIndex((s) => s.seq === deliverySeq);
      if (idx === -1) return null;
      return { kind: 'delivery', targetIdx: idx + 1, stagesUntil: idx - (safeIdx - 1) };
    }
    return null;
  }, [current, safeIdx, stages, liftMaps]);

  return (
    <div className="step-player">
      <div className="sp-controls">
        <button
          className="sp-btn"
          onClick={() => onIdxChange(0)}
          disabled={safeIdx === 0}
          title="Reset (Home)"
        >
          ⏮
        </button>
        <button
          className="sp-btn"
          onClick={() => onIdxChange((c) => Math.max(0, c - 1))}
          disabled={safeIdx === 0}
          title="Prev (←)"
        >
          ←
        </button>
        <button
          className={`sp-btn sp-play${isPlaying ? ' sp-playing' : ''}`}
          onClick={() => onPlayingChange(!isPlaying)}
          title="Play/Pause (Space)"
        >
          {isPlaying ? '❚❚' : '▶'}
        </button>
        <button
          className="sp-btn"
          onClick={() => onIdxChange((c) => Math.min(total, c + 1))}
          disabled={safeIdx >= total}
          title="Next (→)"
        >
          →
        </button>
        <button
          className="sp-btn"
          onClick={() => onIdxChange(total)}
          disabled={safeIdx >= total}
          title="End (End)"
        >
          ⏭
        </button>

        <div className="sp-progress">
          <input
            type="range"
            min={0}
            max={total}
            value={safeIdx}
            onChange={(e) => onIdxChange(parseInt(e.target.value, 10))}
            className="sp-slider"
          />
          <span className="sp-counter">{safeIdx} / {total}</span>
        </div>

        <div className="sp-speed">
          <label>Speed</label>
          <select value={speed} onChange={(e) => onSpeedChange(parseFloat(e.target.value))}>
            <option value={0.5}>0.5×</option>
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={4}>4×</option>
            <option value={8}>8×</option>
          </select>
        </div>
      </div>

      <div className={`sp-card${meta ? ` sp-tone-${meta.tone}` : ''}`}>
        {!current && (
          <div className="sp-card-empty">
            <span className="sp-card-title">Initial state</span>
            <span className="sp-card-desc">Truck loaded at depot, ready to drive.</span>
          </div>
        )}
        {current && (
          <>
            <div className="sp-card-head">
              <span className="sp-card-icon">{meta.icon}</span>
              <span className="sp-card-kind">{meta.label}</span>
              {current.stop_visit_seq !== undefined && (
                <span className="sp-card-stop">Stop #{current.stop_visit_seq} · {current.stop_client_name || current.stop_client_id}</span>
              )}
              <span className="sp-card-times">
                <span><b>Sim time</b> {fmtMin(current.t_min)}</span>
                <span><b>Step Δ</b> +{fmtMin(current.time_min)}</span>
              </span>
            </div>
            <div className="sp-card-body">
              <div className="sp-card-desc">{current.description}</div>
              {current.detail?.intended_client && (
                <div className="sp-card-detail">
                  <b>Box belongs to client</b> {current.detail.intended_client}
                </div>
              )}
              {current.detail?.target_sku && current.kind === 'BLOCKER_LIFT' && (
                <div className="sp-card-detail">
                  <b>Lifted to reach</b> {current.detail.target_sku} for {current.detail.target_client}
                </div>
              )}
              {current.detail?.col_x !== undefined && (
                <div className="sp-card-detail">
                  <b>Position</b> slot {current.detail.slot_id} · col ({current.detail.col_x},{current.detail.col_y}) · level {current.detail.bottom_level}
                </div>
              )}
              {current.detail?.reason && (
                <div className="sp-card-detail sp-card-reason">
                  <b>Reason</b> {current.detail.reason}
                </div>
              )}
              {futureInfo?.kind === 'replace' && (
                <div className="sp-card-detail sp-card-future">
                  <b>Will be put back</b> in {futureInfo.stagesUntil} step(s) — at stage {futureInfo.targetIdx}
                </div>
              )}
              {futureInfo?.kind === 'delivery' && (
                <div className="sp-card-detail sp-card-future">
                  <b>Will be delivered to client</b> in {futureInfo.stagesUntil} step(s) — at stage {futureInfo.targetIdx} (no replace, same client)
                </div>
              )}
              {current.kind === 'TARGET_TAKE' && (
                <div className="sp-card-detail sp-card-future">
                  <b>Outcome</b> Box leaves the truck — delivered to client, never returns.
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {next && (
        <div className="sp-next">
          <span><b>Next</b> {next.description}</span>
          <span className="sp-next-time">+{fmtMin(next.time_min)}</span>
        </div>
      )}
    </div>
  );
}
