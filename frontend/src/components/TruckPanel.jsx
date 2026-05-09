'use client';

import React from 'react';

const SKU_TONE = {
  EST: '1',
  VOL: '3',
  MAL: '4',
  MOR: '5',
  AGV: '2',
};

const STOP_TONES = ['1', '2', '3', '4', '5', '6', '7'];

function skuName(s) {
  return ({ EST: 'Estrella 33cl', VOL: 'Voll-Damm 33cl', MAL: 'Malquerida 75cl', MOR: 'Moritz 33cl', AGV: 'Agua 1.5L' })[s] || s;
}

function shortClient(c) {
  if (!c) return '';
  return c.length > 14 ? c.slice(0, 13) + '…' : c;
}

export default function TruckPanel({ pallets, hoveredStop, onPalletHover, hoveredPallet, onPalletClick, selectedClient, truck }) {
  const cap = truck?.capacity || pallets.length || 0;
  const cols = truck?.cols || 2;
  const totalCells = pallets.length;
  const filled = pallets.filter(p => p.sku).length;
  const utilization = totalCells > 0 ? Math.round((filled / totalCells) * 100) : 0;
  const totalWt = pallets.reduce((acc, p) => acc + (p.wt || 0), 0);
  const rows = Math.ceil(cap / cols);
  const maxKg = truck?.maxKg || 0;

  function tone(p) {
    if (!p.sku) return null;
    if (p.stop != null) return STOP_TONES[(p.stop - 1) % STOP_TONES.length];
    return SKU_TONE[p.sku] || '1';
  }

  const legendItems = (() => {
    const seen = new Map();
    pallets.forEach(p => {
      if (!p.sku) return;
      const key = p.stop ?? `sku-${p.sku}`;
      if (seen.has(key)) {
        const v = seen.get(key); v.count++; seen.set(key, v);
      } else {
        seen.set(key, {
          tone: tone(p),
          label: p.stop != null ? `S-${String(p.stop).padStart(2, '0')}` : p.sku,
          sub: p.client || skuName(p.sku),
          count: 1,
        });
      }
    });
    return Array.from(seen.values());
  })();

  return (
    <div className="panel truck-panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="panel-index">02</span>
          Load
          <span className="panel-code">{truck?.code || '—'} · {cap} PLT</span>
        </div>
        <div className="panel-readout">
          <span className="ro-row"><strong>{utilization}%</strong> · {filled}/{totalCells} PLT</span>
          <span className="ro-row ro-dim">{totalWt} KG{maxKg ? ` / ${maxKg} KG` : ''}</span>
        </div>
      </div>

      <div className="truck-caption">
        <span>Top-down view · {cols} × {rows} grid · {cap} pallet slots</span>
        <span className="t-sub">colour = destination stop</span>
      </div>

      <div className="truck-wrap">
        <span className="truck-label tl-cab">↑ CAB · FRONT</span>
        <span className="truck-label tl-rear">REAR DOORS ↓</span>
        <div className="pallet-grid" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
          {pallets.map(p => {
            const isHL = hoveredStop && p.stop === hoveredStop.id;
            return (
              <div
                key={`pal-${p.idx}`}
                className={`pallet ${!p.sku ? 'empty' : ''} ${isHL ? 'highlight' : ''}`}
                data-tone={tone(p)}
                onMouseEnter={() => p.sku && onPalletHover && onPalletHover(p)}
                onMouseLeave={() => onPalletHover && onPalletHover(null)}
                onClick={() => p.sku && onPalletClick && onPalletClick(p)}
              >
                {p.sku ? (
                  <>
                    <div className="p-top">
                      <span className="p-stop-ring">{p.stop ?? '—'}</span>
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
        <div className="legend-head">BY DESTINATION</div>
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
              <div key={`mf-${p.idx}`} className="manifest-row empty">
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
              key={`mf-${p.idx}`}
              className={`manifest-row ${isHL ? 'highlight' : ''}`}
              onMouseEnter={() => onPalletHover && onPalletHover(p)}
              onMouseLeave={() => onPalletHover && onPalletHover(null)}
              onClick={() => onPalletClick && onPalletClick(p)}
            >
              <span className="m-code">{p.code} · {p.sku}</span>
              <span className="m-client">{p.client}</span>
              <span className="m-stop">{p.stop != null ? `S-${String(p.stop).padStart(2, '0')}` : '—'}</span>
              <span className="m-wt">{p.wt}</span>
              <span className={`m-ret ${p.ret ? 'yes' : ''}`}>{p.ret ? '↻' : '—'}</span>
            </div>
          );
        })}
      </div>

      {selectedClient && (
        <div className="client-detail">
          <div className="cd-name">{selectedClient.name}</div>
          <div className="cd-meta">
            {selectedClient.code} · {selectedClient.neighborhood} · ETA {selectedClient.eta}
          </div>
        </div>
      )}
    </div>
  );
}
