'use client';

import React from 'react';
import { SKU_TONE, STOP_TONE, CLIENT_ORDERS, TRUCK_TYPES } from '@/data';

const TRUCK_MODES = [
  { key: "reference", label: "By Reference" },
  { key: "client",    label: "By Client" },
  { key: "hybrid",    label: "Hybrid" },
];

const MODE_HELP = {
  reference: "same SKU together — fastest pick at depot",
  client:    "one cluster per client — fastest unload",
  hybrid:    "client clusters, heavies near cab — best balance",
};

function skuName(s) {
  return ({ EST: "Estrella 33cl", VOL: "Voll-Damm 33cl", MAL: "Malquerida 75cl", MOR: "Moritz 33cl", AGV: "Agua 1.5L" })[s] || s;
}

function shortClient(c) {
  if (!c) return "";
  return c.length > 12 ? c.slice(0, 11) + "…" : c;
}

export default function TruckPanel({ mode, onModeChange, pallets, hoveredStop, onPalletHover, hoveredPallet, onPalletClick, selectedClient, truckType, onTruckTypeChange }) {
  const spec = TRUCK_TYPES[truckType] || TRUCK_TYPES.T6;
  const totalCells = pallets.length;
  const filled = pallets.filter(p => p.sku).length;
  const utilization = Math.round((filled / totalCells) * 100);
  const totalWt = pallets.reduce((acc, p) => acc + (p.wt || 0), 0);
  const rows = Math.ceil(spec.capacity / spec.cols);

  function tone(p) {
    if (!p.sku) return null;
    if (mode === "reference") return SKU_TONE[p.sku] || "1";
    return STOP_TONE[p.stop] || "1";
  }

  const legendItems = (() => {
    const seen = new Map();
    pallets.forEach(p => {
      if (!p.sku) return;
      const key = mode === "reference" ? p.sku : p.stop;
      if (seen.has(key)) {
        const v = seen.get(key); v.count++; seen.set(key, v);
      } else {
        seen.set(key, {
          tone: tone(p),
          label: mode === "reference" ? p.sku : `S-0${p.stop}`,
          sub:   mode === "reference" ? skuName(p.sku) : (p.client || ""),
          count: 1,
        });
      }
    });
    return Array.from(seen.values());
  })();

  const orders = selectedClient ? CLIENT_ORDERS[selectedClient.name] : null;

  return (
    <div className="panel truck-panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="panel-index">02</span>
          Load
          <span className="panel-code">{spec.code} · {spec.capacity} PLT</span>
        </div>
        <div className="panel-readout">
          <span className="ro-row"><strong>{utilization}%</strong> · {filled}/{totalCells} PLT</span>
          <span className="ro-row ro-dim">{totalWt} KG / {spec.maxKg} KG</span>
        </div>
      </div>

      <div className="truck-type-toggle">
        {Object.values(TRUCK_TYPES).map(t => (
          <button
            key={t.code}
            className={`truck-type-btn ${truckType === t.code ? 'active' : ''}`}
            onClick={() => onTruckTypeChange(t.code)}
          >
            {t.code}
            <span className="tt-sub">{t.capacity}P · ×{t.fleet}</span>
          </button>
        ))}
      </div>

      <div className="mode-toggle">
        {TRUCK_MODES.map(m => (
          <button
            key={m.key}
            className={`mode-btn ${mode === m.key ? 'active' : ''}`}
            onClick={() => onModeChange(m.key)}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="mode-help">{MODE_HELP[mode]}</div>

      <div className="truck-caption">
        <span>Top-down view · {spec.cols} × {rows} grid · {spec.capacity} pallet slots</span>
        <span className="t-sub">colour = {mode === "reference" ? "SKU" : "destination stop"}</span>
      </div>

      <div className="truck-wrap">
        <span className="truck-label tl-cab">↑ CAB · FRONT</span>
        <span className="truck-label tl-rear">REAR DOORS ↓</span>
        <div className="pallet-grid" style={{ gridTemplateColumns: `repeat(${spec.cols}, 1fr)` }}>
          {pallets.map(p => {
            const isHL = hoveredStop && p.stop === hoveredStop.id;
            return (
              <div
                key={`${truckType}-${mode}-${p.idx}`}
                className={`pallet ${!p.sku ? 'empty' : ''} ${isHL ? 'highlight' : ''}`}
                data-tone={tone(p)}
                onMouseEnter={() => p.sku && onPalletHover && onPalletHover(p)}
                onMouseLeave={() => onPalletHover && onPalletHover(null)}
                onClick={() => p.sku && onPalletClick && onPalletClick(p)}
              >
                {p.sku ? (
                  <>
                    <div className="p-top">
                      <span className="p-stop-ring">{p.stop}</span>
                      <span className="p-sku">{p.sku}</span>
                    </div>
                    <div className="p-client">{shortClient(p.client)}</div>
                    {p.ret && <div className="p-recycle" title="returnable">↻</div>}
                  </>
                ) : (
                  <div className="p-empty-label">empty slot</div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="legend">
        <div className="legend-head">
          {mode === "reference" ? "BY SKU" : "BY DESTINATION"}
        </div>
        <div className="legend-items">
          {legendItems.map((it, i) => (
            <div key={i} className="legend-item">
              <span className="legend-swatch" data-tone={it.tone}></span>
              <span className="legend-label">{it.label}</span>
              <span className="legend-sub">{it.sub}</span>
              <span className="legend-count">×{it.count}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="manifest">
        <div className="manifest-head">
          <span>ID</span>
          <span>CLIENT</span>
          <span>STOP</span>
          <span>KG</span>
          <span>RET</span>
        </div>
        {pallets.map(p => {
          const isHL = (hoveredPallet && hoveredPallet.idx === p.idx) ||
                       (hoveredStop && p.stop === hoveredStop.id);
          if (!p.sku) {
            return (
              <div key={`mf-${truckType}-${mode}-${p.idx}`} className="manifest-row empty">
                <span className="m-code">{p.code}</span>
                <span className="m-client">— empty —</span>
                <span className="m-stop">—</span>
                <span className="m-wt">—</span>
                <span className="m-ret">—</span>
              </div>
            );
          }
          return (
            <div
              key={`mf-${truckType}-${mode}-${p.idx}`}
              className={`manifest-row ${isHL ? 'highlight' : ''}`}
              onMouseEnter={() => onPalletHover && onPalletHover(p)}
              onMouseLeave={() => onPalletHover && onPalletHover(null)}
              onClick={() => onPalletClick && onPalletClick(p)}
            >
              <span className="m-code">{p.code} · {p.sku}</span>
              <span className="m-client">{p.client}</span>
              <span className="m-stop">S-0{p.stop}</span>
              <span className="m-wt">{p.wt}</span>
              <span className={`m-ret ${p.ret ? 'yes' : ''}`}>{p.ret ? '↻' : '—'}</span>
            </div>
          );
        })}
      </div>

      {selectedClient && orders && (
        <div className="client-detail">
          <div className="cd-name">{selectedClient.name}</div>
          <div className="cd-meta">
            {selectedClient.code} · {selectedClient.neighborhood} · {selectedClient.window} · ETA {selectedClient.eta}
          </div>
          <div className="cd-list">
            {orders.map(([sku, qty], i) => (
              <React.Fragment key={i}>
                <span>{sku}</span>
                <span className="qty">{qty}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
