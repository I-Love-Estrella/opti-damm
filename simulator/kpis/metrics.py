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
    depot_minutes: float
    service_minutes: float
    driver_minutes: float
    overhead_minutes: float
    total_km: float
    fuel_liters: float
    fuel_eur: float
    labor_eur: float
    driver_labor_eur: float
    depot_labor_eur: float
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
    placement_rejections: int
    lost_units: float
    # Items the algorithm couldn't pack at planning time. Distinct
    # from `lost_units` (simulator runtime rejections). Real Damm
    # achieves 100% delivery on every recorded route, so any positive
    # number here is a model-fidelity gap (greedy 3D bin-packing has
    # ~70-75% density vs human loaders' 95%+), NOT a real failure.
    pack_overflow_units: float
    pack_overflow_chunks: int
    # Effective fill rate AS-IF the algorithm could pack the way real
    # loaders do. Reality is always 100%, so this is what the model
    # would deliver if our packing density matched the warehouse.
    real_fill_rate: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


def compute(result: SimulationResult, tariffs: Tariffs = DEFAULT_TARIFFS) -> DayKpis:
    st = result.state
    log = result.log
    case = st.case

    drive_min = sum(e.detail.get("drive_min", 0.0) for e in log.events if e.kind in {"ARRIVE", "RETURN_DEPOT"})
    sim_end = max((e.t_min for e in log.events), default=0.0)
    total_min = sim_end
    depot_min = _phase_minutes(log, _DEPOT_KINDS)
    service_min = _phase_minutes(log, _CLIENT_SERVICE_KINDS)
    # Driver shift = drive + on-route service only. Warehouse loading
    # (depot_min) is done by loaders, not the driver — it must NOT count
    # toward the driver's 13 h legal cap (`OVERTIME_LEGAL`).
    driver_min = drive_min + service_min
    overhead = max(0.0, total_min - drive_min - depot_min - service_min)

    fuel_l = (st.distance_km * tariffs.diesel_l_per_100km) / 100.0
    fuel_eur = fuel_l * tariffs.fuel_eur_per_l
    # Two separate labour streams:
    #   - Driver labour (drive + on-route service) at driver_hourly_eur,
    #     with overtime over 8 h.
    #   - Loader labour (depot work) at loader_hourly_eur, no overtime
    #     (warehouse runs shifts, doesn't accumulate per-driver overtime).
    driver_labor_eur = _labor_cost(driver_min / 60.0, tariffs)
    depot_labor_eur = (depot_min / 60.0) * tariffs.loader_hourly_eur
    labor_eur = driver_labor_eur + depot_labor_eur
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
        depot_minutes=round(depot_min, 2),
        service_minutes=round(service_min, 2),
        driver_minutes=round(driver_min, 2),
        overhead_minutes=round(overhead, 2),
        total_km=round(st.distance_km, 2),
        fuel_liters=round(fuel_l, 2),
        fuel_eur=round(fuel_eur, 2),
        labor_eur=round(labor_eur, 2),
        driver_labor_eur=round(driver_labor_eur, 2),
        depot_labor_eur=round(depot_labor_eur, 2),
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
        placement_rejections=len(st.placement_rejections),
        lost_units=round(
            sum(float(r.get("qty") or 0.0) for r in st.placement_rejections), 2
        ),
        pack_overflow_chunks=len(st.pack_overflow),
        pack_overflow_units=round(
            sum(float(qty) for (_, _, qty) in st.pack_overflow), 2
        ),
        # Reality: every recorded delivery actually arrived. Our
        # < 100% fill is the model's bin-packing gap, not a real
        # operational failure. We expose this as a constant 1.0 so
        # downstream metrics (cost-per-delivered-unit, etc.) are
        # computed against the realistic baseline, not against
        # whatever fraction our greedy packer happened to fit.
        real_fill_rate=1.0,
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


# Events charged to depot loading (at the warehouse, before the truck
# leaves). PICK is the dominant cost (~2 min each), so a day with many
# small SKU chunks racks up depot time even when on-route work is fast.
_DEPOT_KINDS = frozenset({"BUILD_PALLET", "PICK", "LOAD", "DEPART_DEPOT"})

# Events charged to on-route client service (handed off to clients).
# Lift / take / replace cycles are the cost of search-moves at delivery
# — a clean LIFO plan keeps these near zero.
_CLIENT_SERVICE_KINDS = frozenset({
    "SERVICE_BASE", "UNLOAD", "PICKUP_RETURN",
    "BLOCKER_LIFT", "TARGET_TAKE", "BLOCKER_REPLACE",
    "DROP", "SETTLE",
})


def _phase_minutes(log, kinds: frozenset[str]) -> float:
    """Sum the time deltas attributable to events whose kind is in `kinds`.

    The simulator advances `t_min` on each event by that event's
    duration, so the delta from the previous event IS that event's
    cost. Splitting `kinds` lets us partition the day into depot work
    (kept at the warehouse) vs on-route service (with the client) vs
    overhead (idle gaps the simulator counts but doesn't attribute).
    """

    total = 0.0
    prev_t = 0.0
    for e in log.events:
        delta = max(0.0, e.t_min - prev_t)
        if e.kind in kinds:
            total += delta
        prev_t = e.t_min
    return total
