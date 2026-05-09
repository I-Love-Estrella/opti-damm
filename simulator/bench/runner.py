"""Run N (date, ruta) cases × algorithms, write CSV reports."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from simulator.algorithms import REGISTRY, get
from simulator.config import REPORTS_DIR
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.aggregate import head_to_head, summary, to_dataframe
from simulator.kpis.metrics import compute


@dataclass(frozen=True)
class BenchConfig:
    algorithms: tuple[str, ...]
    max_cases: int = 30
    min_clients: int = 5
    seed: int = 42


def run(cfg: BenchConfig) -> dict[str, pd.DataFrame]:
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)

    cases_df = builder.list_day_cases(min_clients=cfg.min_clients)
    if cases_df.empty:
        raise RuntimeError("No (date, ruta) cases found in data")

    sample = cases_df.sample(min(cfg.max_cases, len(cases_df)), random_state=cfg.seed)
    sample = sample.sort_values(["fecha", "Ruta"]).reset_index(drop=True)

    network = Network()
    sim = Simulator(clients=clients, network=network)

    records = []
    for _, row in sample.iterrows():
        fecha = _to_date(row["fecha"])
        ruta = str(row["Ruta"])
        try:
            case = builder.build(fecha, ruta)
        except KeyError:
            continue
        for algo_name in cfg.algorithms:
            if algo_name not in REGISTRY:
                continue
            algo = get(algo_name)
            plan = algo.plan(case, clients, network)
            result = sim.run(case, plan)
            kpi = compute(result)
            records.append(kpi)

    df = to_dataframe(records)
    summ = summary(df)
    h2h = head_to_head(df, baseline=cfg.algorithms[0]) if cfg.algorithms else pd.DataFrame()

    df.to_csv(REPORTS_DIR / "kpis_per_run.csv", index=False)
    summ.to_csv(REPORTS_DIR / "kpis_summary.csv", index=False)
    if not h2h.empty:
        h2h.to_csv(REPORTS_DIR / "head_to_head.csv", index=False)

    return {"per_run": df, "summary": summ, "head_to_head": h2h}


def _to_date(v) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return pd.to_datetime(v).date()
