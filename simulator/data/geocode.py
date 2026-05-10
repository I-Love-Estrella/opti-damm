"""Postal-code → (lat, lon) geocoder.

Real centroids for known Catalan CPs are loaded from
`data/postal_codes_es.csv` (bundled with the repo). Unknown CPs fall back
to a deterministic synthetic offset from the depot, hashed from the CP
string. Same CP → same coordinate either way, so spatial structure is
preserved.

Each client gets a small per-id jitter (~1 km) so multiple businesses in
the same CP don't all collapse onto the centroid in the visualiser.
"""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from simulator.config import DATA_DIR, DEPOT_LAT, DEPOT_LON


_PREFIX_RADIUS_DEG = 0.55
_SUFFIX_RADIUS_DEG = 0.04
_CLIENT_JITTER_RADIUS_DEG = 0.012  # ~1.3 km — same neighbourhood, distinct points


def _normalize_cp(cp: str | None) -> str | None:
    """Spanish CPs are 5 digits; the source file drops leading zeros for
    08xxx codes. Pad back to 5 chars for matching."""

    if cp is None:
        return None
    s = str(cp).strip()
    if not s:
        return None
    if s.isdigit():
        return s.zfill(5)
    return s


def _load_real_coords() -> dict[str, tuple[float, float]]:
    """Read the bundled CSV once at import time. Missing file → empty
    dict (we silently fall back to the synthetic geocoder)."""

    path = Path(DATA_DIR) / "postal_codes_es.csv"
    if not path.exists():
        return {}
    out: dict[str, tuple[float, float]] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            cp = _normalize_cp(row.get("cp"))
            if cp is None:
                continue
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            out[cp] = (lat, lon)
    return out


_REAL_COORDS: dict[str, tuple[float, float]] = _load_real_coords()


def cp_to_coord(cp: str | None, client_id: str | None = None) -> tuple[float, float]:
    """Resolve a CP (and optional client_id for jitter) to (lat, lon).

    1. If the CP is in the bundled real-centroid table → use it +
       per-client jitter (~1 km).
    2. Otherwise fall back to the deterministic synthetic geocoder
       (hash the CP into an offset from the depot). Anchored on
       Mollet del Vallès (DEPOT_LAT, DEPOT_LON).
    """

    norm = _normalize_cp(cp)
    if norm and norm in _REAL_COORDS:
        base_lat, base_lon = _REAL_COORDS[norm]
        if client_id:
            jitter_lat, jitter_lon = _offset(client_id, _CLIENT_JITTER_RADIUS_DEG)
            return base_lat + jitter_lat, base_lon + jitter_lon
        return base_lat, base_lon

    # ---- Synthetic fallback (same logic as before for unknown CPs) ----
    if not cp:
        if client_id:
            jitter_lat, jitter_lon = _offset(client_id, _CLIENT_JITTER_RADIUS_DEG)
            return DEPOT_LAT + jitter_lat, DEPOT_LON + jitter_lon
        return DEPOT_LAT, DEPOT_LON
    cp_str = str(cp).strip()
    if not cp_str:
        return DEPOT_LAT, DEPOT_LON
    prefix = cp_str[:3] if len(cp_str) >= 3 else cp_str
    suffix = cp_str[3:5] if len(cp_str) >= 5 else cp_str[len(prefix):]

    big_dlat, big_dlon = _offset(prefix, _PREFIX_RADIUS_DEG)
    small_dlat, small_dlon = _offset(suffix, _SUFFIX_RADIUS_DEG)
    jitter_lat, jitter_lon = (0.0, 0.0)
    if client_id:
        jitter_lat, jitter_lon = _offset(client_id, _CLIENT_JITTER_RADIUS_DEG)
    return (
        DEPOT_LAT + big_dlat + small_dlat + jitter_lat,
        DEPOT_LON + big_dlon + small_dlon + jitter_lon,
    )


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
