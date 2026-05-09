"""Postal-code → (lat, lon) deterministic synthetic geocoder.

Anchored on Mollet del Vallès. Same CP → same coordinate, so spatial structure
is preserved at the postal-code level. Suitable for relative algorithm comparison
when real geocoding is unavailable.
"""

from __future__ import annotations

import hashlib
import math

from simulator.config import DEPOT_LAT, DEPOT_LON


_PREFIX_RADIUS_DEG = 0.55
_SUFFIX_RADIUS_DEG = 0.04


def cp_to_coord(cp: str | None) -> tuple[float, float]:
    if not cp:
        return DEPOT_LAT, DEPOT_LON
    cp = str(cp).strip()
    if not cp:
        return DEPOT_LAT, DEPOT_LON
    prefix = cp[:3] if len(cp) >= 3 else cp
    suffix = cp[3:5] if len(cp) >= 5 else cp[len(prefix):]

    big_dlat, big_dlon = _offset(prefix, _PREFIX_RADIUS_DEG)
    small_dlat, small_dlon = _offset(suffix, _SUFFIX_RADIUS_DEG)
    return DEPOT_LAT + big_dlat + small_dlat, DEPOT_LON + big_dlon + small_dlon


def _offset(token: str, radius_deg: float) -> tuple[float, float]:
    if not token:
        return 0.0, 0.0
    seed = hashlib.sha1(token.encode("utf-8")).digest()
    a = int.from_bytes(seed[0:4], "big") / 2**32
    b = int.from_bytes(seed[4:8], "big") / 2**32
    r = radius_deg * math.sqrt(a)
    ang = 2 * math.pi * b
    dlat = r * math.cos(ang)
    dlon = r * math.sin(ang) / math.cos(math.radians(DEPOT_LAT))
    return dlat, dlon


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
