"""Command set executed by the simulator. Each command is an immutable instruction."""

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


@dataclass(frozen=True)
class BuildPallet:
    pallet_id: str
    kind: str
    primary_client: str | None
    notes: str = ""


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
class Unload:
    client_id: str
    sku: str
    qty: float
    slot_id: str


@dataclass(frozen=True)
class PickupReturn:
    client_id: str
    sku: str
    qty: float
    slot_id: str


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
