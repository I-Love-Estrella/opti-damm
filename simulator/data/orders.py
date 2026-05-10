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
from simulator.data.catalog import Catalog, PhysicalType, physical_dims
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

    def build(
        self, fecha: dt.date, ruta: str, truck_code: str | None = None
    ) -> DayCase:
        d = self._detalle
        mask = (d["FECHA"].dt.date == fecha) & (d["Ruta"] == ruta)
        rows = d.loc[mask].copy()
        if rows.empty:
            raise KeyError(f"No deliveries for {fecha} / {ruta}")

        repartidor = str(rows["Repartidor"].dropna().iloc[0]) if rows["Repartidor"].notna().any() else ""
        transports = tuple(sorted(rows["Transporte"].dropna().astype(str).unique().tolist()))

        orders = self._build_orders(rows)
        if truck_code is None:
            truck = self._pick_truck(orders)
        else:
            code = str(truck_code).upper()
            if code not in TRUCK_SPECS:
                raise KeyError(
                    f"Unknown truck code: {truck_code!r}. "
                    f"Known: {sorted(TRUCK_SPECS)}"
                )
            truck = TRUCK_SPECS[code]

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

    def fit_check(
        self, orders: list[ClientOrder] | tuple[ClientOrder, ...], truck: TruckSpec
    ) -> dict:
        """Pure capacity precheck — does the order fit on this truck without
        even running the algorithm? Returns volume/weight stats and a
        list of human-readable reasons when it doesn't."""

        total_vol = sum(o.total_volume_m3 for o in orders)
        total_wt = sum(o.total_weight_kg for o in orders)
        cap_vol = truck.pallet_capacity * PALLET_VOLUME_M3
        cap_wt = truck.max_weight_kg
        reasons: list[str] = []
        if total_vol > cap_vol:
            reasons.append(
                f"Order volume {total_vol:.2f} m³ exceeds truck "
                f"capacity {cap_vol:.2f} m³ ({truck.code})"
            )
        if total_wt > cap_wt:
            reasons.append(
                f"Order weight {total_wt:.0f} kg exceeds truck "
                f"max {cap_wt:.0f} kg ({truck.code})"
            )
        return {
            "fits": not reasons,
            "total_volume_m3": float(total_vol),
            "capacity_volume_m3": float(cap_vol),
            "total_weight_kg": float(total_wt),
            "capacity_weight_kg": float(cap_wt),
            "reasons": reasons,
        }

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
        """Pick the smallest truck that comfortably fits the day.

        We use AABB physical dimensions (dim_x × dim_y × dim_h) — NOT
        the catalog `unit_volume_m3` — because that catalog value is
        often the pure liquid volume (e.g. a 1.5 L bottle = 0.0015 m³)
        while the actual carton or bottle on the pallet occupies an
        AABB cube ~10× that. Picking by liquid volume let DR0017 land
        on T6 with a "1.21 m³" cargo claim, then 31 chunks overflowed
        because the real footprint was ~4 m³.

        Real-world 3D bin-packing rarely exceeds ~70 % of the raw
        cube — extreme-points anchors leave gaps, narrow towers can't
        reach pallet height, KEG and BOX stay on separate pallets.
        So we apply a `PACK_DENSITY_FUDGE = 0.70` headroom on the
        AABB volume.

        Weight has no fudge factor — pallet jacks and the truck axle
        rating are the binding constraint exactly at the listed kg.
        """

        PACK_DENSITY_FUDGE = 0.70

        def _aabb_volume(line) -> float:
            # Prefer the catalog's measured dim_x_m / dim_y_m / dim_h_m.
            # If unavailable, fall back to physical_dims by type.
            if (
                getattr(line, "dim_source", "type") == "data"
                and line.dim_x_m > 0
                and line.dim_y_m > 0
                and line.dim_h_m > 0
            ):
                return line.qty * line.dim_x_m * line.dim_y_m * line.dim_h_m
            ptype = (
                line.physical_type.value
                if hasattr(line.physical_type, "value")
                else str(line.physical_type)
            )
            dx, dy, dh = physical_dims(ptype)
            return line.qty * dx * dy * dh

        total_vol_aabb = sum(
            _aabb_volume(line) for o in orders for line in o.lines
        )
        total_wt = sum(o.total_weight_kg for o in orders)
        for code in (DEFAULT_TRUCK, "T8", "V3"):
            spec = TRUCK_SPECS[code]
            cap_vol_effective = (
                spec.pallet_capacity * PALLET_VOLUME_M3 * PACK_DENSITY_FUDGE
            )
            if total_vol_aabb <= cap_vol_effective and total_wt <= spec.max_weight_kg:
                return spec
        # Largest available — accept that fill-rate may still be low.
        return TRUCK_SPECS["T8"]
