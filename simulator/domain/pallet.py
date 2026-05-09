"""Pallet and item models. Pallets carry stacked items; each item targets a client."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PalletKind(str, Enum):
    MONO_SKU = "mono_sku"
    CLIENT_BLOCK = "client_block"
    MIXED = "mixed"
    EMPTIES = "empties"


@dataclass(frozen=True)
class PalletItem:
    sku: str
    qty: float
    unit_volume_m3: float
    unit_weight_kg: float
    intended_client: str | None
    is_returnable_empty: bool = False

    @property
    def volume_m3(self) -> float:
        return self.qty * self.unit_volume_m3

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg


@dataclass(frozen=True)
class Pallet:
    pallet_id: str
    kind: PalletKind
    items: tuple[PalletItem, ...]
    primary_client: str | None = None
    notes: str = ""

    @property
    def volume_m3(self) -> float:
        return sum(i.volume_m3 for i in self.items)

    @property
    def weight_kg(self) -> float:
        return sum(i.weight_kg for i in self.items)

    @property
    def is_empty(self) -> bool:
        return all(i.qty <= 0 for i in self.items)

    def with_items(self, items: tuple[PalletItem, ...]) -> "Pallet":
        return Pallet(
            pallet_id=self.pallet_id,
            kind=self.kind,
            items=items,
            primary_client=self.primary_client,
            notes=self.notes,
        )

    def remove_item(self, sku: str, qty: float, client: str | None) -> tuple["Pallet", PalletItem | None]:
        new_items: list[PalletItem] = []
        removed: PalletItem | None = None
        remaining = qty
        for it in self.items:
            if removed is None and it.sku == sku and (client is None or it.intended_client == client) and remaining > 0:
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
                )
                removed = PalletItem(
                    sku=it.sku,
                    qty=take,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
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
