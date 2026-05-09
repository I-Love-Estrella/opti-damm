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
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute


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


def _run_one(date: str, ruta: str, algo: str) -> dict:
    ctx = _ctx()
    builder: DayCaseBuilder = ctx["builder"]  # type: ignore
    clients: Clients = ctx["clients"]  # type: ignore
    network: Network = ctx["network"]  # type: ignore
    sim: Simulator = ctx["sim"]  # type: ignore

    fecha = dt.date.fromisoformat(date)
    case = builder.build(fecha, ruta)
    if algo not in REGISTRY:
        raise ValueError(f"Unknown algorithm: {algo}")
    plan = get(algo).plan(case, clients, network)
    result = sim.run(case, plan)
    kpi = compute(result)

    pallet_count = len(
        {c.pallet_id for c in plan.commands if hasattr(c, "pallet_id") and getattr(c, "pallet_id", None)}
    )
    route = list(plan.route_order) if plan.route_order else [
        c.client_id for c in plan.commands if c.__class__.__name__ == "DriveTo"
    ]

    return {
        "algorithm": algo,
        "date": str(case.date),
        "ruta": case.ruta,
        "truck": {
            "code": case.truck.code,
            "pallet_capacity": case.truck.pallet_capacity,
            "max_weight_kg": case.truck.max_weight_kg,
        },
        "n_clients": case.n_clients,
        "rationale": list(plan.rationale),
        "route": route,
        "pallets_planned": pallet_count,
        "kpis": kpi.to_dict(),
    }


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
                if not (date and ruta and algo):
                    self._json(400, {"error": "missing date/ruta/algo"})
                    return
                self._json(200, _run_one(date, ruta, algo))
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
