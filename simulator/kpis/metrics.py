"""Per-day KPIs derived from a SimulationResult."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from simulator.config import (
    DEFAULT_TARIFFS,
    PALLET_VOLUME_M3,
    Tariffs,
)
from simulator.core.simulator import SimulationResult


@dataclass(frozen=True)
class DayKpis:
    algorithm: str
    date: str
    ruta: str
    success: bool
    error: str | None
    n_clients_planned: int
    n_clients_visited: int
    total_minutes: float
    drive_minutes: float
    service_minutes: float
    overhead_minutes: float
    total_km: float
    fuel_liters: float
    fuel_eur: float
    labor_eur: float
    wear_eur: float
    total_cost_eur: float
    co2_kg: float
    search_moves: int
    tw_violations_min: float
    closed_visits: int
    capacity_violations: int
    drops: int
    delivered_units: float
    ordered_units: float
    fill_rate: float
    pallets_loaded: int
    pallet_volume_util: float
    weight_util: float
    returnables_picked_units: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute(result: SimulationResult, tariffs: Tariffs = DEFAULT_TARIFFS) -> DayKpis:
    st = result.state
    log = result.log
    case = st.case

    drive_min = sum(e.detail.get("drive_min", 0.0) for e in log.events if e.kind in {"ARRIVE", "RETURN_DEPOT"})
    sim_end = max((e.t_min for e in log.events), default=0.0)
    total_min = sim_end
    service_min = _service_minutes(log)
    overhead = max(0.0, total_min - drive_min - service_min)

    fuel_l = (st.distance_km * tariffs.diesel_l_per_100km) / 100.0
    fuel_eur = fuel_l * tariffs.fuel_eur_per_l
    labor_h = total_min / 60.0
    labor_eur = _labor_cost(labor_h, tariffs)
    wear_eur = st.distance_km * tariffs.vehicle_wear_eur_per_km
    co2 = fuel_l * tariffs.co2_kg_per_l_diesel

    ordered = sum(line.qty for o in case.orders for line in o.lines)
    delivered = sum(st.delivered_qty.values())
    fill = (delivered / ordered) if ordered > 0 else 1.0

    pallets_total = case.truck.pallet_capacity
    pallets_loaded = max(
        (sum(1 for e in log.events if e.kind == "LOAD")),
        st.cargo.slots_used(),
    )
    cargo_volume = case.total_volume_m3
    capacity_volume = pallets_total * PALLET_VOLUME_M3
    vol_util = cargo_volume / capacity_volume if capacity_volume > 0 else 0.0

    weight_util = (
        sum(o.total_weight_kg for o in case.orders) / case.truck.max_weight_kg
        if case.truck.max_weight_kg > 0
        else 0.0
    )

    visited = len({e.detail.get("client_id") for e in log.events if e.kind == "ARRIVE"})

    return DayKpis(
        algorithm=_algo_name(log),
        date=str(case.date),
        ruta=case.ruta,
        success=result.success,
        error=result.error,
        n_clients_planned=case.n_clients,
        n_clients_visited=visited,
        total_minutes=round(total_min, 2),
        drive_minutes=round(drive_min, 2),
        service_minutes=round(service_min, 2),
        overhead_minutes=round(overhead, 2),
        total_km=round(st.distance_km, 2),
        fuel_liters=round(fuel_l, 2),
        fuel_eur=round(fuel_eur, 2),
        labor_eur=round(labor_eur, 2),
        wear_eur=round(wear_eur, 2),
        total_cost_eur=round(fuel_eur + labor_eur + wear_eur, 2),
        co2_kg=round(co2, 2),
        search_moves=int(st.search_moves),
        tw_violations_min=round(st.tw_violations_min, 1),
        closed_visits=int(st.closed_visits),
        capacity_violations=int(st.capacity_violations),
        drops=len(st.drops),
        delivered_units=round(delivered, 2),
        ordered_units=round(ordered, 2),
        fill_rate=round(fill, 4),
        pallets_loaded=int(pallets_loaded),
        pallet_volume_util=round(vol_util, 4),
        weight_util=round(weight_util, 4),
        returnables_picked_units=round(sum(st.picked_returns.values()), 2),
    )


def _algo_name(log) -> str:
    for e in log.events:
        if e.kind == "SIM_START":
            return str(e.detail.get("algorithm", "unknown"))
    return "unknown"


def _labor_cost(hours: float, tar: Tariffs) -> float:
    base = min(hours, tar.overtime_after_hours) * tar.driver_hourly_eur
    over = max(0.0, hours - tar.overtime_after_hours) * tar.driver_overtime_eur
    return base + over


def _service_minutes(log) -> float:
    kinds = {
        "UNLOAD", "PICKUP_RETURN", "PICK", "BUILD_PALLET", "LOAD",
        "DEPART_DEPOT", "SERVICE_BASE",
    }
    total = 0.0
    prev_t = 0.0
    for e in log.events:
        delta = max(0.0, e.t_min - prev_t)
        if e.kind in kinds:
            total += delta
        prev_t = e.t_min
    return total
