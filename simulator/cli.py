"""Command-line entry. Run with `python -m simulator.cli ...`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from simulator.algorithms import REGISTRY, get
from simulator.bench.runner import BenchConfig, run as bench_run
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="simulator", description="Damm Smart Truck simulator")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list-days", help="list available (date, ruta) cases")
    p_list.add_argument("--min-clients", type=int, default=5)
    p_list.add_argument("--head", type=int, default=20)

    p_run = sub.add_parser("run", help="run one algorithm on one (date, ruta)")
    p_run.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_run.add_argument("--ruta", required=True)
    p_run.add_argument("--algo", default="replay", choices=list(REGISTRY))
    p_run.add_argument("--trace", action="store_true", help="dump event log")

    p_bench = sub.add_parser("bench", help="run multiple algorithms on N sampled cases")
    p_bench.add_argument("--algos", default="replay,nearest")
    p_bench.add_argument("--max-cases", type=int, default=30)
    p_bench.add_argument("--min-clients", type=int, default=5)
    p_bench.add_argument("--seed", type=int, default=42)

    p_summary = sub.add_parser("summary", help="summarize a single (date, ruta) plan + KPIs")
    p_summary.add_argument("--date", required=True)
    p_summary.add_argument("--ruta", required=True)

    args = p.parse_args(argv)

    if args.cmd == "list-days":
        return _cmd_list(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "bench":
        return _cmd_bench(args)
    if args.cmd == "summary":
        return _cmd_summary(args)
    return 2


def _cmd_list(args) -> int:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    df = builder.list_day_cases(min_clients=args.min_clients)
    print(f"Total cases ≥ {args.min_clients} clients: {len(df)}")
    if not df.empty:
        print(df.head(args.head).to_string(index=False))
    return 0


def _cmd_run(args) -> int:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    fecha = dt.date.fromisoformat(args.date)
    case = builder.build(fecha, args.ruta)
    network = Network()
    algo = get(args.algo)
    plan = algo.plan(case, clients, network)
    sim = Simulator(clients=clients, network=network)
    result = sim.run(case, plan)
    kpi = compute(result)
    print(json.dumps(kpi.to_dict(), indent=2, default=str))
    if args.trace:
        records = result.log.to_records()
        print("--- EVENT LOG ---")
        for r in records:
            print(json.dumps(r, default=str))
    return 0 if result.success else 1


def _cmd_bench(args) -> int:
    cfg = BenchConfig(
        algorithms=tuple(a.strip() for a in args.algos.split(",") if a.strip()),
        max_cases=args.max_cases,
        min_clients=args.min_clients,
        seed=args.seed,
    )
    out = bench_run(cfg)
    pd.set_option("display.max_columns", 200)
    pd.set_option("display.width", 240)
    print("=== summary ===")
    print(out["summary"].to_string(index=False))
    print("=== head-to-head vs", cfg.algorithms[0], "===")
    print(out["head_to_head"].to_string(index=False))
    return 0


def _cmd_summary(args) -> int:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    fecha = dt.date.fromisoformat(args.date)
    case = builder.build(fecha, args.ruta)
    print(f"Date: {case.date}  Ruta: {case.ruta}  Repartidor: {case.repartidor}")
    print(f"Truck: {case.truck.code} ({case.truck.pallet_capacity} pallets, {case.truck.max_weight_kg} kg)")
    print(f"Clients: {case.n_clients}   Total volume: {case.total_volume_m3:.2f} m³")
    for o in case.orders[:30]:
        c = clients.get(o.client_id)
        print(f"  {o.visit_seq_actual:>3}  {o.client_id}  {c.name[:30]:<30} {o.total_volume_m3:5.2f} m³  {o.total_weight_kg:6.1f} kg  lines={len(o.lines)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
