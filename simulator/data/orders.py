"""Day-case extraction: pick (FECHA, Ruta) → reconstruct a single delivery day."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from simulator.config import (
    DEFAULT_TRUCK,
    DEPOT_LAT,
    DEPOT_LON,
    DEPOT_NAME,
    PALLET_VOLUME_M3,
    RETURNABLE_RATIO_DEFAULT,
    TRUCK_SPECS,
    TruckSpec,
)
from simulator.data.catalog import Catalog, PhysicalType
from simulator.data.clients import Clients
from simulator.data.loader import RawData


@dataclass(frozen=True)
class OrderLine:
    sku: str
    qty: float
    uma: str
    unit_volume_m3: float
    unit_weight_kg: float
    is_returnable: bool
    physical_type: PhysicalType = PhysicalType.UNIT
    # Real per-unit physical dimensions (m). 0.0 means "no data" — caller
    # should fall back to type defaults.
    dim_x_m: float = 0.0
    dim_y_m: float = 0.0
    dim_h_m: float = 0.0
    dim_source: str = "type"  # "data" | "type"


@dataclass(frozen=True)
class ClientOrder:
    client_id: str
    lines: tuple[OrderLine, ...]
    expected_returnable_units: float
    visit_seq_actual: int

    @property
    def total_volume_m3(self) -> float:
        return sum(l.qty * l.unit_volume_m3 for l in self.lines)

    @property
    def total_weight_kg(self) -> float:
        return sum(l.qty * l.unit_weight_kg for l in self.lines)


@dataclass(frozen=True)
class DepotInfo:
    name: str
    lat: float
    lon: float
    open_at: dt.time = dt.time(6, 0)


@dataclass(frozen=True)
class DayCase:
    date: dt.date
    ruta: str
    repartidor: str
    truck: TruckSpec
    depot: DepotInfo
    orders: tuple[ClientOrder, ...]
    raw_transports: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_clients(self) -> int:
        return len(self.orders)

    @property
    def total_volume_m3(self) -> float:
        return sum(o.total_volume_m3 for o in self.orders)


class DayCaseBuilder:
    def __init__(self, raw: RawData, catalog: Catalog, clients: Clients):
        self._detalle = raw.detalle.copy()
        self._cabecera = raw.cabecera.copy()
        self._catalog = catalog
        self._clients = clients
        self._prepare()

    def _prepare(self) -> None:
        d = self._detalle
        d["Material"] = d["Material"].astype("string").str.strip()
        d["Ruta"] = d["Ruta"].astype("string").str.strip()
        d["ClienteId"] = d.get("ClienteId", pd.Series(dtype="string")).astype("string").str.strip()
        d["Repartidor"] = d.get("Repartidor", pd.Series(dtype="string")).astype("string").str.strip()
        d["Entrega"] = d.get("Entrega", pd.Series(dtype="string")).astype("string").str.strip()

    def list_day_cases(self, min_clients: int = 5) -> pd.DataFrame:
        d = self._detalle.dropna(subset=["FECHA", "Ruta"])
        groups = (
            d.groupby([d["FECHA"].dt.date, "Ruta"])
            .agg(
                clients=("ClienteId", "nunique"),
                lines=("Material", "size"),
                repartidores=("Repartidor", "nunique"),
                first_repartidor=("Repartidor", "first"),
            )
            .reset_index()
            .rename(columns={"FECHA": "fecha"})
        )
        groups = groups[groups["clients"] >= min_clients].sort_values(["fecha", "Ruta"])
        return groups.reset_index(drop=True)

    def build(self, fecha: dt.date, ruta: str) -> DayCase:
        d = self._detalle
        mask = (d["FECHA"].dt.date == fecha) & (d["Ruta"] == ruta)
        rows = d.loc[mask].copy()
        if rows.empty:
            raise KeyError(f"No deliveries for {fecha} / {ruta}")

        repartidor = str(rows["Repartidor"].dropna().iloc[0]) if rows["Repartidor"].notna().any() else ""
        transports = tuple(sorted(rows["Transporte"].dropna().astype(str).unique().tolist()))

        orders = self._build_orders(rows)
        truck = self._pick_truck(orders)

        depot = DepotInfo(name=DEPOT_NAME, lat=DEPOT_LAT, lon=DEPOT_LON)

        return DayCase(
            date=fecha,
            ruta=ruta,
            repartidor=repartidor,
            truck=truck,
            depot=depot,
            orders=tuple(orders),
            raw_transports=transports,
        )

    def _build_orders(self, rows: pd.DataFrame) -> list[ClientOrder]:
        rows = rows.copy()
        rows["Entrega_num"] = pd.to_numeric(rows["Entrega"], errors="coerce")
        rows = rows.sort_values(["Entrega_num", "Material"])

        first_seq = (
            rows.dropna(subset=["ClienteId"])
            .drop_duplicates("ClienteId")
            .reset_index(drop=True)
        )
        seq_by_client = {row.ClienteId: i + 1 for i, row in first_seq.iterrows()}

        out: list[ClientOrder] = []
        for cid, sub in rows.groupby("ClienteId", sort=False):
            if not isinstance(cid, str) or not cid:
                continue
            lines = self._make_lines(sub)
            ret_units = sum(l.qty for l in lines if l.is_returnable) * RETURNABLE_RATIO_DEFAULT
            ret_units += sum(l.qty for l in lines if not l.is_returnable) * 0.10
            out.append(
                ClientOrder(
                    client_id=cid,
                    lines=tuple(lines),
                    expected_returnable_units=float(ret_units),
                    visit_seq_actual=seq_by_client.get(cid, len(out) + 1),
                )
            )
        out.sort(key=lambda o: o.visit_seq_actual)
        return out

    def _make_lines(self, sub: pd.DataFrame) -> list[OrderLine]:
        lines: list[OrderLine] = []
        for sku, grp in sub.groupby("Material"):
            if not isinstance(sku, str) or not sku:
                continue
            rec = self._catalog.get(sku)
            qty = float(grp["Cantidad entrega"].sum())
            if qty <= 0:
                continue
            lines.append(
                OrderLine(
                    sku=sku,
                    qty=qty,
                    uma=rec.uma,
                    unit_volume_m3=rec.unit_volume_m3,
                    unit_weight_kg=rec.unit_weight_kg,
                    is_returnable=rec.is_returnable,
                    physical_type=rec.physical_type,
                    dim_x_m=rec.dim_x_m,
                    dim_y_m=rec.dim_y_m,
                    dim_h_m=rec.dim_h_m,
                    dim_source=rec.dim_source,
                )
            )
        return lines

    def _pick_truck(self, orders: list[ClientOrder]) -> TruckSpec:
        total_vol = sum(o.total_volume_m3 for o in orders)
        total_wt = sum(o.total_weight_kg for o in orders)
        for code in (DEFAULT_TRUCK, "T8", "V3"):
            spec = TRUCK_SPECS[code]
            if total_vol <= spec.pallet_capacity * PALLET_VOLUME_M3 and total_wt <= spec.max_weight_kg:
                return spec
        return TRUCK_SPECS["T8"]
