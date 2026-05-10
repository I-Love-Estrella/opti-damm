'use client';

import React from 'react';

const SKU_COLORS = {
  EST: '#C8553D',
  VOL: '#588B8B',
  MAL: '#F2C078',
  MOR: '#8C7A6B',
  AGV: '#7FB285',
};

const FALLBACK_COLORS = ['#A0937D', '#B5838D', '#6D6875', '#E5989B', '#FFB4A2', '#CDDAFD', '#DFE7FD'];

function skuColor(code) {
  if (SKU_COLORS[code]) return SKU_COLORS[code];
  let hash = 0;
  for (let i = 0; i < (code || '').length; i++) hash = code.charCodeAt(i) + ((hash << 5) - hash);
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
}

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

  const skuLegend = (() => {
    const totals = new Map();
    pallets.forEach(p => {
      for (const it of (p.items || [])) {
        totals.set(it.sku, (totals.get(it.sku) || 0) + it.qty);
      }
    });
    return Array.from(totals.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([sku, qty]) => ({ sku, qty, color: skuColor(sku) }));
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
        <span className="t-sub">colour = SKU breakdown</span>
      </div>

      <div className="truck-wrap">
        <span className="truck-label tl-cab">↑ CAB · FRONT</span>
        <span className="truck-label tl-rear">REAR DOORS ↓</span>
        <div className="pallet-grid" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
          {pallets.map(p => {
            const isHL = hoveredStop && p.stop === hoveredStop.id;
            const items = p.items || [];
            return (
              <div
                key={`pal-${p.idx}`}
                className={`pallet ${!p.sku ? 'empty' : ''} ${isHL ? 'highlight' : ''}`}
                onMouseEnter={() => p.sku && onPalletHover && onPalletHover(p)}
                onMouseLeave={() => onPalletHover && onPalletHover(null)}
                onClick={() => p.sku && onPalletClick && onPalletClick(p)}
              >
                {p.sku ? (
                  <>
                    <div className="p-top">
                      <span className="p-stop-ring">{p.stop ?? '—'}</span>
                      <span className="p-client">{shortClient(p.client)}</span>
                      {p.ret && <span className="p-recycle" title="returnable">↻</span>}
                    </div>
                    <div className="p-bar">
                      {items.map((it, i) => (
                        <div
                          key={i}
                          className="p-bar-seg"
                          style={{
                            flex: it.qty,
                            backgroundColor: skuColor(it.sku),
                          }}
                          title={`${it.sku} ×${it.qty}`}
                        />
                      ))}
                    </div>
                    <div className="p-breakdown">
                      {items.slice(0, 3).map((it, i) => (
                        <span key={i} className="p-item">
                          <span className="p-item-dot" style={{ backgroundColor: skuColor(it.sku) }} />
                          {it.sku} <span className="p-item-qty">×{it.qty}</span>
                        </span>
                      ))}
                      {items.length > 3 && <span className="p-item p-item-more">+{items.length - 3}</span>}
                    </div>
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
        <div className="legend-head">BY SKU</div>
        <div className="legend-items">
          {skuLegend.map((it, i) => (
            <div key={i} className="legend-item">
              <span className="legend-swatch" style={{ backgroundColor: it.color }} />
              <span className="legend-label">{it.sku}</span>
              <span className="legend-sub">{skuName(it.sku)}</span>
              <span className="legend-count">×{it.qty}</span>
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
