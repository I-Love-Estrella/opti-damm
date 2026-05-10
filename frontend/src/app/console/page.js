"use client";

import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import dynamic from "next/dynamic";
import TruckPanel from "@/components/TruckPanel";
import CopilotPanel from "@/components/CopilotPanel";
import MetricsBar from "@/components/MetricsBar";

import { api, SIM_API_BASE } from "@/lib/api";
import {
  adaptStops,
  adaptPallets,
  adaptMetrics,
  adaptDepot,
  adaptTruck,
} from "@/lib/adapters";

const MapPanel = dynamic(() => import("@/components/MapPanel"), { ssr: false });

const SECTIONS = [
  { id: "map", index: "01", title: "Route" },
  { id: "truck", index: "02", title: "Load" },
  { id: "copilot", index: "03", title: "Co-pilot" },
  { id: "metrics", index: "—", title: "Metrics" },
];

function Section({
  id,
  collapsed,
  fullscreen,
  onToggleCollapse,
  onToggleFullscreen,
  dark,
  style,
  children,
}) {
  const cls = [
    "section",
    collapsed && "section-collapsed",
    fullscreen && "section-fullscreen",
    dark && "section-dark",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} style={style}>
      <div className="section-controls">
        <button
          className="sc-btn"
          onClick={() => onToggleCollapse(id)}
          title="Hide panel"
        >
          {"−"}
        </button>
        <button
          className="sc-btn"
          onClick={() => onToggleFullscreen(id)}
          title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
        >
          {fullscreen ? "✕" : "⤢"}
        </button>
      </div>
      {children}
    </div>
  );
}

function DragHandle({ direction, onDrag }) {
  const handleRef = useRef(null);

  const onMouseDown = useCallback(
    (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startY = e.clientY;

      const onMouseMove = (e) => {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        onDrag(direction === "horizontal" ? dx : dy, false);
      };

      const onMouseUp = (e) => {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        onDrag(direction === "horizontal" ? dx : dy, true);
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
      document.body.style.cursor =
        direction === "horizontal" ? "col-resize" : "row-resize";
      document.body.style.userSelect = "none";
    },
    [direction, onDrag],
  );

  return (
    <div
      ref={handleRef}
      className={`drag-handle drag-handle-${direction}`}
      onMouseDown={onMouseDown}
    />
  );
}

export default function Page() {
  const [hoveredStop, setHoveredStop] = useState(null);
  const [hoveredPallet, setHoveredPallet] = useState(null);
  const [selectedPallet, setSelectedPallet] = useState(null);
  const [selectedClient, setSelectedClient] = useState(null);
  const [operatorActions, setOperatorActions] = useState([]);
  const [messages, setMessages] = useState([]);
  const [isTyping, setIsTyping] = useState(false);
  const [sysLog, setSysLog] = useState([]);
  const [collapsed, setCollapsed] = useState(new Set());
  const [fullscreenPanel, setFullscreenPanel] = useState(null);
  const [panelMenuOpen, setPanelMenuOpen] = useState(false);
  const [pdfMenuOpen, setPdfMenuOpen] = useState(false);
  const [availableRoutes, setAvailableRoutes] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [routeDetail, setRouteDetail] = useState(null);
  const [algorithms, setAlgorithms] = useState([]);
  const [selectedAlgo, setSelectedAlgo] = useState(null);
  const [simulationResult, setSimulationResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const pdfMenuRef = useRef(null);
  const [colWidths, setColWidths] = useState({ map: 1.25, truck: 1, right: 1 });
  const [rightSplit, setRightSplit] = useState(0.5);
  const panelMenuRef = useRef(null);
  const panelsRef = useRef(null);
  const dragStartWidths = useRef(null);
  const dragStartRightSplit = useRef(null);

  const pushLog = useCallback((entry) => {
    const d = new Date();
    const t = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    setSysLog((prev) => [...prev, { t, ...entry }]);
  }, []);

  const pushOperatorAction = useCallback((action) => {
    const d = new Date();
    const t = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
    setOperatorActions((prev) => [...prev.slice(-11), { t, ...action }]);
  }, []);

  const toggleCollapse = useCallback((id) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    setFullscreenPanel((prev) => (prev === id ? null : prev));
  }, []);

  const toggleFullscreen = useCallback((id) => {
    setFullscreenPanel((prev) => (prev === id ? null : id));
    setCollapsed((prev) => {
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
      if (e.key === "Escape") {
        setFullscreenPanel(null);
        setPanelMenuOpen(false);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  useEffect(() => {
    Promise.all([api.routes(), api.algorithms()])
      .then(([routes, algos]) => {
        setAvailableRoutes(routes || []);
        if (routes?.length > 0) setSelectedRoute(routes[0]);
        const algoList = algos?.algorithms || [];
        setAlgorithms(algoList);
        if (algoList.length > 0) {
          setSelectedAlgo(algoList[0].name);
          pushLog({
            tag: "ALGO",
            level: "info",
            msg: `SELECTED · ${algoList[0].name.toUpperCase()}`,
          });
        }
      })
      .catch((err) => setError(err?.message || "API unreachable"));
  }, [pushLog]);

  useEffect(() => {
    if (!selectedRoute) return;
    api
      .routeDetail(selectedRoute.fecha, selectedRoute.ruta)
      .then(setRouteDetail)
      .catch(() => setRouteDetail(null));
  }, [selectedRoute]);

  useEffect(() => {
    setSelectedClient(null);
    setSelectedPallet(null);
    setHoveredStop(null);
    setHoveredPallet(null);
  }, [selectedRoute, selectedAlgo, simulationResult]);

  useEffect(() => {
    if (!selectedRoute || !selectedAlgo) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    pushLog({
      tag: "SIM",
      level: "info",
      msg: `RUN · ${selectedRoute.fecha} ${selectedRoute.ruta} · ${selectedAlgo.toUpperCase()}`,
    });
    api
      .run({
        date: selectedRoute.fecha,
        ruta: selectedRoute.ruta,
        algo: selectedAlgo,
      })
      .then((res) => {
        if (cancelled) return;
        setSimulationResult(res);
        pushLog({
          tag: "SIM",
          level: "ok",
          msg: `OK · ${res?.stops?.length || 0} STOPS`,
        });
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || "Simulation failed");
        setSimulationResult(null);
        pushLog({
          tag: "SIM",
          level: "info",
          msg: `ERROR · ${err?.message || "unknown"}`,
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRoute, selectedAlgo, pushLog]);

  useEffect(() => {
    if (!pdfMenuOpen) return;
    const handler = (e) => {
      if (pdfMenuRef.current && !pdfMenuRef.current.contains(e.target)) {
        setPdfMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [pdfMenuOpen]);

  const openPdf = useCallback((path) => {
    window.open(`${SIM_API_BASE}${path}`, "_blank");
  }, []);

  useEffect(() => {
    if (!panelMenuOpen) return;
    const handler = (e) => {
      if (panelMenuRef.current && !panelMenuRef.current.contains(e.target)) {
        setPanelMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [panelMenuOpen]);

  const rightStackVisible = !collapsed.has("copilot");

  const panelGridStyle = useMemo(() => {
    const cols = [];
    if (!collapsed.has("map")) cols.push(`${colWidths.map}fr`);
    if (!collapsed.has("truck")) cols.push(`${colWidths.truck}fr`);
    if (rightStackVisible) cols.push(`${colWidths.right}fr`);
    if (cols.length === 0) cols.push("1fr");
    return { gridTemplateColumns: cols.join(" ") };
  }, [collapsed, rightStackVisible, colWidths]);

  const appGridStyle = useMemo(
    () => ({
      gridTemplateRows: `22px 50px 1fr ${collapsed.has("metrics") ? "0" : "160px"} 26px`,
    }),
    [collapsed],
  );

  const handleColDrag = useCallback(
    (leftKey, rightKey) => (delta, done) => {
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
      const visibleKeys = ["map", "truck", "right"].filter((k) =>
        k === "right" ? rightStackVisible : !collapsed.has(k),
      );
      const totalFr = visibleKeys.reduce((a, k) => a + start[k], 0);
      const pxPerFr = totalWidth / totalFr;
      const deltaFr = delta / pxPerFr;
      setColWidths({
        ...start,
        [leftKey]: Math.max(0.3, start[leftKey] + deltaFr),
        [rightKey]: Math.max(0.3, start[rightKey] - deltaFr),
      });
    },
    [colWidths, collapsed, rightStackVisible],
  );

  const handleRightStackDrag = useCallback(
    (delta, done) => {
      if (done) {
        dragStartRightSplit.current = null;
        return;
      }
      const stack = panelsRef.current?.querySelector(".right-stack");
      if (!stack) return;
      const totalHeight = stack.offsetHeight;
      if (!dragStartRightSplit.current) {
        dragStartRightSplit.current = rightSplit;
      }
      const startFrac = dragStartRightSplit.current;
      const deltaFrac = delta / totalHeight;
      setRightSplit(Math.max(0.15, Math.min(0.85, startFrac + deltaFrac)));
    },
    [rightSplit],
  );

  const stops = useMemo(
    () => (simulationResult ? adaptStops(simulationResult) : []),
    [simulationResult],
  );
  const pallets = useMemo(
    () => (simulationResult ? adaptPallets(simulationResult) : []),
    [simulationResult],
  );
  const depot = useMemo(
    () => (simulationResult ? adaptDepot(simulationResult) : null),
    [simulationResult],
  );
  const truck = useMemo(
    () => (simulationResult ? adaptTruck(simulationResult) : null),
    [simulationResult],
  );
  const kpis = useMemo(
    () => (simulationResult ? adaptMetrics(simulationResult) : null),
    [simulationResult],
  );

  const filledPallets = pallets.filter((p) => p.sku);
  const returnableCount = filledPallets.length
    ? Math.round(
        (filledPallets.filter((p) => p.ret).length / filledPallets.length) *
          100,
      )
    : 0;

  const buildCopilotContext = useCallback(
    (overrides = {}) => {
      const routeSummary = routeDetail
        ? {
            date: routeDetail.date,
            ruta: routeDetail.ruta,
            repartidor: routeDetail.repartidor,
            truck: routeDetail.truck,
            transports: routeDetail.transports,
            n_clients: routeDetail.n_clients,
            total_volume_m3: routeDetail.total_volume_m3,
            orders: routeDetail.orders?.map((order) => ({
              client_id: order.client_id,
              client_name: order.client_name,
              visit_seq: order.visit_seq,
              expected_returnable_units: order.expected_returnable_units,
              total_volume_m3: order.total_volume_m3,
              total_weight_kg: order.total_weight_kg,
              line_count: order.lines?.length || 0,
              lines: order.lines?.slice(0, 8),
            })),
          }
        : null;

      return {
        selected_route: selectedRoute,
        selected_algorithm: selectedAlgo,
        route_detail: routeSummary,
        selected_client: selectedClient,
        selected_stop: selectedClient
          ? {
              id: selectedClient.id,
              visit_seq: selectedClient.visit_seq,
              code: selectedClient.code,
              name: selectedClient.name,
              client_id: selectedClient.client_id,
              eta: selectedClient.eta,
              pallets: selectedClient.pallets,
            }
          : null,
        hovered_stop: hoveredStop,
        selected_pallet: selectedPallet
          ? {
              idx: selectedPallet.idx,
              code: selectedPallet.code,
              sku: selectedPallet.sku,
              stop: selectedPallet.stop,
              client: selectedPallet.client,
              wt: selectedPallet.wt,
              ret: selectedPallet.ret,
              items: selectedPallet.items,
            }
          : null,
        hovered_pallet: hoveredPallet
          ? {
              idx: hoveredPallet.idx,
              code: hoveredPallet.code,
              sku: hoveredPallet.sku,
              stop: hoveredPallet.stop,
              client: hoveredPallet.client,
              wt: hoveredPallet.wt,
              ret: hoveredPallet.ret,
            }
          : null,
        ui_state: {
          fullscreen_panel: fullscreenPanel,
          collapsed_panels: Array.from(collapsed),
          panel_menu_open: panelMenuOpen,
          pdf_menu_open: pdfMenuOpen,
          layout: {
            col_widths: colWidths,
            right_split: rightSplit,
          },
          loading,
          error,
          simulation_ready: Boolean(simulationResult),
        },
        kpis: kpis?.all || null,
        visible_stops: stops.map((s) => ({
          id: s.id,
          code: s.code,
          name: s.name,
          eta: s.eta,
          pallets: s.pallets,
          client_id: s.client_id,
        })),
        pallet_summary: {
          total_slots: pallets.length,
          filled_slots: filledPallets.length,
          returnable_percent: returnableCount,
          sample: pallets.filter((p) => p.sku).slice(0, 16),
        },
        recent_operator_actions: operatorActions.slice(-8),
        system_log: sysLog.slice(-8),
        ...overrides,
      };
    },
    [
      selectedRoute,
      selectedAlgo,
      routeDetail,
      selectedClient,
      hoveredStop,
      selectedPallet,
      hoveredPallet,
      fullscreenPanel,
      collapsed,
      panelMenuOpen,
      pdfMenuOpen,
      colWidths,
      rightSplit,
      loading,
      error,
      simulationResult,
      kpis,
      stops,
      pallets,
      filledPallets.length,
      returnableCount,
      operatorActions,
      sysLog,
    ],
  );

  const askCopilot = useCallback(
    async (text, contextOverrides = {}) => {
      const userText = text.trim();
      if (!userText || isTyping) return;

      const outgoingMessages = messages
        .filter((m) => m.kind === "user" || m.kind === "claude")
        .slice(-10)
        .map((m) => ({
          role: m.kind === "user" ? "user" : "assistant",
          text: m.text,
        }));

      setMessages((prev) => [...prev, { kind: "user", text: userText }]);
      setIsTyping(true);
      pushLog({ tag: "COPILOT", level: "info", msg: "GEMINI REQUEST" });

      try {
        const response = await api.copilotChat({
          message: userText,
          messages: outgoingMessages,
          frontendContext: buildCopilotContext(contextOverrides),
        });
        setMessages((prev) => [
          ...prev,
          { kind: "claude", text: response.reply || "" },
        ]);
        const tools = response.tool_calls?.map((t) => t.name).filter(Boolean);
        pushLog({
          tag: "COPILOT",
          level: "ok",
          msg: tools?.length
            ? `TOOLS · ${tools.join(", ").toUpperCase()}`
            : `MODEL · ${response.model}`,
        });
      } catch (err) {
        const detail =
          err instanceof Error ? err.message : "Unknown copilot error";
        setMessages((prev) => [
          ...prev,
          { kind: "alert", text: `Copilot unavailable: ${detail}` },
        ]);
        pushLog({
          tag: "COPILOT",
          level: "info",
          msg: "ERROR · CHECK GEMINI API KEY",
        });
      } finally {
        setIsTyping(false);
      }
    },
    [buildCopilotContext, isTyping, messages, pushLog],
  );

  const hoveredPalletStops = hoveredPallet ? [hoveredPallet.stop] : [];

  function handleStopClick(stop) {
    setSelectedClient(stop);
    setSelectedPallet(null);
    pushOperatorAction({
      type: "select_stop",
      route: selectedRoute
        ? `${selectedRoute.fecha} ${selectedRoute.ruta}`
        : null,
      stop_id: stop.id,
      client_id: stop.client_id,
      label: stop.name,
    });
  }

  function handlePalletClick(p) {
    setSelectedPallet(p);
    const s = stops.find((s) => s.id === p.stop);
    if (s) {
      setSelectedClient(s);
    }
    pushOperatorAction({
      type: "select_pallet",
      route: selectedRoute
        ? `${selectedRoute.fecha} ${selectedRoute.ruta}`
        : null,
      pallet_code: p.code,
      slot_index: p.idx,
      stop_id: p.stop,
      client: p.client,
      sku: p.sku,
    });
  }

  const truckCode = truck?.code || "—";
  const truckCap = truck?.capacity || 0;
  const routeLabel = selectedRoute
    ? `${selectedRoute.fecha} · ${selectedRoute.ruta}`
    : "—";
  const repartidor = routeDetail?.repartidor || "—";

  return (
    <div className="app" style={appGridStyle}>
      <div className="classification">
        <div className="cl-left">
          <span className="chip red">DDI &middot; PLANNING CONSOLE</span>
          <span className="chip">OPS / DISPATCH</span>
          <span className="sep">/</span>
          <span>{routeLabel}</span>
          {truck && (
            <>
              <span className="sep">/</span>
              <span>
                {truckCode} &middot; {truckCap} PLT
              </span>
            </>
          )}
        </div>
        <div className="cl-right">
          <span>
            {selectedAlgo ? `ALGO · ${selectedAlgo.toUpperCase()}` : "ALGO · —"}
          </span>
          <span className="sep">&middot;</span>
          <span>{loading ? "PLANNING…" : error ? "API OFFLINE" : "READY"}</span>
        </div>
      </div>

      <header className="header">
        <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
          <div className="wordmark">
            <span className="ddi">DDI</span>
            <span className="smart">Smart Truck</span>
          </div>
          <div className="header-meta">
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                style={{
                  fontSize: 11,
                  opacity: 0.6,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                Route
              </span>
              <select
                value={
                  selectedRoute
                    ? `${selectedRoute.fecha}|${selectedRoute.ruta}`
                    : ""
                }
                onChange={(e) => {
                  const [fecha, ruta] = e.target.value.split("|");
                  const r = availableRoutes.find(
                    (r) => r.fecha === fecha && r.ruta === ruta,
                  );
                  if (r) {
                    setSelectedRoute(r);
                    pushOperatorAction({
                      type: "select_route",
                      route: `${r.fecha} ${r.ruta}`,
                      clients: r.clients,
                    });
                  }
                }}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                  padding: "3px 6px",
                  background: "var(--cream, #faf9f6)",
                  border: "1px solid #ccc",
                  borderRadius: 4,
                }}
              >
                {availableRoutes.map((r) => (
                  <option
                    key={`${r.fecha}|${r.ruta}`}
                    value={`${r.fecha}|${r.ruta}`}
                  >
                    {r.fecha} · {r.ruta} · {r.clients} clients
                  </option>
                ))}
              </select>
            </label>
            <span className="sep">&middot;</span>
            <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                style={{
                  fontSize: 11,
                  opacity: 0.6,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                Algo
              </span>
              <select
                value={selectedAlgo || ""}
                onChange={(e) => {
                  setSelectedAlgo(e.target.value);
                  pushOperatorAction({
                    type: "select_algorithm",
                    route: selectedRoute
                      ? `${selectedRoute.fecha} ${selectedRoute.ruta}`
                      : null,
                    algorithm: e.target.value,
                  });
                }}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                  padding: "3px 6px",
                  background: "var(--cream, #faf9f6)",
                  border: "1px solid #ccc",
                  borderRadius: 4,
                }}
              >
                {algorithms.map((a) => (
                  <option key={a.name} value={a.name}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            {repartidor !== "—" && (
              <>
                <span className="sep">&middot;</span>
                <span>DRV &middot; {repartidor}</span>
              </>
            )}
          </div>
        </div>
        <div className="header-right">
          <div className="panel-menu-wrap" ref={pdfMenuRef}>
            <button
              className="panel-menu-btn"
              onClick={() => setPdfMenuOpen((prev) => !prev)}
            >
              <span className="pm-icon">⎙</span>
              Documents
            </button>
            {pdfMenuOpen && selectedRoute && (
              <div className="panel-menu" style={{ width: 320 }}>
                <button
                  className="pm-item"
                  onClick={() => {
                    openPdf(
                      `/pdf/hoja-carga/${selectedRoute.fecha}/${selectedRoute.ruta}`,
                    );
                    setPdfMenuOpen(false);
                  }}
                >
                  <span className="pm-idx">HC</span>
                  <span className="pm-label">Hoja de Carga</span>
                </button>
                <button
                  className="pm-item"
                  onClick={() => {
                    openPdf(
                      `/pdf/hoja-ruta/${selectedRoute.fecha}/${selectedRoute.ruta}`,
                    );
                    setPdfMenuOpen(false);
                  }}
                >
                  <span className="pm-idx">HR</span>
                  <span className="pm-label">Hoja de Ruta</span>
                </button>
                <div
                  style={{
                    padding: "4px 10px 2px",
                    borderTop: "1px solid var(--navy-20, #ccc)",
                  }}
                >
                  <label
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                      opacity: 0.6,
                    }}
                  >
                    Albaranes
                  </label>
                </div>
                {routeDetail &&
                  routeDetail.orders?.map((order) => (
                    <button
                      key={order.client_id}
                      className="pm-item"
                      onClick={() => {
                        openPdf(
                          `/pdf/albaran/${selectedRoute.fecha}/${selectedRoute.ruta}/${order.client_id}`,
                        );
                        setPdfMenuOpen(false);
                      }}
                    >
                      <span className="pm-idx">AB</span>
                      <span className="pm-label">
                        {order.client_name || order.client_id}
                      </span>
                    </button>
                  ))}
              </div>
            )}
          </div>
          <div className="panel-menu-wrap" ref={panelMenuRef}>
            <button
              className="panel-menu-btn"
              onClick={() => setPanelMenuOpen((prev) => !prev)}
            >
              <span className="pm-icon">▦</span>
              Panels
              {collapsed.size > 0 && (
                <span className="pm-badge">{collapsed.size}</span>
              )}
            </button>
            {panelMenuOpen && (
              <div className="panel-menu">
                {SECTIONS.map((s) => (
                  <button
                    key={s.id}
                    className={`pm-item ${collapsed.has(s.id) ? "pm-hidden" : ""}`}
                    onClick={() => toggleCollapse(s.id)}
                  >
                    <span
                      className={`pm-check ${collapsed.has(s.id) ? "" : "pm-checked"}`}
                    />
                    <span className="pm-idx">{s.index}</span>
                    <span className="pm-label">{s.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </header>

      {error && (
        <div
          style={{
            gridColumn: "1 / -1",
            padding: "8px 16px",
            background: "#fde8e8",
            color: "#a33",
            fontFamily: "var(--mono)",
            fontSize: 12,
            borderBottom: "1px solid #c88",
          }}
        >
          API error: {error}
        </div>
      )}

      <main className="panels" style={panelGridStyle} ref={panelsRef}>
        {!collapsed.has("map") && (
          <Section
            id="map"
            collapsed={false}
            fullscreen={fullscreenPanel === "map"}
            onToggleCollapse={toggleCollapse}
            onToggleFullscreen={toggleFullscreen}
          >
            {depot ? (
              <MapPanel
                stops={stops}
                warehouse={depot}
                onStopHover={setHoveredStop}
                onStopClick={handleStopClick}
                hoveredPalletStops={hoveredPalletStops}
                isFullscreen={fullscreenPanel === "map"}
                isCollapsed={false}
              />
            ) : (
              <div
                style={{ padding: 24, fontFamily: "var(--mono)", opacity: 0.6 }}
              >
                {loading
                  ? "Loading simulation…"
                  : "No plan yet — pick a route + algorithm."}
              </div>
            )}
            {!collapsed.has("truck") && (
              <DragHandle
                direction="horizontal"
                onDrag={handleColDrag("map", "truck")}
              />
            )}
            {collapsed.has("truck") && rightStackVisible && (
              <DragHandle
                direction="horizontal"
                onDrag={handleColDrag("map", "right")}
              />
            )}
          </Section>
        )}

        {!collapsed.has("truck") && (
          <Section
            id="truck"
            collapsed={false}
            fullscreen={fullscreenPanel === "truck"}
            onToggleCollapse={toggleCollapse}
            onToggleFullscreen={toggleFullscreen}
          >
            {truck ? (
              <TruckPanel
                pallets={pallets}
                hoveredStop={hoveredStop}
                hoveredPallet={hoveredPallet}
                onPalletHover={setHoveredPallet}
                onPalletClick={handlePalletClick}
                selectedClient={selectedClient}
                truck={truck}
              />
            ) : (
              <div
                style={{ padding: 24, fontFamily: "var(--mono)", opacity: 0.6 }}
              >
                {loading ? "Loading load plan…" : "No load plan."}
              </div>
            )}
            {rightStackVisible && (
              <DragHandle
                direction="horizontal"
                onDrag={handleColDrag("truck", "right")}
              />
            )}
          </Section>
        )}

        {rightStackVisible && (
          <div className="right-stack">
            {!collapsed.has("copilot") && (
              <Section
                id="copilot"
                collapsed={false}
                fullscreen={fullscreenPanel === "copilot"}
                onToggleCollapse={toggleCollapse}
                onToggleFullscreen={toggleFullscreen}
              >
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

      <Section
        id="metrics"
        collapsed={collapsed.has("metrics")}
        fullscreen={fullscreenPanel === "metrics"}
        onToggleCollapse={toggleCollapse}
        onToggleFullscreen={toggleFullscreen}
        dark
      >
        <MetricsBar
          kpis={kpis}
          fullscreen={fullscreenPanel === "metrics"}
          routeDetail={routeDetail}
          simStops={stops}
        />
      </Section>

      <footer className="footer">
        <span>DDI SMART TRUCK &middot; PLANNING CONSOLE &middot; v0.5</span>
        <span>
          {selectedRoute
            ? `${selectedRoute.fecha} · ${selectedRoute.ruta}`
            : "—"}{" "}
          {selectedAlgo ? `· ${selectedAlgo}` : ""}
        </span>
      </footer>

      {fullscreenPanel && (
        <div
          className="fullscreen-backdrop"
          onClick={() => setFullscreenPanel(null)}
        />
      )}
    </div>
  );
}
