'use client';

import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import dynamic from 'next/dynamic';
import {
  STOPS,
  WAREHOUSE,
  ZONES,
  PALLETS_T6,
  PALLETS_T8,
  TRUCK_TYPES,
} from '@/data';
import TruckPanel from '@/components/TruckPanel';
import CopilotPanel from '@/components/CopilotPanel';
import MetricsBar from '@/components/MetricsBar';
import WarehousePanel from '@/components/WarehousePanel';
import { api, SIM_API_BASE } from '@/lib/api';

const MapPanel = dynamic(() => import('@/components/MapPanel'), { ssr: false });

const INITIAL_MESSAGES = [
  { kind: "claude", text: 'Good morning, Manel. **Truck 04** is loaded — route DR0054 heading north from Mollet. Driver J. Martínez left the depot at 08:14. Three stops complete, four to go. The route is optimised for **distance first**, with a soft preference for grouping returnables.' },
  { kind: "claude", text: 'Current ETA back at depot: **15:42**. Score: 87/100. Ask me anything — or pull a slider and I\'ll re-plan live.' },
];

const INITIAL_LOG = [
  { t: "08:14:02", tag: "DEPART", level: "ok",   msg: "TRK-04 · DDI MOLLET DEPOT" },
  { t: "08:42:18", tag: "STOP",   level: "ok",   msg: "S-01 LOS TERESITOS · 1 PLT DELIVERED" },
  { t: "09:38:44", tag: "STOP",   level: "ok",   msg: "S-02 VIENA GRANOLLERS · 2 PLT DELIVERED" },
  { t: "10:24:09", tag: "STOP",   level: "ok",   msg: "S-03 FRANKFURT LEO BOECK · 1 PLT DELIVERED" },
  { t: "10:48:01", tag: "PING",   level: "info", msg: "GPS LOCK · 41.886°N 2.254°E · 23 KM/H" },
];

const SECTIONS = [
  { id: 'map', index: '01', title: 'Route' },
  { id: 'truck', index: '02', title: 'Load' },
  { id: 'warehouse', index: '04', title: 'Warehouse' },
  { id: 'copilot', index: '03', title: 'Co-pilot' },
  { id: 'metrics', index: '—', title: 'Metrics' },
];

function Section({ id, collapsed, fullscreen, onToggleCollapse, onToggleFullscreen, dark, style, children }) {
  const cls = [
    'section',
    collapsed && 'section-collapsed',
    fullscreen && 'section-fullscreen',
    dark && 'section-dark',
  ].filter(Boolean).join(' ');

  return (
    <div className={cls} style={style}>
      <div className="section-controls">
        <button className="sc-btn" onClick={() => onToggleCollapse(id)} title="Hide panel">{'−'}</button>
        <button className="sc-btn" onClick={() => onToggleFullscreen(id)} title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
          {fullscreen ? '✕' : '⤢'}
        </button>
      </div>
      {children}
    </div>
  );
}

function DragHandle({ direction, onDrag }) {
  const handleRef = useRef(null);

  const onMouseDown = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;

    const onMouseMove = (e) => {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      onDrag(direction === 'horizontal' ? dx : dy, false);
    };

    const onMouseUp = (e) => {
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      onDrag(direction === 'horizontal' ? dx : dy, true);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = direction === 'horizontal' ? 'col-resize' : 'row-resize';
    document.body.style.userSelect = 'none';
  }, [direction, onDrag]);

  return (
    <div
      ref={handleRef}
      className={`drag-handle drag-handle-${direction}`}
      onMouseDown={onMouseDown}
    />
  );
}

export default function Page() {
  const [stops] = useState(STOPS);
  const [mode, setMode] = useState("reference");
  const [truckType, setTruckType] = useState("T8");
  const [hoveredStop, setHoveredStop] = useState(null);
  const [hoveredPallet, setHoveredPallet] = useState(null);
  const [selectedClient, setSelectedClient] = useState(null);
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isTyping, setIsTyping] = useState(false);
  const [weights, setWeights] = useState({ route: 60, load: 25, unload: 15 });
  const [now] = useState("10:48");
  const [sysLog, setSysLog] = useState(INITIAL_LOG);
  const [collapsed, setCollapsed] = useState(new Set());
  const [fullscreenPanel, setFullscreenPanel] = useState(null);
  const [panelMenuOpen, setPanelMenuOpen] = useState(false);
  const [pdfMenuOpen, setPdfMenuOpen] = useState(false);
  const [availableRoutes, setAvailableRoutes] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [routeDetail, setRouteDetail] = useState(null);
  const pdfMenuRef = useRef(null);
  const [colWidths, setColWidths] = useState({ map: 1.25, truck: 1, right: 1 });
  const [rightSplit, setRightSplit] = useState(0.5);
  const panelMenuRef = useRef(null);
  const panelsRef = useRef(null);
  const dragStartWidths = useRef(null);
  const dragStartRightSplit = useRef(null);

  const toggleCollapse = useCallback((id) => {
    setCollapsed(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    setFullscreenPanel(prev => prev === id ? null : prev);
  }, []);

  const toggleFullscreen = useCallback((id) => {
    setFullscreenPanel(prev => prev === id ? null : id);
    setCollapsed(prev => {
      if (prev.has(id)) {
        const next = new Set(prev);
        next.delete(id);
        return next;
      }
      return prev;
    });
  }, []);

  useEffect(() => {
    const handleKey = (e) => {
      if (e.key === 'Escape') {
        setFullscreenPanel(null);
        setPanelMenuOpen(false);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, []);

  useEffect(() => {
    api.routes().then(routes => {
      setAvailableRoutes(routes);
      if (routes.length > 0 && !selectedRoute) {
        setSelectedRoute(routes[0]);
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedRoute) return;
    api.routeDetail(selectedRoute.fecha, selectedRoute.ruta)
      .then(setRouteDetail)
      .catch(() => setRouteDetail(null));
  }, [selectedRoute]);

  useEffect(() => {
    if (!pdfMenuOpen) return;
    const handler = (e) => {
      if (pdfMenuRef.current && !pdfMenuRef.current.contains(e.target)) {
        setPdfMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [pdfMenuOpen]);

  const openPdf = useCallback((path) => {
    window.open(`${SIM_API_BASE}${path}`, '_blank');
  }, []);

  useEffect(() => {
    if (!panelMenuOpen) return;
    const handler = (e) => {
      if (panelMenuRef.current && !panelMenuRef.current.contains(e.target)) {
        setPanelMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [panelMenuOpen]);

  const rightStackVisible = !collapsed.has('warehouse') || !collapsed.has('copilot');

  const panelGridStyle = useMemo(() => {
    const cols = [];
    if (!collapsed.has('map')) cols.push(`${colWidths.map}fr`);
    if (!collapsed.has('truck')) cols.push(`${colWidths.truck}fr`);
    if (rightStackVisible) cols.push(`${colWidths.right}fr`);
    if (cols.length === 0) cols.push('1fr');
    return { gridTemplateColumns: cols.join(' ') };
  }, [collapsed, rightStackVisible, colWidths]);

  const appGridStyle = useMemo(() => ({
    gridTemplateRows: `22px 50px 1fr 28px ${collapsed.has('metrics') ? '0' : '160px'} 26px`
  }), [collapsed]);

  const handleColDrag = useCallback((leftKey, rightKey) => (delta, done) => {
    if (!panelsRef.current) return;
    if (done) {
      dragStartWidths.current = null;
      return;
    }
    if (!dragStartWidths.current) {
      dragStartWidths.current = { ...colWidths };
    }
    const start = dragStartWidths.current;
    const totalWidth = panelsRef.current.offsetWidth;
    const visibleKeys = ['map', 'truck', 'right'].filter(k =>
      k === 'right' ? rightStackVisible : !collapsed.has(k)
    );
    const totalFr = visibleKeys.reduce((a, k) => a + start[k], 0);
    const pxPerFr = totalWidth / totalFr;
    const deltaFr = delta / pxPerFr;
    setColWidths({
      ...start,
      [leftKey]: Math.max(0.3, start[leftKey] + deltaFr),
      [rightKey]: Math.max(0.3, start[rightKey] - deltaFr),
    });
  }, [colWidths, collapsed, rightStackVisible]);

  const handleRightStackDrag = useCallback((delta, done) => {
    if (done) {
      dragStartRightSplit.current = null;
      return;
    }
    const stack = panelsRef.current?.querySelector('.right-stack');
    if (!stack) return;
    const totalHeight = stack.offsetHeight;
    if (!dragStartRightSplit.current) {
      dragStartRightSplit.current = rightSplit;
    }
    const startFrac = dragStartRightSplit.current;
    const deltaFrac = delta / totalHeight;
    setRightSplit(Math.max(0.15, Math.min(0.85, startFrac + deltaFrac)));
  }, [rightSplit]);

  const pushLog = useCallback((entry) => {
    const d = new Date();
    const t = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
    setSysLog(prev => [...prev, { t, ...entry }]);
  }, []);

  const palletSource = truckType === "T6" ? PALLETS_T6 : PALLETS_T8;

  const pallets = useMemo(() => {
    const base = palletSource[mode] || palletSource.reference;
    const cancelled = stops.filter(s => s.cancelled).map(s => s.id);
    return base.map(p => cancelled.includes(p.stop) ? { ...p, sku: null, stop: null, ret: false, client: null, wt: 0 } : p);
  }, [mode, stops, palletSource]);

  const filledPallets = pallets.filter(p => p.sku);
  const returnableCount = filledPallets.length
    ? Math.round((filledPallets.filter(p => p.ret).length / filledPallets.length) * 100)
    : 0;

  const metrics = useMemo(() => {
    const visible = stops.filter(s => !s.cancelled);
    const cancelDelta = stops.length - visible.length;
    const baseDist = 84 - (weights.load * 0.06) - (weights.unload * 0.04) + (weights.route * 0.02);
    const baseTime = 7.4 - (weights.route * 0.012) - (weights.unload * 0.006) + (weights.load * 0.004);
    const score = Math.round(
      (weights.route * 0.42 + weights.load * 0.34 + weights.unload * 0.30) / 1.5 + 38
    );
    return {
      distance: Math.max(40, baseDist - cancelDelta * 6.4),
      time: Math.max(3, baseTime - cancelDelta * 0.55),
      stops: visible.length,
      score: Math.min(99, Math.max(40, score)),
    };
  }, [weights, stops]);

  const visibleStops = stops.filter(s => !s.cancelled);
  const completed = stops.filter(s => s.status === "completed" && !s.cancelled).length;
  const nextStop = visibleStops.find(s => s.status === "current") || visibleStops.find(s => s.status === "upcoming");

  const buildCopilotContext = useCallback((overrides = {}) => {
    const routeSummary = routeDetail ? {
      date: routeDetail.date,
      ruta: routeDetail.ruta,
      repartidor: routeDetail.repartidor,
      truck: routeDetail.truck,
      transports: routeDetail.transports,
      n_clients: routeDetail.n_clients,
      total_volume_m3: routeDetail.total_volume_m3,
      orders: routeDetail.orders?.map(order => ({
        client_id: order.client_id,
        client_name: order.client_name,
        visit_seq: order.visit_seq,
        expected_returnable_units: order.expected_returnable_units,
        total_volume_m3: order.total_volume_m3,
        total_weight_kg: order.total_weight_kg,
        line_count: order.lines?.length || 0,
        lines: order.lines?.slice(0, 8),
      })),
    } : null;

    return {
      selected_route: selectedRoute,
      route_detail: routeSummary,
      load_mode: mode,
      truck_type: truckType,
      selected_client: selectedClient,
      hovered_stop: hoveredStop,
      metrics,
      weights,
      visible_stops: visibleStops.map(s => ({
        id: s.id,
        code: s.code,
        name: s.name,
        status: s.status,
        window: s.window,
        eta: s.eta,
        pallets: s.pallets,
        priority: s.priority,
        cancelled: s.cancelled || false,
      })),
      pallet_summary: {
        total_slots: pallets.length,
        filled_slots: filledPallets.length,
        returnable_percent: returnableCount,
        mode,
        sample: pallets.filter(p => p.sku).slice(0, 16),
      },
      system_log: sysLog.slice(-8),
      ...overrides,
    };
  }, [
    selectedRoute,
    routeDetail,
    mode,
    truckType,
    selectedClient,
    hoveredStop,
    metrics,
    weights,
    visibleStops,
    pallets,
    filledPallets.length,
    returnableCount,
    sysLog,
  ]);

  const askCopilot = useCallback(async (text, contextOverrides = {}) => {
    const userText = text.trim();
    if (!userText || isTyping) return;

    const outgoingMessages = messages
      .filter(m => m.kind === 'user' || m.kind === 'claude')
      .slice(-10)
      .map(m => ({
        role: m.kind === 'user' ? 'user' : 'assistant',
        text: m.text,
      }));

    setMessages(prev => [...prev, { kind: 'user', text: userText }]);
    setIsTyping(true);
    pushLog({ tag: "COPILOT", level: "info", msg: "GEMINI REQUEST" });

    try {
      const response = await api.copilotChat({
        message: userText,
        messages: outgoingMessages,
        frontendContext: buildCopilotContext(contextOverrides),
      });
      setMessages(prev => [...prev, { kind: 'claude', text: response.reply || '' }]);
      const tools = response.tool_calls?.map(t => t.name).filter(Boolean);
      pushLog({
        tag: "COPILOT",
        level: "ok",
        msg: tools?.length ? `TOOLS · ${tools.join(', ').toUpperCase()}` : `MODEL · ${response.model}`,
      });
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Unknown copilot error';
      setMessages(prev => [...prev, { kind: 'alert', text: `Copilot unavailable: ${detail}` }]);
      pushLog({ tag: "COPILOT", level: "info", msg: "ERROR · CHECK GEMINI API KEY" });
    } finally {
      setIsTyping(false);
    }
  }, [buildCopilotContext, isTyping, messages, pushLog]);

  const hoveredPalletStops = hoveredPallet ? [hoveredPallet.stop] : [];

  function handleStopClick(stop) {
    if (stop.status === "completed") return;
    setSelectedClient(stop);
    setMode("client");
  }

  function handlePalletClick(p) {
    const s = stops.find(s => s.id === p.stop);
    if (s) setSelectedClient(s);
  }

  function handleModeChange(m) {
    setMode(m);
    pushLog({ tag: "MODE", level: "info", msg: `LOAD-PLAN → ${m.toUpperCase()}` });
  }

  function handleTruckTypeChange(t) {
    setTruckType(t);
    pushLog({ tag: "TRUCK", level: "info", msg: `VEHICLE → ${t} · ${TRUCK_TYPES[t].capacity} PLT` });
  }

  function handleWeightChange(key, val) {
    setWeights(w => {
      const others = Object.keys(w).filter(k => k !== key);
      const remaining = 100 - val;
      const oldOthersSum = others.reduce((acc, k) => acc + w[k], 0) || 1;
      const next = { ...w, [key]: val };
      others.forEach(k => {
        next[k] = Math.max(0, Math.round(remaining * (w[k] / oldOthersSum)));
      });
      const sum = next.route + next.load + next.unload;
      const diff = 100 - sum;
      next[others[0]] = Math.max(0, next[others[0]] + diff);
      return next;
    });
  }

  const spec = TRUCK_TYPES[truckType];
  return (
    <div className="app" style={appGridStyle}>
      <div className="classification">
        <div className="cl-left">
          <span className="chip red">DDI &middot; INTERNAL</span>
          <span className="chip">OPS / DISPATCH</span>
          <span className="sep">/</span>
          <span>TRK-04 &middot; DRIVER MART&Iacute;NEZ</span>
          <span className="sep">/</span>
          <span>DR0054 &middot; MOLLET &rarr; VALL&Egrave;S</span>
        </div>
        <div className="cl-right">
          <span className="session">SESSION 0481</span>
          <span className="sep">&middot;</span>
          <span>NODE MLT-OPS-01</span>
          <span className="sep">&middot;</span>
          <span>CLEARANCE: DISPATCH</span>
        </div>
      </div>

      <header className="header">
        <div style={{display:'flex', alignItems:'center', gap: 28}}>
          <div className="wordmark">
            <span className="ddi">DDI</span>
            <span className="smart">Smart Truck</span>
          </div>
          <div className="header-meta">
            <span>MGR &middot; M. PUIG</span>
            <span className="sep">&middot;</span>
            <span>{spec.code} &middot; {spec.capacity} PLT</span>
            <span className="sep">&middot;</span>
            <span>DRV &middot; J. MART&Iacute;NEZ</span>
            <span className="sep">&middot;</span>
            <span>MOLLET &rarr; VALL&Egrave;S</span>
          </div>
        </div>
        <div className="header-right">
          <div className="panel-menu-wrap" ref={pdfMenuRef}>
            <button className="panel-menu-btn" onClick={() => setPdfMenuOpen(prev => !prev)}>
              <span className="pm-icon">⎙</span>
              Documents
            </button>
            {pdfMenuOpen && (
              <div className="panel-menu" style={{ width: 320 }}>
                <div style={{ padding: '6px 10px', borderBottom: '1px solid var(--navy-20, #ccc)' }}>
                  <label style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', opacity: 0.6 }}>Route</label>
                  <select
                    value={selectedRoute ? `${selectedRoute.fecha}|${selectedRoute.ruta}` : ''}
                    onChange={(e) => {
                      const [fecha, ruta] = e.target.value.split('|');
                      const r = availableRoutes.find(r => r.fecha === fecha && r.ruta === ruta);
                      if (r) setSelectedRoute(r);
                    }}
                    style={{
                      display: 'block', width: '100%', marginTop: 4, padding: '4px 6px',
                      fontFamily: 'var(--mono)', fontSize: 12, background: 'var(--cream, #faf9f6)',
                      border: '1px solid #ccc', borderRadius: 4
                    }}
                  >
                    {availableRoutes.map(r => (
                      <option key={`${r.fecha}|${r.ruta}`} value={`${r.fecha}|${r.ruta}`}>
                        {r.fecha} · {r.ruta} · {r.clients} clients
                      </option>
                    ))}
                  </select>
                </div>
                {selectedRoute && (
                  <>
                    <button
                      className="pm-item"
                      onClick={() => { openPdf(`/pdf/hoja-carga/${selectedRoute.fecha}/${selectedRoute.ruta}`); setPdfMenuOpen(false); }}
                    >
                      <span className="pm-idx">HC</span>
                      <span className="pm-label">Hoja de Carga</span>
                    </button>
                    <button
                      className="pm-item"
                      onClick={() => { openPdf(`/pdf/hoja-ruta/${selectedRoute.fecha}/${selectedRoute.ruta}`); setPdfMenuOpen(false); }}
                    >
                      <span className="pm-idx">HR</span>
                      <span className="pm-label">Hoja de Ruta</span>
                    </button>
                    <div style={{ padding: '4px 10px 2px', borderTop: '1px solid var(--navy-20, #ccc)' }}>
                      <label style={{ fontSize: 11, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', opacity: 0.6 }}>Albaranes</label>
                    </div>
                    {routeDetail && routeDetail.orders.map(order => (
                      <button
                        key={order.client_id}
                        className="pm-item"
                        onClick={() => { openPdf(`/pdf/albaran/${selectedRoute.fecha}/${selectedRoute.ruta}/${order.client_id}`); setPdfMenuOpen(false); }}
                      >
                        <span className="pm-idx">AB</span>
                        <span className="pm-label">{order.client_name || order.client_id}</span>
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
          </div>
          <div className="panel-menu-wrap" ref={panelMenuRef}>
            <button className="panel-menu-btn" onClick={() => setPanelMenuOpen(prev => !prev)}>
              <span className="pm-icon">▦</span>
              Panels
              {collapsed.size > 0 && <span className="pm-badge">{collapsed.size}</span>}
            </button>
            {panelMenuOpen && (
              <div className="panel-menu">
                {SECTIONS.map(s => (
                  <button
                    key={s.id}
                    className={`pm-item ${collapsed.has(s.id) ? 'pm-hidden' : ''}`}
                    onClick={() => toggleCollapse(s.id)}
                  >
                    <span className={`pm-check ${collapsed.has(s.id) ? '' : 'pm-checked'}`} />
                    <span className="pm-idx">{s.index}</span>
                    <span className="pm-label">{s.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <span className="status-line">
            STOP {completed}/{visibleStops.length} &middot; NEXT {nextStop ? nextStop.code : '—'}
          </span>
          <span className="live-dot">
            <span className="pulse"></span>
            LIVE &middot; {now}
          </span>
        </div>
      </header>

      <main className="panels" style={panelGridStyle} ref={panelsRef}>
        {!collapsed.has('map') && (
          <Section id="map" collapsed={false} fullscreen={fullscreenPanel === 'map'} onToggleCollapse={toggleCollapse} onToggleFullscreen={toggleFullscreen}>
            <MapPanel
              stops={stops}
              warehouse={WAREHOUSE}
              onStopHover={setHoveredStop}
              onStopClick={handleStopClick}
              hoveredPalletStops={hoveredPalletStops}
              isFullscreen={fullscreenPanel === 'map'}
              isCollapsed={false}
            />
            {!collapsed.has('truck') && <DragHandle direction="horizontal" onDrag={handleColDrag('map', 'truck')} />}
            {collapsed.has('truck') && rightStackVisible && <DragHandle direction="horizontal" onDrag={handleColDrag('map', 'right')} />}
          </Section>
        )}

        {!collapsed.has('truck') && (
          <Section id="truck" collapsed={false} fullscreen={fullscreenPanel === 'truck'} onToggleCollapse={toggleCollapse} onToggleFullscreen={toggleFullscreen}>
            <TruckPanel
              mode={mode}
              onModeChange={handleModeChange}
              pallets={pallets}
              hoveredStop={hoveredStop}
              hoveredPallet={hoveredPallet}
              onPalletHover={setHoveredPallet}
              onPalletClick={handlePalletClick}
              selectedClient={selectedClient}
              truckType={truckType}
              onTruckTypeChange={handleTruckTypeChange}
            />
            {rightStackVisible && <DragHandle direction="horizontal" onDrag={handleColDrag('truck', 'right')} />}
          </Section>
        )}

        {rightStackVisible && (
          <div className="right-stack">
            {!collapsed.has('warehouse') && (
              <Section id="warehouse" collapsed={false} fullscreen={fullscreenPanel === 'warehouse'} onToggleCollapse={toggleCollapse} onToggleFullscreen={toggleFullscreen} style={!collapsed.has('copilot') ? { flex: `0 0 ${rightSplit * 100}%` } : undefined}>
                <WarehousePanel />
                {!collapsed.has('copilot') && <DragHandle direction="vertical" onDrag={handleRightStackDrag} />}
              </Section>
            )}
            {!collapsed.has('copilot') && (
              <Section id="copilot" collapsed={false} fullscreen={fullscreenPanel === 'copilot'} onToggleCollapse={toggleCollapse} onToggleFullscreen={toggleFullscreen}>
                <CopilotPanel
                  messages={messages}
                  onAsk={askCopilot}
                  isTyping={isTyping}
                  sysLog={sysLog}
                />
              </Section>
            )}
          </div>
        )}
      </main>

      <div className="ops-ticker">
        <span className="tk-label">OPS &middot; LIVE</span>
        <div className="tk-feed">
          <span className="tk-item"><b>RTE-A</b> &middot; 6.2 KM REMAINING</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">NEXT WAYPOINT <b>{nextStop ? nextStop.code : '—'}</b> &middot; ETA <b>{nextStop ? nextStop.eta : '—'}</b></span>
          <span className="tk-sep">/</span>
          <span className="tk-item">TRAFFIC <span className="green">&bull;</span> NORMAL &middot; C-17 OK</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">FUEL 73% &middot; TEMP 4&deg;C</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">TELEMATICS <span className="green">&bull;</span> SYNC</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">DEPOT RETURN <b>15:42</b></span>
        </div>
        <span className="tk-time">{now}:23 CET</span>
      </div>

      <Section id="metrics" collapsed={collapsed.has('metrics')} fullscreen={fullscreenPanel === 'metrics'} onToggleCollapse={toggleCollapse} onToggleFullscreen={toggleFullscreen} dark>
        <MetricsBar
          weights={weights}
          onWeightChange={handleWeightChange}
          metrics={metrics}
        />
      </Section>

      <footer className="footer">
        <span>DDI SMART TRUCK &middot; DISPATCHER CONSOLE &middot; v0.4 &middot; BUILD 2026.05.09</span>
        <span>RUN ID: DDI-04-20260509-A &middot; MLT-OPS-01 &middot; MOLLET DEL VALL&Egrave;S</span>
      </footer>

      {fullscreenPanel && <div className="fullscreen-backdrop" onClick={() => setFullscreenPanel(null)} />}
    </div>
  );
}
