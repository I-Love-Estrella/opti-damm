"""SKU catalog: dimensions, weight, returnable flag, warehouse location.

Combines ZM040 (dimensions) with Materiales zubic (warehouse location). Falls back
to UMA-based defaults when ZM040 has zeros (which is common in the source).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from simulator.config import (
    UMA_DEFAULT_KG,
    UMA_DEFAULT_M3,
    UMA_RETURNABLE,
)
from simulator.data.loader import RawData


class PhysicalType(str, Enum):
    """Packaging form of one physical SKU unit. Drives UI rendering and validation."""

    KEG = "keg"        # Keg / barrel (returnable; UMA codes BAR / BRL / BID)
    CASE = "case"      # Case / carton of products (CAJ / BOX / PAK)
    BOTTLE = "bottle"  # Standalone large bottle (BOT)
    CAN = "can"        # Can (LAT)
    BULK = "bulk"      # Whole pallet sold as a unit (PAL)
    WEIGHT = "weight"  # Sold by weight or volume (KG / L / G/L)
    UNIT = "unit"      # Generic single-unit item (UN / ZPR / fallback)


PHYSICAL_TYPE_LABEL = {
    PhysicalType.KEG:    "keg",
    PhysicalType.CASE:   "case",
    PhysicalType.BOTTLE: "bottle",
    PhysicalType.CAN:    "can",
    PhysicalType.BULK:   "pallet",
    PhysicalType.WEIGHT: "by weight",
    PhysicalType.UNIT:   "unit",
}

# Backwards-compat alias for code that imported the old (RU-flavored) name.
PHYSICAL_TYPE_LABEL_RU = PHYSICAL_TYPE_LABEL

PHYSICAL_TYPE_CODE = {
    PhysicalType.KEG:    "K",
    PhysicalType.CASE:   "C",
    PhysicalType.BOTTLE: "B",
    PhysicalType.CAN:    "N",
    PhysicalType.BULK:   "P",
    PhysicalType.WEIGHT: "W",
    PhysicalType.UNIT:   "U",
}

# Real-world physical dimensions per packaging type, in metres:
# (length along pallet X, depth along pallet Y, height along Z).
PHYSICAL_TYPE_DIMS_M: dict[PhysicalType, tuple[float, float, float]] = {
    PhysicalType.KEG:    (0.40, 0.40, 0.65),  # 30L beer keg
    PhysicalType.CASE:   (0.30, 0.27, 0.30),  # beer case 24x33CL
    PhysicalType.BOTTLE: (0.12, 0.12, 0.42),  # standalone large bottle
    PhysicalType.CAN:    (0.08, 0.08, 0.16),  # single can
    PhysicalType.BULK:   (1.10, 0.75, 1.50),  # whole pallet item
    PhysicalType.WEIGHT: (0.30, 0.30, 0.30),  # generic weight bag
    PhysicalType.UNIT:   (0.20, 0.20, 0.24),  # generic single unit
}


def physical_dims(t: PhysicalType | str) -> tuple[float, float, float]:
    """Return (dim_x, dim_y, dim_h) in metres for a physical type."""
    if isinstance(t, str):
        try:
            t = PhysicalType(t)
        except ValueError:
            t = PhysicalType.UNIT
    return PHYSICAL_TYPE_DIMS_M.get(t, PHYSICAL_TYPE_DIMS_M[PhysicalType.UNIT])

_KEG_UMAS = frozenset({"BAR", "BRL", "BID"})
_CASE_UMAS = frozenset({"CAJ", "BOX", "PAK"})
_BOTTLE_UMAS = frozenset({"BOT"})
_CAN_UMAS = frozenset({"LAT"})
_BULK_UMAS = frozenset({"PAL"})
_WEIGHT_UMAS = frozenset({"KG", "L", "G/L"})

_KEG_NAME_KEYWORDS = ("BARRIL", "BARREL", " KEG", " BIDON", "BIDÓN", "RETOR", "ENVAS")
_BOTTLE_NAME_KEYWORDS = ("BOTELLA",)
_CAN_NAME_KEYWORDS = (" LATA",)


def physical_type(uma: str | None, name: str | None = None) -> PhysicalType:
    """Classify a SKU's packaging type from UMA + (optionally) the name string.

    Name keywords win over UMA when they're unambiguous (e.g. SKUs named
    "ESTRELLA DAMM BARRIL 30L" with UMA=ZPR are still kegs).
    """
    n = (name or "").upper()
    for kw in _KEG_NAME_KEYWORDS:
        if kw in n:
            return PhysicalType.KEG

    u = (uma or "").upper().strip()
    if u in _KEG_UMAS:
        return PhysicalType.KEG
    if u in _CASE_UMAS:
        return PhysicalType.CASE
    if u in _BOTTLE_UMAS:
        return PhysicalType.BOTTLE
    if u in _CAN_UMAS:
        return PhysicalType.CAN
    if u in _BULK_UMAS:
        return PhysicalType.BULK
    if u in _WEIGHT_UMAS:
        return PhysicalType.WEIGHT
    return PhysicalType.UNIT


@dataclass(frozen=True)
class SkuRecord:
    sku: str
    name: str
    uma: str
    unit_volume_m3: float
    unit_weight_kg: float
    is_returnable: bool
    warehouse_location: str | None
    manufacturer: str | None
    physical_type: PhysicalType = PhysicalType.UNIT
    # Real per-unit physical dimensions in metres, sourced from ZM040 when
    # the SKU has plausible Longitud / Ancho / Altura values for its UMA row.
    # If unavailable the fields are 0.0 and callers should fall back to
    # `physical_dims(physical_type)`.
    dim_x_m: float = 0.0
    dim_y_m: float = 0.0
    dim_h_m: float = 0.0
    dim_source: str = "type"  # "data" | "type"


class Catalog:
    def __init__(self, records: dict[str, SkuRecord]):
        self._records = records

    def __contains__(self, sku: str) -> bool:
        return sku in self._records

    def get(self, sku: str) -> SkuRecord:
        rec = self._records.get(sku)
        if rec is None:
            return _fallback_record(sku)
        return rec

    def all(self) -> dict[str, SkuRecord]:
        return dict(self._records)

    @staticmethod
    def build(raw: RawData) -> "Catalog":
        return Catalog(_assemble(raw))


def _fallback_record(sku: str) -> SkuRecord:
    return SkuRecord(
        sku=sku,
        name=sku,
        uma="UN",
        unit_volume_m3=UMA_DEFAULT_M3["UN"],
        unit_weight_kg=UMA_DEFAULT_KG["UN"],
        is_returnable=False,
        warehouse_location=None,
        manufacturer=None,
        physical_type=PhysicalType.UNIT,
    )


def _assemble(raw: RawData) -> dict[str, SkuRecord]:
    zm = raw.zm040.copy()
    mz = raw.materiales_zubic.copy()
    detalle = raw.detalle

    name_by_sku = (
        detalle.dropna(subset=["Material"])
        .drop_duplicates(subset=["Material"])
        .set_index("Material")["Denominación"].astype("string").to_dict()
    )

    mz_index = mz.dropna(subset=["Material"]).set_index("Material")
    zm_index = zm.dropna(subset=["Material"]).drop_duplicates("Material").set_index("Material")
    # Per-SKU multi-row view (one SKU may have rows for UN / CAJ / PAL etc.) —
    # used by _pick_dims to pick the row matching the SKU's primary UMA.
    zm_full_by_sku: dict[str, pd.DataFrame] = {
        str(sku): grp for sku, grp in zm.dropna(subset=["Material"]).groupby("Material")
    }

    skus = set(detalle["Material"].dropna().unique().tolist())
    skus.update(zm_index.index.tolist())
    skus.update(mz_index.index.tolist())

    out: dict[str, SkuRecord] = {}
    for sku in skus:
        if not isinstance(sku, str) or not sku:
            continue
        uma = _pick_uma(zm_index, mz_index, sku)
        vol = _pick_volume(zm_index, sku, uma)
        wt = _pick_weight(zm_index, sku, uma)
        loc = _pick_location(mz_index, sku)
        man = _pick_manufacturer(mz_index, sku)
        is_ret = _is_returnable(uma, name_by_sku.get(sku, ""))
        nm = str(name_by_sku.get(sku, sku))
        ptype = physical_type(uma, nm)
        dim_x, dim_y, dim_h, dim_src = _pick_dims(zm_full_by_sku.get(sku), uma, ptype)
        out[sku] = SkuRecord(
            sku=sku,
            name=nm,
            uma=uma,
            unit_volume_m3=vol,
            unit_weight_kg=wt,
            is_returnable=is_ret,
            warehouse_location=loc,
            manufacturer=man,
            physical_type=ptype,
            dim_x_m=dim_x,
            dim_y_m=dim_y,
            dim_h_m=dim_h,
            dim_source=dim_src,
        )
    return out


_DIM_PLAUSIBLE_RANGE_M = (0.02, 1.50)  # 2 cm .. 1.50 m per side
_DIM_COLS = ("Longitud", "Ancho", "Altura")
_DIM_UNIT_COLS = ("Unidad dimensión", "Unidad dimensión.1", "Unidad dimensión.2")


def _to_metres(value, unit) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not isinstance(unit, str):
        unit = ""
    u = unit.strip().upper()
    if u == "MM":
        return v / 1000.0
    if u == "CM":
        return v / 100.0
    if u == "M":
        return v
    return v / 100.0  # default to CM if unspecified


def _pick_dims(
    zm_rows: pd.DataFrame | None,
    sku_uma: str,
    ptype: PhysicalType,
) -> tuple[float, float, float, str]:
    """Pick the row that best represents one physical unit of the SKU.

    Strategy:
      1. Prefer a row whose UMA matches the SKU's primary UMA — that row
         carries the dimensions of one delivered unit.
      2. Otherwise pick the row with the smallest plausible volume (= the
         most granular packaging level).
      3. If no plausible row exists, return (0, 0, 0, 'type') and let the
         caller fall back to PHYSICAL_TYPE_DIMS_M.
    """
    type_default = PHYSICAL_TYPE_DIMS_M.get(ptype, PHYSICAL_TYPE_DIMS_M[PhysicalType.UNIT])

    if zm_rows is None or zm_rows.empty:
        return (*type_default, "type")

    candidates: list[tuple[float, float, float, str]] = []
    for _, row in zm_rows.iterrows():
        u = str(row.get("UMA") or "").strip().upper()
        dims_m: list[float] = []
        for col, unit_col in zip(_DIM_COLS, _DIM_UNIT_COLS):
            dims_m.append(_to_metres(row.get(col), row.get(unit_col)))
        x, y, h = dims_m
        if not all(_DIM_PLAUSIBLE_RANGE_M[0] <= d <= _DIM_PLAUSIBLE_RANGE_M[1] for d in (x, y, h)):
            continue
        candidates.append((x, y, h, u))

    if not candidates:
        return (*type_default, "type")

    sku_uma_u = (sku_uma or "").strip().upper()
    matched = [c for c in candidates if c[3] == sku_uma_u]
    if matched:
        x, y, h, _ = matched[0]
        return (x, y, h, "data")

    # Otherwise pick the smallest by volume.
    candidates.sort(key=lambda c: c[0] * c[1] * c[2])
    x, y, h, _ = candidates[0]
    return (x, y, h, "data")


def _pick_uma(zm: pd.DataFrame, mz: pd.DataFrame, sku: str) -> str:
    for src, col in ((zm, "UMA"), (mz, "UMB")):
        if sku in src.index and col in src.columns:
            v = src.at[sku, col]
            if isinstance(v, str) and v.strip():
                return v.strip().upper()
    return "UN"


_VOL_PLAUSIBLE_RANGE = (0.0005, 0.40)
_WEIGHT_PLAUSIBLE_RANGE = (0.1, 80.0)


def _pick_volume(zm: pd.DataFrame, sku: str, uma: str) -> float:
    if sku in zm.index and "Volumen" in zm.columns:
        v = zm.at[sku, "Volumen"]
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        if _VOL_PLAUSIBLE_RANGE[0] <= v <= _VOL_PLAUSIBLE_RANGE[1]:
            return v
    return UMA_DEFAULT_M3.get(uma, UMA_DEFAULT_M3["UN"])


def _pick_weight(zm: pd.DataFrame, sku: str, uma: str) -> float:
    if sku in zm.index and "Peso bruto" in zm.columns:
        v = zm.at[sku, "Peso bruto"]
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        if _WEIGHT_PLAUSIBLE_RANGE[0] <= v <= _WEIGHT_PLAUSIBLE_RANGE[1]:
            return v
    return UMA_DEFAULT_KG.get(uma, UMA_DEFAULT_KG["UN"])


def _pick_location(mz: pd.DataFrame, sku: str) -> str | None:
    if sku in mz.index and "Ubic." in mz.columns:
        v = mz.at[sku, "Ubic."]
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _pick_manufacturer(mz: pd.DataFrame, sku: str) -> str | None:
    if sku in mz.index and "Fabricante" in mz.columns:
        v = mz.at[sku, "Fabricante"]
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _is_returnable(uma: str, name: str) -> bool:
    if uma in UMA_RETURNABLE:
        return True
    upper = (name or "").upper()
    return any(k in upper for k in ("BARRIL", "RETOR", "ENVAS", "BARREL", "KEG"))
