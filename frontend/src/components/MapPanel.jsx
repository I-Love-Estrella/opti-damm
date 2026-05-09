'use client';

import { useState, useRef, useEffect, useMemo } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

async function osrmRoute(coords) {
  const path = coords.map(([lat, lng]) => `${lng},${lat}`).join(";");
  const url = `https://router.project-osrm.org/route/v1/driving/${path}?overview=full&geometries=geojson`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("OSRM " + res.status);
  const data = await res.json();
  if (!data.routes || !data.routes.length) throw new Error("no route");
  const r = data.routes[0];
  return {
    line: r.geometry.coordinates.map(([lng, lat]) => [lat, lng]),
    distance: r.distance,
    duration: r.duration,
  };
}

function formatDuration(secs) {
  const h = Math.floor(secs / 3600);
  const m = Math.round((secs % 3600) / 60);
  return `${h}h${m.toString().padStart(2, "0")}`;
}

export default function MapPanel({ stops, warehouse, onStopHover, onStopClick, hoveredPalletStops, isFullscreen, isCollapsed }) {
  const wrapRef = useRef(null);
  const mapRef = useRef(null);
  const layersRef = useRef({ markers: [], routes: [], depot: null });
  const [routeMeta, setRouteMeta] = useState({ km: null, eta: null, status: "init" });
  const [tooltip, setTooltip] = useState(null);

  const visibleStops = useMemo(() => stops.filter(s => !s.cancelled), [stops]);

  useEffect(() => {
    if (!isCollapsed && mapRef.current) {
      const t1 = setTimeout(() => mapRef.current.invalidateSize(), 50);
      const t2 = setTimeout(() => mapRef.current.invalidateSize(), 350);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    }
  }, [isCollapsed, isFullscreen]);

  useEffect(() => {
    if (!wrapRef.current || mapRef.current) return;

    const map = L.map(wrapRef.current, {
      zoomControl: false,
      attributionControl: false,
      scrollWheelZoom: true,
      doubleClickZoom: false,
      dragging: true,
      zoomSnap: 0.25,
    });
    mapRef.current = map;

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      crossOrigin: true,
    }).addTo(map);
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      crossOrigin: true,
      pane: "shadowPane",
    }).addTo(map);
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    layersRef.current.markers.forEach(m => m.remove());
    if (layersRef.current.depot) layersRef.current.depot.remove();
    layersRef.current.markers = [];

    const depotIcon = L.divIcon({
      className: "depot-marker",
      html: `<div class="depot-square"><span class="depot-star">★</span></div><div class="depot-label">MOLLET DEPOT</div>`,
      iconSize: [80, 40],
      iconAnchor: [10, 10],
    });
    layersRef.current.depot = L.marker(warehouse.latlng, { icon: depotIcon, interactive: false }).addTo(map);

    visibleStops.forEach(s => {
      const r = 11 + s.pallets * 2.4;
      const isHL = hoveredPalletStops && hoveredPalletStops.includes(s.id);
      const html = `
        <div class="stop-marker s-${s.status} ${isHL ? "is-hl" : ""}" style="--r:${r}px;">
          <div class="sm-circle">
            <span class="sm-num">${s.n}</span>
            ${s.status === "completed" ? '<span class="sm-strike"></span>' : ""}
            ${s.priority ? '<span class="sm-prio"></span>' : ""}
          </div>
          <div class="sm-tag">${s.code} · ${s.nbCode}</div>
        </div>
      `;
      const icon = L.divIcon({
        className: "stop-marker-wrap",
        html,
        iconSize: [r * 2 + 60, r * 2 + 28],
        iconAnchor: [r + 30, r + 4],
      });
      const m = L.marker(s.latlng, { icon, riseOnHover: true }).addTo(map);
      m.on("mouseover", () => {
        const pt = map.latLngToContainerPoint(s.latlng);
        setTooltip({ x: pt.x, y: pt.y, stop: s });
        onStopHover && onStopHover(s);
      });
      m.on("mouseout", () => {
        setTooltip(null);
        onStopHover && onStopHover(null);
      });
      m.on("click", () => onStopClick && onStopClick(s));
      layersRef.current.markers.push(m);
    });

    const all = [warehouse.latlng, ...visibleStops.map(s => s.latlng)];
    if (all.length > 1) {
      map.fitBounds(all, { padding: [40, 40] });
    }
  }, [visibleStops, warehouse, hoveredPalletStops, onStopHover, onStopClick]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    let cancelled = false;
    setRouteMeta(m => ({ ...m, status: "fetching" }));

    const completedStops = visibleStops.filter(s => s.status === "completed");
    const currentStop = visibleStops.find(s => s.status === "current");
    const upcomingStops = visibleStops.filter(s => s.status === "upcoming");

    const completedSeg = [warehouse.latlng, ...completedStops.map(s => s.latlng)];
    if (currentStop) completedSeg.push(currentStop.latlng);

    const upcomingSeg = [];
    if (currentStop) upcomingSeg.push(currentStop.latlng);
    upcomingStops.forEach(s => upcomingSeg.push(s.latlng));
    if (upcomingStops.length) upcomingSeg.push(warehouse.latlng);

    async function go() {
      layersRef.current.routes.forEach(p => p.remove());
      layersRef.current.routes = [];

      let totalDist = 0, totalTime = 0;
      try {
        const fetches = [];
        if (completedSeg.length >= 2) fetches.push(osrmRoute(completedSeg));
        else fetches.push(Promise.resolve(null));
        if (upcomingSeg.length >= 2) fetches.push(osrmRoute(upcomingSeg));
        else fetches.push(Promise.resolve(null));

        const [completed, upcoming] = await Promise.all(fetches);
        if (cancelled) return;

        if (completed) {
          const halo = L.polyline(completed.line, { color: "#F4EFE3", weight: 6, opacity: 0.85, lineCap: "round" }).addTo(map);
          const line = L.polyline(completed.line, { color: "#C8102E", weight: 2.4, opacity: 1, lineCap: "round" }).addTo(map);
          const elt = line.getElement();
          if (elt) {
            const len = elt.getTotalLength();
            elt.style.strokeDasharray = len;
            elt.style.strokeDashoffset = len;
            elt.style.transition = "stroke-dashoffset 1.4s ease-out";
            requestAnimationFrame(() => requestAnimationFrame(() => { elt.style.strokeDashoffset = 0; }));
          }
          layersRef.current.routes.push(halo, line);
          totalDist += completed.distance;
          totalTime += completed.duration;
        }
        if (upcoming) {
          const halo = L.polyline(upcoming.line, { color: "#F4EFE3", weight: 6, opacity: 0.7, lineCap: "round" }).addTo(map);
          const line = L.polyline(upcoming.line, { color: "#C8102E", weight: 2, opacity: 0.9, dashArray: "4 5", lineCap: "round" }).addTo(map);
          layersRef.current.routes.push(halo, line);
          totalDist += upcoming.distance;
          totalTime += upcoming.duration;
        }
        setRouteMeta({
          km: (totalDist / 1000).toFixed(1),
          eta: formatDuration(totalTime),
          status: "ok",
        });
      } catch (err) {
        if (cancelled) return;
        const drawSeg = (coords, dashed) => {
          if (coords.length < 2) return;
          const halo = L.polyline(coords, { color: "#F4EFE3", weight: 6, opacity: 0.7, lineCap: "round" }).addTo(map);
          const line = L.polyline(coords, { color: "#C8102E", weight: 2, opacity: 0.9, dashArray: dashed ? "4 5" : null, lineCap: "round" }).addTo(map);
          layersRef.current.routes.push(halo, line);
        };
        drawSeg(completedSeg, false);
        drawSeg(upcomingSeg, true);
        setRouteMeta({ km: "—", eta: "—", status: "offline" });
      }
    }
    go();
    return () => { cancelled = true; };
  }, [visibleStops, warehouse]);

  const completedCount = visibleStops.filter(s => s.status === "completed").length;

  return (
    <div className="panel map-panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="panel-index">01</span>
          Route
          <span className="panel-code">RTE-2026.05.09.A</span>
        </div>
        <div className="panel-readout">
          <span className="ro-row"><strong>{completedCount}</strong>/{visibleStops.length} STOPS · MOLLET → VALLÈS</span>
          <span className="ro-row ro-dim">
            {routeMeta.status === "fetching" && "ROUTING via OSRM…"}
            {routeMeta.status === "ok" && `${routeMeta.km} KM · ${routeMeta.eta} DRIVE · OSRM`}
            {routeMeta.status === "offline" && "STRAIGHT-LINE FALLBACK · OSRM unreachable"}
            {routeMeta.status === "init" && "INIT · WGS84"}
          </span>
        </div>
      </div>
      <div className="map-wrap">
        <div className="leaflet-host" ref={wrapRef}></div>

        <div className="map-overlay map-overlay-tl">
          <div className="ov-chip">MOLLET · 41.54°N · 2.21°E</div>
          <div className="ov-chip ov-chip-dim">EPSG:3857 · WEBMERC</div>
        </div>
        <div className="map-overlay map-overlay-tr">
          <div className="ov-chip">N ↑</div>
        </div>
        <div className="map-overlay map-overlay-br">
          <div className="ov-chip ov-chip-dim">© OPENSTREETMAP · CARTODB · OSRM</div>
        </div>

        {tooltip && (
          <div className="tooltip visible" style={{ left: tooltip.x, top: tooltip.y }}>
            <div className="tt-name">{tooltip.stop.name}</div>
            <div className="tt-row"><span>{tooltip.stop.code}</span><span>{tooltip.stop.neighborhood}</span></div>
            <div className="tt-row"><span>Window</span><span>{tooltip.stop.window}</span></div>
            <div className="tt-row"><span>ETA</span><span>{tooltip.stop.eta}</span></div>
            <div className="tt-row"><span>Pallets</span><span>{tooltip.stop.pallets}</span></div>
            <div className="tt-row"><span>Coord</span><span>{tooltip.stop.latlng[0].toFixed(3)}°N · {tooltip.stop.latlng[1].toFixed(3)}°E</span></div>
            {tooltip.stop.priority && <div className="tt-priority">● HIGH PRIORITY</div>}
          </div>
        )}
      </div>
      <div className="map-legend">
        <span className="lg-item"><span className="lg-line"></span>Driving route (OSRM)</span>
        <span className="lg-item"><span className="lg-line lg-line-dashed"></span>Upcoming</span>
        <span className="lg-item"><span className="lg-dot"></span>Priority</span>
        <span className="lg-item"><span className="lg-star">★</span> Depot</span>
        <span className="lg-item" style={{marginLeft: 'auto'}}>WGS84 · UTM 31T</span>
      </div>
    </div>
  );
}
