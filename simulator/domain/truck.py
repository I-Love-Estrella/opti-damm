"""Truck instance bound to a day, with named slots."""

from __future__ import annotations

from dataclasses import dataclass

from simulator.config import TRUCK_SPECS, TruckSpec


@dataclass(frozen=True)
class Slot:
    slot_id: str
    side: str
    position: int
    laterally_accessible: bool


def build_slots(spec: TruckSpec) -> tuple[Slot, ...]:
    cap = spec.pallet_capacity
    half = cap // 2
    slots: list[Slot] = []
    for i in range(half):
        slots.append(Slot(slot_id=f"L{i+1}", side="L", position=i + 1, laterally_accessible=True))
    for i in range(half):
        slots.append(Slot(slot_id=f"R{i+1}", side="R", position=i + 1, laterally_accessible=True))
    if cap % 2 == 1:
        slots.append(Slot(slot_id="B1", side="B", position=1, laterally_accessible=False))
    return tuple(slots)


def truck_for(code: str) -> TruckSpec:
    return TRUCK_SPECS[code]
