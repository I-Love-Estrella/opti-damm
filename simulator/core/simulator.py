"""Simulator: executes a Plan, accumulates costs, validates constraints, emits events."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from simulator.config import (
    DEFAULT_TARIFFS,
    DEFAULT_TIME_MODEL,
    PALLET_VOLUME_M3,
    Tariffs,
    TimeModel,
)
from simulator.core.events import EventLog
from simulator.core.state import WorldState
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import ClientOrder, DayCase
from simulator.domain.commands import (
    BuildPallet,
    Command,
    DepartDepot,
    DriveTo,
    Load,
    Pick,
    PickupReturn,
    ReturnDepot,
    Unload,
)
from simulator.domain.pallet import (
    Pallet,
    PalletClass,
    PalletItem,
    PalletKind,
    sku_class_for_uma,
)
from simulator.domain.plan import Plan


@dataclass
class SimulationResult:
    state: WorldState
    log: EventLog
    success: bool
    error: str | None = None


class Simulator:
    def __init__(
        self,
        clients: Clients,
        network: Network | None = None,
        tariffs: Tariffs = DEFAULT_TARIFFS,
        time_model: TimeModel = DEFAULT_TIME_MODEL,
    ):
        self._clients = clients
        self._network = network or Network(time_model)
        self._tar = tariffs
        self._tm = time_model

    def run(self, case: DayCase, plan: Plan) -> SimulationResult:
        state = WorldState.from_case(case)
        log = EventLog()
        log.emit(state.t_min, "SIM_START", algorithm=plan.algorithm, ruta=case.ruta, date=str(case.date))
        try:
            for cmd in plan.commands:
                self._dispatch(cmd, state, log)
            self._finalize(state, log, plan)
            return SimulationResult(state=state, log=log, success=True)
        except _CommandError as exc:
            log.emit(state.t_min, "SIM_ERROR", message=str(exc))
            return SimulationResult(state=state, log=log, success=False, error=str(exc))

    def _dispatch(self, cmd: Command, st: WorldState, log: EventLog) -> None:
        if isinstance(cmd, BuildPallet):
            self._on_build(cmd, st, log)
        elif isinstance(cmd, Pick):
            self._on_pick(cmd, st, log)
        elif isinstance(cmd, Load):
            self._on_load(cmd, st, log)
        elif isinstance(cmd, DepartDepot):
            self._on_depart(st, log)
        elif isinstance(cmd, DriveTo):
            self._on_drive(cmd, st, log)
        elif isinstance(cmd, Unload):
            self._on_unload(cmd, st, log)
        elif isinstance(cmd, PickupReturn):
            self._on_pickup_return(cmd, st, log)
        elif isinstance(cmd, ReturnDepot):
            self._on_return_depot(st, log)
        else:
            raise _CommandError(f"Unknown command: {cmd!r}")

    def _on_build(self, cmd: BuildPallet, st: WorldState, log: EventLog) -> None:
        if cmd.pallet_id in st.cargo.pallet_by_id or cmd.pallet_id in st.cargo.staging:
            raise _CommandError(f"Pallet {cmd.pallet_id} already exists")
        kind = _coerce_kind(cmd.kind)
        pallet = Pallet(pallet_id=cmd.pallet_id, kind=kind, items=tuple(), primary_client=cmd.primary_client, notes=cmd.notes)
        st.cargo.staging[cmd.pallet_id] = pallet
        st.t_min += self._tm.pallet_build_min
        log.emit(st.t_min, "BUILD_PALLET", pallet_id=cmd.pallet_id, pallet_kind=str(kind), client=cmd.primary_client)

    def _on_pick(self, cmd: Pick, st: WorldState, log: EventLog) -> None:
        pallet = st.cargo.staging.get(cmd.pallet_id) or st.cargo.pallet_by_id.get(cmd.pallet_id)
        if pallet is None:
            raise _CommandError(f"Pick onto unknown pallet {cmd.pallet_id}")
        order_line = _find_order_line(st.case, cmd.sku, cmd.intended_client)
        if order_line is None:
            unit_v, unit_w, uma = 0.001, 1.0, "UN"
        else:
            unit_v, unit_w, uma = (
                order_line.unit_volume_m3,
                order_line.unit_weight_kg,
                order_line.uma,
            )
        item_class = sku_class_for_uma(uma)
        if pallet.pallet_class is None:
            pallet = pallet.with_class(item_class)
        elif pallet.pallet_class != item_class:
            st.capacity_violations += 1
        col_x, col_y, bottom_level = pallet.suggest_position()
        if bottom_level >= pallet.layout.max_level:
            st.capacity_violations += 1
        item = PalletItem(
            sku=cmd.sku,
            qty=cmd.qty,
            unit_volume_m3=unit_v,
            unit_weight_kg=unit_w,
            intended_client=cmd.intended_client,
            is_returnable_empty=False,
            col_x=col_x,
            col_y=col_y,
            bottom_level=bottom_level,
        )
        new_pallet = pallet.add_item(item)
        if cmd.pallet_id in st.cargo.staging:
            st.cargo.staging[cmd.pallet_id] = new_pallet
        else:
            st.cargo.pallet_by_id[cmd.pallet_id] = new_pallet
        elapsed = self._tm.pick_min_per_sku + cmd.qty * self._tm.pick_min_per_box
        st.t_min += elapsed
        log.emit(
            st.t_min,
            "PICK",
            pallet_id=cmd.pallet_id,
            sku=cmd.sku,
            qty=cmd.qty,
            location=cmd.location,
            client=cmd.intended_client,
        )

    def _on_load(self, cmd: Load, st: WorldState, log: EventLog) -> None:
        if cmd.slot_id in st.cargo.pallet_by_slot:
            raise _CommandError(f"Slot {cmd.slot_id} already occupied")
        pallet = st.cargo.staging.pop(cmd.pallet_id, None)
        if pallet is None:
            raise _CommandError(f"Pallet {cmd.pallet_id} not built / already loaded")
        if st.cargo.slot(cmd.slot_id) is None:
            raise _CommandError(f"Slot {cmd.slot_id} does not exist on truck {st.case.truck.code}")
        if pallet.volume_m3 > PALLET_VOLUME_M3 * 1.2:
            st.capacity_violations += 1
        st.cargo.pallet_by_id[pallet.pallet_id] = pallet
        st.cargo.slot_by_pallet[pallet.pallet_id] = cmd.slot_id
        st.cargo.pallet_by_slot[cmd.slot_id] = pallet.pallet_id
        if st.cargo.total_weight_kg() > st.case.truck.max_weight_kg:
            st.capacity_violations += 1
        st.t_min += self._tm.load_min_per_pallet
        log.emit(
            st.t_min,
            "LOAD",
            pallet_id=pallet.pallet_id,
            slot_id=cmd.slot_id,
            volume_m3=pallet.volume_m3,
            weight_kg=pallet.weight_kg,
        )

    def _on_depart(self, st: WorldState, log: EventLog) -> None:
        st.t_min += self._tm.depot_dispatch_min
        log.emit(st.t_min, "DEPART_DEPOT", n_pallets=st.cargo.slots_used())

    def _on_drive(self, cmd: DriveTo, st: WorldState, log: EventLog) -> None:
        client = self._clients.get(cmd.client_id)
        leg = self._network.leg(st.location(), (client.lat, client.lon))
        st.distance_km += leg.distance_km
        st.t_min += leg.duration_min
        st.location_lat, st.location_lon = client.lat, client.lon
        st.current_client = cmd.client_id
        self._check_time_window(client, st)
        log.emit(
            st.t_min,
            "ARRIVE",
            client_id=cmd.client_id,
            client_name=client.name,
            distance_km=leg.distance_km,
            drive_min=leg.duration_min,
        )
        st.t_min += self._tm.base_service_min
        log.emit(st.t_min, "SERVICE_BASE", client_id=cmd.client_id, minutes=self._tm.base_service_min)

    def _on_unload(self, cmd: Unload, st: WorldState, log: EventLog) -> None:
        if st.current_client != cmd.client_id:
            raise _CommandError(f"Unload at {cmd.client_id} but truck is at {st.current_client}")
        pallet = st.cargo.pallet_at(cmd.slot_id)
        if pallet is None:
            raise _CommandError(f"No pallet at slot {cmd.slot_id}")
        moves = self._search_moves(pallet, cmd.client_id, cmd.sku)
        st.search_moves += moves
        new_pallet, removed = pallet.remove_item(cmd.sku, cmd.qty, cmd.client_id)
        if removed is None:
            new_pallet, removed = pallet.remove_item(cmd.sku, cmd.qty, None)
        if removed is None:
            st.drops.append((cmd.client_id, cmd.sku, cmd.qty))
            log.emit(st.t_min, "DROP", client_id=cmd.client_id, sku=cmd.sku, qty=cmd.qty)
            return
        actual_qty = removed.qty
        st.delivered_qty[(cmd.client_id, cmd.sku)] = (
            st.delivered_qty.get((cmd.client_id, cmd.sku), 0.0) + actual_qty
        )
        if new_pallet.is_empty:
            st.cargo.pallet_by_id.pop(pallet.pallet_id, None)
            st.cargo.slot_by_pallet.pop(pallet.pallet_id, None)
            st.cargo.pallet_by_slot.pop(cmd.slot_id, None)
        else:
            st.cargo.pallet_by_id[pallet.pallet_id] = new_pallet
        elapsed = self._tm.service_min_per_pallet
        elapsed += moves * self._tm.service_min_per_search_move
        st.t_min += elapsed
        log.emit(
            st.t_min,
            "UNLOAD",
            client_id=cmd.client_id,
            sku=cmd.sku,
            qty=actual_qty,
            slot_id=cmd.slot_id,
            search_moves=moves,
        )

    def _on_pickup_return(self, cmd: PickupReturn, st: WorldState, log: EventLog) -> None:
        if st.current_client != cmd.client_id:
            raise _CommandError(f"Pickup at {cmd.client_id} but truck is at {st.current_client}")
        slot = st.cargo.slot(cmd.slot_id)
        if slot is None:
            raise _CommandError(f"Slot {cmd.slot_id} unknown")
        pallet = st.cargo.pallet_at(cmd.slot_id)
        if pallet is None:
            empties = Pallet(
                pallet_id=f"R-{cmd.client_id}-{cmd.sku}-{int(st.t_min)}",
                kind=PalletKind.EMPTIES,
                items=tuple(),
                primary_client=cmd.client_id,
                notes="returnables",
                pallet_class=PalletClass.KEG,
            )
            st.cargo.pallet_by_id[empties.pallet_id] = empties
            st.cargo.pallet_by_slot[cmd.slot_id] = empties.pallet_id
            st.cargo.slot_by_pallet[empties.pallet_id] = cmd.slot_id
            pallet = empties
        if pallet.pallet_class is None:
            pallet = pallet.with_class(PalletClass.KEG)
            st.cargo.pallet_by_id[pallet.pallet_id] = pallet
        col_x, col_y, bottom_level = pallet.suggest_position()
        if bottom_level >= pallet.layout.max_level:
            st.capacity_violations += 1
        item = PalletItem(
            sku=cmd.sku,
            qty=cmd.qty,
            unit_volume_m3=0.04,
            unit_weight_kg=2.0,
            intended_client=None,
            is_returnable_empty=True,
            col_x=col_x,
            col_y=col_y,
            bottom_level=bottom_level,
        )
        st.cargo.pallet_by_id[pallet.pallet_id] = pallet.add_item(item)
        st.picked_returns[(cmd.client_id, cmd.sku)] = (
            st.picked_returns.get((cmd.client_id, cmd.sku), 0.0) + cmd.qty
        )
        st.t_min += 0.5 + 0.05 * cmd.qty
        log.emit(st.t_min, "PICKUP_RETURN", client_id=cmd.client_id, sku=cmd.sku, qty=cmd.qty, slot_id=cmd.slot_id)

    def _on_return_depot(self, st: WorldState, log: EventLog) -> None:
        leg = self._network.leg(st.location(), (st.case.depot.lat, st.case.depot.lon))
        st.distance_km += leg.distance_km
        st.t_min += leg.duration_min
        st.location_lat, st.location_lon = st.case.depot.lat, st.case.depot.lon
        st.current_client = None
        n_pallets = st.cargo.slots_used()
        st.t_min += self._tm.return_unload_min_per_pallet * n_pallets
        st.finalized = True
        log.emit(st.t_min, "RETURN_DEPOT", distance_km=leg.distance_km, drive_min=leg.duration_min)

    def _finalize(self, st: WorldState, log: EventLog, plan: Plan) -> None:
        if not st.finalized:
            self._on_return_depot(st, log)
        log.emit(st.t_min, "SIM_END", algorithm=plan.algorithm, distance_km=st.distance_km)

    def _search_moves(self, pallet: Pallet, client: str, sku: str) -> int:
        """Physical-access cost in number of physical units to lift off.

        Convention: col_y is depth from the truck edge. col_y=0 is the door
        side; larger col_y is deeper inside. To reach a target at column
        (col_x, col_y, level), the driver must remove:

          1. every unit in the same column above the target
             (same col_x, same col_y, bottom_level > target.top_level)
          2. every unit in any column closer to the edge
             (any col_x, any level, col_y' < target.col_y)

        Both buckets are summed as physical units (stack_size each).
        """
        target_idx = -1
        for i, it in enumerate(pallet.items):
            if it.sku == sku and it.intended_client in (None, client):
                target_idx = i
                break
        if target_idx < 0:
            return 0

        target = pallet.items[target_idx]
        moves = 0
        for j, it in enumerate(pallet.items):
            if j == target_idx:
                continue
            same_column = it.col_x == target.col_x and it.col_y == target.col_y
            if same_column and it.bottom_level > target.top_level:
                moves += it.stack_size
                continue
            if it.col_y < target.col_y:
                moves += it.stack_size
        return moves

    def _check_time_window(self, client, st: WorldState) -> None:
        if not client.time_windows:
            return
        weekday = (st.case.date.weekday() + 1)
        relevant = [w for w in client.time_windows if w.weekday in (0, weekday)]
        if not relevant:
            return
        if any(w.closed for w in relevant):
            st.closed_visits += 1
        arrive = _minutes_to_time(st.t_min)
        ok = any(w.start <= arrive <= w.end for w in relevant if not w.closed)
        if not ok and relevant:
            best_end = max((w.end for w in relevant if not w.closed), default=None)
            if best_end is not None:
                end_min = best_end.hour * 60 + best_end.minute
                arrive_min = arrive.hour * 60 + arrive.minute
                st.tw_violations_min += max(0.0, arrive_min - end_min)


def _coerce_kind(kind: str) -> PalletKind:
    try:
        return PalletKind(kind)
    except ValueError:
        return PalletKind.MIXED


def _find_order_line(case: DayCase, sku: str, client: str | None):
    for order in case.orders:
        if client is not None and order.client_id != client:
            continue
        for line in order.lines:
            if line.sku == sku:
                return line
    return None


def _minutes_to_time(t_min: float) -> dt.time:
    minutes = int(round(t_min)) % (24 * 60)
    return dt.time(minutes // 60, minutes % 60)


class _CommandError(RuntimeError):
    pass
