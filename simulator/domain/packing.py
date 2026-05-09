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
picks the lowest valid (z, y, x) anchor that:
  - doesn't 3D-overlap any existing item
  - has its base supported (≥ MIN_SUPPORT_FRACTION coverage by items below)
  - won't crush a fragile/lighter item underneath
"""

from __future__ import annotations

from simulator.config import (
    PALLET_HEIGHT_M,
    PALLET_LENGTH_M,
    PALLET_WIDTH_M,
)
from simulator.domain.pallet import PalletItem


_BBOX_EPS = 1e-6
# An item perched above z=0 must rest on supporters whose total xy
# overlap with its base is at least this fraction. Matches the
# simulator's runtime physics check (`MIN_SUPPORT_FRACTION = 0.50`).
MIN_SUPPORT_FRACTION = 0.50
# When stacking, the upper item's per-unit weight must not exceed
# CRUSH_MAX_WEIGHT_RATIO × the supporter's per-unit weight.
# Items lighter than CRUSH_LIGHT_KG are exempt — a tiny can on a thin
# bottle isn't actually crushing anything.
CRUSH_MAX_WEIGHT_RATIO = 3.0
CRUSH_LIGHT_KG = 5.0


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


def _support_coverage(
    pos_x: float,
    pos_y: float,
    pos_z: float,
    dim_x: float,
    dim_y: float,
    items: list[PalletItem],
) -> float:
    """Fraction of the new item's xy base that's directly supported by
    items whose top_z meets pos_z. 1.0 means fully supported, 0.0
    means floating in mid-air. Floor anchors (pos_z≈0) are always 1."""
    if pos_z < _BBOX_EPS:
        return 1.0
    base = dim_x * dim_y
    if base <= 0:
        return 0.0
    end_x = pos_x + dim_x
    end_y = pos_y + dim_y
    covered = 0.0
    for it in items:
        if it.qty <= 0:
            continue
        if abs(it.top_z - pos_z) > _BBOX_EPS:
            continue
        ox = max(0.0, min(end_x, it.end_x) - max(pos_x, it.pos_x))
        oy = max(0.0, min(end_y, it.end_y) - max(pos_y, it.pos_y))
        covered += ox * oy
    return covered / base


def _stack_min_narrow(
    pos_x: float,
    pos_y: float,
    dim_x: float,
    dim_y: float,
    items: list[PalletItem],
) -> float:
    """Smallest narrow side (`min(dim_x, dim_y)`) of any item whose
    footprint overlaps `(pos_x, pos_y, dim_x, dim_y)`. Used by the
    aspect-ratio check to evaluate the WHOLE column, not just the
    new item — matches the validator's `STACK_UNSTABLE` rule.
    Returns the new item's own narrow side as a fallback when there
    are no overlapping items below."""
    end_x = pos_x + dim_x
    end_y = pos_y + dim_y
    narrowest = min(dim_x, dim_y)
    for it in items:
        if it.qty <= 0:
            continue
        ox = max(0.0, min(end_x, it.end_x) - max(pos_x, it.pos_x))
        oy = max(0.0, min(end_y, it.end_y) - max(pos_y, it.pos_y))
        if ox <= 0 or oy <= 0:
            continue
        narrowest = min(narrowest, min(it.dim_x, it.dim_y))
    return narrowest


def _would_crush(
    pos_x: float,
    pos_y: float,
    pos_z: float,
    dim_x: float,
    dim_y: float,
    unit_weight_kg: float,
    items: list[PalletItem],
) -> bool:
    """True if placing an item with the given unit weight at the given
    pos would land it on a noticeably lighter item directly below.
    Used to keep the packer from creating CRUSH_RISK situations."""
    if pos_z < _BBOX_EPS or unit_weight_kg < CRUSH_LIGHT_KG:
        return False
    end_x = pos_x + dim_x
    end_y = pos_y + dim_y
    for it in items:
        if it.qty <= 0:
            continue
        if abs(it.top_z - pos_z) > _BBOX_EPS:
            continue
        ox = max(0.0, min(end_x, it.end_x) - max(pos_x, it.pos_x))
        oy = max(0.0, min(end_y, it.end_y) - max(pos_y, it.pos_y))
        if ox <= 0 or oy <= 0:
            continue
        lower_w = float(it.unit_weight_kg or 0.0)
        if lower_w <= 0:
            continue
        if unit_weight_kg > CRUSH_MAX_WEIGHT_RATIO * lower_w:
            return True
    return False


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
    require_support: bool = False,
    min_support_fraction: float | None = None,
    unit_weight_kg: float = 0.0,
    avoid_crush: bool = False,
    prefer_max_support: bool = False,
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
      require_support: when True (default) reject anchors above z=0 that
        don't have ≥ `MIN_SUPPORT_FRACTION` of the new item's base
        covered by items directly below. Without this the packer
        emits FLOATING / UNSTABLE_OVERHANG positions that the
        validator immediately rejects.
      unit_weight_kg: per-unit weight of the new item — used by the
        crush check.
      avoid_crush: when True (default) reject anchors that would land
        a heavy item on a noticeably lighter one (matches the
        validator's CRUSH_RISK rule).

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
    best_score: tuple | None = None
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
        ):
            # Check the WHOLE column's narrow side, not just the
            # new item's. A wide item perched on a thin base is
            # what the validator's STACK_UNSTABLE catches.
            col_narrow = _stack_min_narrow(x, y, dim_x, dim_y, items)
            col_narrow = max(col_narrow, 1e-6)
            stack_top = z + dim_h
            stack_floor = 0.0
            for it in items:
                if it.qty <= 0:
                    continue
                ox = max(0.0, min(x + dim_x, it.end_x) - max(x, it.pos_x))
                oy = max(0.0, min(y + dim_y, it.end_y) - max(y, it.pos_y))
                if ox > 0 and oy > 0:
                    stack_floor = min(stack_floor, it.pos_z)
            stack_height = stack_top - stack_floor
            if stack_height / col_narrow > aspect_limit + _BBOX_EPS:
                continue
        new_box = (x, y, z, x + dim_x, y + dim_y, z + dim_h)
        if any(_aabb_overlaps(new_box, it.aabb()) for it in items if it.qty > 0):
            continue
        if require_support and z > _BBOX_EPS:
            coverage = _support_coverage(x, y, z, dim_x, dim_y, items)
            threshold = (
                min_support_fraction
                if min_support_fraction is not None
                else MIN_SUPPORT_FRACTION
            )
            if coverage < threshold:
                continue
        if avoid_crush and _would_crush(
            x, y, z, dim_x, dim_y, unit_weight_kg, items
        ):
            continue
        if prefer_max_support:
            # Score: maximize support, then minimize (z, y, x).
            # Floor anchors get coverage=1.0 (perfectly supported).
            cov = _support_coverage(x, y, z, dim_x, dim_y, items)
            score = (-cov, z, y, x)
            if best_score is None or score < best_score:
                best_score = score
                best = (x, y, z)
        else:
            if best is None or (z, y, x) < (best[2], best[1], best[0]):
                best = (x, y, z)

    if (
        best is None
        and not enforce_pallet_height
        and aspect_limit is None
        and not require_support
        and y_min_eff == 0.0
        and y_max_eff >= PALLET_WIDTH_M - _BBOX_EPS
    ):
        max_z = max((it.top_z for it in items if it.qty > 0), default=0.0)
        best = (0.0, 0.0, max_z)
    return best
