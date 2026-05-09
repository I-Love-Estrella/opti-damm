"""HTTP API for the simulator. Stdlib-only — no FastAPI/Flask.

Endpoints
---------
GET  /api/algorithms                          → list registered algorithms
GET  /api/days?min_clients=5&head=20          → list available (date, ruta) cases
POST /api/run    body: {date, ruta, algo}     → KPIs + plan summary for one run
POST /api/bench  body: {algos, max_cases, seed}  → summary + head-to-head

Run with:  python3 -m simulator.api  [--port 8000]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from simulator.algorithms import REGISTRY, get
from simulator.bench.runner import BenchConfig, run as bench_run
from simulator.core.simulator import Simulator
from simulator.data.catalog import (
    Catalog,
    PHYSICAL_TYPE_CODE,
    PHYSICAL_TYPE_LABEL,
    PhysicalType,
)
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute
from simulator.validation import validate_plan


_LOCK = threading.Lock()
_CACHE: dict[str, object] = {}


def _ctx() -> dict[str, object]:
    """Lazy-init heavy data structures once per process."""
    with _LOCK:
        if "builder" not in _CACHE:
            raw = load_all()
            catalog = Catalog.build(raw)
            clients = Clients.build(raw)
            builder = DayCaseBuilder(raw, catalog, clients)
            network = Network()
            sim = Simulator(clients=clients, network=network)
            _CACHE.update(
                {
                    "raw": raw,
                    "catalog": catalog,
                    "clients": clients,
                    "builder": builder,
                    "network": network,
                    "sim": sim,
                }
            )
    return _CACHE


def _list_physical_types() -> list[dict]:
    return [
        {
            "value": t.value,
            "label": PHYSICAL_TYPE_LABEL[t],
            "code": PHYSICAL_TYPE_CODE[t],
        }
        for t in PhysicalType
    ]


def _list_algorithms() -> list[dict]:
    out = []
    for name, cls in REGISTRY.items():
        out.append(
            {
                "name": name,
                "description": getattr(cls, "description", "") or name,
            }
        )
    return out


def _list_days(min_clients: int, head: int) -> dict:
    ctx = _ctx()
    builder: DayCaseBuilder = ctx["builder"]  # type: ignore
    df = builder.list_day_cases(min_clients=min_clients)
    if df.empty:
        return {"total": 0, "items": []}
    head = max(1, min(head, len(df)))
    items = []
    for _, row in df.head(head).iterrows():
        fecha = row["fecha"]
        if isinstance(fecha, dt.datetime):
            fecha = fecha.date()
        items.append(
            {
                "date": str(fecha),
                "ruta": str(row["Ruta"]),
                "clients": int(row["clients"]),
                "lines": int(row["lines"]),
                "first_repartidor": str(row.get("first_repartidor") or ""),
            }
        )
    return {"total": int(len(df)), "items": items}


def _run_one(date: str, ruta: str, algo: str, strict_physics: bool = False) -> dict:
    ctx = _ctx()
    builder: DayCaseBuilder = ctx["builder"]  # type: ignore
    clients: Clients = ctx["clients"]  # type: ignore
    network: Network = ctx["network"]  # type: ignore
    sim_default: Simulator = ctx["sim"]  # type: ignore

    fecha = dt.date.fromisoformat(date)
    case = builder.build(fecha, ruta)
    if algo not in REGISTRY:
        raise ValueError(f"Unknown algorithm: {algo}")
    plan = get(algo).plan(case, clients, network)
    # Strict mode requires its own simulator (immutable strict flag).
    sim = (
        Simulator(clients=clients, network=network, strict_physics=True)
        if strict_physics
        else sim_default
    )
    result = sim.run(case, plan)
    kpi = compute(result)
    validation = validate_plan(case, plan, result, sim)

    pallet_count = len(
        {c.pallet_id for c in plan.commands if hasattr(c, "pallet_id") and getattr(c, "pallet_id", None)}
    )
    route = list(plan.route_order) if plan.route_order else [
        c.client_id for c in plan.commands if c.__class__.__name__ == "DriveTo"
    ]

    stops = []
    for seq, cid in enumerate(route, start=1):
        rec = clients.get(cid)
        stops.append(
            {
                "client_id": cid,
                "name": rec.name,
                "city": rec.city,
                "cp": rec.cp,
                "lat": rec.lat,
                "lon": rec.lon,
                "visit_seq": seq,
            }
        )

    depot = {
        "name": case.depot.name,
        "lat": case.depot.lat,
        "lon": case.depot.lon,
    }

    stops_trace = _build_stop_trace(result.log.to_records(), clients)

    initial = sim.simulate_loading(case, plan)
    cargo_state = initial.state.cargo
    initial_cargo = []
    for slot in cargo_state.slots:
        pid = cargo_state.pallet_by_slot.get(slot.slot_id)
        if pid is None:
            initial_cargo.append({"slot_id": slot.slot_id, "side": slot.side, "pallet": None})
            continue
        pallet = cargo_state.pallet_by_id.get(pid)
        if pallet is None:
            initial_cargo.append({"slot_id": slot.slot_id, "side": slot.side, "pallet": None})
            continue
        layout = pallet.layout
        items_payload = []
        for it in pallet.items:
            items_payload.append(
                {
                    "sku": it.sku,
                    "qty": float(it.qty),
                    "intended_client": it.intended_client,
                    "is_returnable_empty": it.is_returnable_empty,
                    "col_x": int(it.col_x),
                    "col_y": int(it.col_y),
                    "bottom_level": int(it.bottom_level),
                    "stack_size": int(it.stack_size),
                    "physical_type": getattr(it, "physical_type", "unit"),
                    "pos_x": float(it.pos_x),
                    "pos_y": float(it.pos_y),
                    "pos_z": float(it.pos_z),
                    "dim_x": float(it.dim_x),
                    "dim_y": float(it.dim_y),
                    "dim_h": float(it.dim_h),
                }
            )
        initial_cargo.append(
            {
                "slot_id": slot.slot_id,
                "side": slot.side,
                "pallet": {
                    "pallet_id": pallet.pallet_id,
                    "kind": pallet.kind.value if hasattr(pallet.kind, "value") else str(pallet.kind),
                    "pallet_class": pallet.pallet_class.value if pallet.pallet_class else None,
                    "primary_client": pallet.primary_client,
                    "notes": pallet.notes,
                    "layout": {
                        "cols_x": layout.cols_x,
                        "cols_y": layout.cols_y,
                        "max_level": layout.max_level,
                    },
                    "volume_m3": float(pallet.volume_m3),
                    "weight_kg": float(pallet.weight_kg),
                    "items": items_payload,
                },
            }
        )

    for s in stops:
        s["stages"] = stops_trace.get(s["client_id"], {}).get("stages", [])
        s["arrive_t_min"] = stops_trace.get(s["client_id"], {}).get("arrive_t_min")
        s["depart_t_min"] = stops_trace.get(s["client_id"], {}).get("depart_t_min")
        s["dwell_min"] = stops_trace.get(s["client_id"], {}).get("dwell_min")

    legs = _build_legs(stops, depot, result.log.to_records())

    # Deduplicate physics violations: same overlap re-emits on every
    # subsequent state change. Key by (code, slot, sku_a, sku_b, pos
    # rounded to MILLIMETRE) so visually-distinct overlaps stay
    # separate, but the same overlap re-emitting after each next
    # command is collapsed.
    physics_violations = []
    seen_violations: set[tuple] = set()
    for r in result.log.to_records():
        if r.get("kind") != "PHYSICS_VIOLATION":
            continue
        pos_a = r.get("pos") or r.get("pos_a") or [0, 0, 0]
        pos_b = r.get("pos_b") or [0, 0, 0]
        key = (
            r.get("code"),
            r.get("slot_id"),
            r.get("sku") or r.get("sku_a"),
            r.get("sku_b"),
            tuple(round(float(x), 3) for x in pos_a),
            tuple(round(float(x), 3) for x in pos_b),
        )
        if key in seen_violations:
            continue
        seen_violations.add(key)
        physics_violations.append(
            {
                "seq": int(r.get("seq", 0)),
                "t_min": float(r.get("t_min", 0.0) or 0.0),
                "code": r.get("code"),
                "message": r.get("message"),
                "where": r.get("where"),
                "slot_id": r.get("slot_id"),
                "sku": r.get("sku") or r.get("sku_a"),
            }
        )

    return {
        "algorithm": algo,
        "date": str(case.date),
        "ruta": case.ruta,
        "truck": {
            "code": case.truck.code,
            "pallet_capacity": case.truck.pallet_capacity,
            "max_weight_kg": case.truck.max_weight_kg,
            "sides": list(case.truck.sides),
        },
        "depot": depot,
        "stops": stops,
        "legs": legs,
        "n_clients": case.n_clients,
        "rationale": list(plan.rationale),
        "route": route,
        "pallets_planned": pallet_count,
        "initial_cargo": initial_cargo,
        "kpis": kpi.to_dict(),
        "validation": validation.to_dict(),
        "physics_violations": physics_violations,
    }


def _build_legs(stops: list[dict], depot: dict, records: list[dict]) -> list[dict]:
    """Per-segment (from → to) info: distance, drive time, leg index."""
    legs: list[dict] = []
    arrive_by_client: dict[str, dict] = {}
    return_record: dict | None = None
    for r in records:
        if r.get("kind") == "ARRIVE":
            cid = r.get("client_id")
            if cid:
                arrive_by_client.setdefault(cid, r)
        elif r.get("kind") == "RETURN_DEPOT":
            return_record = r

    prev_lat = depot["lat"]
    prev_lon = depot["lon"]
    prev_name = depot["name"]
    prev_id = "DEPOT"

    for idx, s in enumerate(stops, start=1):
        ar = arrive_by_client.get(s["client_id"]) or {}
        legs.append(
            {
                "leg_index": idx,
                "from_id": prev_id,
                "from_name": prev_name,
                "from_lat": prev_lat,
                "from_lon": prev_lon,
                "to_id": s["client_id"],
                "to_name": s["name"],
                "to_lat": s["lat"],
                "to_lon": s["lon"],
                "to_visit_seq": s["visit_seq"],
                "distance_km": float(ar.get("distance_km", 0.0) or 0.0),
                "drive_min": float(ar.get("drive_min", 0.0) or 0.0),
                "arrive_t_min": s.get("arrive_t_min"),
            }
        )
        prev_lat = s["lat"]
        prev_lon = s["lon"]
        prev_name = s["name"]
        prev_id = s["client_id"]

    if return_record is not None:
        legs.append(
            {
                "leg_index": len(legs) + 1,
                "from_id": prev_id,
                "from_name": prev_name,
                "from_lat": prev_lat,
                "from_lon": prev_lon,
                "to_id": "DEPOT",
                "to_name": depot["name"],
                "to_lat": depot["lat"],
                "to_lon": depot["lon"],
                "to_visit_seq": None,
                "distance_km": float(return_record.get("distance_km", 0.0) or 0.0),
                "drive_min": float(return_record.get("drive_min", 0.0) or 0.0),
                "arrive_t_min": float(return_record.get("t_min", 0.0) or 0.0),
            }
        )
    return legs


_PER_STOP_KINDS = {
    "ARRIVE",
    "SERVICE_BASE",
    "BLOCKER_LIFT",
    "TARGET_TAKE",
    "BLOCKER_REPLACE",
    "UNLOAD",
    "DROP",
    "PICKUP_RETURN",
    # SETTLE moves a previously-placed box that just lost its
    # supporter. The frontend reads it to update the box's pos so the
    # visualizer never shows a keg floating or "100% inside another
    # keg" at its stale pre-settle location.
    "SETTLE",
}


def _type_tag(rec: dict) -> str:
    pt = rec.get("physical_type")
    if not pt:
        return ""
    try:
        ptype = PhysicalType(pt)
    except ValueError:
        return ""
    return f"[{PHYSICAL_TYPE_CODE.get(ptype, '?')}]"


def _stage_description(rec: dict) -> str:
    kind = rec["kind"]
    if kind == "ARRIVE":
        return f"Arrive at {rec.get('client_name') or rec.get('client_id') or 'client'} (drove {rec.get('distance_km', 0):.1f} km)"
    if kind == "SERVICE_BASE":
        return "Park, paperwork, open doors"
    if kind == "BLOCKER_LIFT":
        sku = rec.get("sku")
        whose = rec.get("intended_client") or "—"
        col = f"col ({rec.get('col_x')},{rec.get('col_y')})"
        level = rec.get("level")
        unit = rec.get("unit_idx")
        total = rec.get("total_units")
        unit_tag = f" [{int(unit) + 1}/{int(total)}]" if unit is not None and total else ""
        type_tag = _type_tag(rec)
        type_prefix = f"{type_tag} " if type_tag else ""
        return f"Lift 1 {type_prefix}box {sku}{unit_tag} from {col} lvl {level} (for client {whose}) — {rec.get('reason')}"
    if kind == "TARGET_TAKE":
        sku = rec.get("sku")
        col = f"col ({rec.get('col_x')},{rec.get('col_y')})"
        level = rec.get("level")
        unit = rec.get("unit_idx")
        total = rec.get("total_units")
        unit_tag = f" [{int(unit) + 1}/{int(total)}]" if unit is not None and total else ""
        type_tag = _type_tag(rec)
        type_prefix = f"{type_tag} " if type_tag else ""
        return f"Take 1 {type_prefix}box {sku}{unit_tag} from {col} lvl {level} → hand to client"
    if kind == "BLOCKER_REPLACE":
        sku = rec.get("sku")
        col = f"col ({rec.get('col_x')},{rec.get('col_y')})"
        level = rec.get("level")
        unit = rec.get("unit_idx")
        total = rec.get("total_units")
        unit_tag = f" [{int(unit) + 1}/{int(total)}]" if unit is not None and total else ""
        type_tag = _type_tag(rec)
        type_prefix = f"{type_tag} " if type_tag else ""
        return f"Put 1 {type_prefix}box {sku}{unit_tag} back into {col} lvl {level}"
    if kind == "UNLOAD":
        return f"Delivery line complete — {rec.get('sku')} ×{rec.get('qty', 0):g} ({rec.get('search_moves', 0)} search-moves)"
    if kind == "DROP":
        return f"DROP — {rec.get('sku')} ×{rec.get('qty', 0):g} could not be delivered"
    if kind == "PICKUP_RETURN":
        return f"Pick up empties — {rec.get('sku')} ×{rec.get('qty', 0):g}"
    if kind == "SETTLE":
        return (
            f"Settle floating {rec.get('sku')} from "
            f"({rec.get('from_pos_x', 0):.2f},{rec.get('from_pos_y', 0):.2f},{rec.get('from_pos_z', 0):.2f}) "
            f"to ({rec.get('pos_x', 0):.2f},{rec.get('pos_y', 0):.2f},{rec.get('pos_z', 0):.2f})"
        )
    return kind


def _build_stop_trace(records: list[dict], clients: Clients) -> dict[str, dict]:
    """Group event-log records into per-client stop traces.

    Returns: { client_id → { arrive_t_min, depart_t_min, dwell_min, stages: [...] } }
    """
    out: dict[str, dict] = {}
    current: dict | None = None
    prev_t: float | None = None

    for r in records:
        kind = r.get("kind")
        t = float(r.get("t_min") or 0.0)

        if kind == "ARRIVE":
            cid = r.get("client_id")
            if not cid:
                continue
            current = out.setdefault(
                cid,
                {
                    "client_id": cid,
                    "arrive_t_min": t,
                    "depart_t_min": t,
                    "dwell_min": 0.0,
                    "stages": [],
                },
            )
            current["arrive_t_min"] = t
            prev_t = t
            current["stages"].append(
                {
                    "seq": int(r.get("seq", 0)),
                    "kind": kind,
                    "t_min": round(t, 3),
                    "time_min": 0.0,
                    "description": _stage_description(r),
                    "detail": {k: v for k, v in r.items() if k not in {"seq", "t_min", "kind"}},
                }
            )
            continue

        if current is None:
            continue

        if kind in _PER_STOP_KINDS:
            time_min = float(r.get("time_min") or 0.0)
            if time_min == 0.0 and prev_t is not None:
                time_min = max(0.0, t - prev_t)
            current["stages"].append(
                {
                    "seq": int(r.get("seq", 0)),
                    "kind": kind,
                    "t_min": round(t, 3),
                    "time_min": round(time_min, 3),
                    "description": _stage_description(r),
                    "detail": {k: v for k, v in r.items() if k not in {"seq", "t_min", "kind"}},
                }
            )
            current["depart_t_min"] = t
            current["dwell_min"] = round(t - current["arrive_t_min"], 3)
            prev_t = t

    return out


def _bench(algos: list[str], max_cases: int, seed: int, min_clients: int) -> dict:
    cfg = BenchConfig(
        algorithms=tuple(a for a in algos if a in REGISTRY),
        max_cases=max_cases,
        min_clients=min_clients,
        seed=seed,
    )
    if not cfg.algorithms:
        raise ValueError("No valid algorithms")
    out = bench_run(cfg)

    summary_records = json.loads(out["summary"].to_json(orient="records"))
    h2h_records = (
        json.loads(out["head_to_head"].to_json(orient="records"))
        if not out["head_to_head"].empty
        else []
    )
    per_run_records = json.loads(out["per_run"].to_json(orient="records"))

    return {
        "config": {
            "algorithms": list(cfg.algorithms),
            "max_cases": cfg.max_cases,
            "min_clients": cfg.min_clients,
            "seed": cfg.seed,
        },
        "summary": summary_records,
        "head_to_head": h2h_records,
        "per_run": per_run_records,
    }


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, body: object) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        try:
            if url.path == "/api/algorithms":
                self._json(200, {"algorithms": _list_algorithms()})
                return
            if url.path == "/api/types":
                self._json(200, {"types": _list_physical_types()})
                return
            if url.path == "/api/days":
                qs = parse_qs(url.query)
                min_clients = int((qs.get("min_clients") or ["5"])[0])
                head = int((qs.get("head") or ["50"])[0])
                self._json(200, _list_days(min_clients, head))
                return
            if url.path == "/api/health":
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or "0")
        body_raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(body_raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return

        try:
            if url.path == "/api/run":
                date = str(body.get("date") or "")
                ruta = str(body.get("ruta") or "")
                algo = str(body.get("algo") or "")
                strict = bool(body.get("strict_physics") or False)
                if not (date and ruta and algo):
                    self._json(400, {"error": "missing date/ruta/algo"})
                    return
                self._json(200, _run_one(date, ruta, algo, strict_physics=strict))
                return
            if url.path == "/api/bench":
                algos = body.get("algos") or list(REGISTRY.keys())
                max_cases = int(body.get("max_cases") or 30)
                seed = int(body.get("seed") or 42)
                min_clients = int(body.get("min_clients") or 5)
                self._json(200, _bench(list(algos), max_cases, seed, min_clients))
                return
            self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[api] {fmt % args}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="simulator-api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    print(f"[api] warming caches...", file=sys.stderr)
    _ctx()
    print(f"[api] ready", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[api] listening on http://{args.host}:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[api] shutting down", file=sys.stderr)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
