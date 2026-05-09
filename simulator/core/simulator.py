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
from simulator.data.catalog import physical_dims
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
        cmds = list(plan.commands)
        try:
            i = 0
            while i < len(cmds):
                cmd = cmds[i]
                if isinstance(cmd, (Unload, PickupReturn)) and state.current_client is not None:
                    # Batch all consecutive Unload/PickupReturn for the same client.
                    j = i
                    unloads: list[Unload] = []
                    pickups: list[PickupReturn] = []
                    while j < len(cmds):
                        c = cmds[j]
                        if isinstance(c, Unload) and c.client_id == state.current_client:
                            unloads.append(c)
                            j += 1
                            continue
                        if isinstance(c, PickupReturn) and c.client_id == state.current_client:
                            pickups.append(c)
                            j += 1
                            continue
                        break
                    if unloads:
                        self._on_stop_batch(unloads, pickups, state, log)
                    else:
                        for p in pickups:
                            self._on_pickup_return(p, state, log)
                    i = j
                    continue
                self._dispatch(cmd, state, log)
                i += 1
            self._finalize(state, log, plan)
            return SimulationResult(state=state, log=log, success=True)
        except _CommandError as exc:
            log.emit(state.t_min, "SIM_ERROR", message=str(exc))
            return SimulationResult(state=state, log=log, success=False, error=str(exc))

    def simulate_loading(self, case: DayCase, plan: Plan) -> SimulationResult:
        """Run the plan only up to (and including) the first DepartDepot.

        Returns a SimulationResult whose WorldState reflects the truck cargo
        right after loading — before any driving. Useful for visualizing the
        initial pallet layout independent of routing.
        """
        state = WorldState.from_case(case)
        log = EventLog()
        log.emit(state.t_min, "SIM_START", algorithm=plan.algorithm, ruta=case.ruta, date=str(case.date))
        try:
            for cmd in plan.commands:
                if isinstance(cmd, DriveTo):
                    break
                self._dispatch(cmd, state, log)
                if isinstance(cmd, DepartDepot):
                    break
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
            unit_v, unit_w, uma, ptype = 0.001, 1.0, "UN", "unit"
        else:
            unit_v, unit_w, uma = (
                order_line.unit_volume_m3,
                order_line.unit_weight_kg,
                order_line.uma,
            )
            ptype = order_line.physical_type.value if hasattr(order_line.physical_type, "value") else str(order_line.physical_type)
        item_class = sku_class_for_uma(uma)
        if pallet.pallet_class is None:
            pallet = pallet.with_class(item_class)
        elif pallet.pallet_class != item_class:
            st.capacity_violations += 1
        # Physical extents: prefer real ZM040 dimensions for this SKU, fall
        # back to per-type estimates. Height scales with qty so a Pick(qty=24)
        # of small cans takes a stack ~24 cans tall.
        if (
            order_line is not None
            and order_line.dim_source == "data"
            and order_line.dim_x_m > 0
            and order_line.dim_y_m > 0
            and order_line.dim_h_m > 0
        ):
            dim_x, dim_y, single_h = (
                order_line.dim_x_m,
                order_line.dim_y_m,
                order_line.dim_h_m,
            )
        else:
            dim_x, dim_y, single_h = physical_dims(ptype)
        total_h = max(1.0, float(cmd.qty)) * single_h
        pos_x, pos_y, pos_z = pallet.suggest_position(dim_x, dim_y, total_h)
        if pos_z + total_h > 1.80 + 0.01:
            # Soft signal — exact validation runs in the validator.
            st.capacity_violations += 1
        item = PalletItem(
            sku=cmd.sku,
            qty=cmd.qty,
            unit_volume_m3=unit_v,
            unit_weight_kg=unit_w,
            intended_client=cmd.intended_client,
            is_returnable_empty=False,
            physical_type=ptype,
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            dim_x=dim_x,
            dim_y=dim_y,
            dim_h=total_h,
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

        target = None
        for it in pallet.items:
            if it.sku == cmd.sku and it.intended_client in (None, cmd.client_id):
                target = it
                break
        if target is None:
            st.drops.append((cmd.client_id, cmd.sku, cmd.qty))
            log.emit(
                st.t_min,
                "DROP",
                client_id=cmd.client_id,
                sku=cmd.sku,
                qty=cmd.qty,
                slot_id=cmd.slot_id,
                reason="target item not on pallet",
            )
            return

        blockers = self._blockers_in_lift_order(pallet, target)
        moves = sum(b.stack_size for b in blockers)
        st.search_moves += moves

        per_box_lift = self._tm.service_min_per_search_move / 2.0
        per_box_replace = self._tm.service_min_per_search_move / 2.0

        # Phase 1: lift each blocker box-by-box. Top of each blocker stack
        # comes off first (highest level → lowest level).
        for b in blockers:
            same_col = b.col_x == target.col_x and b.col_y == target.col_y
            reason_text = (
                "in target column above target" if same_col else "in stack closer to truck edge"
            )
            for unit in range(b.stack_size - 1, -1, -1):
                level = b.bottom_level + unit
                st.t_min += per_box_lift
                log.emit(
                    st.t_min,
                    "BLOCKER_LIFT",
                    client_id=cmd.client_id,
                    slot_id=cmd.slot_id,
                    target_sku=cmd.sku,
                    target_client=cmd.client_id,
                    sku=b.sku,
                    qty=1,
                    intended_client=b.intended_client,
                    col_x=b.col_x,
                    col_y=b.col_y,
                    bottom_level=b.bottom_level,
                    pos_x=b.pos_x,
                    pos_y=b.pos_y,
                    pos_z=b.pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=b.dim_h,
                    level=level,
                    unit_idx=unit,
                    total_units=b.stack_size,
                    time_min=round(per_box_lift, 4),
                    reason=reason_text,
                )

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

        # Phase 2: take target box-by-box (top → bottom). Total time stays
        # service_min_per_pallet, split across actual_qty units.
        take_total_min = self._tm.service_min_per_pallet
        target_units = max(1, int(round(actual_qty)))
        per_unit_take = take_total_min / target_units
        target_top = target.bottom_level + target.stack_size - 1
        for u in range(target_units):
            level = max(target.bottom_level, target_top - u)
            st.t_min += per_unit_take
            log.emit(
                st.t_min,
                "TARGET_TAKE",
                client_id=cmd.client_id,
                sku=cmd.sku,
                qty=1,
                slot_id=cmd.slot_id,
                col_x=target.col_x,
                col_y=target.col_y,
                bottom_level=target.bottom_level,
                pos_x=target.pos_x,
                pos_y=target.pos_y,
                pos_z=target.pos_z,
                dim_x=target.dim_x,
                dim_y=target.dim_y,
                dim_h=target.dim_h,
                level=level,
                unit_idx=u,
                total_units=target_units,
                time_min=round(per_unit_take, 4),
                reason="hand target box to client",
            )

        if new_pallet.is_empty:
            st.cargo.pallet_by_id.pop(pallet.pallet_id, None)
            st.cargo.slot_by_pallet.pop(pallet.pallet_id, None)
            st.cargo.pallet_by_slot.pop(cmd.slot_id, None)
        else:
            st.cargo.pallet_by_id[pallet.pallet_id] = new_pallet

        # Phase 3: replace blockers in reverse-of-lift LIFO order. Within each
        # blocker, replay bottom → top (rebuild the original stack).
        for b in reversed(blockers):
            for unit in range(b.stack_size):
                level = b.bottom_level + unit
                st.t_min += per_box_replace
                log.emit(
                    st.t_min,
                    "BLOCKER_REPLACE",
                    client_id=cmd.client_id,
                    slot_id=cmd.slot_id,
                    target_sku=cmd.sku,
                    sku=b.sku,
                    qty=1,
                    intended_client=b.intended_client,
                    col_x=b.col_x,
                    col_y=b.col_y,
                    bottom_level=b.bottom_level,
                    pos_x=b.pos_x,
                    pos_y=b.pos_y,
                    pos_z=b.pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=b.dim_h,
                    level=level,
                    unit_idx=unit,
                    total_units=b.stack_size,
                    time_min=round(per_box_replace, 4),
                    reason="restore stack after target taken",
                )

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
        # Empty kegs use keg dimensions; cmd.qty stacks them vertically.
        dim_x, dim_y, single_h = physical_dims("keg")
        total_h = max(1.0, float(cmd.qty)) * single_h
        pos_x, pos_y, pos_z = pallet.suggest_position(dim_x, dim_y, total_h)
        if pos_z + total_h > 1.80 + 0.01:
            st.capacity_violations += 1
        item = PalletItem(
            sku=cmd.sku,
            qty=cmd.qty,
            unit_volume_m3=0.04,
            unit_weight_kg=2.0,
            intended_client=None,
            is_returnable_empty=True,
            physical_type="keg",
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            dim_x=dim_x,
            dim_y=dim_y,
            dim_h=total_h,
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

    def _on_stop_batch(
        self,
        unload_cmds: list[Unload],
        pickup_cmds: list[PickupReturn],
        st: WorldState,
        log: EventLog,
    ) -> None:
        """Process all Unload + PickupReturn commands at one stop as a batch.

        Optimization rules (matches a real driver's behavior):
          1. For each slot, gather ALL targets at this stop, then compute the
             combined set of items physically blocking any of them.
          2. Items above-or-in-front whose intended_client == current client
             are not blockers — they go to this client too. Lift them with
             the target, then DELIVER them (no replace).
          3. Items belonging to OTHER clients are foreign blockers — lift,
             then replace AFTER all targets and same-client items are taken.
          4. Each blocker is lifted at most once across the whole stop,
             never re-lifted between deliveries to the same client.
        """
        if not unload_cmds:
            for p in pickup_cmds:
                self._on_pickup_return(p, st, log)
            return

        client_id = unload_cmds[0].client_id
        if st.current_client != client_id:
            raise _CommandError(
                f"Unload at {client_id} but truck is at {st.current_client}"
            )

        by_slot: dict[str, list[Unload]] = {}
        for cmd in unload_cmds:
            by_slot.setdefault(cmd.slot_id, []).append(cmd)

        for slot_id, slot_cmds in by_slot.items():
            self._unload_slot_batch(slot_id, client_id, slot_cmds, st, log)

        for p in pickup_cmds:
            self._on_pickup_return(p, st, log)

    def _unload_slot_batch(
        self,
        slot_id: str,
        client_id: str,
        cmds: list[Unload],
        st: WorldState,
        log: EventLog,
    ) -> None:
        pallet = st.cargo.pallet_at(slot_id)
        if pallet is None:
            for cmd in cmds:
                st.drops.append((cmd.client_id, cmd.sku, cmd.qty))
                log.emit(
                    st.t_min,
                    "DROP",
                    client_id=cmd.client_id,
                    sku=cmd.sku,
                    qty=cmd.qty,
                    slot_id=slot_id,
                    reason="no pallet at slot",
                )
            return

        targets: list[tuple[Unload, PalletItem]] = []
        target_ids: set[int] = set()
        for cmd in cmds:
            picked: PalletItem | None = None
            for it in pallet.items:
                if id(it) in target_ids:
                    continue
                if it.sku == cmd.sku and it.intended_client in (None, client_id):
                    picked = it
                    target_ids.add(id(it))
                    break
            if picked is None:
                st.drops.append((cmd.client_id, cmd.sku, cmd.qty))
                log.emit(
                    st.t_min,
                    "DROP",
                    client_id=cmd.client_id,
                    sku=cmd.sku,
                    qty=cmd.qty,
                    slot_id=slot_id,
                    reason="target item not on pallet",
                )
                continue
            targets.append((cmd, picked))

        if not targets:
            return

        foreign_blockers: list[PalletItem] = []
        same_client_in_path: list[PalletItem] = []
        foreign_seen: set[int] = set()
        same_seen: set[int] = set()
        reasons: dict[int, str] = {}

        for _, target in targets:
            for it in pallet.items:
                if id(it) in target_ids or it.qty <= 0:
                    continue
                is_above = it.pos_z + 1e-6 >= target.top_z and it.overlaps_xy(target)
                is_edge = it.pos_y + 1e-6 < target.pos_y and it.overlaps_xz(target)
                if not (is_above or is_edge):
                    continue
                it_id = id(it)
                reasons.setdefault(
                    it_id,
                    "stacked above target" if is_above else "in front of target (closer to door)",
                )
                if it.intended_client == client_id:
                    if it_id not in same_seen:
                        same_seen.add(it_id)
                        same_client_in_path.append(it)
                else:
                    if it_id not in foreign_seen:
                        foreign_seen.add(it_id)
                        foreign_blockers.append(it)

        # Lift order: closer to edge first, then top-down within column.
        lift_order = sorted(
            foreign_blockers + same_client_in_path,
            key=lambda b: (b.pos_y, b.pos_x, -b.pos_z),
        )

        moves = sum(b.stack_size for b in lift_order)
        st.search_moves += moves

        per_box_lift = self._tm.service_min_per_search_move / 2.0
        per_box_replace = self._tm.service_min_per_search_move / 2.0

        # Phase 1: lift everything in the way (foreign + same-client).
        for b in lift_order:
            will_be_delivered = b.intended_client == client_id
            for unit in range(b.stack_size - 1, -1, -1):
                level = b.bottom_level + unit
                st.t_min += per_box_lift
                log.emit(
                    st.t_min,
                    "BLOCKER_LIFT",
                    client_id=client_id,
                    slot_id=slot_id,
                    sku=b.sku,
                    qty=1,
                    intended_client=b.intended_client,
                    col_x=b.col_x,
                    col_y=b.col_y,
                    bottom_level=b.bottom_level,
                    pos_x=b.pos_x,
                    pos_y=b.pos_y,
                    pos_z=b.pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=b.dim_h,
                    level=level,
                    unit_idx=unit,
                    total_units=b.stack_size,
                    time_min=round(per_box_lift, 4),
                    reason=reasons.get(id(b), ""),
                    will_be_delivered=will_be_delivered,
                    physical_type=b.physical_type,
                )

        # Phase 2: take all targets (top-down, edge-first to keep physics tidy).
        targets_sorted = sorted(targets, key=lambda ct: (ct[1].pos_y, ct[1].pos_x, -ct[1].pos_z))
        for cmd, target in targets_sorted:
            self._take_item(
                slot_id=slot_id,
                client_id=client_id,
                cmd_sku=cmd.sku,
                cmd_qty=cmd.qty,
                item_for_log=target,
                reason="hand target box to client",
                opportunistic=False,
                st=st,
                log=log,
            )

        # Phase 3: deliver same-client items that were lifted as path-clearing.
        same_client_sorted = sorted(
            same_client_in_path, key=lambda b: (b.pos_y, b.pos_x, -b.pos_z)
        )
        for it in same_client_sorted:
            self._take_item(
                slot_id=slot_id,
                client_id=client_id,
                cmd_sku=it.sku,
                cmd_qty=it.qty,
                item_for_log=it,
                reason="opportunistic delivery (was in lift path)",
                opportunistic=True,
                st=st,
                log=log,
            )

        # Phase 4: replace ONLY foreign blockers, in reverse-of-lift LIFO order.
        for b in reversed(foreign_blockers):
            for unit in range(b.stack_size):
                level = b.bottom_level + unit
                st.t_min += per_box_replace
                log.emit(
                    st.t_min,
                    "BLOCKER_REPLACE",
                    client_id=client_id,
                    slot_id=slot_id,
                    sku=b.sku,
                    qty=1,
                    intended_client=b.intended_client,
                    col_x=b.col_x,
                    col_y=b.col_y,
                    bottom_level=b.bottom_level,
                    pos_x=b.pos_x,
                    pos_y=b.pos_y,
                    pos_z=b.pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=b.dim_h,
                    level=level,
                    unit_idx=unit,
                    total_units=b.stack_size,
                    time_min=round(per_box_replace, 4),
                    reason="restore stack after target taken",
                    physical_type=b.physical_type,
                )

    def _take_item(
        self,
        *,
        slot_id: str,
        client_id: str,
        cmd_sku: str,
        cmd_qty: float,
        item_for_log: PalletItem,
        reason: str,
        opportunistic: bool,
        st: WorldState,
        log: EventLog,
    ) -> None:
        """Remove item from pallet (or partial), emit per-box TARGET_TAKE + UNLOAD."""
        current_pallet = st.cargo.pallet_at(slot_id)
        if current_pallet is None:
            st.drops.append((client_id, cmd_sku, cmd_qty))
            log.emit(
                st.t_min,
                "DROP",
                client_id=client_id,
                sku=cmd_sku,
                qty=cmd_qty,
                slot_id=slot_id,
                reason="pallet vanished mid-batch",
            )
            return

        new_pallet, removed = current_pallet.remove_item(cmd_sku, cmd_qty, client_id)
        if removed is None:
            new_pallet, removed = current_pallet.remove_item(cmd_sku, cmd_qty, None)
        if removed is None:
            st.drops.append((client_id, cmd_sku, cmd_qty))
            log.emit(
                st.t_min,
                "DROP",
                client_id=client_id,
                sku=cmd_sku,
                qty=cmd_qty,
                slot_id=slot_id,
                reason="item already gone (took as same-client earlier)",
            )
            return

        actual_qty = removed.qty
        st.delivered_qty[(client_id, cmd_sku)] = (
            st.delivered_qty.get((client_id, cmd_sku), 0.0) + actual_qty
        )

        target_units = max(1, int(round(actual_qty)))
        per_unit_take = self._tm.service_min_per_pallet / target_units
        target_top = item_for_log.bottom_level + item_for_log.stack_size - 1
        for u in range(target_units):
            level = max(item_for_log.bottom_level, target_top - u)
            st.t_min += per_unit_take
            log.emit(
                st.t_min,
                "TARGET_TAKE",
                client_id=client_id,
                sku=cmd_sku,
                qty=1,
                slot_id=slot_id,
                col_x=item_for_log.col_x,
                col_y=item_for_log.col_y,
                bottom_level=item_for_log.bottom_level,
                pos_x=item_for_log.pos_x,
                pos_y=item_for_log.pos_y,
                pos_z=item_for_log.pos_z,
                dim_x=item_for_log.dim_x,
                dim_y=item_for_log.dim_y,
                dim_h=item_for_log.dim_h,
                level=level,
                unit_idx=u,
                total_units=target_units,
                time_min=round(per_unit_take, 4),
                reason=reason,
                opportunistic=opportunistic,
                physical_type=item_for_log.physical_type,
            )

        if new_pallet.is_empty:
            st.cargo.pallet_by_id.pop(current_pallet.pallet_id, None)
            st.cargo.slot_by_pallet.pop(current_pallet.pallet_id, None)
            st.cargo.pallet_by_slot.pop(slot_id, None)
        else:
            st.cargo.pallet_by_id[current_pallet.pallet_id] = new_pallet

        log.emit(
            st.t_min,
            "UNLOAD",
            client_id=client_id,
            sku=cmd_sku,
            qty=actual_qty,
            slot_id=slot_id,
            search_moves=0,
            opportunistic=opportunistic,
        )

    def _blockers_in_lift_order(self, pallet: Pallet, target: PalletItem) -> list[PalletItem]:
        """Items that must be lifted off to reach `target`. Two reasons to
        block: (1) item sits physically above target (z above target.top_z and
        xy footprint overlaps), (2) item is closer to the truck edge (lower y)
        and overlaps target's xz extent — driver has to pull it out of the way.

        Lift order: edge-first (lowest pos_y), then top-down (highest pos_z).
        """
        out: list[PalletItem] = []
        for it in pallet.items:
            if it is target or it.qty <= 0:
                continue
            above = it.pos_z + 1e-6 >= target.top_z and it.overlaps_xy(target)
            front = it.pos_y + 1e-6 < target.pos_y and it.overlaps_xz(target)
            if above or front:
                out.append(it)
        out.sort(key=lambda b: (b.pos_y, b.pos_x, -b.pos_z))
        return out

    def _search_moves(self, pallet: Pallet, client: str, sku: str) -> int:
        """Physical-access cost: number of stacked-unit equivalents that must
        be lifted off to reach the target item, computed via bbox overlap.

        An item blocks the target when:
          - it sits above target (pos_z >= target.top_z) and xy footprints overlap
          - or it is closer to the truck edge (lower pos_y) and overlaps the
            target in (x, z)
        """
        target = None
        for it in pallet.items:
            if it.sku == sku and it.intended_client in (None, client):
                target = it
                break
        if target is None:
            return 0

        moves = 0
        for it in pallet.items:
            if it is target or it.qty <= 0:
                continue
            above = it.pos_z + 1e-6 >= target.top_z and it.overlaps_xy(target)
            front = it.pos_y + 1e-6 < target.pos_y and it.overlaps_xz(target)
            if above or front:
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
