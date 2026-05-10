"""Algorithm-side shadow truck.

Algorithms own the geometry of the plan — including positions for
returnables (`PickupReturn`). To pick a sane anchor for empties, the
algorithm has to know what is still on the pallet at the moment of
pickup, i.e. *after* the deliveries at that stop have popped items off.

`VirtualTruck` mirrors the simulator's pallet state during command
emission. It is updated as the algorithm walks its plan:

  - `add` after every `Pick` (item lands on pallet).
  - `remove` for every `Unload` (client takes their item).
  - `find_position` for every `PickupReturn` to choose where the empties
    physically fit on the now partially-empty pallet.

The simulator never sees this object. It just executes the verbatim
coordinates the algorithm emitted.
"""

from __future__ import annotations

from collections import defaultdict

from simulator.config import PALLET_HEIGHT_M, PALLET_LENGTH_M, PALLET_WIDTH_M
from simulator.domain.commands import (
    Command,
    LiftedItem,
    PickupReturn,
    RestockItem,
)
from simulator.domain.packing import find_position
from simulator.domain.pallet import PalletItem


KEG_MAX_STACK = 1  # max kegs per single PickupReturn stack —
# always pick empties one at a time so each placement gets its own
# pos check and overlap snap. Two kegs glued into one PalletItem
# would render as a tall stack rather than two distinct pickups.
STACK_RATIO = 3.5  # aspect-ratio cap for stacked returnables


_BBOX_EPS = 1e-6


def _aabb_xy_overlaps(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return (
        a[0] < b[2] - _BBOX_EPS
        and b[0] < a[2] - _BBOX_EPS
        and a[1] < b[3] - _BBOX_EPS
        and b[1] < a[3] - _BBOX_EPS
    )


def _find_floor_position(
    items: list[PalletItem],
    dim_x: float,
    dim_y: float,
    dim_h: float,
) -> tuple[float, float, float] | None:
    """Pick a corner-anchor at z=0 that doesn't 3D-overlap any existing
    item. We look at the FULL z-range [0, dim_h] of the candidate, not
    just the floor — an item perched at pos_z=0.4 still occupies the
    floor's projection, and dropping a 1.30 m stack at z=0 underneath
    would phase right through it."""

    live = [it for it in items if it.qty > 0]
    # Anchor seeds = floor items' XY corners + (0,0) and corners of any
    # item whose footprint touches the floor projection.
    anchors: list[tuple[float, float]] = [(0.0, 0.0)]
    for it in live:
        anchors.append((it.end_x, it.pos_y))
        anchors.append((it.pos_x, it.end_y))
        anchors.append((it.end_x, it.end_y))

    seen: set[tuple[float, float]] = set()
    best: tuple[float, float] | None = None
    for (x, y) in anchors:
        key = (round(x, 4), round(y, 4))
        if key in seen:
            continue
        seen.add(key)
        if x + dim_x > PALLET_LENGTH_M + _BBOX_EPS:
            continue
        if y + dim_y > PALLET_WIDTH_M + _BBOX_EPS:
            continue
        new_box = (x, y, 0.0, x + dim_x, y + dim_y, dim_h)
        # Reject if the candidate's full 3D AABB collides with ANY
        # existing item — including ones perched above the floor whose
        # footprint we'd phase through if we dropped a tall stack here.
        collides = False
        for it in live:
            if (
                new_box[0] < it.end_x - _BBOX_EPS
                and it.pos_x < new_box[3] - _BBOX_EPS
                and new_box[1] < it.end_y - _BBOX_EPS
                and it.pos_y < new_box[4] - _BBOX_EPS
                and new_box[2] < it.top_z - _BBOX_EPS
                and it.pos_z < new_box[5] - _BBOX_EPS
            ):
                collides = True
                break
        if collides:
            continue
        if best is None or (y, x) < best:
            best = (y, x)
    if best is None:
        return None
    return (best[1], best[0], 0.0)


class VirtualTruck:
    def __init__(self) -> None:
        self._by_slot: dict[str, list[PalletItem]] = defaultdict(list)

    def add(self, slot_id: str, item: PalletItem) -> None:
        self._by_slot[slot_id].append(item)

    def items(self, slot_id: str) -> list[PalletItem]:
        return list(self._by_slot.get(slot_id, []))

    def snapshot(self) -> dict[str, list[PalletItem]]:
        """Read-only view of all slots (for strategies that need COG / weight totals)."""
        return {sid: list(items) for sid, items in self._by_slot.items()}

    def remove(
        self,
        slot_id: str,
        sku: str,
        qty: float,
        client: str | None,
    ) -> None:
        """Mirror `Pallet.remove_item` — remove `qty` of (sku, client).

        Falls back to ignoring client when the strict (sku, client) match
        finds nothing, matching the simulator's behaviour.
        """

        items = self._by_slot.get(slot_id)
        if not items:
            return

        remaining = self._consume(items, sku, qty, client)
        if remaining > 1e-9 and client is not None:
            # Try again ignoring intended_client (matches simulator fallback).
            self._consume(items, sku, remaining, None)

    def _consume(
        self,
        items: list[PalletItem],
        sku: str,
        qty: float,
        client: str | None,
    ) -> float:
        remaining = qty
        new_items: list[PalletItem] = []
        for it in items:
            if (
                remaining > 0
                and it.qty > 0
                and it.sku == sku
                and (client is None or it.intended_client == client)
            ):
                take = min(it.qty, remaining)
                remaining -= take
                if take >= it.qty - 1e-9:
                    continue
                kept_qty = it.qty - take
                ratio = kept_qty / it.qty if it.qty > 0 else 0.0
                kept = PalletItem(
                    sku=it.sku,
                    qty=kept_qty,
                    unit_volume_m3=it.unit_volume_m3,
                    unit_weight_kg=it.unit_weight_kg,
                    intended_client=it.intended_client,
                    is_returnable_empty=it.is_returnable_empty,
                    physical_type=it.physical_type,
                    pos_x=it.pos_x,
                    pos_y=it.pos_y,
                    pos_z=it.pos_z,
                    dim_x=it.dim_x,
                    dim_y=it.dim_y,
                    dim_h=it.dim_h * ratio,
                )
                new_items.append(kept)
                continue
            new_items.append(it)
        items[:] = new_items
        return remaining

    def find_position(
        self,
        slot_id: str,
        dim_x: float,
        dim_y: float,
        dim_h: float,
        *,
        floor_only: bool = False,
    ) -> tuple[float, float, float]:
        """Anchor for a new (dim_x, dim_y, dim_h) box on the slot's pallet.

        Always returns a position — falls back to stacking on top so the
        algorithm can always emit a valid command.

        `floor_only=True` rejects every anchor whose z > 0 — used for
        pickup of empties, which a real driver always places on the
        pallet floor next to whatever cargo is left, never on top of a
        foreign client's stack.
        """

        items = self._by_slot.get(slot_id, [])
        if floor_only:
            pos = _find_floor_position(items, dim_x, dim_y, dim_h)
            if pos is not None:
                return pos
            # No floor space — try a stable stack on top.
        pos = find_position(
            items,
            dim_x,
            dim_y,
            dim_h,
            enforce_pallet_height=False,
            aspect_limit=STACK_RATIO,
        )
        if pos is not None:
            return pos
        # Last resort — accept whatever fits, even if unstable. The
        # validator will flag it; better than overlapping at (0,0,0).
        pos = find_position(items, dim_x, dim_y, dim_h, enforce_pallet_height=False)
        return pos if pos is not None else (0.0, 0.0, 0.0)

    def emit_returnables(
        self,
        cmds: list[Command],
        slot_id: str,
        client_id: str,
        total_units: float,
        unit_dx: float,
        unit_dy: float,
        unit_dh: float,
        candidate_slots: list[str] | None = None,
    ) -> None:
        """Emit PickupReturn commands that always stay inside the pallet
        bounding box (pos_z + dim_h ≤ PALLET_HEIGHT_M, pos_x/y inside
        pallet footprint). Strategy per stack:

          1. Find any candidate slot with FLOOR space — preferred.
          2. Failing that, find any candidate slot with a stable stack
             that fits under PALLET_HEIGHT_M (aspect-ratio gated).
          3. If even a 2-keg stack doesn't fit anywhere, downgrade to
             1 keg and retry.
          4. If a single keg still doesn't fit anywhere → drop the
             stack (record nothing). Better to lose track of a few
             empties in the plan than to render them floating in mid-
             air or below the truck floor.
        """

        # Real drivers don't pick up fractional kegs — round up to the
        # nearest whole keg so the visualization always shows full
        # cubes at the canonical keg height (0.65 m). Anything under
        # 0.3 kegs is dropped (negligible).
        if total_units < 0.3:
            return
        remaining = float(round(total_units))
        if remaining <= 0:
            return
        candidates = list(dict.fromkeys([slot_id, *(candidate_slots or [])]))
        max_per_stack = float(KEG_MAX_STACK)

        while remaining > 0:
            take = min(remaining, max_per_stack)
            placement = self._place_returnable_stack(
                candidates, unit_dx, unit_dy, take, unit_dh
            )
            if placement is None and take > 1:
                # Try a smaller stack.
                take = 1.0
                placement = self._place_returnable_stack(
                    candidates, unit_dx, unit_dy, take, unit_dh
                )
            if placement is None:
                # Genuinely no room anywhere on the truck — skip rather
                # than place out of bounds. Caller can detect this via
                # picked_returns vs expected_returnable_units.
                remaining -= take
                continue
            chosen_slot, pos = placement
            stack_h = take * unit_dh
            cmds.append(
                PickupReturn(
                    client_id=client_id,
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
            self.add(
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

    def _place_returnable_stack(
        self,
        candidates: list[str],
        dim_x: float,
        dim_y: float,
        qty: float,
        dim_h_unit: float,
    ) -> tuple[str, tuple[float, float, float]] | None:
        """Try every candidate slot in order: floor first, then stable
        stack (with aspect_limit), enforcing pallet height. Return None
        if nothing fits anywhere."""

        stack_h = qty * dim_h_unit
        # Pass 1 — floor only.
        for sid in candidates:
            pos = _find_floor_position(
                self._by_slot.get(sid, []), dim_x, dim_y, stack_h
            )
            if pos is None:
                continue
            if pos[2] + stack_h <= PALLET_HEIGHT_M + 1e-6:
                return (sid, pos)
        # Pass 2 — stable stack with aspect_limit and enforced height.
        for sid in candidates:
            pos = find_position(
                self._by_slot.get(sid, []),
                dim_x,
                dim_y,
                stack_h,
                enforce_pallet_height=True,
                aspect_limit=STACK_RATIO,
            )
            if pos is not None:
                return (sid, pos)
        # Pass 3 — anywhere within pallet height (drop aspect rule).
        for sid in candidates:
            pos = find_position(
                self._by_slot.get(sid, []),
                dim_x,
                dim_y,
                stack_h,
                enforce_pallet_height=True,
            )
            if pos is not None:
                return (sid, pos)
        return None

    @staticmethod
    def to_lifts(items: list[PalletItem]) -> list[LiftedItem]:
        """Convert pallet items into algorithm→simulator lift orders."""
        return [
            LiftedItem(
                sku=it.sku,
                qty=it.qty,
                intended_client=it.intended_client,
                pos_x=it.pos_x,
                pos_y=it.pos_y,
                pos_z=it.pos_z,
            )
            for it in items
        ]

    def find_blockers(
        self,
        slot_id: str,
        targets: list[tuple[str, str | None]],
    ) -> tuple[list[PalletItem], list[PalletItem], list[PalletItem]]:
        """Mirror the simulator's blocker logic.

        Given the (sku, client) keys the algorithm plans to take from
        this slot, classify every other item on the pallet as:
          - target_items: items that will be taken (one per Unload).
          - same_client: blockers belonging to the same client — they'll
            be opportunistically delivered (lifted but not restocked).
          - foreign: blockers belonging to a different client — these
            need an algorithm-supplied restock position.
        """

        items = self._by_slot.get(slot_id, [])
        target_items: list[PalletItem] = []
        target_ids: set[int] = set()
        for sku, client in targets:
            for it in items:
                if id(it) in target_ids or it.qty <= 0:
                    continue
                if it.sku == sku and it.intended_client in (None, client):
                    target_items.append(it)
                    target_ids.add(id(it))
                    break

        same_client: list[PalletItem] = []
        foreign: list[PalletItem] = []
        seen_same: set[int] = set()
        seen_foreign: set[int] = set()
        stop_client = targets[0][1] if targets else None

        def add_blocker(it: PalletItem) -> None:
            if id(it) in target_ids:
                return
            if it.intended_client == stop_client:
                if id(it) not in seen_same:
                    seen_same.add(id(it))
                    same_client.append(it)
            else:
                if id(it) not in seen_foreign:
                    seen_foreign.add(id(it))
                    foreign.append(it)

        # Phase 1: collect direct blockers (above-target or edge-blocker
        # at lower y with xz overlap) for every target.
        queue: list[PalletItem] = []
        for tgt in target_items:
            for it in items:
                if id(it) in target_ids or it.qty <= 0:
                    continue
                is_above = (
                    it.pos_z + 1e-6 >= tgt.top_z and it.overlaps_xy(tgt)
                )
                is_edge = (
                    it.pos_y + 1e-6 < tgt.pos_y and it.overlaps_xz(tgt)
                )
                if not (is_above or is_edge):
                    continue
                add_blocker(it)
                queue.append(it)

        # Phase 2: TRANSITIVELY collect items resting on top of any
        # already-recorded blocker. When the simulator lifts a blocker
        # to set it aside, anything physically supported by that
        # blocker must come off first — otherwise the support is
        # whisked away mid-route and the upper item is left hanging.
        # We BFS down the support chain until no new items are added.
        seen_visit = {id(b) for b in queue}
        while queue:
            base = queue.pop()
            for it in items:
                if id(it) in target_ids or id(it) in seen_visit:
                    continue
                if it.qty <= 0:
                    continue
                # "Above base" means it.pos_z is at or above base.top_z
                # AND its footprint overlaps base in xy.
                if it.pos_z + 1e-6 < base.top_z:
                    continue
                if not it.overlaps_xy(base):
                    continue
                seen_visit.add(id(it))
                add_blocker(it)
                queue.append(it)
        return target_items, same_client, foreign

    def plan_restock(
        self,
        slot_id: str,
        target_items: list[PalletItem],
        same_client_items: list[PalletItem],
        foreign_blockers: list[PalletItem],
        *,
        aspect_limit: float = 3.5,
        compact: bool = False,
    ) -> list[RestockItem]:
        """Compute restock positions for foreign blockers after target +
        same-client items are removed.

        Default — `compact=False` — every blocker goes BACK TO ITS ORIGINAL
        position. This matches what a real driver does (nobody re-packs
        the truck mid-route) and is overlap-safe by construction: items
        were compatible at load time, removing some doesn't change that.

        Optional — `compact=True` — re-pack blockers densely into the
        space freed by the deliveries. Useful for runtime-aware
        algorithms that want to pull items toward the pallet floor
        after deliveries; off by default because greedy packing can
        place a blocker into another blocker's vacated cell, producing
        runtime AABB overlaps.
        """

        if not compact:
            # The safe path: keep original positions. Phase 4 in the
            # simulator becomes a logical no-op (lift events still
            # emitted for the time cost), no physical move happens.
            return [
                RestockItem(
                    sku=b.sku,
                    qty=b.qty,
                    intended_client=b.intended_client,
                    pos_x=b.pos_x,
                    pos_y=b.pos_y,
                    pos_z=b.pos_z,
                    dim_x=b.dim_x,
                    dim_y=b.dim_y,
                    dim_h=b.dim_h,
                    physical_type=b.physical_type,
                    unit_volume_m3=b.unit_volume_m3,
                    unit_weight_kg=b.unit_weight_kg,
                    from_pos_x=b.pos_x,
                    from_pos_y=b.pos_y,
                    from_pos_z=b.pos_z,
                )
                for b in foreign_blockers
            ]

        # Compact path — kept for experimentation. Largest blockers go
        # first, leftover tracks the resulting state to avoid overlaps.
        leftover_ids = {id(it) for it in (target_items + same_client_items + foreign_blockers)}
        leftover: list[PalletItem] = [
            it for it in self._by_slot.get(slot_id, [])
            if id(it) not in leftover_ids and it.qty > 0
        ]
        ordered = sorted(
            foreign_blockers,
            key=lambda b: (-b.dim_x * b.dim_y, -b.dim_h),
        )
        new_positions: dict[int, tuple[float, float, float]] = {}
        for b in ordered:
            pos = find_position(
                leftover, b.dim_x, b.dim_y, b.dim_h,
                enforce_pallet_height=False, aspect_limit=aspect_limit,
            )
            if pos is None:
                pos = find_position(
                    leftover, b.dim_x, b.dim_y, b.dim_h,
                    enforce_pallet_height=False,
                )
            # If the packer can't fit it, keep the original — but mark
            # leftover so subsequent blockers see this footprint as taken.
            if pos is None:
                pos = (b.pos_x, b.pos_y, b.pos_z)
            new_positions[id(b)] = pos
            leftover.append(
                PalletItem(
                    sku=b.sku, qty=b.qty,
                    unit_volume_m3=b.unit_volume_m3,
                    unit_weight_kg=b.unit_weight_kg,
                    intended_client=b.intended_client,
                    is_returnable_empty=b.is_returnable_empty,
                    physical_type=b.physical_type,
                    pos_x=pos[0], pos_y=pos[1], pos_z=pos[2],
                    dim_x=b.dim_x, dim_y=b.dim_y, dim_h=b.dim_h,
                )
            )

        return [
            RestockItem(
                sku=b.sku,
                qty=b.qty,
                intended_client=b.intended_client,
                pos_x=new_positions[id(b)][0],
                pos_y=new_positions[id(b)][1],
                pos_z=new_positions[id(b)][2],
                dim_x=b.dim_x,
                dim_y=b.dim_y,
                dim_h=b.dim_h,
                physical_type=b.physical_type,
                unit_volume_m3=b.unit_volume_m3,
                unit_weight_kg=b.unit_weight_kg,
                from_pos_x=b.pos_x,
                from_pos_y=b.pos_y,
                from_pos_z=b.pos_z,
            )
            for b in foreign_blockers
        ]

    def apply_restock(
        self,
        slot_id: str,
        restock: list[RestockItem],
        target_items: list[PalletItem],
        same_client_items: list[PalletItem],
        foreign_blockers: list[PalletItem] | None = None,
    ) -> None:
        """Update the shadow truck after a stop's lift+take+restock cycle:
        remove targets and same-client items, reposition foreign blockers
        according to the restock plan.

        When `foreign_blockers` is provided, restock entries are matched
        to blockers by Python identity (parallel to `restock`). This
        mirrors the simulator's Phase-4 use of `remove_specific(b)` and
        guarantees that two items sharing (sku, client) never get their
        new positions swapped — which used to leave one of them sitting
        on top of the other (visible as `UE902 overlaps UE902 at same
        position` in the validator output).

        Without `foreign_blockers` the function falls back to FIFO
        matching by (sku, client), which is correct only when every
        (sku, client) key is unique on the pallet.
        """

        gone_ids = {id(it) for it in (target_items + same_client_items)}

        if foreign_blockers is not None:
            # Identity-based path: build {id(blocker) → RestockItem}.
            restock_by_id: dict[int, RestockItem] = {}
            for blocker, entry in zip(foreign_blockers, restock):
                restock_by_id[id(blocker)] = entry
            new_items: list[PalletItem] = []
            for it in self._by_slot.get(slot_id, []):
                if id(it) in gone_ids or it.qty <= 0:
                    continue
                r = restock_by_id.get(id(it))
                if r is None:
                    new_items.append(it)
                    continue
                new_items.append(
                    PalletItem(
                        sku=it.sku,
                        qty=it.qty,
                        unit_volume_m3=it.unit_volume_m3,
                        unit_weight_kg=it.unit_weight_kg,
                        intended_client=it.intended_client,
                        is_returnable_empty=it.is_returnable_empty,
                        physical_type=it.physical_type,
                        pos_x=r.pos_x,
                        pos_y=r.pos_y,
                        pos_z=r.pos_z,
                        dim_x=it.dim_x,
                        dim_y=it.dim_y,
                        dim_h=it.dim_h,
                    )
                )
            self._by_slot[slot_id] = new_items
            return

        # Legacy FIFO path.
        queues: dict[tuple[str, str | None], list[RestockItem]] = {}
        for r in restock:
            queues.setdefault((r.sku, r.intended_client), []).append(r)
        new_items_legacy: list[PalletItem] = []
        for it in self._by_slot.get(slot_id, []):
            if id(it) in gone_ids or it.qty <= 0:
                continue
            q = queues.get((it.sku, it.intended_client))
            r = q.pop(0) if q else None
            if r is not None:
                new_items_legacy.append(
                    PalletItem(
                        sku=it.sku,
                        qty=it.qty,
                        unit_volume_m3=it.unit_volume_m3,
                        unit_weight_kg=it.unit_weight_kg,
                        intended_client=it.intended_client,
                        is_returnable_empty=it.is_returnable_empty,
                        physical_type=it.physical_type,
                        pos_x=r.pos_x,
                        pos_y=r.pos_y,
                        pos_z=r.pos_z,
                        dim_x=it.dim_x,
                        dim_y=it.dim_y,
                        dim_h=it.dim_h,
                    )
                )
            else:
                new_items_legacy.append(it)
        self._by_slot[slot_id] = new_items_legacy

    def _first_floor_slot(
        self,
        candidates: list[str],
        dim_x: float,
        dim_y: float,
        dim_h: float,
    ) -> tuple[str, tuple[float, float, float]] | None:
        """Find the first candidate slot whose pallet has free floor for
        the new box. Returns (slot_id, pos) or None if none qualify."""

        for sid in candidates:
            pos = _find_floor_position(
                self._by_slot.get(sid, []), dim_x, dim_y, dim_h
            )
            if pos is not None:
                return (sid, pos)
        return None
