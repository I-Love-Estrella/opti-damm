"""historic-load vs historic break-even calculator.

Runs both algorithms on a sample of historic days and computes:
  - Δ depot minutes saved by historic-load (loader work avoided)
  - Δ driver minutes added (or saved) by historic-load
  - Loader savings at €12/h
  - Driver extra cost at the current €18/h rate (with overtime)
  - The break-even DRIVER hourly rate at which historic-load == historic
    on total labor cost. If driver time goes DOWN with historic-load
    (TSP routing recoups the extra search-moves), the break-even is
    "any rate" — historic-load is unconditionally cheaper.

Usage:
  PYTHONPATH=. python3 scripts/break_even.py [--n 10] [--algo-base historic]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.algorithms import get
from simulator.config import DEFAULT_TARIFFS
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=10, help="Number of days to sample")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-clients", type=int, default=5)
    p.add_argument(
        "--algo-base", default="historic",
        help="Baseline algorithm to compare historic-load against",
    )
    args = p.parse_args(argv)

    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    network = Network()
    sim = Simulator(clients=clients, network=network)

    df = builder.list_day_cases(min_clients=args.min_clients)
    sample = df.sample(n=min(args.n, len(df)), random_state=args.seed)

    base_algo = get(args.algo_base)
    load_algo = get("historic-load")
    loader_rate = DEFAULT_TARIFFS.loader_hourly_eur
    driver_rate = DEFAULT_TARIFFS.driver_hourly_eur

    print(
        f"\nLoader rate: €{loader_rate}/h | Driver rate: €{driver_rate}/h "
        f"(overtime €{DEFAULT_TARIFFS.driver_overtime_eur}/h after "
        f"{DEFAULT_TARIFFS.overtime_after_hours}h)\n"
    )
    print(
        f"{'date':>10s} {'ruta':>8s} {'depot Δ':>9s} {'driver Δ':>9s} "
        f"{'loader €':>9s} {'driver €':>9s} {'TOTAL €':>9s} {'break-even':>11s}"
    )
    print("-" * 90)

    sum_depot_save = 0.0
    sum_driver_extra = 0.0
    sum_loader_save = 0.0
    sum_driver_cost = 0.0
    sum_total_save = 0.0
    rows = 0
    for _, row in sample.iterrows():
        case = builder.build(row.fecha, row.Ruta)
        try:
            kp_base = compute(sim.run(case, base_algo.plan(case, clients, network)))
            kp_load = compute(sim.run(case, load_algo.plan(case, clients, network)))
        except Exception as e:
            print(f"  skip {row.fecha} {row.Ruta}: {e}")
            continue
        depot_save = kp_base.depot_minutes - kp_load.depot_minutes        # min
        driver_extra = kp_load.driver_minutes - kp_base.driver_minutes    # min
        loader_save_eur = (depot_save / 60.0) * loader_rate
        driver_cost_eur = kp_load.driver_labor_eur - kp_base.driver_labor_eur
        total_save = kp_base.total_cost_eur - kp_load.total_cost_eur

        # Break-even driver rate = rate at which loader savings just
        # cancel driver overtime cost. If driver_extra ≤ 0 the new
        # algo is unconditionally cheaper.
        if driver_extra <= 0.5:  # driver got faster (or unchanged)
            be = "always wins"
        elif depot_save <= 0:
            be = "never wins"
        else:
            be_rate = (depot_save * loader_rate) / driver_extra
            be = f"€{be_rate:.1f}/h"

        sum_depot_save += depot_save
        sum_driver_extra += driver_extra
        sum_loader_save += loader_save_eur
        sum_driver_cost += driver_cost_eur
        sum_total_save += total_save
        rows += 1
        print(
            f"{str(row.fecha):>10s} {row.Ruta:>8s} "
            f"{depot_save:>+8.1f}m {driver_extra:>+8.1f}m "
            f"{loader_save_eur:>+8.2f} {driver_cost_eur:>+8.2f} "
            f"{total_save:>+8.2f} {be:>11s}"
        )

    print("-" * 90)
    if rows:
        print(
            f"{'TOTAL':>10s} {'':>8s} "
            f"{sum_depot_save:>+8.1f}m {sum_driver_extra:>+8.1f}m "
            f"{sum_loader_save:>+8.2f} {sum_driver_cost:>+8.2f} "
            f"{sum_total_save:>+8.2f}"
        )
        print(
            f"\nAcross {rows} days: historic-load saves "
            f"€{sum_total_save:.2f} total vs `{args.algo_base}`."
        )
        if sum_driver_extra > 0:
            be_avg = (sum_depot_save * loader_rate) / sum_driver_extra
            print(
                f"Aggregate break-even driver rate: €{be_avg:.1f}/h "
                f"(current driver rate €{driver_rate}/h is "
                f"{'BELOW' if driver_rate < be_avg else 'ABOVE'} that — "
                f"historic-load is "
                f"{'cheaper' if driver_rate < be_avg else 'more expensive'} on average)."
            )
        else:
            print(
                "Driver time also went DOWN on average → historic-load is "
                "unconditionally cheaper at any reasonable driver rate."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
