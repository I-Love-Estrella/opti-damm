"""Simulator: executes a Plan, accumulates costs, validates constraints, emits events."""

from __future__ import annotations

import datetime as dt
import sys
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
        strict_physics: bool = False,
    ):
        self._clients = clients
        self._network = network or Network(time_model)
        self._tar = tariffs
        self._tm = time_model
        # When True the simulator raises _CommandError on floating /
        # overlap. When False (default) those become capacity_violations
        # and the run continues so we can still see KPIs.
        self._strict_physics = strict_physics

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
            _check_all_pallets(
                st, where=f"after Pick {cmd.sku}@{cmd.pallet_id}",
                strict=self._strict_physics, log=log,
            )
        elif isinstance(cmd, Load):
            self._on_load(cmd, st, log)
            _check_all_pallets(
                st, where=f"after Load {cmd.pallet_id}->{cmd.slot_id}",
                strict=self._strict_physics, log=log,
            )
        elif isinstance(cmd, DepartDepot):
            self._on_depart(st, log)
        elif isinstance(cmd, DriveTo):
            self._on_drive(cmd, st, log)
        elif isinstance(cmd, Unload):
            self._on_unload(cmd, st, log)
        elif isinstance(cmd, PickupReturn):
            self._on_pickup_return(cmd, st, log)
            _check_all_pallets(
                st, where=f"after PickupReturn {cmd.client_id}",
                strict=self._strict_physics, log=log,
            )
        elif isinstance(cmd, ReturnDepot):
            self._on_return_depot(st, log)
        else:
            raise _CommandError(f"Unknown command: {cmd!r}")

    def _on_build(self, cmd: BuildPallet, st: WorldState, log: EventLog) -> None:
        if cmd.pallet_id in st.cargo.pallet_by_id or cmd.pallet_id in st.cargo.staging:
            raise _CommandError(f"Pallet {cmd.pallet_id} already exists")
        kind = _coerce_kind(cmd.kind)
        cls = _coerce_class(cmd.pallet_class) if cmd.pallet_class else None
        pallet = Pallet(
            pallet_id=cmd.pallet_id,
            kind=kind,
            items=tuple(),
            primary_client=cmd.primary_client,
            notes=cmd.notes,
            pallet_class=cls,
        )
        st.cargo.staging[cmd.pallet_id] = pallet
        st.t_min += self._tm.pallet_build_min
        log.emit(st.t_min, "BUILD_PALLET", pallet_id=cmd.pallet_id, pallet_kind=str(kind), client=cmd.primary_client)

    def _on_pick(self, cmd: Pick, st: WorldState, log: EventLog) -> None:
        """Place an item exactly where the algorithm asked. The simulator
        performs no geometry inference — Pick must arrive with pos / dim /
        unit physics already filled in."""
        pallet = st.cargo.staging.get(cmd.pallet_id) or st.cargo.pallet_by_id.get(cmd.pallet_id)
        if pallet is None:
            raise _CommandError(f"Pick onto unknown pallet {cmd.pallet_id}")
        if cmd.dim_x <= 0 or cmd.dim_y <= 0 or cmd.dim_h <= 0:
            raise _CommandError(
                f"Pick {cmd.sku}@{cmd.pallet_id} missing dimensions "
                f"(dim_x={cmd.dim_x}, dim_y={cmd.dim_y}, dim_h={cmd.dim_h}). "
                "Algorithms must fill geometry — simulator does not infer."
            )

        item_class = _class_for_phys(cmd.physical_type)
        if pallet.pallet_class is None:
            pallet = pallet.with_class(item_class)
        elif pallet.pallet_class != item_class:
            st.capacity_violations += 1

        if cmd.pos_z + cmd.dim_h > 1.80 + 0.01:
            st.capacity_violations += 1

        item = PalletItem(
            sku=cmd.sku,
            qty=cmd.qty,
            unit_volume_m3=cmd.unit_volume_m3,
            unit_weight_kg=cmd.unit_weight_kg,
            intended_client=cmd.intended_client,
            is_returnable_empty=False,
            physical_type=cmd.physical_type,
            pos_x=cmd.pos_x,
            pos_y=cmd.pos_y,
            pos_z=cmd.pos_z,
            dim_x=cmd.dim_x,
            dim_y=cmd.dim_y,
            dim_h=cmd.dim_h,
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
            slice_h = (b.dim_h / b.stack_size) if b.stack_size > 0 else b.dim_h
            for unit in range(b.stack_size - 1, -1, -1):
                level = b.bottom_level + unit
                unit_pos_z = b.pos_z + unit * slice_h
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
                    pos_z=unit_pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=slice_h,
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
        slice_h = (target.dim_h / target.stack_size) if target.stack_size > 0 else target.dim_h
        for u in range(target_units):
            level = max(target.bottom_level, target_top - u)
            unit_pos_z = target.pos_z + (level - target.bottom_level) * slice_h
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
                pos_z=unit_pos_z,
                dim_x=target.dim_x,
                dim_y=target.dim_y,
                dim_h=slice_h,
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
            slice_h = (b.dim_h / b.stack_size) if b.stack_size > 0 else b.dim_h
            for unit in range(b.stack_size):
                level = b.bottom_level + unit
                unit_pos_z = b.pos_z + unit * slice_h
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
                    pos_z=unit_pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=slice_h,
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
        if cmd.dim_x <= 0 or cmd.dim_y <= 0 or cmd.dim_h <= 0:
            raise _CommandError(
                f"PickupReturn {cmd.sku}@{cmd.slot_id} missing dimensions. "
                "Algorithms must fill geometry — simulator does not infer."
            )
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
        if cmd.pos_z + cmd.dim_h > 1.80 + 0.01:
            st.capacity_violations += 1

        # Defensive snap: if the algorithm-supplied position would
        # (a) collide with an existing item, or (b) float in mid-air
        # with nothing supporting it, find a clean anchor instead.
        # Counts as a capacity_violation and prints a warning so the
        # algorithm bug stays visible — but the visualizer never
        # shows two items occupying the same volume or a keg
        # hovering. As a last resort the height constraint is
        # dropped (an over-tall stack is the lesser evil); if even
        # that fails we drop the empty.
        pos_x, pos_y, pos_z = cmd.pos_x, cmd.pos_y, cmd.pos_z
        would_collide = _aabb_collides_with_pallet(
            pos_x, pos_y, pos_z, cmd.dim_x, cmd.dim_y, cmd.dim_h, pallet
        )
        would_float = not _has_support(
            pos_x, pos_y, pos_z, cmd.dim_x, cmd.dim_y, pallet
        )
        if would_collide or would_float:
            new_pos = _find_clean_position(
                pallet, cmd.dim_x, cmd.dim_y, cmd.dim_h
            )
            if new_pos is None:
                new_pos = _find_clean_position(
                    pallet, cmd.dim_x, cmd.dim_y, cmd.dim_h,
                    enforce_pallet_height=False,
                )
            if new_pos is None:
                # No clean spot anywhere on this pallet — refuse rather
                # than render an overlap. The validator surfaces this as
                # OVERLAP / dropped returnable.
                _print_violation(
                    f"PHYSICS [PickupReturn {cmd.sku}@{cmd.slot_id}]: "
                    f"no clean position for empty at "
                    f"({pos_x:.3f},{pos_y:.3f},{pos_z:.3f}) — DROPPED"
                )
                log.emit(
                    st.t_min,
                    "PHYSICS_VIOLATION",
                    code="PICKUP_DROPPED_NO_FIT",
                    message=(
                        f"empty keg dropped at slot {cmd.slot_id}: "
                        f"requested pos ({pos_x:.3f},{pos_y:.3f},{pos_z:.3f}) "
                        f"would overlap and pallet has no clean anchor"
                    ),
                    where="PickupReturn",
                    slot_id=cmd.slot_id,
                    sku=cmd.sku,
                    pos=[pos_x, pos_y, pos_z],
                )
                st.capacity_violations += 1
                st.t_min += 0.5 + 0.05 * cmd.qty
                return
            _print_violation(
                f"PHYSICS [PickupReturn {cmd.sku}@{cmd.slot_id}]: "
                f"requested ({pos_x:.3f},{pos_y:.3f},{pos_z:.3f}) collides — "
                f"snapping to ({new_pos[0]:.3f},{new_pos[1]:.3f},{new_pos[2]:.3f})"
            )
            log.emit(
                st.t_min,
                "PHYSICS_VIOLATION",
                code="PICKUP_OVERLAP_SNAPPED",
                message=(
                    f"empty keg requested at ({pos_x:.3f},{pos_y:.3f},{pos_z:.3f}) "
                    f"would overlap existing items — snapped to "
                    f"({new_pos[0]:.3f},{new_pos[1]:.3f},{new_pos[2]:.3f})"
                ),
                where="PickupReturn",
                slot_id=cmd.slot_id,
                sku=cmd.sku,
                pos=[pos_x, pos_y, pos_z],
            )
            st.capacity_violations += 1
            pos_x, pos_y, pos_z = new_pos

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
            dim_x=cmd.dim_x,
            dim_y=cmd.dim_y,
            dim_h=cmd.dim_h,
        )
        st.cargo.pallet_by_id[pallet.pallet_id] = pallet.add_item(item)
        st.picked_returns[(cmd.client_id, cmd.sku)] = (
            st.picked_returns.get((cmd.client_id, cmd.sku), 0.0) + cmd.qty
        )
        st.t_min += 0.5 + 0.05 * cmd.qty
        # Carry the ACTUAL placed position (after any defensive snap)
        # in the event so the frontend renders the keg where the
        # simulator put it. Without these, the frontend defaulted to
        # (0,0,0) and stacked every empty on top of the door corner —
        # the source of "small keg inside another keg" overlap reports.
        log.emit(
            st.t_min,
            "PICKUP_RETURN",
            client_id=cmd.client_id,
            sku=cmd.sku,
            qty=cmd.qty,
            slot_id=cmd.slot_id,
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            dim_x=cmd.dim_x,
            dim_y=cmd.dim_y,
            dim_h=cmd.dim_h,
        )

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
            # Settle floating items: when a delivered item was supporting
            # another (e.g. an empty perched on a half-keg whose client
            # just took the half-keg), the item above is now hovering.
            # Drop it down to a supported anchor before the physics check
            # so we don't leave the cargo in an unphysical state.
            _settle_pallet(st, slot_id, log)
            _check_all_pallets(
                st, where=f"after Unload batch slot={slot_id} client={client_id}",
                strict=self._strict_physics, log=log,
            )

        for p in pickup_cmds:
            self._on_pickup_return(p, st, log)
            _settle_pallet(st, p.slot_id, log)
            _check_all_pallets(
                st, where=f"after PickupReturn {p.client_id}",
                strict=self._strict_physics, log=log,
            )

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

        # Algorithm-supplied lift list: simulator does NO discovery. If
        # any cmd in this slot batch carries `lifts`, use the union of
        # all lifts and skip the geometry-based blocker search.
        explicit_lifts = []
        for cmd in cmds:
            for lift in getattr(cmd, "lifts", ()):
                explicit_lifts.append(lift)

        if explicit_lifts:
            for lift in explicit_lifts:
                matched = self._match_lift(pallet, lift, target_ids, foreign_seen | same_seen)
                if matched is None:
                    continue
                it_id = id(matched)
                reasons.setdefault(it_id, "algorithm-ordered lift")
                if matched.intended_client == client_id:
                    same_seen.add(it_id)
                    same_client_in_path.append(matched)
                else:
                    foreign_seen.add(it_id)
                    foreign_blockers.append(matched)
        else:
            # Legacy path — simulator searches for blockers itself.
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
            slice_h = (b.dim_h / b.stack_size) if b.stack_size > 0 else b.dim_h
            for unit in range(b.stack_size - 1, -1, -1):
                level = b.bottom_level + unit
                # Per-unit pos_z so the frontend, which splits the
                # PalletItem into stack_size cubes, can match the right
                # cube by exact (pos_x, pos_y, pos_z).
                unit_pos_z = b.pos_z + unit * slice_h
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
                    pos_z=unit_pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=slice_h,
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

        # Phase 4: replace foreign blockers. Algorithm-supplied restock
        # plan wins — moves the blocker physically to the new position
        # and updates the pallet state. If no plan was provided, the
        # blocker stays at its original pos (legacy behaviour, just an
        # event log of the lift→replace cycle).
        #
        # Matching strategy: per-blocker, find the restock entry whose
        # (sku, client) AND `from_pos_*` match the blocker's actual
        # position. This is identity-strong even when several blockers
        # share (sku, client) — used to be the source of "UE902 stacked
        # on top of UE902" overlap bugs because two blockers with the
        # same key would get their destinations swapped.
        # Legacy plans (from_pos == 0,0,0 and not equal to b.pos) fall
        # through to the old (sku, client) FIFO queue.
        restock_lookup = _collect_restock_static(cmds)
        for b in reversed(foreign_blockers):
            key = (b.sku, b.intended_client)
            queue = restock_lookup.get(key, [])
            new_pos = _pop_matching_restock(queue, b)
            target_pos_x = new_pos.pos_x if new_pos is not None else b.pos_x
            target_pos_y = new_pos.pos_y if new_pos is not None else b.pos_y
            target_pos_z = new_pos.pos_z if new_pos is not None else b.pos_z

            if new_pos is not None and (
                abs(target_pos_x - b.pos_x) > 1e-6
                or abs(target_pos_y - b.pos_y) > 1e-6
                or abs(target_pos_z - b.pos_z) > 1e-6
            ):
                # Physically move the blocker on the pallet — but only
                # if its target spot actually differs from where it is.
                # Identity removal (`remove_specific`) avoids the bug
                # where two items share (sku, client) and `remove_item`
                # picks the wrong one.
                cur_pallet = st.cargo.pallet_at(slot_id)
                if cur_pallet is not None:
                    cur_pallet, removed = cur_pallet.remove_specific(b)
                    if removed is not None:
                        # Defensive snap — if the algorithm asked us to
                        # restock the blocker into a spot that's already
                        # occupied by another item, find a clean anchor
                        # so we never produce visible overlap.
                        if _aabb_collides_with_pallet(
                            target_pos_x, target_pos_y, target_pos_z,
                            b.dim_x, b.dim_y, b.dim_h, cur_pallet,
                        ):
                            snapped = _find_clean_position(
                                cur_pallet, b.dim_x, b.dim_y, b.dim_h
                            )
                            if snapped is None:
                                snapped = _find_clean_position(
                                    cur_pallet, b.dim_x, b.dim_y, b.dim_h,
                                    enforce_pallet_height=False,
                                )
                            if snapped is not None:
                                _print_violation(
                                    f"PHYSICS [restock {b.sku}@{slot_id}]: "
                                    f"requested ({target_pos_x:.3f},{target_pos_y:.3f},{target_pos_z:.3f}) "
                                    f"collides — snapping to "
                                    f"({snapped[0]:.3f},{snapped[1]:.3f},{snapped[2]:.3f})"
                                )
                                log.emit(
                                    st.t_min,
                                    "PHYSICS_VIOLATION",
                                    code="RESTOCK_OVERLAP_SNAPPED",
                                    message=(
                                        f"blocker {b.sku} restock at "
                                        f"({target_pos_x:.3f},{target_pos_y:.3f},{target_pos_z:.3f}) "
                                        f"would overlap — snapped to "
                                        f"({snapped[0]:.3f},{snapped[1]:.3f},{snapped[2]:.3f})"
                                    ),
                                    where="restock",
                                    slot_id=slot_id,
                                    sku=b.sku,
                                    pos=[target_pos_x, target_pos_y, target_pos_z],
                                )
                                st.capacity_violations += 1
                                target_pos_x, target_pos_y, target_pos_z = snapped
                        moved = PalletItem(
                            sku=b.sku,
                            qty=b.qty,
                            unit_volume_m3=b.unit_volume_m3,
                            unit_weight_kg=b.unit_weight_kg,
                            intended_client=b.intended_client,
                            is_returnable_empty=b.is_returnable_empty,
                            physical_type=b.physical_type,
                            pos_x=target_pos_x,
                            pos_y=target_pos_y,
                            pos_z=target_pos_z,
                            dim_x=b.dim_x,
                            dim_y=b.dim_y,
                            dim_h=b.dim_h,
                        )
                        cur_pallet = cur_pallet.add_item(moved)
                        st.cargo.pallet_by_id[cur_pallet.pallet_id] = cur_pallet

            slice_h = (b.dim_h / b.stack_size) if b.stack_size > 0 else b.dim_h
            for unit in range(b.stack_size):
                level = b.bottom_level + unit
                # Per-unit pos_z. For BLOCKER_LIFT the matcher used the
                # ORIGINAL pos to find the right cube. For REPLACE we
                # write the NEW per-unit pos so the cube lands exactly
                # where the algorithm planned. `from_pos_*` carries the
                # blocker's ORIGINAL position so the frontend can pair
                # this REPLACE with the correct LIFT — without it, two
                # blockers sharing (sku, client) get their destinations
                # swapped in the visualizer.
                unit_pos_z = target_pos_z + unit * slice_h
                from_unit_pos_z = b.pos_z + unit * slice_h
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
                    pos_x=target_pos_x,
                    pos_y=target_pos_y,
                    pos_z=unit_pos_z,
                    from_pos_x=b.pos_x,
                    from_pos_y=b.pos_y,
                    from_pos_z=from_unit_pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=slice_h,
                    level=level,
                    unit_idx=unit,
                    total_units=b.stack_size,
                    time_min=round(per_box_replace, 4),
                    reason=(
                        "restocked by algorithm"
                        if new_pos is not None
                        else "restore stack after target taken"
                    ),
                    physical_type=b.physical_type,
                )

    @staticmethod
    def _match_lift(
        pallet: Pallet,
        lift,
        target_ids: set[int],
        already_taken: set[int],
    ) -> PalletItem | None:
        """Find the pallet item that the algorithm meant when it added
        `lift` to an Unload command. Match on (sku, intended_client) +
        approximate position; first qualifying item wins."""
        eps = 1e-3
        for it in pallet.items:
            if id(it) in target_ids or id(it) in already_taken or it.qty <= 0:
                continue
            if it.sku != lift.sku:
                continue
            if it.intended_client != lift.intended_client:
                continue
            if (
                abs(it.pos_x - lift.pos_x) < eps
                and abs(it.pos_y - lift.pos_y) < eps
                and abs(it.pos_z - lift.pos_z) < eps
            ):
                return it
        # Fall back: ignore position, match by (sku, client).
        for it in pallet.items:
            if id(it) in target_ids or id(it) in already_taken or it.qty <= 0:
                continue
            if it.sku == lift.sku and it.intended_client == lift.intended_client:
                return it
        return None

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
        # Driver takes the top unit first, then works downward — match
        # the per-cube pos_z so the frontend marks the correct visible
        # cube as `delivered`.
        slice_h = (
            item_for_log.dim_h / item_for_log.stack_size
            if item_for_log.stack_size > 0
            else item_for_log.dim_h
        )
        for u in range(target_units):
            level = max(item_for_log.bottom_level, target_top - u)
            unit_idx_in_stack = level - item_for_log.bottom_level
            unit_pos_z = item_for_log.pos_z + unit_idx_in_stack * slice_h
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
                pos_z=unit_pos_z,
                dim_x=item_for_log.dim_x,
                dim_y=item_for_log.dim_y,
                dim_h=slice_h,
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


def _collect_restock_static(unload_cmds: list) -> dict:
    """Index restock instructions across all unload cmds in this slot
    batch by (sku, intended_client). Returns a FIFO list per key so we
    pop one entry per matched blocker — supporting multiple blockers
    with the same (sku, client) (e.g. six 1-unit cases of UE902 to one
    customer all need their own restock pos)."""

    out: dict[tuple[str, str | None], list] = {}
    for cmd in unload_cmds:
        for r in getattr(cmd, "restock", ()):
            out.setdefault((r.sku, r.intended_client), []).append(r)
    return out


def _pop_matching_restock(queue: list, blocker) -> object | None:
    """Pop the restock entry whose `from_pos_*` matches `blocker`'s
    actual position. Falls back to popping from the end of the queue
    (legacy FIFO) when no entry has a non-default from_pos that
    matches.

    Why match by from_pos: when several blockers share (sku, client)
    on one pallet, popping by FIFO order can swap their destinations,
    producing overlap. from_pos pins each restock entry to its
    intended blocker by original location."""

    if not queue:
        return None
    eps = 1e-3  # 1 mm — tolerant of float noise from earlier moves
    for idx, r in enumerate(queue):
        fx = getattr(r, "from_pos_x", 0.0)
        fy = getattr(r, "from_pos_y", 0.0)
        fz = getattr(r, "from_pos_z", 0.0)
        # An entry with from_pos exactly (0,0,0) might be a legacy
        # plan that didn't fill it OR a real (0,0,0) blocker. Accept
        # the match either way — the FIFO fallback below catches
        # entries that simply have no from_pos set.
        if (
            abs(fx - blocker.pos_x) < eps
            and abs(fy - blocker.pos_y) < eps
            and abs(fz - blocker.pos_z) < eps
        ):
            return queue.pop(idx)
    # No from_pos match — legacy plan, pop from the end so iteration
    # in reverse order pairs up with the algorithm's append order.
    return queue.pop()


def _coerce_kind(kind: str) -> PalletKind:
    try:
        return PalletKind(kind)
    except ValueError:
        return PalletKind.MIXED


def _coerce_class(value: str) -> PalletClass | None:
    try:
        return PalletClass(value)
    except ValueError:
        return None


def _class_for_phys(ptype: str) -> PalletClass:
    """Pallet class derived from an item's physical type. Kegs go on KEG
    pallets; everything else on BOX pallets."""
    return PalletClass.KEG if (ptype or "").lower() == "keg" else PalletClass.BOX


def _minutes_to_time(t_min: float) -> dt.time:
    minutes = int(round(t_min)) % (24 * 60)
    return dt.time(minutes // 60, minutes % 60)


_PALLET_LENGTH_M = 1.20
_PALLET_WIDTH_M = 0.80
_PALLET_HEIGHT_M = 1.80
# 0.1 mm — strict enough to catch real overlaps, loose enough to not
# trip on floating-point noise. Accumulated rounding inside a long
# stack chain stays well under this.
_PHYSICS_EPS = 1e-4


def _print_violation(msg: str) -> None:
    """Always print every physics violation to stderr so it shows up
    in server logs alongside the EventLog entry. Operators see
    failures even if they ignore the structured response."""
    print(f"[PHYSICS] {msg}", file=sys.stderr)


def _aabb_collides_with_pallet(
    pos_x: float,
    pos_y: float,
    pos_z: float,
    dim_x: float,
    dim_y: float,
    dim_h: float,
    pallet,
) -> bool:
    """Return True if a candidate AABB overlaps any live item on
    `pallet`. Same epsilon as `_check_pallet_invariants` so the two
    checks agree."""
    new_end_x = pos_x + dim_x
    new_end_y = pos_y + dim_y
    new_top_z = pos_z + dim_h
    for it in pallet.items:
        if it.qty <= 0:
            continue
        if (
            pos_x < it.end_x - _PHYSICS_EPS
            and it.pos_x < new_end_x - _PHYSICS_EPS
            and pos_y < it.end_y - _PHYSICS_EPS
            and it.pos_y < new_end_y - _PHYSICS_EPS
            and pos_z < it.top_z - _PHYSICS_EPS
            and it.pos_z < new_top_z - _PHYSICS_EPS
        ):
            return True
    return False


_MIN_SUPPORT_FRACTION = 0.50


def _has_support(
    pos_x: float,
    pos_y: float,
    pos_z: float,
    dim_x: float,
    dim_y: float,
    pallet,
) -> bool:
    """Floor anchors are always supported. For perched anchors, sum
    the xy-overlap area of items whose top_z matches our pos_z and
    require ≥ 50% coverage (matches `_check_pallet_invariants`)."""
    if pos_z < _PHYSICS_EPS:
        return True
    base = dim_x * dim_y
    if base <= 0:
        return False
    end_x = pos_x + dim_x
    end_y = pos_y + dim_y
    covered = 0.0
    for it in pallet.items:
        if it.qty <= 0:
            continue
        if abs(it.top_z - pos_z) > _PHYSICS_EPS:
            continue
        ox = max(0.0, min(end_x, it.end_x) - max(pos_x, it.pos_x))
        oy = max(0.0, min(end_y, it.end_y) - max(pos_y, it.pos_y))
        covered += ox * oy
    return covered / base >= _MIN_SUPPORT_FRACTION


def _find_clean_position(
    pallet,
    dim_x: float,
    dim_y: float,
    dim_h: float,
    *,
    enforce_pallet_height: bool = True,
) -> tuple[float, float, float] | None:
    """Greedy search for a non-overlapping AND supported anchor on
    `pallet`. Tries floor anchors at item corners + (0, 0, 0), then
    perched anchors at (it.pos_x, it.pos_y, it.top_z). Returns the
    lowest (z, y, x) anchor that fits inside the pallet footprint,
    doesn't collide with anything, and rests on something solid
    (≥50% base coverage by items below).

    Used as a defensive snap when an algorithm-supplied position
    would overlap. Without the support requirement, snapped empties
    can end up floating in mid-air (z>0 with nothing under) — the
    validator then logs a `FLOATING_ITEM` error which looks like a
    new bug to the operator.

    `enforce_pallet_height=False` allows anchors whose top exceeds
    `PALLET_HEIGHT_M`. The validator still reports the height
    overflow as a soft warning, but the priority here is to never
    let two items occupy the same volume — a too-tall stack is the
    lesser evil."""
    items = [it for it in pallet.items if it.qty > 0]
    anchors: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    for it in items:
        anchors.append((it.end_x, it.pos_y, it.pos_z))
        anchors.append((it.pos_x, it.end_y, it.pos_z))
        anchors.append((it.pos_x, it.pos_y, it.top_z))

    best: tuple[float, float, float] | None = None
    seen: set[tuple[float, float, float]] = set()
    for (x, y, z) in anchors:
        key = (round(x, 4), round(y, 4), round(z, 4))
        if key in seen:
            continue
        seen.add(key)
        if x < -_PHYSICS_EPS or y < -_PHYSICS_EPS or z < -_PHYSICS_EPS:
            continue
        if x + dim_x > _PALLET_LENGTH_M + _PHYSICS_EPS:
            continue
        if y + dim_y > _PALLET_WIDTH_M + _PHYSICS_EPS:
            continue
        if enforce_pallet_height and z + dim_h > _PALLET_HEIGHT_M + _PHYSICS_EPS:
            continue
        if _aabb_collides_with_pallet(x, y, z, dim_x, dim_y, dim_h, pallet):
            continue
        if not _has_support(x, y, z, dim_x, dim_y, pallet):
            continue
        if best is None or (z, y, x) < (best[2], best[1], best[0]):
            best = (x, y, z)
    return best


def _check_pallet_invariants(
    pallet, slot_id: str, where: str, st=None, strict: bool = True, log=None
) -> None:
    """Walk a pallet's items and stop the simulation if any of:

      1. an item leaves the pallet footprint horizontally (X/Y) or sinks
         below the pallet floor — these are physically impossible and
         indicate an algorithm bug.
      2. an item floats — pos_z > 0 with no item underneath supporting it.
      3. two items overlap as 3D AABBs.

    Height overflow (top_z > PALLET_HEIGHT_M) is a soft warning — the
    truck cabin is taller than the pallet, so the load can technically
    survive it. Counted via `capacity_violations` and surfaced by the
    validator after the run.
    """

    items = [it for it in pallet.items if it.qty > 0]

    def _emit_oob(code: str, msg: str) -> None:
        _print_violation(msg)
        if log is not None:
            log.emit(
                st.t_min if st is not None else 0.0,
                "PHYSICS_VIOLATION",
                code=code,
                message=msg,
                where=where,
                slot_id=slot_id,
            )
        if st is not None:
            st.capacity_violations += 1

    # 1. Out-of-bounds — hard errors for X/Y (physically impossible).
    for it in items:
        if it.pos_x < -_PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} pos_x={it.pos_x:.4f} is negative (item slipped off the door edge)"
            _emit_oob("OUT_OF_BOUNDS", msg)
            raise _CommandError(msg)
        if it.pos_y < -_PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} pos_y={it.pos_y:.4f} is negative"
            _emit_oob("OUT_OF_BOUNDS", msg)
            raise _CommandError(msg)
        if it.pos_z < -_PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} pos_z={it.pos_z:.4f} is below the pallet floor"
            _emit_oob("OUT_OF_BOUNDS", msg)
            raise _CommandError(msg)
        if it.end_x > _PALLET_LENGTH_M + _PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} end_x={it.end_x:.4f} exceeds pallet length {_PALLET_LENGTH_M:.2f} m"
            _emit_oob("OUT_OF_BOUNDS", msg)
            raise _CommandError(msg)
        if it.end_y > _PALLET_WIDTH_M + _PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} end_y={it.end_y:.4f} exceeds pallet width {_PALLET_WIDTH_M:.2f} m"
            _emit_oob("OUT_OF_BOUNDS", msg)
            raise _CommandError(msg)
        # Height overflow → soft warning (truck cabin is taller than pallet).
        if it.top_z > _PALLET_HEIGHT_M + _PHYSICS_EPS:
            msg = f"PHYSICS [{where} slot={slot_id}]: {it.sku} top_z={it.top_z:.4f} exceeds pallet height {_PALLET_HEIGHT_M:.2f} m"
            _emit_oob("HEIGHT_OVERFLOW", msg)

    # 2. Overlap — full 3D AABB intersection. Hard error in strict mode,
    # capacity_violations counter otherwise (so legacy plans still run).
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if (
                a.pos_x < b.end_x - _PHYSICS_EPS
                and b.pos_x < a.end_x - _PHYSICS_EPS
                and a.pos_y < b.end_y - _PHYSICS_EPS
                and b.pos_y < a.end_y - _PHYSICS_EPS
                and a.pos_z < b.top_z - _PHYSICS_EPS
                and b.pos_z < a.top_z - _PHYSICS_EPS
            ):
                msg = (
                    f"PHYSICS [{where} slot={slot_id}]: "
                    f"{a.sku}@({a.pos_x:.2f},{a.pos_y:.2f},{a.pos_z:.2f}) "
                    f"overlaps {b.sku}@({b.pos_x:.2f},{b.pos_y:.2f},{b.pos_z:.2f}) "
                    "— two items occupy the same volume"
                )
                _print_violation(msg)
                if strict:
                    raise _CommandError(msg)
                if st is not None:
                    st.capacity_violations += 1
                if log is not None:
                    log.emit(
                        st.t_min if st is not None else 0.0,
                        "PHYSICS_VIOLATION",
                        code="OVERLAP",
                        message=msg,
                        where=where,
                        slot_id=slot_id,
                        sku_a=a.sku,
                        sku_b=b.sku,
                        pos_a=[a.pos_x, a.pos_y, a.pos_z],
                        pos_b=[b.pos_x, b.pos_y, b.pos_z],
                    )

    # 3. Floating — anything above the floor needs a supporter directly
    # underneath whose top_z matches its pos_z. The supporter's XY
    # footprint must cover at least MIN_SUPPORT_FRACTION of the item's
    # base area, otherwise the item teeters on a corner. Multiple
    # supporters can be summed (cube on top of two cubes side by side).
    MIN_SUPPORT_FRACTION = 0.50
    for it in items:
        if it.pos_z < _PHYSICS_EPS:
            continue  # rests on the pallet floor — always supported.
        item_area = it.dim_x * it.dim_y
        if item_area <= 0:
            continue
        covered = 0.0
        supporters: list[str] = []
        for other in items:
            if other is it:
                continue
            if abs(other.top_z - it.pos_z) > _PHYSICS_EPS:
                continue
            ox = max(0.0, min(it.end_x, other.end_x) - max(it.pos_x, other.pos_x))
            oy = max(0.0, min(it.end_y, other.end_y) - max(it.pos_y, other.pos_y))
            overlap_area = ox * oy
            if overlap_area > _PHYSICS_EPS * _PHYSICS_EPS:
                covered += overlap_area
                supporters.append(other.sku)
        coverage = covered / item_area
        if coverage < MIN_SUPPORT_FRACTION:
            if coverage <= 0:
                detail = "no item directly below provides support"
                code = "FLOATING"
            else:
                detail = (
                    f"only {coverage * 100:.0f}% of base is supported "
                    f"(min {int(MIN_SUPPORT_FRACTION * 100)}%) — teeters on edge"
                )
                code = "UNSTABLE_OVERHANG"
            msg = (
                f"PHYSICS [{where} slot={slot_id}]: "
                f"{it.sku}@({it.pos_x:.2f},{it.pos_y:.2f},{it.pos_z:.2f}) "
                f"{detail}"
            )
            _print_violation(msg)
            if strict:
                raise _CommandError(msg)
            if st is not None:
                st.capacity_violations += 1
            if log is not None:
                log.emit(
                    st.t_min if st is not None else 0.0,
                    "PHYSICS_VIOLATION",
                    code=code,
                    message=msg,
                    where=where,
                    slot_id=slot_id,
                    sku=it.sku,
                    pos=[it.pos_x, it.pos_y, it.pos_z],
                )


def _settle_pallet(st, slot_id: str, log=None) -> None:
    """Drop floating items on a pallet down to their lowest supported
    anchor. Iterated in (z, y, x) order so each settled item updates
    the support landscape for items above it.

    Triggered after every unload batch and PickupReturn so a delivery
    that removed the supporter under another item never leaves that
    item hovering. Each move emits a `PHYSICS_VIOLATION` event with
    code `SETTLE` so the operator can see what happened."""
    pallet = st.cargo.pallet_at(slot_id)
    if pallet is None:
        return
    # Iterate until no more moves — a single pass might settle item A
    # at z=0, freeing item B (which was above A) but B's new "supported"
    # position depends on A's new position, so we re-check.
    for _ in range(8):
        pallet = st.cargo.pallet_at(slot_id)
        if pallet is None:
            return
        items = sorted(
            [it for it in pallet.items if it.qty > 0],
            key=lambda it: (it.pos_z, it.pos_y, it.pos_x),
        )
        moved = False
        for it in items:
            if it.pos_z < _PHYSICS_EPS:
                continue
            if _has_support(
                it.pos_x, it.pos_y, it.pos_z, it.dim_x, it.dim_y, pallet
            ):
                continue
            # Floating — find a clean supported anchor.
            new_pos = _find_clean_position(pallet, it.dim_x, it.dim_y, it.dim_h)
            if new_pos is None:
                new_pos = _find_clean_position(
                    pallet, it.dim_x, it.dim_y, it.dim_h, enforce_pallet_height=False
                )
            if new_pos is None or (
                abs(new_pos[0] - it.pos_x) < _PHYSICS_EPS
                and abs(new_pos[1] - it.pos_y) < _PHYSICS_EPS
                and abs(new_pos[2] - it.pos_z) < _PHYSICS_EPS
            ):
                continue
            _print_violation(
                f"PHYSICS [settle slot={slot_id}]: {it.sku}@"
                f"({it.pos_x:.3f},{it.pos_y:.3f},{it.pos_z:.3f}) was floating after "
                f"a delivery — settled to ({new_pos[0]:.3f},{new_pos[1]:.3f},{new_pos[2]:.3f})"
            )
            if log is not None:
                # Two events:
                # 1. PHYSICS_VIOLATION with code=SETTLE — picked up by
                #    the validator and surfaced as a FLOATING_ITEM error.
                # 2. SETTLE — picked up by the frontend's cargoState
                #    so the visualizer moves the box to the new pos
                #    instead of leaving it rendered at the (now stale)
                #    original pos. Without this the visualizer shows a
                #    keg "100% inside another keg" at the old spot.
                log.emit(
                    st.t_min,
                    "PHYSICS_VIOLATION",
                    code="SETTLE",
                    message=(
                        f"{it.sku} at ({it.pos_x:.3f},{it.pos_y:.3f},{it.pos_z:.3f}) "
                        f"left floating by a delivery — settled to "
                        f"({new_pos[0]:.3f},{new_pos[1]:.3f},{new_pos[2]:.3f})"
                    ),
                    where="settle",
                    slot_id=slot_id,
                    sku=it.sku,
                    pos=[it.pos_x, it.pos_y, it.pos_z],
                )
                log.emit(
                    st.t_min,
                    "SETTLE",
                    slot_id=slot_id,
                    sku=it.sku,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    from_pos_x=float(it.pos_x),
                    from_pos_y=float(it.pos_y),
                    from_pos_z=float(it.pos_z),
                    pos_x=float(new_pos[0]),
                    pos_y=float(new_pos[1]),
                    pos_z=float(new_pos[2]),
                    dim_x=float(it.dim_x),
                    dim_y=float(it.dim_y),
                    dim_h=float(it.dim_h),
                )
            cur_pallet, removed = pallet.remove_specific(it)
            if removed is None:
                continue
            settled = PalletItem(
                sku=it.sku,
                qty=it.qty,
                unit_volume_m3=it.unit_volume_m3,
                unit_weight_kg=it.unit_weight_kg,
                intended_client=it.intended_client,
                is_returnable_empty=it.is_returnable_empty,
                physical_type=it.physical_type,
                pos_x=new_pos[0],
                pos_y=new_pos[1],
                pos_z=new_pos[2],
                dim_x=it.dim_x,
                dim_y=it.dim_y,
                dim_h=it.dim_h,
            )
            cur_pallet = cur_pallet.add_item(settled)
            st.cargo.pallet_by_id[cur_pallet.pallet_id] = cur_pallet
            st.capacity_violations += 1
            moved = True
            break  # restart pass — pallet state changed
        if not moved:
            return


def _check_all_pallets(st, where: str, strict: bool = False, log=None) -> None:
    """Run invariants on every loaded pallet."""
    for pallet_id, slot_id in st.cargo.slot_by_pallet.items():
        pallet = st.cargo.pallet_by_id.get(pallet_id)
        if pallet is None:
            continue
        _check_pallet_invariants(pallet, slot_id, where, st=st, strict=strict, log=log)
    for pallet_id, pallet in st.cargo.staging.items():
        _check_pallet_invariants(
            pallet, f"staging:{pallet_id}", where, st=st, strict=strict, log=log
        )


class _CommandError(RuntimeError):
    pass
