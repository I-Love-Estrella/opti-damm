"""Baseline: replay current Damm practice.

Route: visit clients in the order they appear in the source `Detalle entrega`
(approximated via Entrega number). Loading: SKU-block bin-pack into multi-SKU
pallets via First-Fit-Decreasing — units of the same SKU stay together (this
is what "load by reference" means in practice). This is the right baseline:
warehouse-friendly, but indifferent to the unloading order.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.algorithms.base import Algorithm
from simulator.algorithms.virtual_truck import VirtualTruck
from simulator.config import PALLET_VOLUME_M3
from simulator.data.catalog import physical_dims
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import DayCase, OrderLine
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
from simulator.domain.packing import find_position
from simulator.domain.pallet import (
    PalletClass,
    PalletItem,
    PalletKind,
    sku_class_for_uma,
)
from simulator.domain.plan import Plan
from simulator.domain.truck import build_slots


def _line_dims(line: OrderLine) -> tuple[float, float, float]:
    if line.dim_source == "data" and line.dim_x_m > 0 and line.dim_y_m > 0 and line.dim_h_m > 0:
        return line.dim_x_m, line.dim_y_m, line.dim_h_m
    ptype = line.physical_type.value if hasattr(line.physical_type, "value") else str(line.physical_type)
    return physical_dims(ptype)


def _physical_type_str(line: OrderLine) -> str:
    return line.physical_type.value if hasattr(line.physical_type, "value") else str(line.physical_type)


_STACK_RATIO = 3.5


def _stack_chunk_qty(qty: float, dx: float, dy: float, dh: float) -> float:
    narrow = max(1e-3, min(dx, dy))
    max_units = max(1, int((_STACK_RATIO * narrow) / max(dh, 1e-3)))
    return float(min(qty, max_units))


@dataclass
class _OpenPallet:
    pallet_id: str
    slot_id: str
    cap_left_m3: float


class ReplayBaseline(Algorithm):
    name = "replay"
    description = (
        "Baseline mimicking current Damm practice: visit clients in the actual "
        "delivery-note order, pack pallets by SKU (FFD), keg/box-aware."
    )

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        cmds: list[Command] = []
        rationale = [
            "Route: actual driver visit order (Entrega sequence).",
            "Loading: SKU-block FFD bin-pack into multi-SKU pallets (load by reference).",
        ]

        sku_blocks = self._sku_blocks(case)
        slots = list(build_slots(case.truck))
        slot_iter = iter(slots)
        open_by_class: dict[PalletClass, list[_OpenPallet]] = {
            PalletClass.KEG: [],
            PalletClass.BOX: [],
        }
        unload_slot: dict[tuple[str, str], list[tuple[str, float]]] = {}
        overflow: list[tuple[str, str, float]] = []
        next_id = [0]
        # Virtual pallet contents — used to compute Pick positions ourselves
        # since the simulator no longer infers geometry.
        items_by_pid: dict[str, list[PalletItem]] = {}
        class_by_pid: dict[str, PalletClass] = {}

        def new_id() -> str:
            next_id[0] += 1
            return f"P{next_id[0]:03d}"

        for sku, demands, _vol in sku_blocks:
            line = self._first_line(case, sku)
            if line is None:
                continue
            cls = sku_class_for_uma(line.uma)
            open_pallets = open_by_class[cls]
            remaining = list(demands)
            while remaining:
                cid, qty = remaining[0]
                want = qty * line.unit_volume_m3
                target = self._fit(open_pallets, want) or self._open(
                    slot_iter, new_id, cmds, open_pallets, sku, cls,
                    items_by_pid, class_by_pid,
                )
                if target is None:
                    overflow.extend((sku, c, q) for c, q in remaining)
                    break
                cap_left = target.cap_left_m3
                if cap_left < line.unit_volume_m3:
                    open_pallets.remove(target)
                    continue
                take = min(qty, cap_left / max(line.unit_volume_m3, 1e-6))
                self._append_pick(
                    cmds, items_by_pid[target.pallet_id],
                    target.pallet_id, cid, line, take,
                )
                unload_slot.setdefault((cid, sku), []).append((target.slot_id, take))
                target.cap_left_m3 -= take * line.unit_volume_m3
                if take >= qty - 1e-9:
                    remaining.pop(0)
                else:
                    remaining[0] = (cid, qty - take)
                if target.cap_left_m3 < line.unit_volume_m3:
                    open_pallets.remove(target)
                    cmds.append(Load(pallet_id=target.pallet_id, slot_id=target.slot_id))

        for cls_pallets in open_by_class.values():
            for op in list(cls_pallets):
                cmds.append(Load(pallet_id=op.pallet_id, slot_id=op.slot_id))
            cls_pallets.clear()

        if overflow:
            rationale.append(f"OVERFLOW: {len(overflow)} (sku, client) demands exceeded capacity.")

        cmds.append(DepartDepot())

        # Shadow truck — track what's actually on each pallet at every step.
        # Replay opens each pallet on a single slot, so we map pid → slot via
        # the open-pallet records that were just emitted.
        pid_to_slot: dict[str, str] = {}
        for c in cmds:
            if isinstance(c, Load):
                pid_to_slot[c.pallet_id] = c.slot_id

        vt = VirtualTruck()
        for pid, items in items_by_pid.items():
            slot_id = pid_to_slot.get(pid)
            if slot_id is None:
                continue
            for it in items:
                vt.add(slot_id, it)

        keg_dx, keg_dy, keg_dh = physical_dims("keg")

        for order in case.orders:
            cmds.append(DriveTo(client_id=order.client_id))
            last_slot = "L1"
            splits_by_slot: dict[str, list[tuple[str, float]]] = {}
            for line in order.lines:
                for slot_id, qty in unload_slot.get((order.client_id, line.sku), []):
                    splits_by_slot.setdefault(slot_id, []).append((line.sku, qty))
                    last_slot = slot_id

            for slot_id, items in splits_by_slot.items():
                target_keys = [(sku, order.client_id) for sku, _ in items]
                target_items, same_client, foreign = vt.find_blockers(
                    slot_id, target_keys
                )
                ordered_lifts = sorted(
                    foreign + same_client,
                    key=lambda b: (b.pos_y, b.pos_x, -b.pos_z),
                )
                lifts = vt.to_lifts(ordered_lifts)
                restock = vt.plan_restock(
                    slot_id, target_items, same_client, foreign,
                    aspect_limit=_STACK_RATIO,
                )
                first = True
                for sku, qty in items:
                    cmds.append(
                        Unload(
                            client_id=order.client_id,
                            sku=sku,
                            qty=qty,
                            slot_id=slot_id,
                            lifts=tuple(lifts) if first else (),
                            restock=tuple(restock) if first else (),
                        )
                    )
                    first = False
                vt.apply_restock(slot_id, restock, target_items, same_client, foreign)
            if order.expected_returnable_units > 0:
                vt.emit_returnables(
                    cmds,
                    last_slot,
                    order.client_id,
                    order.expected_returnable_units,
                    keg_dx,
                    keg_dy,
                    keg_dh,
                    candidate_slots=list(pid_to_slot.values()),
                )
        cmds.append(ReturnDepot())

        return Plan(
            algorithm=self.name,
            commands=tuple(cmds),
            rationale=tuple(rationale),
            route_order=tuple(o.client_id for o in case.orders),
        )

    def _sku_blocks(self, case: DayCase) -> list[tuple[str, list[tuple[str, float]], float]]:
        blocks: dict[str, list[tuple[str, float]]] = {}
        unit_vol: dict[str, float] = {}
        for o in case.orders:
            for line in o.lines:
                blocks.setdefault(line.sku, []).append((o.client_id, line.qty))
                unit_vol[line.sku] = line.unit_volume_m3
        out = [
            (sku, demands, sum(q for _, q in demands) * unit_vol.get(sku, 0.001))
            for sku, demands in blocks.items()
        ]
        out.sort(key=lambda b: b[2], reverse=True)
        return out

    def _first_line(self, case: DayCase, sku: str) -> OrderLine | None:
        for o in case.orders:
            for line in o.lines:
                if line.sku == sku:
                    return line
        return None

    def _fit(self, open_pallets: list[_OpenPallet], want: float) -> _OpenPallet | None:
        for op in open_pallets:
            if op.cap_left_m3 >= want:
                return op
        return open_pallets[0] if open_pallets else None

    @staticmethod
    def _append_pick(
        cmds: list[Command],
        items: list[PalletItem],
        pid: str,
        cid: str,
        line: OrderLine,
        qty: float,
    ) -> None:
        dx, dy, dh_unit = _line_dims(line)
        ptype = _physical_type_str(line)
        remaining = float(qty)
        while remaining > 0:
            take = _stack_chunk_qty(remaining, dx, dy, dh_unit)
            stack_h = take * dh_unit
            pos = find_position(
                items, dx, dy, stack_h,
                enforce_pallet_height=True,
                aspect_limit=_STACK_RATIO,
            )
            if pos is None:
                pos = find_position(
                    items, dx, dy, stack_h, enforce_pallet_height=True
                )
            if pos is None:
                # No physical room left on this pallet for this chunk.
                # Skip rather than spawning a floating tower.
                remaining -= take
                continue
            item = PalletItem(
                sku=line.sku,
                qty=take,
                unit_volume_m3=line.unit_volume_m3,
                unit_weight_kg=line.unit_weight_kg,
                intended_client=cid,
                is_returnable_empty=False,
                physical_type=ptype,
                pos_x=pos[0],
                pos_y=pos[1],
                pos_z=pos[2],
                dim_x=dx,
                dim_y=dy,
                dim_h=stack_h,
            )
            items.append(item)
            cmds.append(
                Pick(
                    sku=line.sku,
                    qty=take,
                    location=None,
                    pallet_id=pid,
                    intended_client=cid,
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=dx,
                    dim_y=dy,
                    dim_h=stack_h,
                    unit_volume_m3=line.unit_volume_m3,
                    unit_weight_kg=line.unit_weight_kg,
                    physical_type=ptype,
                )
            )
            remaining -= take

    def _open(
        self,
        slot_iter,
        mk_id,
        cmds: list[Command],
        open_pallets: list[_OpenPallet],
        sku: str,
        cls: PalletClass,
        items_by_pid: dict[str, list[PalletItem]],
        class_by_pid: dict[str, PalletClass],
    ) -> _OpenPallet | None:
        slot = next(slot_iter, None)
        if slot is None:
            return None
        pid = mk_id()
        cmds.append(
            BuildPallet(
                pallet_id=pid,
                kind=PalletKind.MIXED.value,
                primary_client=None,
                notes="reference-pack",
                pallet_class=cls.value,
            )
        )
        items_by_pid[pid] = []
        class_by_pid[pid] = cls
        op = _OpenPallet(pallet_id=pid, slot_id=slot.slot_id, cap_left_m3=PALLET_VOLUME_M3)
        open_pallets.append(op)
        return op
