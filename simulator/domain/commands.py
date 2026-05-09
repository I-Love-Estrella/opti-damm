"""Command set executed by the simulator. Each command is an immutable
instruction. The simulator does not infer geometry — algorithms must put
everything they need (position, dimensions, unit physics) on each Pick /
PickupReturn so the simulator can place items verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class Pick:
    sku: str
    qty: float
    location: str | None
    pallet_id: str
    intended_client: str | None = None
    # Physical placement decided by the algorithm (metres from pallet corner).
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    # AABB extents of this item on the pallet.
    dim_x: float = 0.0
    dim_y: float = 0.0
    dim_h: float = 0.0
    # Per-unit physics (used for KPIs / weight checks).
    unit_volume_m3: float = 0.0
    unit_weight_kg: float = 0.0
    physical_type: str = "unit"


@dataclass(frozen=True)
class BuildPallet:
    pallet_id: str
    kind: str
    primary_client: str | None
    notes: str = ""
    # Optional class hint; if unset, derived from the first picked item.
    pallet_class: str | None = None


@dataclass(frozen=True)
class Load:
    pallet_id: str
    slot_id: str


@dataclass(frozen=True)
class DepartDepot:
    pass


@dataclass(frozen=True)
class DriveTo:
    client_id: str


@dataclass(frozen=True)
class LiftedItem:
    """Identifies an item the algorithm orders the simulator to lift.

    The simulator does NOT decide which items are blockers — the
    algorithm does, and lists them here. The simulator matches each
    LiftedItem to an actual pallet item by (sku, intended_client) +
    original position, then physically lifts it. Items in `lifts` but
    not in the corresponding `restock` are treated as same-client
    opportunistic deliveries.
    """

    sku: str
    qty: float
    intended_client: str | None
    pos_x: float
    pos_y: float
    pos_z: float


@dataclass(frozen=True)
class RestockItem:
    """Where to put a lifted foreign blocker after the target is taken.

    The simulator matches a lifted blocker to a restock entry by
    (sku, intended_client, from_pos). `from_pos_*` is the blocker's
    ORIGINAL position before lifting — supplied by the algorithm so
    two blockers sharing (sku, client) cannot have their destinations
    swapped (which used to leave them stacked on top of each other).

    `pos_*` is the destination position. With the default
    "no-op restock" plan, `pos_* == from_pos_*` and the simulator
    skips the move entirely.

    Blockers without a matching entry stay at their original pos.
    """

    sku: str
    qty: float
    intended_client: str | None
    pos_x: float
    pos_y: float
    pos_z: float
    dim_x: float
    dim_y: float
    dim_h: float
    physical_type: str = "unit"
    unit_volume_m3: float = 0.0
    unit_weight_kg: float = 0.0
    # Original location of the blocker before the lift. Used by the
    # simulator to match restock entries to specific blockers. Default
    # (0,0,0) preserves backwards compatibility with older plans that
    # didn't fill these — the simulator falls back to (sku, client)
    # FIFO matching when no entry's from_pos matches.
    from_pos_x: float = 0.0
    from_pos_y: float = 0.0
    from_pos_z: float = 0.0


@dataclass(frozen=True)
class Unload:
    client_id: str
    sku: str
    qty: float
    slot_id: str
    # All items the simulator must lift to reach the target — chosen by
    # the algorithm. The simulator does no blocker-detection itself
    # when this list is non-empty. Items not present here are NOT
    # lifted, even if they would physically obstruct the target.
    lifts: tuple[LiftedItem, ...] = ()
    # Where to put each foreign blocker after the target is taken.
    # Items in `lifts` whose (sku, client) doesn't appear in `restock`
    # are treated as same-client opportunistic deliveries (lifted +
    # delivered, not restocked).
    restock: tuple[RestockItem, ...] = ()


@dataclass(frozen=True)
class PickupReturn:
    client_id: str
    sku: str
    qty: float
    slot_id: str
    # Physical placement on the pickup pallet (algorithm-decided).
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    dim_x: float = 0.0
    dim_y: float = 0.0
    dim_h: float = 0.0
    # Type of empty being picked up. Drives visual rendering and the
    # crush/stability checks the simulator runs against the resulting
    # PalletItem. Defaults to "keg" so older plans keep working
    # unchanged. Algorithms that pick up empty cases / bottles should
    # set this explicitly.
    physical_type: str = "keg"
    unit_weight_kg: float = 2.0  # empty keg ≈ 2 kg
    unit_volume_m3: float = 0.04


@dataclass(frozen=True)
class ReturnDepot:
    pass


Command = Union[
    BuildPallet,
    Pick,
    Load,
    DepartDepot,
    DriveTo,
    Unload,
    PickupReturn,
    ReturnDepot,
]
