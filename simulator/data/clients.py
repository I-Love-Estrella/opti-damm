"""Client master + delivery time windows."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from simulator.data.geocode import cp_to_coord
from simulator.data.loader import RawData


@dataclass(frozen=True)
class TimeWindow:
    weekday: int
    shift: int
    start: dt.time
    end: dt.time
    closed: bool


@dataclass(frozen=True)
class ClientRecord:
    client_id: str
    name: str
    address: str
    cp: str
    city: str
    lat: float
    lon: float
    time_windows: tuple[TimeWindow, ...]


class Clients:
    def __init__(self, by_id: dict[str, ClientRecord]):
        self._by_id = by_id

    def __contains__(self, cid: str) -> bool:
        return cid in self._by_id

    def get(self, cid: str) -> ClientRecord:
        rec = self._by_id.get(cid)
        if rec is None:
            return _placeholder(cid)
        return rec

    def all(self) -> dict[str, ClientRecord]:
        return dict(self._by_id)

    @staticmethod
    def build(raw: RawData) -> "Clients":
        return Clients(_assemble(raw))


def _placeholder(cid: str) -> ClientRecord:
    lat, lon = cp_to_coord(None, client_id=cid)
    return ClientRecord(
        client_id=cid,
        name=cid,
        address="",
        cp="",
        city="",
        lat=lat,
        lon=lon,
        time_windows=tuple(),
    )


def _assemble(raw: RawData) -> dict[str, ClientRecord]:
    direcciones = raw.direcciones.copy()
    direcciones["Cliente"] = direcciones["Cliente"].astype("string").str.strip()
    direcciones = direcciones.dropna(subset=["Cliente"]).drop_duplicates("Cliente")

    detalle_clients = (
        raw.detalle.dropna(subset=["ClienteId"])
        .assign(
            ClienteId=lambda d: d["ClienteId"].astype("string").str.strip(),
            ClienteName=lambda d: d.get("ClienteName", "").astype("string"),
            CP=lambda d: d["CP"].astype("string").str.strip(),
        )
        .drop_duplicates("ClienteId")[["ClienteId", "ClienteName", "Calle", "CP", "Población"]]
        .rename(columns={"ClienteId": "Cliente", "ClienteName": "Nombre 1"})
    )

    merged = pd.concat(
        [
            direcciones[["Cliente", "Nombre 1", "Calle", "CP", "Población"]],
            detalle_clients,
        ],
        ignore_index=True,
    ).drop_duplicates("Cliente", keep="first")

    windows = _build_time_windows(raw.horarios)

    out: dict[str, ClientRecord] = {}
    for _, row in merged.iterrows():
        cid = str(row.get("Cliente", "")).strip()
        if not cid:
            continue
        cp = str(row.get("CP", "") or "").strip()
        lat, lon = cp_to_coord(cp, client_id=cid)
        out[cid] = ClientRecord(
            client_id=cid,
            name=str(row.get("Nombre 1", "") or cid),
            address=str(row.get("Calle", "") or ""),
            cp=cp,
            city=str(row.get("Población", "") or ""),
            lat=lat,
            lon=lon,
            time_windows=tuple(windows.get(cid, ())),
        )
    return out


def _build_time_windows(horarios: pd.DataFrame) -> dict[str, list[TimeWindow]]:
    if horarios.empty:
        return {}

    df = horarios.copy()
    df["Deudor"] = df["Deudor"].astype("string").str.strip()

    by_client: dict[str, list[TimeWindow]] = {}
    for _, row in df.iterrows():
        cid = row.get("Deudor")
        if not isinstance(cid, str) or not cid:
            continue
        weekday = _to_int(row.get("Día semana"))
        shift = _to_int(row.get("Turno"))
        start = _to_time(row.get("Horario inicia a"))
        end = _to_time(row.get("Horario termina a"))
        if start is None or end is None:
            continue
        closed = str(row.get("Cierre Si/No", "") or "").strip().upper() in {"SI", "S", "Y", "YES"}
        by_client.setdefault(cid, []).append(
            TimeWindow(weekday=weekday or 0, shift=shift or 0, start=start, end=end, closed=closed)
        )
    return by_client


def _to_int(v) -> int:
    try:
        return int(v) if v is not None and pd.notna(v) else 0
    except Exception:
        return 0


def _to_time(v) -> dt.time | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, dt.time):
        return v
    if isinstance(v, dt.datetime):
        return v.time()
    if isinstance(v, str):
        try:
            return dt.time.fromisoformat(v)
        except ValueError:
            return None
    return None
