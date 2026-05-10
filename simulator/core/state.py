"""Mutable world state used during a single simulation run."""

from __future__ import annotations

from dataclasses import dataclass, field

from simulator.config import TruckSpec
from simulator.data.orders import DayCase
from simulator.domain.pallet import Pallet
from simulator.domain.truck import Slot, build_slots


@dataclass
class CargoState:
    truck: TruckSpec
    slots: list[Slot] = field(default_factory=list)
    pallet_by_id: dict[str, Pallet] = field(default_factory=dict)
    slot_by_pallet: dict[str, str] = field(default_factory=dict)
    pallet_by_slot: dict[str, str] = field(default_factory=dict)
    staging: dict[str, Pallet] = field(default_factory=dict)

    @classmethod
    def initial(cls, truck: TruckSpec) -> "CargoState":
        return cls(truck=truck, slots=list(build_slots(truck)))

    def slot(self, slot_id: str) -> Slot | None:
        for s in self.slots:
            if s.slot_id == slot_id:
                return s
        return None

    def pallet_at(self, slot_id: str) -> Pallet | None:
        pid = self.pallet_by_slot.get(slot_id)
        return self.pallet_by_id.get(pid) if pid else None

    def total_volume_m3(self) -> float:
        return sum(p.volume_m3 for p in self.pallet_by_id.values() if p.pallet_id in self.slot_by_pallet)

    def total_weight_kg(self) -> float:
        return sum(p.weight_kg for p in self.pallet_by_id.values() if p.pallet_id in self.slot_by_pallet)

    def slots_used(self) -> int:
        return len(self.slot_by_pallet)


@dataclass
class WorldState:
    case: DayCase
    cargo: CargoState
    t_min: float = 0.0
    location_lat: float = 0.0
    location_lon: float = 0.0
    current_client: str | None = None
    distance_km: float = 0.0
    visited_clients: set[str] = field(default_factory=set)
    delivered_qty: dict[tuple[str, str], float] = field(default_factory=dict)
    picked_returns: dict[tuple[str, str], float] = field(default_factory=dict)
    search_moves: int = 0
    tw_violations_min: float = 0.0
    closed_visits: int = 0
    capacity_violations: int = 0
    drops: list[tuple[str, str, float]] = field(default_factory=list)
    # Items the simulator REJECTED placing because the algorithm-supplied
    # position would overlap, float, or fall outside the pallet. Each
    # entry is a structured dict (kind, slot_id, sku, qty, reason, pos).
    # Surfaced as KPI `placement_rejections` and `lost_units`.
    placement_rejections: list[dict] = field(default_factory=list)
    # Chunks the ALGORITHM couldn't pack at planning time (overflow).
    # Each entry is (client_id, sku, qty). Surfaced as KPI
    # `pack_overflow_units` so the operator can distinguish:
    #   - placement_rejections → simulator rejected at runtime
    #     (algorithm bug, geometry impossible)
    #   - pack_overflow_units → algorithm couldn't even plan it
    #     (greedy packer hit its density ceiling vs real-world ~95%)
    # Real Damm data shows 100% delivery for every recorded route, so
    # any overflow > 0 is purely a model-fidelity gap, NOT a real fail.
    pack_overflow: list[tuple[str, str, float]] = field(default_factory=list)
    finalized: bool = False

    @classmethod
    def from_case(cls, case: DayCase) -> "WorldState":
        return cls(
            case=case,
            cargo=CargoState.initial(case.truck),
            location_lat=case.depot.lat,
            location_lon=case.depot.lon,
        )

    def location(self) -> tuple[float, float]:
        return self.location_lat, self.location_lon
