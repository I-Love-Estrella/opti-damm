"""Excel ingestion with parquet cache.

First run reads the heavy .xlsx files; subsequent runs read parquet (~100x faster).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from simulator.config import CACHE_DIR, SOURCE_FILES


@dataclass(frozen=True)
class RawData:
    detalle: pd.DataFrame
    cabecera: pd.DataFrame
    direcciones: pd.DataFrame
    zonas: pd.DataFrame
    materiales_zubic: pd.DataFrame
    zm040: pd.DataFrame
    horarios: pd.DataFrame


_HACKATON_SHEETS = {
    "detalle": "Detalle entrega",
    "cabecera": "Cabecera Transporte",
    "direcciones": "Direcciones",
    "zonas": "ZONAS",
    "materiales_zubic": "Materiales zubic",
}


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.parquet"


def _load_or_cache(name: str, build) -> pd.DataFrame:
    cache = _cache_path(name)
    if cache.exists():
        return pd.read_parquet(cache)
    df = build()
    df = _normalize_for_parquet(df)
    df.to_parquet(cache, index=False)
    return df


def _normalize_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == "object":
            out[col] = out[col].astype("string")
    out.columns = [str(c) for c in out.columns]
    return out


def _read_hackaton_sheet(sheet: str) -> pd.DataFrame:
    return pd.read_excel(SOURCE_FILES["hackaton"], sheet_name=sheet, engine="openpyxl")


def _read_zm040() -> pd.DataFrame:
    return pd.read_excel(SOURCE_FILES["zm040"], sheet_name="Sheet1", engine="openpyxl")


def _read_horarios() -> pd.DataFrame:
    return pd.read_excel(SOURCE_FILES["horarios"], sheet_name="Sheet1", engine="openpyxl")


def load_all(force_refresh: bool = False) -> RawData:
    if force_refresh:
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()

    detalle = _load_or_cache("detalle", lambda: _read_hackaton_sheet(_HACKATON_SHEETS["detalle"]))
    cabecera = _load_or_cache("cabecera", lambda: _read_hackaton_sheet(_HACKATON_SHEETS["cabecera"]))
    direcciones = _load_or_cache("direcciones", lambda: _read_hackaton_sheet(_HACKATON_SHEETS["direcciones"]))
    zonas = _load_or_cache("zonas", lambda: _read_hackaton_sheet(_HACKATON_SHEETS["zonas"]))
    materiales_zubic = _load_or_cache(
        "materiales_zubic", lambda: _read_hackaton_sheet(_HACKATON_SHEETS["materiales_zubic"])
    )
    zm040 = _load_or_cache("zm040", _read_zm040)
    horarios = _load_or_cache("horarios", _read_horarios)

    return RawData(
        detalle=_clean_detalle(detalle),
        cabecera=_clean_cabecera(cabecera),
        direcciones=_clean_direcciones(direcciones),
        zonas=zonas,
        materiales_zubic=_clean_materiales(materiales_zubic),
        zm040=_clean_zm040(zm040),
        horarios=_clean_horarios(horarios),
    )


def _clean_detalle(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    rename_map = {
        "Destinatario mcía.": "ClienteName",
        "Destinatario mcía..1": "ClienteId",
        "ZonaTransp": "ZonaCliente",
        "ZonaTransp.1": "ZonaPoblacion",
    }
    out = out.rename(columns=rename_map)
    if "FECHA" in out.columns:
        out["FECHA"] = pd.to_datetime(out["FECHA"], errors="coerce", dayfirst=True)
    out["Cantidad entrega"] = pd.to_numeric(out.get("Cantidad entrega"), errors="coerce").fillna(0)
    if "CP" in out.columns:
        out["CP"] = out["CP"].astype("string").str.strip()
    if "ClienteId" in out.columns:
        out["ClienteId"] = out["ClienteId"].astype("string").str.strip()
    if "ClienteName" in out.columns:
        out["ClienteName"] = out["ClienteName"].astype("string").str.strip()
    return out


def _clean_cabecera(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    if "Creado el" in out.columns:
        out["Creado el"] = pd.to_datetime(out["Creado el"], errors="coerce", dayfirst=True)
    return out


def _clean_direcciones(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    if "CP" in out.columns:
        out["CP"] = out["CP"].astype("string").str.strip()
    if "Cliente" in out.columns:
        out["Cliente"] = out["Cliente"].astype("string").str.strip()
    return out


def _clean_materiales(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    return out


def _clean_zm040(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    for c in ("Longitud", "Ancho", "Altura", "Volumen", "Peso bruto"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _clean_horarios(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    return out
