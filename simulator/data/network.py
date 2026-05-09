"""Distance / time matrix between locations.

Pure haversine × road_factor. Lazy: pairs are cached on demand.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.config import DEFAULT_TIME_MODEL, TimeModel
from simulator.data.geocode import haversine_km


@dataclass(frozen=True)
class Leg:
    distance_km: float
    duration_min: float


class Network:
    def __init__(self, time_model: TimeModel = DEFAULT_TIME_MODEL):
        self._tm = time_model
        self._cache: dict[tuple[float, float, float, float], Leg] = {}

    def leg(self, a: tuple[float, float], b: tuple[float, float]) -> Leg:
        key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        straight = haversine_km(a, b)
        km = straight * self._tm.road_factor
        speed = self._tm.avg_speed_kmh_urban if km < 8 else self._tm.avg_speed_kmh_interurban
        minutes = (km / max(speed, 1.0)) * 60.0
        leg = Leg(distance_km=km, duration_min=minutes)
        self._cache[key] = leg
        return leg
