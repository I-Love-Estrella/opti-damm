from __future__ import annotations

from pydantic import BaseModel


class TimeWindowOut(BaseModel):
    weekday: int
    shift: int
    start: str
    end: str
    closed: bool


class ClientOut(BaseModel):
    client_id: str
    name: str
    address: str
    cp: str
    city: str
    lat: float
    lon: float
    time_windows: list[TimeWindowOut]


class OrderLineOut(BaseModel):
    sku: str
    qty: float
    uma: str
    unit_volume_m3: float
    unit_weight_kg: float
    is_returnable: bool


class ClientOrderOut(BaseModel):
    client_id: str
    client_name: str
    lines: list[OrderLineOut]
    expected_returnable_units: float
    visit_seq: int
    total_volume_m3: float
    total_weight_kg: float


class TruckOut(BaseModel):
    code: str
    name: str
    pallet_capacity: int
    max_weight_kg: float


class DayCaseOut(BaseModel):
    date: str
    ruta: str
    repartidor: str
    truck: TruckOut
    transports: list[str]
    n_clients: int
    total_volume_m3: float
    orders: list[ClientOrderOut]


class RouteSummary(BaseModel):
    fecha: str
    ruta: str
    clients: int
    lines: int
