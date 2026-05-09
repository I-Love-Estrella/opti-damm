"""Smart v1: nearest-neighbor + 2-opt route, client-block loading in reverse visit order.

Route: greedy nearest-neighbor from depot through all clients, then 2-opt passes.
Loading: client-block pallets. When clients > slots, consecutive-in-route clients
are merged onto the same pallet so the last-visited client sits closest to the
back door (loaded first, accessed last).
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.algorithms.base import Algorithm
from simulator.algorithms.virtual_truck import VirtualTruck
from simulator.config import PALLET_VOLUME_M3
from simulator.data.catalog import physical_dims
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import ClientOrder, DayCase, OrderLine
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
    """Per-unit (dim_x, dim_y, dim_h) for an order line — real ZM040 dims if
    available, else type defaults."""

    if line.dim_source == "data" and line.dim_x_m > 0 and line.dim_y_m > 0 and line.dim_h_m > 0:
        return line.dim_x_m, line.dim_y_m, line.dim_h_m
    ptype = line.physical_type.value if hasattr(line.physical_type, "value") else str(line.physical_type)
    return physical_dims(ptype)


def _physical_type_str(line: OrderLine) -> str:
    return line.physical_type.value if hasattr(line.physical_type, "value") else str(line.physical_type)


# Stack stability: height ≤ STACK_RATIO × narrow side. Matches the
# validator's STACK_RATIO_ERROR — we cap the per-Pick stack here so the
# simpler algorithms can't accidentally emit a narrow tower.
_STACK_RATIO = 3.5


def _stack_chunk_qty(qty: float, dx: float, dy: float, dh: float) -> float:
    """Largest qty that fits the stack-stability rule for a single Pick."""

    narrow = max(1e-3, min(dx, dy))
    max_units = max(1, int((_STACK_RATIO * narrow) / max(dh, 1e-3)))
    return float(min(qty, max_units))


@dataclass
class _ClientGroup:
    slot_id: str
    pallet_id: str
    clients: list[ClientOrder]


class NearestNeighborSmart(Algorithm):
    name = "nearest"
    description = (
        "Greedy nearest-neighbor route + 2-opt; one CLIENT_BLOCK pallet per "
        "(client, class). Last visit goes to the back-most slot."
    )

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        rationale = ["Route: nearest-neighbor + 2-opt. Loading: client-block in reverse visit order."]
        order = self._route(case, clients, network)
        order = self._two_opt(order, case, clients, network, max_passes=2)
        rationale.append(f"Visit order: {[o.client_id for o in order]}")

        groups = self._group_clients(order, case)
        slot_to_group = {g.slot_id: g for g in groups}
        rationale.append(f"Pallet groups: {len(groups)} (truck capacity {case.truck.pallet_capacity}).")

        cmds: list[Command] = []
        # Track virtual pallet contents per pallet_id so we can compute Pick
        # positions ourselves — the simulator no longer infers geometry.
        items_by_pid: dict[str, list[PalletItem]] = {}
        pid_to_slot: dict[str, str] = {}

        client_class_to_slot: dict[tuple[str, PalletClass], str] = {}
        for g in groups:
            lines_by_class: dict[PalletClass, list[tuple[str, "OrderLine"]]] = {
                PalletClass.KEG: [],
                PalletClass.BOX: [],
            }
            for client_order in g.clients:
                for line in client_order.lines:
                    lines_by_class[sku_class_for_uma(line.uma)].append(
                        (client_order.client_id, line)
                    )

            sub_idx = 0
            for cls in (PalletClass.BOX, PalletClass.KEG):
                lines = lines_by_class[cls]
                if not lines:
                    continue
                pid = g.pallet_id if sub_idx == 0 else f"{g.pallet_id}-{cls.value}"
                slot_id = g.slot_id if sub_idx == 0 else g.slot_id
                primary = g.clients[-1].client_id if len(g.clients) == 1 else None
                kind = PalletKind.CLIENT_BLOCK.value if primary else PalletKind.MIXED.value
                note = f"clients={[c.client_id for c in g.clients]} class={cls.value}"
                cmds.append(BuildPallet(
                    pallet_id=pid,
                    kind=kind,
                    primary_client=primary,
                    notes=note,
                    pallet_class=cls.value,
                ))
                items_by_pid[pid] = []
                for cid, line in lines:
                    self._append_pick(
                        cmds, items_by_pid[pid], pid, cid, line, line.qty
                    )
                if sub_idx == 0:
                    cmds.append(Load(pallet_id=pid, slot_id=slot_id))
                    pid_to_slot[pid] = slot_id
                    for cid, _ in lines:
                        client_class_to_slot[(cid, cls)] = slot_id
                else:
                    free_slot = self._first_free_slot(case, cmds)
                    target_slot = free_slot or slot_id
                    cmds.append(Load(pallet_id=pid, slot_id=target_slot))
                    pid_to_slot[pid] = target_slot
                    for cid, _ in lines:
                        client_class_to_slot[(cid, cls)] = target_slot
                sub_idx += 1

        cmds.append(DepartDepot())

        # Shadow truck for picking PickupReturn anchors.
        vt = VirtualTruck()
        for pid, items in items_by_pid.items():
            slot_id = pid_to_slot.get(pid)
            if slot_id is None:
                continue
            for it in items:
                vt.add(slot_id, it)

        keg_dx, keg_dy, keg_dh = physical_dims("keg")

        for o in order:
            cmds.append(DriveTo(client_id=o.client_id))
            splits_by_slot: dict[str, list[tuple[str, float]]] = {}
            for line in o.lines:
                cls = sku_class_for_uma(line.uma)
                slot_id = client_class_to_slot.get(
                    (o.client_id, cls)
                ) or client_class_to_slot.get((o.client_id, PalletClass.BOX), "L1")
                splits_by_slot.setdefault(slot_id, []).append((line.sku, line.qty))

            for slot_id, items in splits_by_slot.items():
                target_keys = [(sku, o.client_id) for sku, _ in items]
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
                            client_id=o.client_id,
                            sku=sku,
                            qty=qty,
                            slot_id=slot_id,
                            lifts=tuple(lifts) if first else (),
                            restock=tuple(restock) if first else (),
                        )
                    )
                    first = False
                vt.apply_restock(slot_id, restock, target_items, same_client, foreign)
            if o.expected_returnable_units > 0:
                slot_id = client_class_to_slot.get(
                    (o.client_id, PalletClass.KEG)
                ) or client_class_to_slot.get((o.client_id, PalletClass.BOX), "L1")
                vt.emit_returnables(
                    cmds,
                    slot_id,
                    o.client_id,
                    o.expected_returnable_units,
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
            route_order=tuple(o.client_id for o in order),
        )

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
        # Split into stack-stable chunks so the validator doesn't trip
        # the STACK_UNSTABLE rule on tall narrow towers.
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

    def _route(self, case: DayCase, clients: Clients, network: Network) -> list[ClientOrder]:
        remaining = list(case.orders)
        loc = (case.depot.lat, case.depot.lon)
        ordered: list[ClientOrder] = []
        while remaining:
            best, best_km = None, float("inf")
            for o in remaining:
                c = clients.get(o.client_id)
                d = network.leg(loc, (c.lat, c.lon)).distance_km
                if d < best_km:
                    best_km, best = d, o
            ordered.append(best)
            remaining.remove(best)
            c = clients.get(best.client_id)
            loc = (c.lat, c.lon)
        return ordered

    def _two_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        def total_km(seq: list[ClientOrder]) -> float:
            loc = (case.depot.lat, case.depot.lon)
            total = 0.0
            for o in seq:
                c = clients.get(o.client_id)
                total += network.leg(loc, (c.lat, c.lon)).distance_km
                loc = (c.lat, c.lon)
            total += network.leg(loc, (case.depot.lat, case.depot.lon)).distance_km
            return total

        best = order
        best_km = total_km(best)
        for _ in range(max_passes):
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + list(reversed(best[i:j + 1])) + best[j + 1:]
                    km = total_km(cand)
                    if km + 1e-6 < best_km:
                        best, best_km = cand, km
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        return best

    def _group_clients(self, order: list[ClientOrder], case: DayCase) -> list[_ClientGroup]:
        slots = list(build_slots(case.truck))
        slot_count = len(slots)
        n = len(order)

        if n <= slot_count:
            chunks = [[o] for o in order]
        else:
            chunks = self._chunk_by_volume(order, max_chunks=slot_count)

        chunks.reverse()
        slot_seq = slots[: len(chunks)][::-1]
        groups: list[_ClientGroup] = []
        for i, chunk in enumerate(chunks):
            slot_id = slot_seq[i].slot_id
            pid = f"PB-{slot_id}"
            chunk_in_visit_order = list(reversed(chunk))
            groups.append(_ClientGroup(slot_id=slot_id, pallet_id=pid, clients=chunk_in_visit_order))
        return groups

    def _first_free_slot(self, case: DayCase, cmds: list[Command]) -> str | None:
        used = {c.slot_id for c in cmds if isinstance(c, Load)}
        for s in build_slots(case.truck):
            if s.slot_id not in used:
                return s.slot_id
        return None

    def _chunk_by_volume(self, order: list[ClientOrder], max_chunks: int) -> list[list[ClientOrder]]:
        target_per_chunk = max(1, (len(order) + max_chunks - 1) // max_chunks)
        chunks: list[list[ClientOrder]] = [[]]
        cur_vol = 0.0
        for o in order:
            if (
                len(chunks[-1]) >= target_per_chunk
                or cur_vol + o.total_volume_m3 > PALLET_VOLUME_M3
            ) and len(chunks) < max_chunks:
                chunks.append([])
                cur_vol = 0.0
            chunks[-1].append(o)
            cur_vol += o.total_volume_m3
        return chunks
