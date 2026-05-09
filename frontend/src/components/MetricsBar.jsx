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
  const v = useCountUp(value);
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

export default function MetricsBar({ weights, onWeightChange, metrics }) {
  function fmtTime(h) {
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    return `${hh}:${String(mm).padStart(2, '0')}`;
  }

  const captions = {
    route: "minimize distance",
    load:  "maximize utilization",
    unload: "minimize per-stop time",
  };
  const labels = {
    route: "Route",
    load:  "Load",
    unload: "Unload",
  };

  return (
    <div className="metrics">
      <div className="weights">
        <div className="weights-head">Optimization weights — make the trade-offs visible</div>
        <div className="sliders">
          {["route", "load", "unload"].map(key => (
            <div key={key} className="slider-block">
              <div className="slider-row">
                <span className="slider-label">{labels[key]}</span>
                <span className="slider-value">{weights[key]}%</span>
              </div>
              <input
                type="range"
                min="0" max="100" step="1"
                value={weights[key]}
                onChange={(e) => onWeightChange(key, parseInt(e.target.value))}
                className="slider"
              />
              <div className="slider-caption">{captions[key]}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="stats">
        <div className="stats-head">Live metrics</div>
        <div className="stats-grid">
          <Stat label="Distance" value={metrics.distance} unit="km" />
          <Stat label="Time"     value={metrics.time}     unit="h" format={fmtTime} />
          <Stat label="Stops"    value={metrics.stops} />
          <Stat label="Score"    value={metrics.score}    unit="/100" />
        </div>
      </div>
    </div>
  );
}
