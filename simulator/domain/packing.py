"""3D bin-packing utility for ALGORITHMS only.

The simulator never calls this module — all positioning logic lives on the
algorithm side. This is a shared helper so each algorithm doesn't reinvent
extreme-point packing.

Pallet corner system:
  X axis — along the long pallet side (1.20 m).
  Y axis — depth: 0 at the truck-edge side, increasing toward the cab.
  Z axis — height: 0 at the pallet floor, up.

`find_position` runs a greedy "extreme points" 3D bin-packer. It tests
anchor points at the corners of currently-loaded items plus (0, 0, 0) and
picks the lowest valid (z, y, x) anchor with no AABB overlap.
"""

from __future__ import annotations

from simulator.config import (
    PALLET_HEIGHT_M,
    PALLET_LENGTH_M,
    PALLET_WIDTH_M,
)
from simulator.domain.pallet import PalletItem


_BBOX_EPS = 1e-6


def _aabb_overlaps(
    a: tuple[float, float, float, float, float, float],
    b: tuple[float, float, float, float, float, float],
) -> bool:
    return (
        a[0] < b[3] - _BBOX_EPS
        and b[0] < a[3] - _BBOX_EPS
        and a[1] < b[4] - _BBOX_EPS
        and b[1] < a[4] - _BBOX_EPS
        and a[2] < b[5] - _BBOX_EPS
        and b[2] < a[5] - _BBOX_EPS
    )


def find_position(
    items: list[PalletItem],
    dim_x: float,
    dim_y: float,
    dim_h: float,
    *,
    enforce_pallet_height: bool = False,
    aspect_limit: float | None = None,
    y_min: float = 0.0,
    y_max: float = PALLET_WIDTH_M,
) -> tuple[float, float, float] | None:
    """Find an anchor for a new (dim_x, dim_y, dim_h) box on a pallet.

    Args:
      items: items already on the pallet.
      dim_x, dim_y, dim_h: extents of the new box (metres).
      enforce_pallet_height: if True, refuse anchors where the box top
        would exceed PALLET_HEIGHT_M and return None instead.
      aspect_limit: max allowed `top_z / min_footprint_side` for the
        resulting stack — rejects narrow-tower anchors. Floor anchors
        (z = 0) are exempt because a single layer can never tip.
      y_min: minimum allowed pos_y for the anchor — used by LIFO-style
        algorithms that confine a client's items to a depth band.
      y_max: maximum allowed `pos_y + dim_y`. Defaults to the pallet width.

    Returns the (x, y, z) of the bottom-left-front corner, or None when
    no fit exists (only possible with strict flags).
    """

    y_max_eff = min(y_max, PALLET_WIDTH_M)
    y_min_eff = max(0.0, y_min)
    anchors: list[tuple[float, float, float]] = [(0.0, y_min_eff, 0.0)]
    for it in items:
        if it.qty <= 0:
            continue
        anchors.append((it.end_x, max(it.pos_y, y_min_eff), it.pos_z))
        anchors.append((it.pos_x, max(it.end_y, y_min_eff), it.pos_z))
        anchors.append((it.pos_x, max(it.pos_y, y_min_eff), it.top_z))

    narrow = min(dim_x, dim_y)

    seen: set[tuple[float, float, float]] = set()
    best: tuple[float, float, float] | None = None
    for (x, y, z) in anchors:
        key = (round(x, 4), round(y, 4), round(z, 4))
        if key in seen:
            continue
        seen.add(key)
        if x + dim_x > PALLET_LENGTH_M + _BBOX_EPS:
            continue
        if y < y_min_eff - _BBOX_EPS:
            continue
        if y + dim_y > y_max_eff + _BBOX_EPS:
            continue
        if enforce_pallet_height and z + dim_h > PALLET_HEIGHT_M + _BBOX_EPS:
            continue
        if (
            aspect_limit is not None
            and z > _BBOX_EPS
            and narrow > 0
            and (z + dim_h) / narrow > aspect_limit + _BBOX_EPS
        ):
            continue
        new_box = (x, y, z, x + dim_x, y + dim_y, z + dim_h)
        if any(_aabb_overlaps(new_box, it.aabb()) for it in items if it.qty > 0):
            continue
        if best is None or (z, y, x) < (best[2], best[1], best[0]):
            best = (x, y, z)

    if (
        best is None
        and not enforce_pallet_height
        and aspect_limit is None
        and y_min_eff == 0.0
        and y_max_eff >= PALLET_WIDTH_M - _BBOX_EPS
    ):
        max_z = max((it.top_z for it in items if it.qty > 0), default=0.0)
        best = (0.0, 0.0, max_z)
    return best
