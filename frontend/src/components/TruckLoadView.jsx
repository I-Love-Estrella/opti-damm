'use client';

import { useMemo } from 'react';

// Deterministic color from a string (client_id) — golden-angle hue spread.
function clientColor(id) {
  if (!id) return '#3a3a3a';
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const hue = (h * 137.508) % 360;
  return `oklch(72% 0.16 ${hue.toFixed(1)})`;
}

function clientShortId(id, names) {
  if (!id) return '';
  const name = names?.[id];
  if (name) {
    const trimmed = name.split(/\s+/).slice(0, 2).join(' ');
    return trimmed.length > 14 ? trimmed.slice(0, 14) + '…' : trimmed;
  }
  return id.slice(-6);
}

function PalletGrid({ pallet, clientNames }) {
  const layout = pallet?.layout;
  if (!layout) return null;

  // Build (col_x, col_y) → max_top_level among items in that column.
  const cells = useMemo(() => {
    const grid = {};
    for (let cx = 0; cx < layout.cols_x; cx++) {
      for (let cy = 0; cy < layout.cols_y; cy++) {
        grid[`${cx},${cy}`] = { items: [], top: 0 };
      }
    }
    for (const it of pallet.items || []) {
      const k = `${it.col_x},${it.col_y}`;
      if (!grid[k]) grid[k] = { items: [], top: 0 };
      grid[k].items.push(it);
      grid[k].top = Math.max(grid[k].top, it.bottom_level + it.stack_size);
    }
    return grid;
  }, [pallet, layout.cols_x, layout.cols_y]);

  // For each cell, dominant client = the client at the topmost item there.
  return (
    <div
      className="pallet-grid"
      style={{
        gridTemplateColumns: `repeat(${layout.cols_x}, 1fr)`,
        gridTemplateRows: `repeat(${layout.cols_y}, 1fr)`,
      }}
    >
      {/* col_y rows from edge (0) to deep (max). Render row 0 at top = closest to door. */}
      {Array.from({ length: layout.cols_y }, (_, cy) =>
        Array.from({ length: layout.cols_x }, (_, cx) => {
          const cell = cells[`${cx},${cy}`] || { items: [], top: 0 };
          const topItem = cell.items.length
            ? cell.items.slice().sort((a, b) => b.bottom_level - a.bottom_level)[0]
            : null;
          const fillRatio = layout.max_level > 0 ? Math.min(1, cell.top / layout.max_level) : 0;
          const color = topItem ? clientColor(topItem.intended_client) : 'transparent';
          const stackUnits = cell.items.reduce((acc, it) => acc + it.stack_size, 0);
          return (
            <div
              key={`${cx}-${cy}`}
              className="pcell"
              style={{
                background: topItem ? `linear-gradient(180deg, ${color} 0%, ${color} ${fillRatio * 100}%, #0a0a0a ${fillRatio * 100}%, #0a0a0a 100%)` : '#0a0a0a',
                borderColor: cy === 0 ? '#fc0' : '#2a2a2a',
              }}
              title={
                topItem
                  ? `Col (${cx},${cy}) · stack ${stackUnits}/${layout.max_level}\nTop: ${topItem.sku} qty=${topItem.qty} → ${topItem.intended_client || 'empty'}`
                  : `Col (${cx},${cy}) · empty`
              }
            >
              {topItem && (
                <>
                  <span className="pcell-stack">{stackUnits}</span>
                  <span className="pcell-client">{clientShortId(topItem.intended_client, clientNames)}</span>
                </>
              )}
            </div>
          );
        })
      ).flat()}
    </div>
  );
}

function ColumnStackProfile({ pallet }) {
  const layout = pallet?.layout;
  if (!layout) return null;

  // Side view: draw each column as a vertical bar of stacked colored segments.
  return (
    <div className="stack-profile" style={{ gridTemplateColumns: `repeat(${layout.cols_x * layout.cols_y}, 1fr)` }}>
      {Array.from({ length: layout.cols_y }, (_, cy) =>
        Array.from({ length: layout.cols_x }, (_, cx) => {
          const colItems = (pallet.items || [])
            .filter((it) => it.col_x === cx && it.col_y === cy)
            .sort((a, b) => a.bottom_level - b.bottom_level);
          const labelKey = `${cx}-${cy}`;
          return (
            <div key={labelKey} className="stack-col">
              <div className="stack-col-bar">
                {Array.from({ length: layout.max_level }, (_, lvl) => {
                  const filled = colItems.find(
                    (it) => lvl >= it.bottom_level && lvl < it.bottom_level + it.stack_size,
                  );
                  return (
                    <div
                      key={lvl}
                      className={`stack-cell${filled ? ' filled' : ''}`}
                      style={filled ? { background: clientColor(filled.intended_client) } : undefined}
                      title={
                        filled
                          ? `Lvl ${lvl} · ${filled.sku} · ${filled.intended_client || 'empty'}`
                          : `Lvl ${lvl} · empty`
                      }
                    />
                  );
                }).reverse()}
              </div>
              <div className="stack-col-label">
                {cx},{cy}
              </div>
            </div>
          );
        })
      ).flat()}
    </div>
  );
}

function SlotCard({ entry, clientNames }) {
  const empty = !entry.pallet;
  const cls = entry.pallet?.pallet_class;
  const items = entry.pallet?.items || [];
  const clientList = useMemo(() => {
    const set = new Set();
    items.forEach((it) => it.intended_client && set.add(it.intended_client));
    return Array.from(set);
  }, [items]);

  return (
    <div className={`slot-card${empty ? ' slot-empty' : ''}`}>
      <div className="slot-head">
        <span className="slot-id">{entry.slot_id}</span>
        <span className="slot-side">{entry.side}-side</span>
        {!empty && (
          <span className={`slot-class slot-class-${cls}`}>{(cls || 'unknown').toUpperCase()}</span>
        )}
      </div>
      {empty ? (
        <div className="slot-empty-msg">empty</div>
      ) : (
        <>
          <PalletGrid pallet={entry.pallet} clientNames={clientNames} />
          <ColumnStackProfile pallet={entry.pallet} />
          <div className="slot-meta">
            <span>
              <b>Items</b> {items.length}
            </span>
            <span>
              <b>Clients</b> {clientList.length}
            </span>
            <span>
              <b>Vol</b> {(entry.pallet.volume_m3 || 0).toFixed(2)}m³
            </span>
            <span>
              <b>Wt</b> {(entry.pallet.weight_kg || 0).toFixed(0)}kg
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export default function TruckLoadView({ truck, cargo = [], clientNames = {} }) {
  if (!truck || !cargo.length) return null;

  // Group slots by side for layout: L (left), R (right), B (back)
  const left = cargo.filter((c) => c.side === 'L');
  const right = cargo.filter((c) => c.side === 'R');
  const back = cargo.filter((c) => c.side === 'B');

  return (
    <div className="truck-view">
      <div className="truck-view-head">
        <span className="truck-tag">TRUCK · {truck.code}</span>
        <span>Capacity: {truck.pallet_capacity} pallets · {truck.max_weight_kg} kg</span>
        <span className="truck-edge-hint">↑ truck edge / door</span>
      </div>
      <div className="truck-rows">
        {left.length > 0 && (
          <div className="truck-row">
            <div className="row-label">L (left)</div>
            <div className="row-slots">
              {left.map((c) => (
                <SlotCard key={c.slot_id} entry={c} clientNames={clientNames} />
              ))}
            </div>
          </div>
        )}
        {right.length > 0 && (
          <div className="truck-row">
            <div className="row-label">R (right)</div>
            <div className="row-slots">
              {right.map((c) => (
                <SlotCard key={c.slot_id} entry={c} clientNames={clientNames} />
              ))}
            </div>
          </div>
        )}
        {back.length > 0 && (
          <div className="truck-row">
            <div className="row-label">B (back)</div>
            <div className="row-slots">
              {back.map((c) => (
                <SlotCard key={c.slot_id} entry={c} clientNames={clientNames} />
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="truck-legend">
        <span>Top-down view (top row = door edge, bottom row = deep)</span>
        <span>Number = stack height in column · color = client</span>
      </div>
    </div>
  );
}
