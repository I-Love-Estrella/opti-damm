from __future__ import annotations

import datetime as dt

import pandas as pd

from simulator.data.catalog import Catalog
from simulator.data.clients import ClientRecord, Clients
from simulator.data.loader import load_all
from simulator.data.orders import DayCase, DayCaseBuilder


class DataLayer:
    def __init__(self) -> None:
        raw = load_all()
        self._catalog = Catalog.build(raw)
        self._clients = Clients.build(raw)
        self._builder = DayCaseBuilder(raw, self._catalog, self._clients)

    def list_day_cases(self) -> pd.DataFrame:
        return self._builder.list_day_cases(min_clients=1)

    def build_case(self, fecha: dt.date, ruta: str) -> DayCase:
        return self._builder.build(fecha, ruta)

    def all_clients(self) -> dict[str, ClientRecord]:
        return self._clients.all()

    def get_client(self, client_id: str) -> ClientRecord:
        return self._clients.get(client_id)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def clients(self) -> Clients:
        return self._clients
