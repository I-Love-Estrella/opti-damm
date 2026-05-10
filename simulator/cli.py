"""Command-line entry. Run with `python -m simulator.cli ...`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from simulator.algorithms import REGISTRY, get
from simulator.bench.multi_compare import run_compare
from simulator.bench.multi_run import CaseRef, run_multi, write_csv
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
    p_run.add_argument("--algo", default="historic", choices=list(REGISTRY))
    p_run.add_argument("--trace", action="store_true", help="dump event log")

    p_bench = sub.add_parser("bench", help="run multiple algorithms on N sampled cases")
    p_bench.add_argument("--algos", default="historic,historic-load")
    p_bench.add_argument("--max-cases", type=int, default=30)
    p_bench.add_argument("--min-clients", type=int, default=5)
    p_bench.add_argument("--seed", type=int, default=42)

    p_summary = sub.add_parser("summary", help="summarize a single (date, ruta) plan + KPIs")
    p_summary.add_argument("--date", required=True)
    p_summary.add_argument("--ruta", required=True)

    p_multi = sub.add_parser(
        "multi-run",
        help="run ONE algorithm on N (date,ruta) cases, aggregate stats",
    )
    p_multi.add_argument("--algo", required=True, choices=list(REGISTRY))
    p_multi.add_argument(
        "--mode", default="first", choices=["first", "random", "all", "explicit"],
        help="how to pick cases (ignored if --cases is supplied)",
    )
    p_multi.add_argument("--n", type=int, default=10, help="cases to run when mode=first|random")
    p_multi.add_argument("--min-clients", type=int, default=5)
    p_multi.add_argument("--seed", type=int, default=42)
    p_multi.add_argument(
        "--cases", default="",
        help="comma-separated date:ruta pairs (e.g. 2026-01-30:DR0001,2026-01-30:DR0006)",
    )
    p_multi.add_argument("--truck", default=None, help="force truck code (T6/T8/V3)")
    p_multi.add_argument("--csv", action="store_true", help="also dump per-case + summary CSVs")
    p_multi.add_argument("--json", action="store_true", help="emit full JSON instead of pretty table")

    p_cmp = sub.add_parser(
        "compare",
        help="head-to-head: TWO algorithms on the SAME N cases, paired deltas",
    )
    p_cmp.add_argument("--algo-a", required=True, choices=list(REGISTRY))
    p_cmp.add_argument("--algo-b", required=True, choices=list(REGISTRY))
    p_cmp.add_argument(
        "--mode", default="first", choices=["first", "random", "all", "explicit"],
    )
    p_cmp.add_argument("--n", type=int, default=10)
    p_cmp.add_argument("--min-clients", type=int, default=5)
    p_cmp.add_argument("--seed", type=int, default=42)
    p_cmp.add_argument("--cases", default="", help="comma-separated date:ruta pairs")
    p_cmp.add_argument("--truck", default=None)
    p_cmp.add_argument("--json", action="store_true", help="emit full JSON")

    args = p.parse_args(argv)

    if args.cmd == "list-days":
        return _cmd_list(args)
    if args.cmd == "run":
        return _cmd_run(args)
    if args.cmd == "bench":
        return _cmd_bench(args)
    if args.cmd == "summary":
        return _cmd_summary(args)
    if args.cmd == "multi-run":
        return _cmd_multi_run(args)
    if args.cmd == "compare":
        return _cmd_compare(args)
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


def _cmd_multi_run(args) -> int:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    network = Network()
    sim = Simulator(clients=clients, network=network)

    case_refs: list[CaseRef] | None = None
    if args.cases.strip():
        case_refs = []
        for tok in args.cases.split(","):
            tok = tok.strip()
            if not tok:
                continue
            d, _, r = tok.partition(":")
            if not d or not r:
                print(f"skip malformed case spec: {tok!r}", file=sys.stderr)
                continue
            case_refs.append(CaseRef(date=dt.date.fromisoformat(d), ruta=r))

    sample = None if case_refs else args.mode
    truck = args.truck.strip().upper() if args.truck else None

    stats = run_multi(
        args.algo,
        cases=case_refs,
        sample=sample,
        n=args.n,
        min_clients=args.min_clients,
        seed=args.seed,
        truck_code=truck,
        builder=builder, clients=clients, network=network, sim=sim,
    )

    if args.json:
        print(json.dumps(stats.to_dict(), indent=2, default=str))
    else:
        _print_multi_run(stats)

    if args.csv:
        paths = write_csv(stats)
        print(f"\nwrote: {paths['cases_csv']}")
        print(f"wrote: {paths['summary_csv']}")
    return 0


def _print_multi_run(stats) -> None:
    s = stats.to_dict()
    print(f"\n=== multi-run · algo={s['algorithm']} · cases={s['n_cases']} ===")
    print(f"  success         : {s['n_success']}/{s['n_cases']}  "
          f"(rate {s['success_rate'] * 100:.1f}%)")
    print(f"  failed (sim)    : {s['n_failed']}")
    print(f"  invalid plan    : {s['n_invalid_plan']}")
    print(f"  with physics-V  : {s['n_with_physics']}  "
          f"(total events: {s['total_physics_violations']})")
    print(f"  validation      : {s['total_validation_errors']} errors, "
          f"{s['total_validation_warnings']} warnings")
    print(f"  drops total     : {s['total_drops']}")
    print(f"  capacity-V tot  : {s['total_capacity_violations']}")
    print(f"  clean rate      : {s['clean_rate'] * 100:.1f}% "
          f"(success ∧ valid ∧ no physics)")
    print(f"  wall time       : {s['duration_sec']:.2f}s\n")

    print(f"{'metric':<24} {'n':>4}  {'sum':>12}  {'mean':>10}  "
          f"{'median':>10}  {'stdev':>10}  {'min':>10}  {'max':>10}  {'p95':>10}")
    print("  " + "-" * 110)
    for metric, agg in s["aggregates"].items():
        print(
            f"  {metric:<22} {agg['n']:>4}  {agg['sum']:>12.2f}  "
            f"{agg['mean']:>10.3f}  {agg['median']:>10.3f}  "
            f"{agg['stdev']:>10.3f}  {agg['min']:>10.3f}  "
            f"{agg['max']:>10.3f}  {agg['p95']:>10.3f}"
        )

    if s["failures"]:
        print(f"\nfailures ({len(s['failures'])}):")
        for f in s["failures"][:25]:
            print(f"  {f['date']} {f['ruta']:<8}  {f['error']}")
        if len(s["failures"]) > 25:
            print(f"  ... +{len(s['failures']) - 25} more")


def _cmd_compare(args) -> int:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    network = Network()
    sim = Simulator(clients=clients, network=network)

    case_refs: list[CaseRef] | None = None
    if args.cases.strip():
        case_refs = []
        for tok in args.cases.split(","):
            tok = tok.strip()
            if not tok:
                continue
            d, _, r = tok.partition(":")
            if d and r:
                case_refs.append(CaseRef(date=dt.date.fromisoformat(d), ruta=r))
    sample = None if case_refs else args.mode
    truck = args.truck.strip().upper() if args.truck else None

    report = run_compare(
        args.algo_a, args.algo_b,
        cases=case_refs, sample=sample,
        n=args.n, min_clients=args.min_clients, seed=args.seed,
        truck_code=truck,
        builder=builder, clients=clients, network=network, sim=sim,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        _print_compare(report)
    return 0


def _print_compare(report) -> None:
    d = report.to_dict()
    a, b = d["algo_a"], d["algo_b"]
    print(f"\n=== compare · A={a}  B={b}  cases={d['n_cases']} ===")
    print(f"  paired (both ran)  : {d['n_paired']}")
    print(f"  A-only success     : {d['a_only_success']}")
    print(f"  B-only success     : {d['b_only_success']}")
    print(f"  both failed        : {d['both_failed']}")
    print(f"  duration           : {d['duration_sec']:.2f}s\n")

    print(f"  A success rate     : {d['a_stats']['success_rate'] * 100:5.1f}% "
          f"({d['a_stats']['n_success']}/{d['a_stats']['n_cases']})")
    print(f"  B success rate     : {d['b_stats']['success_rate'] * 100:5.1f}% "
          f"({d['b_stats']['n_success']}/{d['b_stats']['n_cases']})")
    print(f"  A clean rate       : {d['a_stats']['clean_rate'] * 100:5.1f}%")
    print(f"  B clean rate       : {d['b_stats']['clean_rate'] * 100:5.1f}%")
    print(f"  A physics events   : {d['a_stats']['total_physics_violations']}")
    print(f"  B physics events   : {d['b_stats']['total_physics_violations']}\n")

    header = (
        f"{'metric':<22}  {'A mean':>10}  {'B mean':>10}  "
        f"{'Δ mean':>10}  {'Δ %':>8}  "
        f"{'A wins':>6}  {'B wins':>6}  {'ties':>5}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for m in d["metrics"]:
        winner = ""
        if m["b_wins"] > m["a_wins"]:
            winner = "  ← B"
        elif m["a_wins"] > m["b_wins"]:
            winner = "  ← A"
        print(
            f"  {m['metric']:<20}  "
            f"{m['a_mean']:>10.3f}  {m['b_mean']:>10.3f}  "
            f"{m['delta_mean']:>10.3f}  {m['delta_pct_mean']:>+7.1f}%  "
            f"{m['a_wins']:>6}  {m['b_wins']:>6}  {m['ties']:>5}{winner}"
        )


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
