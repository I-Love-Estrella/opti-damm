'use client';

import { useEffect, useRef } from 'react';

const TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

function bearingDeg(a, b) {
  // a, b: [lat, lon]
  const toRad = (d) => (d * Math.PI) / 180;
  const toDeg = (r) => (r * 180) / Math.PI;
  const lat1 = toRad(a[0]);
  const lat2 = toRad(b[0]);
  const dLon = toRad(b[1] - a[1]);
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

function pointAlong(a, b, frac) {
  return [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac];
}

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export default function RouteMap({ depot, stops = [], legs = [], height = 360 }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cancelled = false;
    (async () => {
      const Lmod = await import('leaflet');
      const L = Lmod.default || Lmod;
      await import('leaflet/dist/leaflet.css');
      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        zoomControl: true,
        attributionControl: false,
        scrollWheelZoom: false,
      }).setView([depot?.lat ?? 41.54, depot?.lon ?? 2.21], 9);

      L.tileLayer(TILE_URL, { attribution: TILE_ATTR, maxZoom: 18 }).addTo(map);
      mapRef.current = { L, map, layers: [] };
      drawAll();
    })();

    return () => {
      cancelled = true;
      if (mapRef.current?.map) {
        mapRef.current.map.remove();
        mapRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    drawAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depot, stops, legs]);

  function drawAll() {
    if (!mapRef.current) return;
    const { L, map, layers } = mapRef.current;

    layers.forEach((l) => map.removeLayer(l));
    mapRef.current.layers = [];
    const newLayers = [];

    if (depot) {
      const depotIcon = L.divIcon({
        className: 'depot-icon',
        html: `<div class="depot-marker">D</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      const m = L.marker([depot.lat, depot.lon], { icon: depotIcon }).addTo(map);
      m.bindTooltip(`Depot · ${depot.name || ''}`, { direction: 'top' });
      newLayers.push(m);
    }

    const validStops = stops.filter(
      (s) => Number.isFinite(s.lat) && Number.isFinite(s.lon) && Math.abs(s.lat) > 0.0001 && Math.abs(s.lon) > 0.0001,
    );

    validStops.forEach((s) => {
      const icon = L.divIcon({
        className: 'stop-icon',
        html: `<div class="stop-marker">${s.visit_seq}</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      const m = L.marker([s.lat, s.lon], { icon }).addTo(map);
      m.bindTooltip(
        `<b>#${s.visit_seq} · ${escapeHtml(s.client_id)}</b><br>${escapeHtml(s.name || '')}<br>${escapeHtml(s.city || '')} ${escapeHtml(s.cp || '')}`,
        { direction: 'top' },
      );
      newLayers.push(m);
    });

    const allLatLng = [];
    if (depot) allLatLng.push([depot.lat, depot.lon]);
    validStops.forEach((s) => allLatLng.push([s.lat, s.lon]));

    const validLegs = (legs || []).filter(
      (l) =>
        Number.isFinite(l.from_lat) &&
        Number.isFinite(l.from_lon) &&
        Number.isFinite(l.to_lat) &&
        Number.isFinite(l.to_lon),
    );

    validLegs.forEach((leg, idx) => {
      const from = [leg.from_lat, leg.from_lon];
      const to = [leg.to_lat, leg.to_lon];

      const line = L.polyline([from, to], {
        color: '#fc0',
        weight: 2.6,
        opacity: 0.85,
        dashArray: '6 4',
        className: 'route-leg-line',
      }).addTo(map);

      const popupHtml = `
        <div class="leg-popup">
          <div class="leg-popup-head">
            <span class="leg-popup-num">Leg #${leg.leg_index}</span>
            <span class="leg-popup-arrow">${escapeHtml(leg.from_id === 'DEPOT' ? 'DEPOT' : `#${leg.leg_index - 1}`)} → ${escapeHtml(leg.to_id === 'DEPOT' ? 'DEPOT' : `#${leg.to_visit_seq}`)}</span>
          </div>
          <div class="leg-popup-row">
            <span class="leg-popup-label">From</span>
            <span class="leg-popup-val">${escapeHtml(leg.from_name)}</span>
          </div>
          <div class="leg-popup-row">
            <span class="leg-popup-label">To</span>
            <span class="leg-popup-val">${escapeHtml(leg.to_name)}</span>
          </div>
          <div class="leg-popup-grid">
            <div><span class="leg-popup-label">Distance</span><span class="leg-popup-val">${leg.distance_km.toFixed(2)} km</span></div>
            <div><span class="leg-popup-label">Drive time</span><span class="leg-popup-val">${leg.drive_min.toFixed(1)} min</span></div>
            ${leg.arrive_t_min !== null && leg.arrive_t_min !== undefined ? `<div><span class="leg-popup-label">Arrive at</span><span class="leg-popup-val">${(leg.arrive_t_min || 0).toFixed(1)} min</span></div>` : ''}
            <div><span class="leg-popup-label">Avg speed</span><span class="leg-popup-val">${leg.drive_min > 0 ? ((leg.distance_km / (leg.drive_min / 60)) || 0).toFixed(1) : '—'} km/h</span></div>
          </div>
        </div>
      `;
      line.bindPopup(popupHtml, { className: 'route-leg-popup', closeButton: true });
      line.bindTooltip(
        `Leg #${leg.leg_index} · ${leg.distance_km.toFixed(1)} km · ${leg.drive_min.toFixed(1)} min`,
        { sticky: true, direction: 'top' },
      );
      line.on('mouseover', () => {
        line.setStyle({ weight: 4.5, opacity: 1, color: '#ffd633' });
      });
      line.on('mouseout', () => {
        line.setStyle({ weight: 2.6, opacity: 0.85, color: '#fc0' });
      });
      newLayers.push(line);

      // Arrow head: divIcon at 70% along the line, rotated by bearing
      const head = pointAlong(from, to, 0.7);
      const ang = bearingDeg(from, to);
      const arrowIcon = L.divIcon({
        className: 'leg-arrow',
        html: `<div class="leg-arrow-head" style="transform: rotate(${ang.toFixed(1)}deg)">▲</div>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });
      const arrowM = L.marker(head, { icon: arrowIcon, interactive: true, keyboard: false }).addTo(map);
      arrowM.bindTooltip(
        `<b>Leg #${leg.leg_index}</b><br>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}<br>${leg.distance_km.toFixed(2)} km · ${leg.drive_min.toFixed(1)} min`,
        { direction: 'top', sticky: false, offset: [0, -10] },
      );
      arrowM.bindPopup(popupHtml, { className: 'route-leg-popup', closeButton: true });
      newLayers.push(arrowM);

      allLatLng.push(from, to);
    });

    if (allLatLng.length > 0) {
      const bounds = L.latLngBounds(allLatLng);
      map.fitBounds(bounds, { padding: [22, 22] });
    } else if (depot) {
      map.setView([depot.lat, depot.lon], 11);
    }

    mapRef.current.layers = newLayers;
  }

  return (
    <div className="route-map-wrap" style={{ height }}>
      <div ref={containerRef} className="route-map" />
      <style jsx global>{`
        .route-map {
          width: 100%;
          height: 100%;
          background: #0a0a0a;
          border: 1px solid #2a2a2a;
        }
        .route-map .leaflet-control-zoom a {
          background: #161616;
          color: #e8e8e8;
          border-color: #2a2a2a;
        }
        .route-map .leaflet-control-zoom a:hover {
          background: #1c1c1c;
          color: #fc0;
        }
        .depot-marker {
          width: 22px;
          height: 22px;
          background: #fc0;
          color: #000;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 700;
          font-size: 11px;
          font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
          border: 2px solid #000;
          box-shadow: 0 0 0 1px #fc0;
        }
        .stop-marker {
          width: 22px;
          height: 22px;
          background: #1a1a1a;
          color: #fc0;
          border: 2px solid #fc0;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: 600;
          font-size: 11px;
          font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
        }
        .leg-arrow {
          background: transparent;
          border: none;
          pointer-events: auto;
        }
        .leg-arrow-head {
          width: 18px;
          height: 18px;
          color: #fc0;
          font-size: 16px;
          line-height: 18px;
          text-align: center;
          font-weight: 700;
          text-shadow: 0 0 4px #000, 0 0 2px #000;
          transform-origin: 50% 50%;
          cursor: pointer;
        }
        .leg-arrow-head:hover {
          color: #ffd633;
          transform: scale(1.25);
        }
        .leaflet-tooltip {
          background: #0a0a0a;
          color: #e8e8e8;
          border: 1px solid #2a2a2a;
          font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
          font-size: 11px;
          padding: 6px 8px;
        }
        .leaflet-tooltip-top:before {
          border-top-color: #2a2a2a;
        }
        .route-leg-popup .leaflet-popup-content-wrapper {
          background: #0a0a0a;
          color: #e8e8e8;
          border: 1px solid #2a2a2a;
          border-radius: 2px;
          font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
        }
        .route-leg-popup .leaflet-popup-tip {
          background: #0a0a0a;
          border: 1px solid #2a2a2a;
        }
        .route-leg-popup .leaflet-popup-close-button {
          color: #888 !important;
        }
        .leg-popup {
          font-size: 11.5px;
          min-width: 230px;
        }
        .leg-popup-head {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding-bottom: 6px;
          margin-bottom: 6px;
          border-bottom: 1px solid #2a2a2a;
        }
        .leg-popup-num {
          background: #fc0;
          color: #000;
          padding: 1px 6px;
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 0.06em;
          border-radius: 2px;
        }
        .leg-popup-arrow {
          color: #fc0;
          font-weight: 600;
          font-size: 11px;
          letter-spacing: 0.04em;
        }
        .leg-popup-row {
          display: flex;
          align-items: baseline;
          gap: 8px;
          margin: 2px 0;
        }
        .leg-popup-label {
          color: #888;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          font-size: 9.5px;
          min-width: 70px;
        }
        .leg-popup-val {
          color: #e8e8e8;
        }
        .leg-popup-grid {
          margin-top: 6px;
          padding-top: 6px;
          border-top: 1px dashed #2a2a2a;
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 4px 12px;
        }
        .leg-popup-grid > div {
          display: flex;
          flex-direction: column;
        }
      `}</style>
    </div>
  );
}
