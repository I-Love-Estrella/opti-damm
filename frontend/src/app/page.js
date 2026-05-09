'use client';

import { useState, useMemo, useCallback } from 'react';
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

const MapPanel = dynamic(() => import('@/components/MapPanel'), { ssr: false });

const PROMPT_DEFS = [
  { id: "why",      label: "Why this route order?",        kind: "explain-route" },
  { id: "compare",  label: "Compare loading modes",        kind: "compare-load" },
  { id: "traffic",  label: "Traffic on C-17 14:00",        kind: "traffic", alert: true },
  { id: "cancel",   label: "Cancel Cafeteria Pradals",     kind: "cancel",  alert: true },
  { id: "reset",    label: "↻ Reset scenario",             kind: "reset",   disabled: true },
];

const INITIAL_MESSAGES = [
  { kind: "claude", text: 'Good morning, Manel. <b>Truck 04</b> is loaded — route DR0054 heading north from Mollet. Driver J. Martínez left the depot at 08:14. Three stops complete, four to go. The route is optimised for <b>distance first</b>, with a soft preference for grouping returnables.' },
  { kind: "claude", text: 'Current ETA back at depot: <b>15:42</b>. Score: 87/100. Ask me anything — or pull a slider and I\'ll re-plan live.' },
];

const INITIAL_LOG = [
  { t: "08:14:02", tag: "DEPART", level: "ok",   msg: "TRK-04 · DDI MOLLET DEPOT" },
  { t: "08:42:18", tag: "STOP",   level: "ok",   msg: "S-01 LOS TERESITOS · 1 PLT DELIVERED" },
  { t: "09:38:44", tag: "STOP",   level: "ok",   msg: "S-02 VIENA GRANOLLERS · 2 PLT DELIVERED" },
  { t: "10:24:09", tag: "STOP",   level: "ok",   msg: "S-03 FRANKFURT LEO BOECK · 1 PLT DELIVERED" },
  { t: "10:48:01", tag: "PING",   level: "info", msg: "GPS LOCK · 41.886°N 2.254°E · 23 KM/H" },
];

export default function Page() {
  const [stops, setStops] = useState(STOPS);
  const [mode, setMode] = useState("reference");
  const [truckType, setTruckType] = useState("T8");
  const [hoveredStop, setHoveredStop] = useState(null);
  const [hoveredPallet, setHoveredPallet] = useState(null);
  const [selectedClient, setSelectedClient] = useState(null);
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isTyping, setIsTyping] = useState(false);
  const [scenario, setScenario] = useState(null);
  const [weights, setWeights] = useState({ route: 60, load: 25, unload: 15 });
  const [now] = useState("10:48");
  const [sysLog, setSysLog] = useState(INITIAL_LOG);

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

  const promptList = PROMPT_DEFS.map(p => {
    if (p.kind === "reset") return { ...p, disabled: !scenario };
    if (p.kind === "traffic" && scenario === "traffic") return { ...p, disabled: true };
    if (p.kind === "cancel" && scenario === "cancel") return { ...p, disabled: true };
    return p;
  });

  const pushMsg = useCallback((m, delay = 0) => {
    if (delay) {
      setIsTyping(true);
      setTimeout(() => {
        setIsTyping(false);
        setMessages(prev => [...prev, m]);
      }, delay);
    } else {
      setMessages(prev => [...prev, m]);
    }
  }, []);

  function handlePrompt(p) {
    if (p.kind === "explain-route") {
      pushMsg({ kind: "user", text: "Why this route order?" });
      pushMsg({ kind: "claude", text: 'Three constraints stacked: <b>tight windows first</b> (Los Teresitos 08:30, Viena Granollers 09:00), then <b>geographic clustering</b> through Granollers and Vic, and finally <b>Hospital de Manlleu</b> last because it\'s flagged high-priority and the driver wants the empties pickup on the return leg via C-17. Reversing 5 and 6 saves 0.4 km but breaks Area Truck Shell\'s window.' }, 700);
      pushLog({ tag: "QUERY", level: "info", msg: "EXPLAIN-ROUTE · RTE-A" });
    }
    if (p.kind === "compare-load") {
      pushMsg({ kind: "user", text: "Compare loading modes." });
      pushMsg({ kind: "claude", text: '<b>By Reference</b>: fastest pick at the depot — same SKUs together. Slower at each stop. <b>By Client</b>: 4 min faster per stop on average; bigger pick window. <b>Hybrid</b>: client groups but heavies near the cab, balances axle weight. Right now you\'re on <b>By Reference</b> — switch to Hybrid for this run; it shaves ~7 minutes.' }, 750);
      pushLog({ tag: "QUERY", level: "info", msg: "COMPARE-LOAD · 3 MODES" });
    }
    if (p.kind === "traffic") {
      setScenario("traffic");
      pushMsg({ kind: "alert", text: "Traffic incident — C-17 closed at Centelles 14:00–14:40. Three upcoming stops affected." });
      pushMsg({ kind: "user", text: "Re-plan around it." });
      pushMsg({ kind: "claude", text: 'Already done. Routing through <b>BV-5301</b> instead of C-17 between Vic and Manlleu. Adds 1.8 km but stays within all delivery windows. <b>Hospital de Manlleu</b> moves from 14:00 to 14:18 — still on time. Score drops 87 → 82.' }, 800);
      setWeights(w => ({ ...w, route: Math.max(30, w.route - 8), unload: Math.min(40, w.unload + 5) }));
      pushLog({ tag: "ALERT", level: "info", msg: "TRAFFIC · C-17 CLOSED 14:00-14:40" });
      pushLog({ tag: "REPLAN", level: "ok", msg: "RTE-A → RTE-A2 · BV-5301 DETOUR" });
    }
    if (p.kind === "cancel") {
      setScenario("cancel");
      pushMsg({ kind: "alert", text: "Cafeteria Pradals cancelled the order. 1 pallet, EST 33CL × 24." });
      pushMsg({ kind: "user", text: "Drop the stop and re-optimise." });
      setTimeout(() => {
        setStops(prev => prev.map(s => s.id === 4 ? { ...s, cancelled: true } : s));
        if (selectedClient && selectedClient.id === 4) setSelectedClient(null);
      }, 250);
      pushMsg({ kind: "claude", text: 'Stop dropped. Route shortened from 7 to 6 stops, distance falls <b>11.4 km</b>, ETA back to depot now <b>14:58</b>. EST pallet stays loaded — re-route to tomorrow\'s 08:30 delivery. <b>Score climbs to 91</b>.' }, 900);
      setWeights(w => ({ ...w, load: Math.min(40, w.load + 6) }));
      pushLog({ tag: "CANCEL", level: "info", msg: "S-04 CAFETERIA PRADALS · 1 PLT EST RE-ROUTED" });
      pushLog({ tag: "REPLAN", level: "ok", msg: "RTE-A → RTE-B · 6 STOPS · -11.4 KM" });
    }
    if (p.kind === "reset") {
      setStops(STOPS);
      setScenario(null);
      setSelectedClient(null);
      setWeights({ route: 60, load: 25, unload: 15 });
      pushMsg({ kind: "claude", text: 'Scenario reset. Back to the original 7-stop run.' });
      pushLog({ tag: "RESET", level: "info", msg: "BASELINE RESTORED · RTE-A" });
    }
  }

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
  const visibleStops = stops.filter(s => !s.cancelled);
  const completed = stops.filter(s => s.status === "completed" && !s.cancelled).length;
  const nextStop = visibleStops.find(s => s.status === "current") || visibleStops.find(s => s.status === "upcoming");

  return (
    <div className="app">
      <div className="classification">
        <div className="cl-left">
          <span className="chip red">DDI · INTERNAL</span>
          <span className="chip">OPS / DISPATCH</span>
          <span className="sep">/</span>
          <span>TRK-04 · DRIVER MARTÍNEZ</span>
          <span className="sep">/</span>
          <span>DR0054 · MOLLET → VALLÈS</span>
        </div>
        <div className="cl-right">
          <span className="session">SESSION 0481</span>
          <span className="sep">·</span>
          <span>NODE MLT-OPS-01</span>
          <span className="sep">·</span>
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
            <span>MGR · M. PUIG</span>
            <span className="sep">·</span>
            <span>{spec.code} · {spec.capacity} PLT</span>
            <span className="sep">·</span>
            <span>DRV · J. MARTÍNEZ</span>
            <span className="sep">·</span>
            <span>MOLLET → VALLÈS</span>
          </div>
        </div>
        <div className="header-right">
          <span className="status-line">
            STOP {completed}/{visibleStops.length} · NEXT {nextStop ? nextStop.code : '—'}
          </span>
          <span className="live-dot">
            <span className="pulse"></span>
            LIVE · {now}
          </span>
        </div>
      </header>

      <main className="panels">
        <MapPanel
          stops={stops}
          warehouse={WAREHOUSE}
          onStopHover={setHoveredStop}
          onStopClick={handleStopClick}
          hoveredPalletStops={hoveredPalletStops}
        />
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
        <div className="right-stack">
          <WarehousePanel />
          <CopilotPanel
            messages={messages}
            prompts={promptList}
            onPrompt={handlePrompt}
            isTyping={isTyping}
            sysLog={sysLog}
          />
        </div>
      </main>

      <div className="ops-ticker">
        <span className="tk-label">OPS · LIVE</span>
        <div className="tk-feed">
          <span className="tk-item"><b>RTE-A</b> · 6.2 KM REMAINING</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">NEXT WAYPOINT <b>{nextStop ? nextStop.code : '—'}</b> · ETA <b>{nextStop ? nextStop.eta : '—'}</b></span>
          <span className="tk-sep">/</span>
          <span className="tk-item">TRAFFIC <span className="green">●</span> NORMAL · C-17 OK</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">FUEL 73% · TEMP 4°C</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">TELEMATICS <span className="green">●</span> SYNC</span>
          <span className="tk-sep">/</span>
          <span className="tk-item">DEPOT RETURN <b>15:42</b></span>
        </div>
        <span className="tk-time">{now}:23 CET</span>
      </div>

      <MetricsBar
        weights={weights}
        onWeightChange={handleWeightChange}
        metrics={metrics}
      />

      <footer className="footer">
        <span>DDI SMART TRUCK · DISPATCHER CONSOLE · v0.4 · BUILD 2026.05.09</span>
        <span>RUN ID: DDI-04-20260509-A · MLT-OPS-01 · MOLLET DEL VALLÈS</span>
      </footer>
    </div>
  );
}
