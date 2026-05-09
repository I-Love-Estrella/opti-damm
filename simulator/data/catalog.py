"""SKU catalog: dimensions, weight, returnable flag, warehouse location.

Combines ZM040 (dimensions) with Materiales zubic (warehouse location). Falls back
to UMA-based defaults when ZM040 has zeros (which is common in the source).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from simulator.config import (
    UMA_DEFAULT_KG,
    UMA_DEFAULT_M3,
    UMA_RETURNABLE,
)
from simulator.data.loader import RawData


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
        out[sku] = SkuRecord(
            sku=sku,
            name=str(name_by_sku.get(sku, sku)),
            uma=uma,
            unit_volume_m3=vol,
            unit_weight_kg=wt,
            is_returnable=is_ret,
            warehouse_location=loc,
            manufacturer=man,
        )
    return out


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
