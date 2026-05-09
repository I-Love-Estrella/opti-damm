"""Project-wide configuration: paths, tariffs, defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / "data_cache"
REPORTS_DIR = REPO_ROOT / "reports"

CACHE_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)


SOURCE_FILES = {
    "hackaton": DATA_DIR / "Hackaton.xlsx",
    "zm040": DATA_DIR / "ZM040.XLSX",
    "horarios": DATA_DIR / "Horarios Entrega.XLSX",
    "layout": DATA_DIR / "Layout Mollet.xlsx",
}


DEPOT_LAT = 41.5400
DEPOT_LON = 2.2107
DEPOT_NAME = "DDI Mollet"


@dataclass(frozen=True)
class Tariffs:
    fuel_eur_per_l: float = 1.55
    diesel_l_per_100km: float = 30.0
    co2_kg_per_l_diesel: float = 2.65
    driver_hourly_eur: float = 18.0
    driver_overtime_eur: float = 27.0
    overtime_after_hours: float = 8.0
    vehicle_wear_eur_per_km: float = 0.12


@dataclass(frozen=True)
class TimeModel:
    avg_speed_kmh_urban: float = 22.0
    avg_speed_kmh_interurban: float = 55.0
    road_factor: float = 1.30
    base_service_min: float = 8.0
    service_min_per_pallet: float = 1.5
    service_min_per_search_move: float = 0.1
    pallet_build_min: float = 5.0
    pick_min_per_sku: float = 2.0
    pick_min_per_box: float = 0.05
    load_min_per_pallet: float = 1.0
    depot_dispatch_min: float = 10.0
    return_unload_min_per_pallet: float = 0.8


@dataclass(frozen=True)
class TruckSpec:
    code: str
    name: str
    pallet_capacity: int
    max_weight_kg: float
    sides: tuple[str, ...]
    fleet_count: int


TRUCK_SPECS: dict[str, TruckSpec] = {
    "T6": TruckSpec("T6", "6-Pallet truck", 6, 6000.0, ("L", "R", "B"), 11),
    "T8": TruckSpec("T8", "8-Pallet truck", 8, 8000.0, ("L", "R", "B"), 4),
    "V3": TruckSpec("V3", "3-Pallet van", 3, 1500.0, ("B",), 1),
}
DEFAULT_TRUCK = "T6"


PALLET_LENGTH_M = 1.20
PALLET_WIDTH_M = 0.80
PALLET_HEIGHT_M = 1.80
PALLET_VOLUME_M3 = PALLET_LENGTH_M * PALLET_WIDTH_M * PALLET_HEIGHT_M
PALLET_FOOTPRINT_M2 = PALLET_LENGTH_M * PALLET_WIDTH_M
EMPTY_PALLET_KG = 25.0


UMA_DEFAULT_M3 = {
    "CAJ": 0.024,
    "BAR": 0.06,
    "BOT": 0.0015,
    "UN":  0.001,
    "LAT": 0.0004,
    "PAL": PALLET_VOLUME_M3,
    "BID": 0.04,
}

UMA_DEFAULT_KG = {
    "CAJ": 12.0,
    "BAR": 35.0,
    "BOT": 1.0,
    "UN":  1.0,
    "LAT": 0.4,
    "PAL": 600.0,
    "BID": 25.0,
}

UMA_RETURNABLE = {"BAR", "BID"}


RETURNABLE_RATIO_DEFAULT = 0.60


DEFAULT_TARIFFS = Tariffs()
DEFAULT_TIME_MODEL = TimeModel()
