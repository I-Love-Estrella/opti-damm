"""Pallet and item models — discrete grid model.

Physical model
--------------
A pallet is one of two classes, set when the first item is picked onto it:

  * KEG pallet — 2×2 columns × max 4 levels  (4 columns, 16 keg cells max)
  * BOX pallet — 4×3 columns × max 6 levels  (12 columns, 72 box cells max)

A column holds a vertical stack of homogeneous-class units (kegs or boxes).
Items are dropped into the column with the lowest current top; the new item
sits on top of whatever is already there.

A PalletItem occupies a contiguous range of levels [bottom_level .. top_level]
in a single (col_x, col_y) column. Its `stack_size` = ceil(qty), so qty=24
takes 24 cells stacked vertically (or as many as fit; overflow is allowed
but counts as a capacity violation downstream).

Search-moves
------------
To reach an item I in column (cx, cy) at levels [I.bottom .. I.top], the
driver must remove every unit physically above I.top in the same column.
Same-client units above are "free" (they'd be unloaded at this stop anyway).
Foreign-client units cost 1 search-move per unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import ceil


PALLET_LENGTH_M = 1.20
PALLET_WIDTH_M = 0.80
PALLET_HEIGHT_M = 1.80


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
    # Discrete grid position within pallet:
    col_x: int = 0
    col_y: int = 0
    bottom_level: int = 0

    @property
    def volume_m3(self) -> float:
        return self.qty * self.unit_volume_m3

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg

    @property
    def stack_size(self) -> int:
        if self.qty <= 0:
            return 0
        return max(1, int(ceil(self.qty)))

    @property
    def top_level(self) -> int:
        return self.bottom_level + max(0, self.stack_size - 1)


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

    def column_top(self, col_x: int, col_y: int) -> int:
        """Next free level in the (col_x, col_y) column. 0 if empty."""
        max_top = -1
        for it in self.items:
            if it.col_x == col_x and it.col_y == col_y and it.qty > 0:
                if it.top_level > max_top:
                    max_top = it.top_level
        return max_top + 1

    def suggest_position(self) -> tuple[int, int, int]:
        """Pick column with lowest current top. Returns (col_x, col_y, bottom_level)."""
        layout = self.layout
        best = (0, 0, 0)
        best_top = 10**9
        for cx in range(layout.cols_x):
            for cy in range(layout.cols_y):
                top = self.column_top(cx, cy)
                if top < best_top:
                    best_top = top
                    best = (cx, cy, top)
        return best

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
                kept = PalletItem(
                    sku=it.sku,
                    qty=it.qty - take,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    col_x=it.col_x,
                    col_y=it.col_y,
                    bottom_level=it.bottom_level,
                )
                removed = PalletItem(
                    sku=it.sku,
                    qty=take,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    col_x=it.col_x,
                    col_y=it.col_y,
                    bottom_level=it.bottom_level,
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
