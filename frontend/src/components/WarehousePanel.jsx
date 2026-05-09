'use client';

import { useMemo } from 'react';
import warehouseGrid from '@/data/warehouse-grid.json';

const CELL_COLORS = {
  0: 'transparent',
  1: 'var(--wh-shelf)',
  2: 'var(--wh-compact)',
  3: 'var(--wh-rack)',
  4: 'var(--wh-floor)',
  9: 'var(--wh-special)',
};

const LEGEND = [
  { val: 1, label: 'Shelving', desc: 'Standard rack storage' },
  { val: 3, label: 'Rack (3-high)', desc: 'Triple-stack pallets' },
  { val: 4, label: 'Rack (4-high)', desc: 'Quad-stack pallets' },
  { val: 2, label: 'Compact', desc: 'Drive-in racking' },
  { val: 9, label: 'Special', desc: 'Staging / dispatch' },
];

const SUMMARY = {
  interior: 2055,
  exterior: 305,
  total: 2360,
};

export default function WarehousePanel() {
  const cellSize = 3.2;
  const rows = warehouseGrid.length;
  const cols = warehouseGrid[0].length;
  const w = cols * cellSize;
  const h = rows * cellSize;

  const counts = useMemo(() => {
    const c = {};
    for (const row of warehouseGrid) {
      for (const v of row) {
        if (v > 0) c[v] = (c[v] || 0) + 1;
      }
    }
    return c;
  }, []);

  return (
    <div className="panel warehouse-panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="panel-index">04</span>
          Warehouse
          <span className="panel-code">DDI MOLLET</span>
        </div>
        <div className="panel-readout">
          <span className="ro-row"><strong>{SUMMARY.total}</strong> PLT POSITIONS</span>
          <span className="ro-row ro-dim">{SUMMARY.interior} INT · {SUMMARY.exterior} EXT</span>
        </div>
      </div>

      <div className="wh-caption">
        <span>Top-down layout · {rows} × {cols} grid · Mollet del Vallès</span>
      </div>

      <div className="wh-grid-wrap">
        <svg
          viewBox={`0 0 ${w} ${h}`}
          className="wh-svg"
          preserveAspectRatio="xMidYMid meet"
        >
          {warehouseGrid.map((row, ri) =>
            row.map((val, ci) => {
              if (val === 0) return null;
              return (
                <rect
                  key={`${ri}-${ci}`}
                  x={ci * cellSize}
                  y={ri * cellSize}
                  width={cellSize}
                  height={cellSize}
                  fill={CELL_COLORS[val]}
                  stroke="var(--navy-10)"
                  strokeWidth={0.15}
                />
              );
            })
          )}
          <text x={w / 2} y={8} textAnchor="middle" className="wh-label-svg">LOADING DOCKS</text>
          <text x={w / 2} y={h - 3} textAnchor="middle" className="wh-label-svg">RECEIVING</text>
        </svg>
      </div>

      <div className="wh-legend">
        <div className="wh-legend-head">ZONE TYPES</div>
        <div className="wh-legend-items">
          {LEGEND.map(l => (
            <div key={l.val} className="wh-legend-item">
              <span className="wh-swatch" data-wh={l.val}></span>
              <span className="wh-legend-label">{l.label}</span>
              <span className="wh-legend-count">{counts[l.val] || 0}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="wh-stats">
        <div className="wh-stat">
          <span className="wh-stat-label">Shelving</span>
          <span className="wh-stat-value">1,194</span>
        </div>
        <div className="wh-stat">
          <span className="wh-stat-label">Compact</span>
          <span className="wh-stat-value">240</span>
        </div>
        <div className="wh-stat">
          <span className="wh-stat-label">Floor</span>
          <span className="wh-stat-value">621</span>
        </div>
        <div className="wh-stat">
          <span className="wh-stat-label">Exterior</span>
          <span className="wh-stat-value">305</span>
        </div>
      </div>
    </div>
  );
}
