"""Per-algorithm restock + returnable-placement strategies.

Each loading algorithm has its own opinion about where lifted blockers
and incoming empties should go. Sharing one global helper hides those
opinions; this module makes them explicit.

Every strategy operates on the algorithm-side `VirtualTruck` shadow and
emits commands. The simulator just applies whatever pos/dim arrive.

Strategies provided:

  - `FloorFirstStrategy`   — empties land on the first slot with free
    floor space; lifted blockers go to the most compact stable anchor.
    Generic baseline used by replay/nearest.
  - `BalancedStrategy`     — like FloorFirst but, when several candidate
    slots have free floor, picks the one that keeps the truck COG
    closest to the ideal centre. Used by `BalancedLoader`.
  - `LifoBandStrategy`     — empties land in the same depth band the
    client owned on their pallet, so `search_moves` stays at zero.
    Used by `LifoArchitect`.

Algorithms can subclass these or write their own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from simulator.algorithms.virtual_truck import (
    KEG_MAX_STACK,
    STACK_RATIO,
    VirtualTruck,
    _find_floor_position,
)
from simulator.config import PALLET_HEIGHT_M, PALLET_WIDTH_M
from simulator.domain.commands import Command, PickupReturn, RestockItem
from simulator.domain.packing import find_position
from simulator.domain.pallet import PalletItem


def _find_position_on_empties(
    items: list[PalletItem],
    dim_x: float,
    dim_y: float,
    stack_h: float,
) -> tuple[float, float, float] | None:
    """Find a position for a new empty keg that stacks ONLY on top of
    other empties (or on the floor at a corner of an empty). Returning
    None forces the caller to try a different slot or fall back."""
    from simulator.config import PALLET_HEIGHT_M, PALLET_LENGTH_M, PALLET_WIDTH_M

    eps = 1e-6
    live = [it for it in items if it.qty > 0]
    empties = [it for it in live if it.is_returnable_empty]
    # Anchors: top-of-empty (perched) + corners-beside-empty (floor).
    anchors: list[tuple[float, float, float]] = []
    for it in empties:
        anchors.append((it.pos_x, it.pos_y, it.top_z))
        anchors.append((it.end_x, it.pos_y, it.pos_z))
        anchors.append((it.pos_x, it.end_y, it.pos_z))

    seen: set[tuple[float, float, float]] = set()
    best: tuple[float, float, float] | None = None
    for (x, y, z) in anchors:
        key = (round(x, 4), round(y, 4), round(z, 4))
        if key in seen:
            continue
        seen.add(key)
        if x < -eps or y < -eps or z < -eps:
            continue
        if x + dim_x > PALLET_LENGTH_M + eps:
            continue
        if y + dim_y > PALLET_WIDTH_M + eps:
            continue
        if z + stack_h > PALLET_HEIGHT_M + eps:
            continue
        # 3D collision against all live items (incl. non-empty).
        new_box = (x, y, z, x + dim_x, y + dim_y, z + stack_h)
        collides = False
        for it in live:
            if (
                new_box[0] < it.end_x - eps
                and it.pos_x < new_box[3] - eps
                and new_box[1] < it.end_y - eps
                and it.pos_y < new_box[4] - eps
                and new_box[2] < it.top_z - eps
                and it.pos_z < new_box[5] - eps
            ):
                collides = True
                break
        if collides:
            continue
        # If perched (z > 0), require ≥50% support coverage AND require
        # the supporter(s) to be empties.
        if z > eps:
            base = dim_x * dim_y
            covered = 0.0
            covered_by_empty = 0.0
            for it in live:
                if abs(it.top_z - z) > eps:
                    continue
                ox = max(0.0, min(x + dim_x, it.end_x) - max(x, it.pos_x))
                oy = max(0.0, min(y + dim_y, it.end_y) - max(y, it.pos_y))
                area = ox * oy
                if area <= 0:
                    continue
                covered += area
                if it.is_returnable_empty:
                    covered_by_empty += area
            if covered < 0.5 * base:
                continue
            if covered_by_empty < covered - eps:
                # Some supporter is not an empty → reject (would float
                # later when the cargo item gets delivered).
                continue
        if best is None or (z, y, x) < (best[2], best[1], best[0]):
            best = (x, y, z)
    return best


@dataclass(frozen=True)
class _RestockContext:
    """All info a strategy may need to pick spots for a single stop."""
    client_id: str
    primary_slot: str
    candidate_slots: list[str]


class RestockStrategy:
    """Base interface — pluggable per-algorithm.

    Two extension points:
      - `place_empties(...)` — emit PickupReturn commands.
      - `plan_restock(...)`  — return RestockItem list for foreign blockers.
    """

    def place_empties(
        self,
        cmds: list[Command],
        vt: VirtualTruck,
        ctx: _RestockContext,
        total_units: float,
        unit_dx: float,
        unit_dy: float,
        unit_dh: float,
    ) -> None:
        """Default — delegate to VirtualTruck's generic helper."""
        vt.emit_returnables(
            cmds,
            ctx.primary_slot,
            ctx.client_id,
            total_units,
            unit_dx,
            unit_dy,
            unit_dh,
            candidate_slots=ctx.candidate_slots,
        )

    def plan_restock(
        self,
        vt: VirtualTruck,
        slot_id: str,
        target_items: list[PalletItem],
        same_client_items: list[PalletItem],
        foreign_blockers: list[PalletItem],
    ) -> list[RestockItem]:
        return vt.plan_restock(
            slot_id, target_items, same_client_items, foreign_blockers,
            aspect_limit=STACK_RATIO,
        )


# ---- Floor-first (replay / nearest baseline) -------------------------


class FloorFirstStrategy(RestockStrategy):
    """The default — empties land on the first slot with floor space."""


# ---- Balanced (COG-aware) --------------------------------------------


class BalancedStrategy(RestockStrategy):
    """Pick the candidate that keeps truck COG closest to the ideal."""

    IDEAL_X = 0.52
    IDEAL_Y = 0.50

    def __init__(self, slot_centers: dict[str, tuple[float, float]]):
        # slot_id → normalized (x, y) of slot's centre on the truck.
        self._centers = slot_centers

    def place_empties(
        self,
        cmds: list[Command],
        vt: VirtualTruck,
        ctx: _RestockContext,
        total_units: float,
        unit_dx: float,
        unit_dy: float,
        unit_dh: float,
    ) -> None:
        if total_units < 0.3:
            return
        remaining = float(round(total_units))
        if remaining <= 0:
            return
        max_per_stack = float(KEG_MAX_STACK)

        candidates = list(dict.fromkeys(
            [ctx.primary_slot, *ctx.candidate_slots]
        ))

        while remaining > 0:
            take = min(remaining, max_per_stack)
            stack_h = take * unit_dh
            placement = self._best_placement(
                vt, candidates, unit_dx, unit_dy, take, unit_dh
            )
            if placement is None and take > 1:
                take = 1.0
                stack_h = unit_dh
                placement = self._best_placement(
                    vt, candidates, unit_dx, unit_dy, take, unit_dh
                )
            if placement is None:
                remaining -= take
                continue
            chosen_slot, pos = placement
            cmds.append(
                PickupReturn(
                    client_id=ctx.client_id,
                    sku="EMPTY",
                    qty=take,
                    slot_id=chosen_slot,
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                )
            )
            vt.add(
                chosen_slot,
                PalletItem(
                    sku="EMPTY",
                    qty=take,
                    unit_volume_m3=0.04,
                    unit_weight_kg=2.0,
                    intended_client=None,
                    is_returnable_empty=True,
                    physical_type="keg",
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                ),
            )
            remaining -= take

    def _best_placement(
        self,
        vt: VirtualTruck,
        candidates: list[str],
        dim_x: float,
        dim_y: float,
        qty: float,
        dim_h_unit: float,
    ) -> tuple[str, tuple[float, float, float]] | None:
        stack_h = qty * dim_h_unit
        kg_added = qty * 10.0  # a keg ≈ 10 kg empty
        # Pass 1 — only floor anchors, ranked by COG.
        scored: list[tuple[float, str, tuple[float, float, float]]] = []
        for sid in candidates:
            pos = _find_floor_position(
                vt.items(sid), dim_x, dim_y, stack_h
            )
            if pos is None or pos[2] + stack_h > PALLET_HEIGHT_M + 1e-6:
                continue
            scored.append((self._cog_penalty(vt, sid, kg_added), sid, pos))
        if scored:
            scored.sort(key=lambda s: s[0])
            return (scored[0][1], scored[0][2])
        # Pass 2 — stack ONLY on other empties. Stacking on a
        # non-empty cargo item leaves the empty floating once that
        # cargo gets delivered to its client (visible as repeated
        # SETTLE events in the validator output).
        for sid in candidates:
            pos = _find_position_on_empties(
                vt.items(sid), dim_x, dim_y, stack_h
            )
            if pos is not None:
                return (sid, pos)
        # Pass 3 — last resort: any stable stack. The simulator's
        # settle pass will move the empty when its supporter goes.
        for sid in candidates:
            pos = find_position(
                vt.items(sid),
                dim_x,
                dim_y,
                stack_h,
                enforce_pallet_height=True,
                aspect_limit=STACK_RATIO,
            )
            if pos is not None:
                return (sid, pos)
        return None

    def _cog_penalty(
        self, vt: VirtualTruck, slot_id: str, added_kg: float
    ) -> float:
        """Distance² of new truck COG from (IDEAL_X, IDEAL_Y) if we
        drop `added_kg` into `slot_id`."""
        sx = sy = total = 0.0
        for sid, items in vt.snapshot().items():
            w = sum(it.qty * it.unit_weight_kg for it in items if it.qty > 0)
            if sid == slot_id:
                w += added_kg
            if w <= 0:
                continue
            cx, cy = self._centers.get(sid, (0.5, 0.5))
            sx += cx * w
            sy += cy * w
            total += w
        if total <= 0:
            return 0.0
        sx /= total
        sy /= total
        dx = sx - self.IDEAL_X
        dy = sy - self.IDEAL_Y
        # Y matters more (rollover risk).
        return dx * dx + 4.0 * dy * dy


# ---- LIFO band (lifo) ------------------------------------------------


class LifoBandStrategy(RestockStrategy):
    """Empties land in the *same* depth band on the client's pallet
    where the algorithm just took the cargo from. Keeps the LIFO
    invariant: first-visited client owns y < N's y_min, so the door
    edge is empty by the time we hand him the empties."""

    def __init__(self, client_bands: dict[str, list[tuple[str, float, float]]]):
        # client_id → list of (slot_id, y_start, y_end) bands.
        self._bands = client_bands

    def place_empties(
        self,
        cmds: list[Command],
        vt: VirtualTruck,
        ctx: _RestockContext,
        total_units: float,
        unit_dx: float,
        unit_dy: float,
        unit_dh: float,
    ) -> None:
        if total_units < 0.3:
            return
        remaining = float(round(total_units))
        if remaining <= 0:
            return
        max_per_stack = float(KEG_MAX_STACK)

        # Try the client's own bands first (in route order); fall back
        # to any candidate slot.
        client_bands = self._bands.get(ctx.client_id, [])
        slot_priority: list[str] = []
        seen: set[str] = set()
        for sid, _, _ in client_bands:
            if sid not in seen:
                slot_priority.append(sid)
                seen.add(sid)
        for sid in (ctx.primary_slot, *ctx.candidate_slots):
            if sid not in seen:
                slot_priority.append(sid)
                seen.add(sid)

        while remaining > 0:
            take = min(remaining, max_per_stack)
            stack_h = take * unit_dh
            placement = self._first_band_fit(
                vt, ctx.client_id, slot_priority,
                unit_dx, unit_dy, stack_h,
            )
            if placement is None and take > 1:
                take = 1.0
                stack_h = unit_dh
                placement = self._first_band_fit(
                    vt, ctx.client_id, slot_priority,
                    unit_dx, unit_dy, stack_h,
                )
            if placement is None:
                # Last resort — generic floor search.
                placement = vt._first_floor_slot(
                    slot_priority, unit_dx, unit_dy, stack_h
                )
            if placement is None:
                remaining -= take
                continue
            chosen_slot, pos = placement
            cmds.append(
                PickupReturn(
                    client_id=ctx.client_id,
                    sku="EMPTY",
                    qty=take,
                    slot_id=chosen_slot,
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                )
            )
            vt.add(
                chosen_slot,
                PalletItem(
                    sku="EMPTY",
                    qty=take,
                    unit_volume_m3=0.04,
                    unit_weight_kg=2.0,
                    intended_client=None,
                    is_returnable_empty=True,
                    physical_type="keg",
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                ),
            )
            remaining -= take

    def _first_band_fit(
        self,
        vt: VirtualTruck,
        client_id: str,
        slot_priority: list[str],
        dim_x: float,
        dim_y: float,
        stack_h: float,
    ) -> tuple[str, tuple[float, float, float]] | None:
        """Find a floor anchor inside one of the client's depth bands."""
        bands = self._bands.get(client_id, [])
        for sid, y_min, y_max in bands:
            pos = find_position(
                vt.items(sid),
                dim_x,
                dim_y,
                stack_h,
                enforce_pallet_height=True,
                aspect_limit=STACK_RATIO,
                y_min=y_min,
                y_max=y_max,
            )
            if pos is not None:
                return (sid, pos)
        return None
