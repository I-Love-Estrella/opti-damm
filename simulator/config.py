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
    # Warehouse loader rate (BUILD_PALLET / PICK / LOAD / DEPART_DEPOT).
    # Lower than the driver rate — picking is less skilled, no driving
    # licence required, no on-road risk premium. Spanish DDI ~€10–14/h.
    loader_hourly_eur: float = 12.0
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

    # Per-physical-unit handling time in minutes — covers one full
    # "grab + carry + place" cycle by the driver. Calibrated against
    # observed DDI service times: a 30-L keg (~30 kg, two-handed,
    # awkward) is ~5x slower per unit than a single can. Used by the
    # simulator for Unload (handing target box to client),
    # BLOCKER_LIFT/REPLACE (each = 0.5 cycles, since lifting and
    # replacing are the two halves of a full handling), and
    # PickupReturn (loading empty back onto truck).
    handle_min_keg: float = 0.20      # 12 s — heavy, two-handed
    handle_min_bulk: float = 0.30     # 18 s — pallet jack reposition
    handle_min_weight: float = 0.15   # 9 s  — bag/sack
    handle_min_case: float = 0.10     # 6 s  — cardboard case
    handle_min_bottle: float = 0.07   # 4 s  — single large bottle
    handle_min_unit: float = 0.08     # 5 s  — generic single unit
    handle_min_can: float = 0.04      # 2.5 s — small, lightweight

    # Fixed setup cost added once per PickupReturn (driver greets
    # client, opens lateral tarp, positions the empty before lifting).
    pickup_setup_min: float = 0.3

    def handle_min(self, physical_type: str | None) -> float:
        t = (physical_type or "unit").lower()
        if t == "keg":
            return self.handle_min_keg
        if t == "bulk":
            return self.handle_min_bulk
        if t == "weight":
            return self.handle_min_weight
        if t == "case":
            return self.handle_min_case
        if t == "bottle":
            return self.handle_min_bottle
        if t == "can":
            return self.handle_min_can
        return self.handle_min_unit


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
PALLET_HEIGHT_M = 2.40
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
