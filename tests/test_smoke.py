"""Smoke test: end-to-end pipeline must run on the first available day."""

from __future__ import annotations

import datetime as dt

import pytest

from simulator.algorithms import get
from simulator.core.simulator import Simulator
from simulator.data.catalog import Catalog
from simulator.data.clients import Clients
from simulator.data.loader import load_all
from simulator.data.network import Network
from simulator.data.orders import DayCaseBuilder
from simulator.kpis.metrics import compute


@pytest.fixture(scope="session")
def state():
    raw = load_all()
    catalog = Catalog.build(raw)
    clients = Clients.build(raw)
    builder = DayCaseBuilder(raw, catalog, clients)
    return raw, catalog, clients, builder


def _first_case(builder):
    df = builder.list_day_cases(min_clients=5)
    assert not df.empty, "no historical (date, ruta) cases"
    row = df.iloc[0]
    fecha = row["fecha"]
    if isinstance(fecha, dt.datetime):
        fecha = fecha.date()
    return builder.build(fecha, str(row["Ruta"]))


@pytest.mark.parametrize("algo", ["replay", "nearest"])
def test_end_to_end(algo, state):
    _, _, clients, builder = state
    case = _first_case(builder)
    network = Network()
    plan = get(algo).plan(case, clients, network)
    sim = Simulator(clients=clients, network=network)
    result = sim.run(case, plan)
    assert result.success, f"{algo} failed: {result.error}"
    kpi = compute(result)
    assert kpi.total_minutes > 0
    assert kpi.total_km > 0
    assert kpi.fill_rate > 0.5
    assert kpi.n_clients_visited == case.n_clients
