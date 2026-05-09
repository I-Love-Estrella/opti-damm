"""Pallet and item models — continuous 3D positioning.

Each PalletItem is an axis-aligned bounding box (AABB) inside the pallet.
The item is positioned by its bottom-left-front corner (pos_x, pos_y, pos_z)
in metres from the pallet origin, with extents (dim_x, dim_y, dim_h).

Pallet corner system:
  X axis — along the long pallet side (1.20 m).
  Y axis — depth: 0 at the truck-edge side, increasing toward the cab.
  Z axis — height: 0 at the pallet floor, up.

Pallet.suggest_position(dim_x, dim_y, dim_h) runs a greedy "extreme points"
3D bin-packer: it tests anchor points at the corners of currently-loaded items
plus (0, 0, 0), and picks the lowest valid (z, y, x) anchor with no AABB
overlap. This lets small items (cans, units) tuck into gaps left by larger
ones (kegs, bulk) instead of wasting a full cell.

Legacy discrete fields (col_x, col_y, bottom_level, stack_size) are exposed as
properties derived from the continuous position so the existing event log,
validator and frontend keep working without churn.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import ceil


PALLET_LENGTH_M = 1.20
PALLET_WIDTH_M = 0.80
PALLET_HEIGHT_M = 1.80

# Logical cell sizes used only to derive legacy col_x/col_y/level coordinates
# from continuous positions for backwards compatibility with existing events.
_LEGACY_CELL_X_M = 0.30
_LEGACY_CELL_Y_M = 0.27
_LEGACY_CELL_H_M = 0.30

_BBOX_EPS = 1e-6


class PalletKind(str, Enum):
    MONO_SKU = "mono_sku"
    CLIENT_BLOCK = "client_block"
    MIXED = "mixed"
    EMPTIES = "empties"


class PalletClass(str, Enum):
    KEG = "keg"
    BOX = "box"


@dataclass(frozen=True)
class PalletLayout:
    """Legacy layout descriptor — kept for the validator and old visualizers
    that still need approximate cell counts. The new bin-packer ignores it."""

    cols_x: int
    cols_y: int
    max_level: int

    @property
    def total_columns(self) -> int:
        return self.cols_x * self.cols_y

    @property
    def capacity_units(self) -> int:
        return self.cols_x * self.cols_y * self.max_level


KEG_LAYOUT = PalletLayout(cols_x=2, cols_y=2, max_level=4)
BOX_LAYOUT = PalletLayout(cols_x=4, cols_y=3, max_level=6)

LAYOUT_FOR_CLASS: dict[PalletClass, PalletLayout] = {
    PalletClass.KEG: KEG_LAYOUT,
    PalletClass.BOX: BOX_LAYOUT,
}

KEG_UMAS = frozenset({"BAR", "BID"})


def sku_class_for_uma(uma: str | None) -> PalletClass:
    return PalletClass.KEG if (uma or "").upper() in KEG_UMAS else PalletClass.BOX


@dataclass(frozen=True)
class PalletItem:
    sku: str
    qty: float
    unit_volume_m3: float
    unit_weight_kg: float
    intended_client: str | None
    is_returnable_empty: bool = False
    physical_type: str = "unit"

    # Continuous 3D placement (metres from pallet corner).
    # pos_* is the bottom-left-front corner of the AABB.
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    dim_x: float = 0.20
    dim_y: float = 0.20
    dim_h: float = 0.24

    @property
    def volume_m3(self) -> float:
        return self.qty * self.unit_volume_m3

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg

    # AABB extents.
    @property
    def end_x(self) -> float:
        return self.pos_x + self.dim_x

    @property
    def end_y(self) -> float:
        return self.pos_y + self.dim_y

    @property
    def top_z(self) -> float:
        return self.pos_z + self.dim_h

    def aabb(self) -> tuple[float, float, float, float, float, float]:
        return (self.pos_x, self.pos_y, self.pos_z, self.end_x, self.end_y, self.top_z)

    def overlaps_xy(self, other: "PalletItem") -> bool:
        return (
            self.pos_x < other.end_x - _BBOX_EPS
            and other.pos_x < self.end_x - _BBOX_EPS
            and self.pos_y < other.end_y - _BBOX_EPS
            and other.pos_y < self.end_y - _BBOX_EPS
        )

    def overlaps_xz(self, other: "PalletItem") -> bool:
        return (
            self.pos_x < other.end_x - _BBOX_EPS
            and other.pos_x < self.end_x - _BBOX_EPS
            and self.pos_z < other.top_z - _BBOX_EPS
            and other.pos_z < self.top_z - _BBOX_EPS
        )

    # ---- Legacy discrete-grid properties (derived from continuous coords) ----

    @property
    def col_x(self) -> int:
        return max(0, int(self.pos_x / _LEGACY_CELL_X_M))

    @property
    def col_y(self) -> int:
        return max(0, int(self.pos_y / _LEGACY_CELL_Y_M))

    @property
    def bottom_level(self) -> int:
        return max(0, int(self.pos_z / _LEGACY_CELL_H_M))

    @property
    def stack_size(self) -> int:
        if self.dim_h <= 0:
            return 0
        return max(1, int(ceil(self.dim_h / _LEGACY_CELL_H_M)))

    @property
    def top_level(self) -> int:
        return self.bottom_level + max(0, self.stack_size - 1)


def _aabb_overlaps(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
) -> bool:
    return (
        a[0] < b[3] - _BBOX_EPS
        and b[0] < a[3] - _BBOX_EPS
        and a[1] < b[4] - _BBOX_EPS
        and b[1] < a[4] - _BBOX_EPS
        and a[2] < b[5] - _BBOX_EPS
        and b[2] < a[5] - _BBOX_EPS
    )


@dataclass(frozen=True)
class Pallet:
    pallet_id: str
    kind: PalletKind
    items: tuple[PalletItem, ...]
    primary_client: str | None = None
    notes: str = ""
    pallet_class: PalletClass | None = None

    @property
    def volume_m3(self) -> float:
        return sum(i.volume_m3 for i in self.items)

    @property
    def weight_kg(self) -> float:
        return sum(i.weight_kg for i in self.items)

    @property
    def is_empty(self) -> bool:
        return all(i.qty <= 0 for i in self.items)

    @property
    def layout(self) -> PalletLayout:
        cls = self.pallet_class or PalletClass.BOX
        return LAYOUT_FOR_CLASS[cls]

    def with_items(self, items: tuple[PalletItem, ...]) -> "Pallet":
        return Pallet(
            pallet_id=self.pallet_id,
            kind=self.kind,
            items=items,
            primary_client=self.primary_client,
            notes=self.notes,
            pallet_class=self.pallet_class,
        )

    def with_class(self, cls: PalletClass) -> "Pallet":
        return Pallet(
            pallet_id=self.pallet_id,
            kind=self.kind,
            items=self.items,
            primary_client=self.primary_client,
            notes=self.notes,
            pallet_class=cls,
        )

    # ---- 3D bin-packing ----

    def suggest_position(
        self, dim_x: float, dim_y: float, dim_h: float
    ) -> tuple[float, float, float]:
        """Find a fit for the new item. Greedy extreme-point bin-packer."""
        candidates: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        for it in self.items:
            if it.qty <= 0:
                continue
            candidates.append((it.end_x, it.pos_y, it.pos_z))
            candidates.append((it.pos_x, it.end_y, it.pos_z))
            candidates.append((it.pos_x, it.pos_y, it.top_z))

        # Deduplicate close anchors.
        seen: list[tuple[float, float, float]] = []
        for c in candidates:
            if not any(
                abs(c[0] - s[0]) < 1e-4
                and abs(c[1] - s[1]) < 1e-4
                and abs(c[2] - s[2]) < 1e-4
                for s in seen
            ):
                seen.append(c)
        candidates = seen

        best: tuple[float, float, float] | None = None
        for (x, y, z) in candidates:
            # Inside pallet footprint (height is allowed to overflow — validator
            # catches PALLET_HEIGHT_EXCEEDS_TRUCK).
            if x + dim_x > PALLET_LENGTH_M + _BBOX_EPS:
                continue
            if y + dim_y > PALLET_WIDTH_M + _BBOX_EPS:
                continue
            new_box = (x, y, z, x + dim_x, y + dim_y, z + dim_h)
            collides = False
            for it in self.items:
                if it.qty <= 0:
                    continue
                if _aabb_overlaps(new_box, it.aabb()):
                    collides = True
                    break
            if collides:
                continue
            if best is None or (z, y, x) < (best[2], best[1], best[0]):
                best = (x, y, z)

        if best is None:
            # No legal fit — drop the item on top of the tallest stack at (0,0).
            max_z = max((it.top_z for it in self.items if it.qty > 0), default=0.0)
            best = (0.0, 0.0, max_z)
        return best

    # ---- Mutation helpers ----

    def remove_item(
        self, sku: str, qty: float, client: str | None
    ) -> tuple["Pallet", PalletItem | None]:
        new_items: list[PalletItem] = []
        removed: PalletItem | None = None
        remaining = qty
        for it in self.items:
            if (
                removed is None
                and it.sku == sku
                and (client is None or it.intended_client == client)
                and remaining > 0
            ):
                take = min(it.qty, remaining)
                remaining -= take
                if take >= it.qty:
                    removed = it
                    continue
                # Partial take — kept item shrinks proportionally in height
                # so the AABB stays consistent.
                kept_qty = it.qty - take
                ratio = kept_qty / it.qty if it.qty > 0 else 0.0
                kept = PalletItem(
                    sku=it.sku,
                    qty=kept_qty,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    physical_type=it.physical_type,
                    pos_x=it.pos_x,
                    pos_y=it.pos_y,
                    pos_z=it.pos_z,
                    dim_x=it.dim_x,
                    dim_y=it.dim_y,
                    dim_h=it.dim_h * ratio,
                )
                removed = PalletItem(
                    sku=it.sku,
                    qty=take,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    physical_type=it.physical_type,
                    pos_x=it.pos_x,
                    pos_y=it.pos_y,
                    pos_z=it.pos_z + kept.dim_h,
                    dim_x=it.dim_x,
                    dim_y=it.dim_y,
                    dim_h=it.dim_h - kept.dim_h,
                )
                new_items.append(kept)
                continue
            new_items.append(it)
        return self.with_items(tuple(new_items)), removed

    def add_item(self, item: PalletItem) -> "Pallet":
        return self.with_items(self.items + (item,))


@dataclass(frozen=True)
class PalletInTruck:
    slot_id: str
    pallet: Pallet
