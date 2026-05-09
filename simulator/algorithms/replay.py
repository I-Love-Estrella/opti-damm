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
from simulator.config import PALLET_VOLUME_M3
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
from simulator.domain.pallet import PalletKind
from simulator.domain.plan import Plan
from simulator.domain.truck import build_slots


@dataclass
class _OpenPallet:
    pallet_id: str
    slot_id: str
    cap_left_m3: float


class ReplayBaseline(Algorithm):
    name = "replay"

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        cmds: list[Command] = []
        rationale = [
            "Route: actual driver visit order (Entrega sequence).",
            "Loading: SKU-block FFD bin-pack into multi-SKU pallets (load by reference).",
        ]

        sku_blocks = self._sku_blocks(case)
        slots = list(build_slots(case.truck))
        slot_iter = iter(slots)
        open_pallets: list[_OpenPallet] = []
        unload_slot: dict[tuple[str, str], list[tuple[str, float]]] = {}
        overflow: list[tuple[str, str, float]] = []
        next_id = [0]

        def new_id() -> str:
            next_id[0] += 1
            return f"P{next_id[0]:03d}"

        for sku, demands, _vol in sku_blocks:
            line = self._first_line(case, sku)
            if line is None:
                continue
            remaining = list(demands)
            while remaining:
                cid, qty = remaining[0]
                want = qty * line.unit_volume_m3
                target = self._fit(open_pallets, want) or self._open(
                    slot_iter, new_id, cmds, open_pallets, sku
                )
                if target is None:
                    overflow.extend((sku, c, q) for c, q in remaining)
                    break
                cap_left = target.cap_left_m3
                if cap_left < line.unit_volume_m3:
                    open_pallets.remove(target)
                    continue
                take = min(qty, cap_left / max(line.unit_volume_m3, 1e-6))
                cmds.append(
                    Pick(sku=sku, qty=take, location=None, pallet_id=target.pallet_id, intended_client=cid)
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

        for op in list(open_pallets):
            cmds.append(Load(pallet_id=op.pallet_id, slot_id=op.slot_id))
        open_pallets.clear()

        if overflow:
            rationale.append(f"OVERFLOW: {len(overflow)} (sku, client) demands exceeded capacity.")

        cmds.append(DepartDepot())

        for order in case.orders:
            cmds.append(DriveTo(client_id=order.client_id))
            last_slot = "L1"
            for line in order.lines:
                splits = unload_slot.get((order.client_id, line.sku), [])
                for slot_id, qty in splits:
                    cmds.append(Unload(client_id=order.client_id, sku=line.sku, qty=qty, slot_id=slot_id))
                    last_slot = slot_id
            if order.expected_returnable_units > 0:
                cmds.append(
                    PickupReturn(
                        client_id=order.client_id,
                        sku="EMPTY",
                        qty=order.expected_returnable_units,
                        slot_id=last_slot,
                    )
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

    def _open(
        self,
        slot_iter,
        mk_id,
        cmds: list[Command],
        open_pallets: list[_OpenPallet],
        sku: str,
    ) -> _OpenPallet | None:
        slot = next(slot_iter, None)
        if slot is None:
            return None
        pid = mk_id()
        cmds.append(
            BuildPallet(pallet_id=pid, kind=PalletKind.MIXED.value, primary_client=None, notes=f"reference-pack")
        )
        op = _OpenPallet(pallet_id=pid, slot_id=slot.slot_id, cap_left_m3=PALLET_VOLUME_M3)
        open_pallets.append(op)
        return op
