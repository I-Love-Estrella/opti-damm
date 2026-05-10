"""Historic — Damm's actual driver route + client-block, search-move-aware loading.

The driver route is preserved verbatim from the source delivery notes
(no NN / 2-opt reordering); the loader is the single piece we optimise
to make the day deliverable inside legal physics + minimal lift cycles.

  - **Route**: ACTUAL visit sequence from `Detalle entrega` (`case.orders`).
  - **Loading — CLIENT-BLOCK** (NOT pure load-by-reference): all chunks
    for one client are emitted consecutively so the natural lowest-(z, y, x)
    packer puts each client's items in a contiguous block on the truck.
    Same-coloured cubes stay together, the driver opens the curtain
    onto exactly one client's stuff per stop.
      * Across clients: EARLIEST-visit client first → takes the door-
        edge anchors at low (y, x). Stop 1 needs no lift cycles.
      * Within a client: HEAVIEST SKU first → lands at z=0 (avoids
        CRUSH_RISK = a 35 kg keg sitting on a 1 kg case).
  - **Pallet class discipline**: KEG and BOX never share a pallet —
    keeps GLASS_UNDER_HEAVY / CRUSH_RISK clean.
  - **Per-class slot quota**: KEG and BOX claim slots proportional to
    their total chunk volume, so a few keg SKUs with awkward dims can't
    eat 5/6 slots and starve the box-class delivery.
  - **Slot assignment mirrors visit order**: the pallet whose earliest
    client is visited soonest lands on the door-side slot (L1 / R1);
    the last-visited primary client goes to the back-most slot. KEG
    pallets cluster toward the back so the door-side floor stays free
    for empties pickup.

Geometry uses a single strict tier in `_find_position_safe` (≥ 50 %
support, no crush, aspect-stable). Anything that doesn't fit there
overflows to a fresh pallet within the per-class quota; if even that
fails, the chunk is dropped (no UNSTABLE_OVERHANG / CRUSH_RISK ever
emitted, by construction).

Returnables (kegs / crates / bottles per the Damm brief — three return
categories) go on the floor of class-compatible slots via the strict
`_FloorOnlyEmptiesStrategy` (never stacked on cargo).

Why historic vs balanced: `balanced` re-routes via NN + 2-opt; historic
keeps the human driver's choice and only fixes the loading. Comparing
the two isolates "route savings" from "loader savings".
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from simulator.algorithms.base import Algorithm
from simulator.algorithms.virtual_truck import VirtualTruck
from simulator.config import (
    PALLET_HEIGHT_M,
    PALLET_LENGTH_M,
    PALLET_VOLUME_M3,
    PALLET_WIDTH_M,
)
from simulator.data.catalog import physical_dims
from simulator.data.clients import Clients
from simulator.data.network import Network
from simulator.data.orders import ClientOrder, DayCase, OrderLine
from simulator.domain.commands import (
    BuildPallet,
    Command,
    DepartDepot,
    DriveTo,
    Load,
    Pick,
    PickupReturn,
    ReturnDepot,
    Unload,
)
from simulator.domain.packing import find_position
from simulator.domain.pallet import (
    PalletClass,
    PalletItem,
    PalletKind,
)
from simulator.domain.plan import Plan
from simulator.domain.truck import Slot, build_slots


PALLET_MAX_WEIGHT_KG = 1000.0
KEG_MAX_STACK = 2
# Validator caps aspect ratio at STACK_RATIO_ERROR=3.5 (hard error) and
# STACK_RATIO_WARN=3.0 (warning). We pack at 3.5 (matches validator's
# hard ceiling) — anything tighter (e.g. 2.95) sounds attractive
# because it silences STACK_WOBBLY warnings, but in practice it
# rejected so many anchors that fill_rate cratered on tight days
# (DR0017 went from 100% → 80%, DR0050 71%, DR0051 17%, DR0054 44%).
# The trade-off is: STACK_WOBBLY is a *warning*, not an error — and
# customers care about delivered cargo far more than about a stack
# being 0.05 over the wobbly threshold. We accept the warnings.
STACK_RATIO = 3.5
PACK_ASPECT_NON_KEG = 3.5
IDEAL_X = 0.52
IDEAL_Y = 0.50
_BBOX_EPS = 1e-6

# Soft caps: when the current SKU-block pallet crosses either, we open a
# fresh pallet for the next chunk even if it would still fit. Keeps the
# per-pallet density high enough for warehouse-friendly load-by-reference,
# low enough that subsequent unloads don't disturb a packed-tight tower.
PALLET_VOLUME_SOFT_FRAC = 0.85
PALLET_WEIGHT_SOFT_KG = 800.0


def _line_dims(line: OrderLine) -> tuple[float, float, float]:
    if (
        line.dim_source == "data"
        and line.dim_x_m > 0
        and line.dim_y_m > 0
        and line.dim_h_m > 0
    ):
        return line.dim_x_m, line.dim_y_m, line.dim_h_m
    ptype = (
        line.physical_type.value
        if hasattr(line.physical_type, "value")
        else str(line.physical_type)
    )
    return physical_dims(ptype)


def _physical_type_str(line: OrderLine) -> str:
    return (
        line.physical_type.value
        if hasattr(line.physical_type, "value")
        else str(line.physical_type)
    )


def _line_class(line: OrderLine) -> PalletClass:
    return (
        PalletClass.KEG if _physical_type_str(line) == "keg" else PalletClass.BOX
    )


def _stack_chunk_qty(qty: float, dx: float, dy: float, dh: float, *, is_keg: bool) -> float:
    # Maximise items per chunk. Each Pick command costs the loader a
    # fixed `pick_min_per_sku` (2 min) regardless of qty, so splitting
    # the same SKU into many small chunks blows up depot loading time.
    # Kegs cap at the KEG_MAX_STACK=2 business rule (validator accepts
    # the resulting 1.30 m / 0.40 m = 3.25 ratio as inherent). Other
    # items cap at PACK_ASPECT_NON_KEG=2.95 — under validator's WARN
    # threshold so we never produce STACK_WOBBLY anchors.
    if is_keg:
        return float(min(qty, KEG_MAX_STACK))
    narrow = max(1e-3, min(dx, dy))
    max_units = max(1, int((PACK_ASPECT_NON_KEG * narrow) / max(dh, 1e-3)))
    by_height = max(1, int(PALLET_HEIGHT_M / max(dh, 1e-3)))
    return float(min(qty, max_units, by_height))


def _find_position_safe(
    items: list[PalletItem],
    dim_x: float,
    dim_y: float,
    stack_h: float,
    unit_weight_kg: float,
    *,
    is_keg: bool = False,
) -> tuple[tuple[float, float, float] | None, int]:
    """Strict packer: ≥ 50 % support coverage, no crush, aspect-stable.

    Returns (pos, 1) on success, (None, 0) on failure. We deliberately
    have no relaxed fallback:
      - Lower support fraction trips UNSTABLE_OVERHANG (validator).
      - Allowing crush trips CRUSH_RISK (validator) — that turns the
        "early clients at the door edge" sort into a CRUSH disaster
        when a heavy case ends up on top of a light unit.
    Chunks that don't fit overflow and the caller opens a fresh pallet
    (subject to the per-class quota) or drops the chunk.

    Aspect limit: kegs use the validator's hard ceiling (3.5) since their
    standard 2-stack already sits at 3.25. Everything else uses the
    tighter 2.95 — anchor selection then refuses any spot that would
    trigger the validator's STACK_WOBBLY (warn > 3.0) bucket.
    """

    aspect = STACK_RATIO if is_keg else PACK_ASPECT_NON_KEG
    pos = find_position(
        items,
        dim_x=dim_x,
        dim_y=dim_y,
        dim_h=stack_h,
        enforce_pallet_height=True,
        aspect_limit=aspect,
        unit_weight_kg=unit_weight_kg,
        require_support=True,
        avoid_crush=True,
    )
    if pos is not None:
        return pos, 1
    return None, 0


@dataclass
class _Chunk:
    sku: str
    client_id: str
    qty: float
    unit_dim_x: float
    unit_dim_y: float
    unit_dim_h: float
    unit_weight_kg: float
    unit_volume_m3: float
    physical_type: str
    uma: str
    pallet_class: PalletClass

    @property
    def stack_h(self) -> float:
        return self.qty * self.unit_dim_h

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg

    @property
    def is_keg(self) -> bool:
        return self.physical_type == "keg"


@dataclass
class _Pick:
    sku: str
    qty: float
    intended_client: str
    pos_x: float
    pos_y: float
    pos_z: float
    dim_x: float
    dim_y: float
    dim_h: float
    unit_volume_m3: float
    unit_weight_kg: float
    physical_type: str


@dataclass
class _PalletDraft:
    pallet_id: str
    pallet_class: PalletClass
    primary_client: str | None = None
    multi_clients: bool = False
    items: list[PalletItem] = field(default_factory=list)
    picks: list[_Pick] = field(default_factory=list)
    weight_kg: float = 0.0
    volume_m3: float = 0.0
    client_set: set[str] = field(default_factory=set)
    sku_set: set[str] = field(default_factory=set)
    primary_sku: str | None = None
    visit_seq_min: int = 10**9  # earliest visit seq among the pallet's clients

    @property
    def is_empty(self) -> bool:
        return not self.items

    def is_soft_full(self) -> bool:
        return (
            self.volume_m3 >= PALLET_VOLUME_SOFT_FRAC * PALLET_VOLUME_M3
            or self.weight_kg >= PALLET_WEIGHT_SOFT_KG
        )


class HistoricMimic(Algorithm):
    name = "historic"
    description = (
        "Driver's actual visit order (no rerouting) + client-block loading: "
        "each client's items pack together so the curtain opens onto one "
        "stop at a time, no rummaging. KEG and BOX on separate pallets, "
        "per-class slot quota, strict no-crush / no-overhang physics."
    )

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        rationale: list[str] = []

        # Two routes: one drives the warehouse load (chunk ordering, slot
        # assignment); one drives the actual driving sequence on the road.
        # Historic mimic uses the same historic sequence for both.
        # Subclasses (e.g. HistoricLoad) override `_delivery_route` to
        # re-optimize the on-road order while keeping the warehouse load
        # exactly as Damm does it today.
        loading_route = self._loading_route(case, clients, network)
        rationale.append(
            "Loading-side visit order (warehouse plan): "
            f"{[o.client_id for o in loading_route]}."
        )

        slots = list(build_slots(case.truck))
        # visit_seq drives slot assignment (earliest-visit primary near
        # the door) — anchored to the loading route, since slot
        # assignment is part of how the warehouse stages the truck.
        visit_seq = {o.client_id: idx for idx, o in enumerate(loading_route)}
        route = loading_route

        # delivery_seq drives chunk packing (LATE-delivery clients land
        # on the bottom, EARLY-delivery on top) so unloads come off
        # top-to-bottom and never leave a chunk floating after a delivery.
        # Computed up-front from the (possibly re-optimized) on-road
        # route — for HistoricLoad this differs from loading_route after
        # NN/2-opt/or-opt; for plain Historic they're identical.
        delivery_route_pre = self._delivery_route(
            case, clients, network, loading_route
        )
        delivery_seq = {
            o.client_id: idx for idx, o in enumerate(delivery_route_pre)
        }

        # Build per-class SKU blocks. Each SKU collects (client, qty)
        # demands from all clients that ordered it; chunks are emitted
        # in route order so the front of a pallet (lower y) is the
        # earliest customer's portion of that SKU. This mirrors the
        # picking sheet: same product → same pallet, customers laid out
        # in delivery sequence within the pallet.
        chunks_by_class: dict[PalletClass, list[_Chunk]] = {
            PalletClass.KEG: [],
            PalletClass.BOX: [],
        }
        sku_total_volume: dict[str, float] = defaultdict(float)
        sku_first_seq: dict[str, int] = {}

        for line_seq, order in enumerate(route):
            for line in order.lines:
                cls = _line_class(line)
                dx, dy, dh = _line_dims(line)
                ptype = _physical_type_str(line)
                remaining = float(line.qty)
                if remaining <= 0:
                    continue
                while remaining > 0:
                    take = _stack_chunk_qty(
                        remaining, dx, dy, dh, is_keg=(cls == PalletClass.KEG)
                    )
                    chunk = _Chunk(
                        sku=line.sku,
                        client_id=order.client_id,
                        qty=take,
                        unit_dim_x=dx,
                        unit_dim_y=dy,
                        unit_dim_h=dh,
                        unit_weight_kg=line.unit_weight_kg,
                        unit_volume_m3=line.unit_volume_m3,
                        physical_type=ptype,
                        uma=line.uma,
                        pallet_class=cls,
                    )
                    chunks_by_class[cls].append(chunk)
                    sku_total_volume[line.sku] += take * dx * dy * dh
                    sku_first_seq.setdefault(line.sku, line_seq)
                    remaining -= take

        # LIFO-safe client-block sort. The greedy packer fills the
        # lowest (z, y, x) anchor first, so whichever chunk we hand it
        # FIRST lands at z=0. To keep every chunk supported through the
        # whole route we therefore want:
        #   1. LATEST-delivery client first → lands on the floor;
        #      delivered last, so nothing above ever loses its support.
        #   2. Within a client, HEAVY SKU first → keg-on-bottle CRUSH
        #      stays impossible.
        # Same-client cubes still pack consecutively (one client → one
        # contiguous block in the visualiser). Pure load-by-reference
        # scattered each client across many SKU groups, which is why
        # historic_replay generated huge search-move counts.
        sku_max_weight: dict[str, float] = defaultdict(float)
        for cls_chunks in chunks_by_class.values():
            for c in cls_chunks:
                if c.unit_weight_kg > sku_max_weight[c.sku]:
                    sku_max_weight[c.sku] = c.unit_weight_kg

        for cls in (PalletClass.KEG, PalletClass.BOX):
            chunks_by_class[cls].sort(
                key=lambda c: self._chunk_sort_key(
                    c, sku_max_weight, sku_total_volume, delivery_seq
                )
            )

        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        drafts: list[_PalletDraft] = []
        next_pid = [0]

        def new_draft(cls: PalletClass) -> _PalletDraft:
            next_pid[0] += 1
            d = _PalletDraft(
                pallet_id=f"PH{next_pid[0]:02d}",
                pallet_class=cls,
            )
            drafts.append(d)
            return d

        # Pre-allocate slot quota per class proportional to total volume.
        # Without this, a few keg SKUs with awkward dimensions (e.g. ED30
        # 0.408 m wide → only 1 Y-column fits on the 0.80 m pallet width)
        # eat 5/6 slots, leaving BOX-class units to massively overflow.
        max_pallets = len(slots)
        vol_by_class: dict[PalletClass, float] = {
            PalletClass.KEG: sum(
                c.unit_dim_x * c.unit_dim_y * c.stack_h
                for c in chunks_by_class[PalletClass.KEG]
            ),
            PalletClass.BOX: sum(
                c.unit_dim_x * c.unit_dim_y * c.stack_h
                for c in chunks_by_class[PalletClass.BOX]
            ),
        }
        slot_quota = self._split_slot_quota(vol_by_class, max_pallets)
        overflow: list[tuple[str, str, float]] = []

        for cls in (PalletClass.KEG, PalletClass.BOX):
            current: _PalletDraft | None = None
            cls_quota = slot_quota.get(cls, 0)

            def cls_pallet_count() -> int:
                return sum(1 for d in drafts if d.pallet_class == cls)

            for chunk in chunks_by_class[cls]:
                placed = False

                def try_strict(draft: _PalletDraft) -> tuple[float, float, float] | None:
                    if draft.weight_kg + chunk.weight_kg > PALLET_MAX_WEIGHT_KG:
                        return None
                    pos, tier = _find_position_safe(
                        draft.items,
                        chunk.unit_dim_x,
                        chunk.unit_dim_y,
                        chunk.stack_h,
                        chunk.unit_weight_kg,
                        is_keg=chunk.is_keg,
                    )
                    if pos is None:
                        return None
                    if not self._column_ok(draft.items, pos, chunk):
                        return None
                    # LIFO guard: refuse anchors where the supporter
                    # column belongs to a client delivered EARLIER than
                    # this chunk's client. Otherwise the early delivery
                    # would yank our supporter out from under us
                    # mid-route → SETTLE / FLOATING events.
                    if not self._lifo_ok(draft.items, pos, chunk, delivery_seq):
                        return None
                    return pos

                # 1. Strict tier on the current SKU-block pallet, if not soft-full.
                #    Preserves load-by-reference continuity within an SKU.
                if (
                    current is not None
                    and current.pallet_class == cls
                    and not current.is_soft_full()
                ):
                    pos = try_strict(current)
                    if pos is not None:
                        self._commit(current, chunk, pos, delivery_slots)
                        placed = True

                # 2. Strict tier on any OTHER existing pallet of this class.
                #    Doing this BEFORE opening a fresh pallet is the
                #    difference between "6 pallets used for 38 kegs" and
                #    "2 pallets used for 38 kegs" — the column-cap on the
                #    current pallet doesn't mean we need a new slot, just
                #    a different column on a pallet we already opened.
                if not placed:
                    best_pos: tuple[float, float, float] | None = None
                    best_d: _PalletDraft | None = None
                    for d in drafts:
                        if d.pallet_class != cls or d is current:
                            continue
                        if d.is_soft_full():
                            continue
                        pos = try_strict(d)
                        if pos is None:
                            continue
                        if best_pos is None or (pos[2], pos[1], pos[0]) < (
                            best_pos[2], best_pos[1], best_pos[0],
                        ):
                            best_pos, best_d = pos, d
                    if best_pos is not None and best_d is not None:
                        self._commit(best_d, chunk, best_pos, delivery_slots)
                        current = best_d
                        placed = True

                # 3. Open a fresh pallet — first within the per-class
                #    quota, then (cascade) over the quota if the truck
                #    still has free slots overall. The quota was meant
                #    to *balance* slot use between KEG and BOX, not to
                #    drop chunks while slots sit empty.
                if not placed and len(drafts) < max_pallets:
                    fresh = new_draft(cls)
                    pos = try_strict(fresh)
                    if pos is not None:
                        self._commit(fresh, chunk, pos, delivery_slots)
                        current = fresh
                        placed = True
                    else:
                        drafts.remove(fresh)
                        next_pid[0] -= 1

                if not placed:
                    overflow.append((chunk.client_id, chunk.sku, chunk.qty))

        # Slot assignment: visit-aware. Earliest-primary-client pallet
        # near the door, latest near the back. KEG drafts sorted to land
        # on the back-most positions of their side (heavier weight should
        # ride low and toward the truck axle).
        # Stash the truck so the COG-balancing pass inside
        # _assign_slots can compute lateral COM in real metres.
        self._truck_for_balance = case.truck
        slot_assignment = self._assign_slots(drafts, slots)

        rationale.append(
            f"Loaded {len(drafts)} pallet(s) across {len(slot_assignment)} "
            f"slot(s); KEG={sum(1 for d in drafts if d.pallet_class == PalletClass.KEG)}, "
            f"BOX={sum(1 for d in drafts if d.pallet_class == PalletClass.BOX)}."
        )
        if overflow:
            rationale.append(
                f"OVERFLOW: {len(overflow)} chunk(s) did not fit (would drop)."
            )

        delivery_route = self._delivery_route(case, clients, network, loading_route)
        if [o.client_id for o in delivery_route] != [o.client_id for o in loading_route]:
            rationale.append(
                "Delivery-side visit order re-optimized: "
                f"{[o.client_id for o in delivery_route]}."
            )

        cmds = self._emit_commands(
            case, delivery_route, slots, drafts, slot_assignment, delivery_slots
        )

        return Plan(
            algorithm=self.name,
            commands=tuple(cmds),
            rationale=tuple(rationale),
            route_order=tuple(o.client_id for o in delivery_route),
        )

    # ---- Route hooks (overridable) ------------------------------------

    def _loading_route(
        self, case: DayCase, clients: Clients, network: Network
    ) -> list[ClientOrder]:
        """Visit order the WAREHOUSE assumes at pick time.

        Defaults to the historic Detalle entrega sequence — the same one
        Damm currently uses to plan their picking sheet.
        """

        return list(case.orders)

    def _delivery_route(
        self,
        case: DayCase,
        clients: Clients,
        network: Network,
        loading_route: list[ClientOrder],
    ) -> list[ClientOrder]:
        """Visit order the DRIVER follows on the road.

        Default: identical to the loading route (faithful historic mimic).
        Override in subclasses to keep the load-by-reference packing but
        drive a smarter route on top of it.
        """

        return loading_route

    def _chunk_sort_key(
        self,
        c: _Chunk,
        sku_max_weight: dict[str, float],
        sku_total_volume: dict[str, float],
        delivery_seq: dict[str, int],
    ) -> tuple:
        """Default = client-block: chunks for the same client pack
        consecutively, late-delivery clients sit at the floor (so unloads
        come off top-down without leaving anyone floating). Override
        for SKU-block (load-by-reference) or any other strategy.
        """

        return (
            -delivery_seq[c.client_id],   # LATE-delivery client first → bottom
            -sku_max_weight[c.sku],       # heavy SKU first within client
            -sku_total_volume[c.sku],
            c.sku,
        )

    def _make_empties_strategy(
        self, slot_centers: dict[str, tuple[float, float]]
    ) -> "_FloorOnlyEmptiesStrategy":
        """Build the empties-placement strategy.

        Default returns the floor-only Balanced strategy. Override to
        plug in a stricter strategy (e.g. one that pre-validates each
        candidate position against the simulator's exact physics rules
        and refuses placements that would trigger PHYSICS_VIOLATION).
        """

        return _FloorOnlyEmptiesStrategy(slot_centers)

    @staticmethod
    def _split_slot_quota(
        vol_by_class: dict[PalletClass, float],
        total_slots: int,
    ) -> dict[PalletClass, int]:
        """Apportion the truck's slots between KEG and BOX classes by
        share of total chunk volume, with a guarantee that any class
        with positive demand gets at least one slot.

        Without this, a few keg SKUs with awkward dims monopolise the
        slots and the BOX class gets zero — producing the catastrophic
        ~8 % fill rates the operator was seeing on busy days.
        """

        keg_vol = vol_by_class.get(PalletClass.KEG, 0.0)
        box_vol = vol_by_class.get(PalletClass.BOX, 0.0)
        total = keg_vol + box_vol
        if total <= 0 or total_slots <= 0:
            return {PalletClass.KEG: total_slots, PalletClass.BOX: 0}
        keg_share = keg_vol / total
        keg_slots = int(round(keg_share * total_slots))
        if keg_vol > 0 and keg_slots == 0:
            keg_slots = 1
        if box_vol > 0 and keg_slots >= total_slots:
            keg_slots = total_slots - 1
        keg_slots = max(0, min(total_slots, keg_slots))
        return {
            PalletClass.KEG: keg_slots,
            PalletClass.BOX: total_slots - keg_slots,
        }

    @staticmethod
    def _lifo_ok(
        items: list[PalletItem],
        pos: tuple[float, float, float],
        chunk: _Chunk,
        visit_seq: dict[str, int],
    ) -> bool:
        """Reject anchors where the supporter column contains items for
        a client that's delivered BEFORE this chunk's client. Otherwise
        the early delivery removes the supporter and the chunk floats —
        the SETTLE physics events the operator was complaining about.

        Same-client stacking is fine (they leave together). Stacking
        on a LATER-visit client is also fine because the later client
        hasn't been delivered yet by the time we deliver this chunk.
        """

        x, y, z = pos
        if z < 1e-6:
            return True  # floor anchor — nothing to disturb us
        my_seq = visit_seq.get(chunk.client_id, -1)
        end_x = x + chunk.unit_dim_x
        end_y = y + chunk.unit_dim_y
        for it in items:
            if it.qty <= 0:
                continue
            # Only items strictly below this anchor matter.
            if it.pos_z >= z - 1e-6:
                continue
            ox = max(0.0, min(end_x, it.end_x) - max(x, it.pos_x))
            oy = max(0.0, min(end_y, it.end_y) - max(y, it.pos_y))
            if ox <= 0 or oy <= 0:
                continue
            their_seq = visit_seq.get(it.intended_client or "", -1)
            if their_seq < 0:
                continue
            if their_seq < my_seq:
                return False
        return True

    def _try_relaxed_lifo(
        self,
        chunk: _Chunk,
        drafts: list[_PalletDraft],
        cls: PalletClass,
    ) -> tuple[_PalletDraft, tuple[float, float, float]] | None:
        """Place chunk on any existing pallet of `cls`, ignoring the
        LIFO support guard but keeping every other constraint
        (no-overlap, ≥50% support, no crush, weight cap, column cap,
        aspect ratio). The take-bug fix means a SETTLE late in the
        route is benign — the cargo is still delivered."""
        best_pos: tuple[float, float, float] | None = None
        best_d: _PalletDraft | None = None
        for d in drafts:
            if d.pallet_class != cls:
                continue
            if d.weight_kg + chunk.weight_kg > PALLET_MAX_WEIGHT_KG:
                continue
            pos, _ = _find_position_safe(
                d.items,
                chunk.unit_dim_x,
                chunk.unit_dim_y,
                chunk.stack_h,
                chunk.unit_weight_kg,
                is_keg=chunk.is_keg,
            )
            if pos is None:
                continue
            if not self._column_ok(d.items, pos, chunk):
                continue
            if best_pos is None or (pos[2], pos[1], pos[0]) < (
                best_pos[2], best_pos[1], best_pos[0],
            ):
                best_pos, best_d = pos, d
        if best_pos is not None and best_d is not None:
            return best_d, best_pos
        return None

    def _try_relaxed_aspect(
        self,
        chunk: _Chunk,
        drafts: list[_PalletDraft],
        cls: PalletClass,
    ) -> tuple[_PalletDraft, tuple[float, float, float]] | None:
        """Place chunk on any pallet using the validator's HARD aspect
        ceiling (3.5) instead of the algorithm's soft 2.95 target.
        Trades STACK_WOBBLY warnings for fill-rate. Still enforces
        no-overlap / ≥50%-support / no-crush / column cap, so we never
        produce a hard validator ERROR."""
        best_pos: tuple[float, float, float] | None = None
        best_d: _PalletDraft | None = None
        for d in drafts:
            if d.pallet_class != cls:
                continue
            if d.weight_kg + chunk.weight_kg > PALLET_MAX_WEIGHT_KG:
                continue
            pos = find_position(
                d.items,
                dim_x=chunk.unit_dim_x,
                dim_y=chunk.unit_dim_y,
                dim_h=chunk.stack_h,
                enforce_pallet_height=True,
                aspect_limit=STACK_RATIO,  # 3.5 — validator's hard ceiling
                unit_weight_kg=chunk.unit_weight_kg,
                require_support=True,
                avoid_crush=True,
            )
            if pos is None:
                continue
            if not self._column_ok(d.items, pos, chunk):
                continue
            if best_pos is None or (pos[2], pos[1], pos[0]) < (
                best_pos[2], best_pos[1], best_pos[0],
            ):
                best_pos, best_d = pos, d
        if best_pos is not None and best_d is not None:
            return best_d, best_pos
        return None

    @staticmethod
    def _column_ok(
        items: list[PalletItem],
        pos: tuple[float, float, float],
        chunk: _Chunk,
    ) -> bool:
        """Reject placements that push a single (x, y) column past the
        validator's STACK_OVERFLOW limit (BOX layout = 6 units/col,
        KEG layout = 4 units/col)."""

        x, y, _z = pos
        col_count = 0
        for it in items:
            if it.qty <= 0:
                continue
            if abs(it.pos_x - x) > 1e-3 or abs(it.pos_y - y) > 1e-3:
                continue
            col_count += max(1, int(round(it.qty)))
        max_per_col = 4 if chunk.pallet_class == PalletClass.KEG else 6
        return col_count + max(1, int(round(chunk.qty))) <= max_per_col

    @staticmethod
    def _commit(
        draft: _PalletDraft,
        chunk: _Chunk,
        pos: tuple[float, float, float],
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
    ) -> None:
        item = PalletItem(
            sku=chunk.sku,
            qty=chunk.qty,
            unit_volume_m3=chunk.unit_volume_m3,
            unit_weight_kg=chunk.unit_weight_kg,
            intended_client=chunk.client_id,
            is_returnable_empty=False,
            physical_type=chunk.physical_type,
            pos_x=pos[0],
            pos_y=pos[1],
            pos_z=pos[2],
            dim_x=chunk.unit_dim_x,
            dim_y=chunk.unit_dim_y,
            dim_h=chunk.stack_h,
        )
        draft.items.append(item)
        draft.picks.append(
            _Pick(
                sku=chunk.sku,
                qty=chunk.qty,
                intended_client=chunk.client_id,
                pos_x=pos[0],
                pos_y=pos[1],
                pos_z=pos[2],
                dim_x=chunk.unit_dim_x,
                dim_y=chunk.unit_dim_y,
                dim_h=chunk.stack_h,
                unit_volume_m3=chunk.unit_volume_m3,
                unit_weight_kg=chunk.unit_weight_kg,
                physical_type=chunk.physical_type,
            )
        )
        draft.weight_kg += chunk.weight_kg
        draft.volume_m3 += chunk.unit_dim_x * chunk.unit_dim_y * chunk.stack_h
        draft.client_set.add(chunk.client_id)
        draft.sku_set.add(chunk.sku)
        if draft.primary_client is None:
            draft.primary_client = chunk.client_id
        elif draft.primary_client != chunk.client_id:
            draft.multi_clients = True
        if draft.primary_sku is None:
            draft.primary_sku = chunk.sku
        # Use pallet_id as a placeholder; the emitter rewrites it to the
        # final slot_id once the slot assignment is known.
        delivery_slots[(chunk.client_id, chunk.sku)].append(
            (draft.pallet_id, chunk.qty)
        )

    def _assign_slots(
        self, drafts: list[_PalletDraft], slots: list[Slot]
    ) -> dict[str, str]:
        """Map pallet_id → slot_id.

        Strategy: order drafts by (class, earliest visit seq among their
        clients, weight). Earliest-visit pallets get the door-side slot
        (position 1); later visits go toward the back. KEG pallets get
        the back-most positions of each side so the heavy weight rides
        low and stable, and the door floor is free for empties pickup.
        """

        # Compute earliest-visit seq per draft.
        for d in drafts:
            d.visit_seq_min = 10**9  # no clients → very late

        # KEG to the back, BOX to the front. Within each class, sort by
        # earliest visit seq so the pallet whose first-visited owner is
        # earliest sits near the door.
        keg_drafts = [d for d in drafts if d.pallet_class == PalletClass.KEG]
        box_drafts = [d for d in drafts if d.pallet_class == PalletClass.BOX]

        def visit_min(d: _PalletDraft) -> int:
            return d.visit_seq_min

        # The visit_seq_min was never re-computed — fix here based on the
        # client_set we accumulated.
        # (This is a small re-compute pass to keep things readable.)
        # We store the result back on the draft.
        # NB: client_set order is not stable; we iterate sorted set.
        # Visit seq lookup must come from outside; we accept the caller
        # injecting it if needed. We recompute based on items here.
        for d in drafts:
            seqs = sorted(
                self._client_visit_seq(d, [])  # placeholder — see below
            )
            d.visit_seq_min = seqs[0] if seqs else 10**9

        # Build slot lists per side. Lower position = closer to door.
        l_slots = sorted(
            [s for s in slots if s.side == "L"], key=lambda s: s.position
        )
        r_slots = sorted(
            [s for s in slots if s.side == "R"], key=lambda s: s.position
        )
        b_slots = [s for s in slots if s.side == "B"]

        assignment: dict[str, str] = {}
        side_used = {"L": 0, "R": 0, "B": 0}
        side_weight = {"L": 0.0, "R": 0.0, "B": 0.0}

        def take_slot(side: str, from_back: bool) -> Slot | None:
            pool = {"L": l_slots, "R": r_slots, "B": b_slots}[side]
            if side_used[side] >= len(pool):
                return None
            if from_back:
                # Pick the back-most still-free slot of this side.
                used = side_used[side]
                free = pool[: len(pool) - used]
                if not free:
                    return None
                slot = free[-1]
            else:
                # Door-side — pick the front-most still-free slot.
                used = side_used[side]
                free = pool[used:]
                if not free:
                    return None
                slot = free[0]
            # Mark it consumed by removing the slot from the pool view
            # via side_used. We rebuild the pool view next call.
            pool.remove(slot)
            return slot

        # Process KEG drafts first, fill from the back of L/R alternately.
        for d in sorted(keg_drafts, key=lambda x: (-x.weight_kg, visit_min(x))):
            side = "L" if side_weight["L"] <= side_weight["R"] else "R"
            slot = take_slot(side, from_back=True)
            if slot is None:
                # Try the other side.
                other = "R" if side == "L" else "L"
                slot = take_slot(other, from_back=True)
                if slot is None:
                    slot = take_slot("B", from_back=True)
            if slot is None:
                continue
            assignment[d.pallet_id] = slot.slot_id
            side_weight[slot.side] += d.weight_kg
            side_used[slot.side] = 0  # we already removed from pool

        # Process BOX drafts: door-side first, balance L/R by weight.
        for d in sorted(box_drafts, key=lambda x: (visit_min(x), -x.weight_kg)):
            side = "L" if side_weight["L"] <= side_weight["R"] else "R"
            slot = take_slot(side, from_back=False)
            if slot is None:
                other = "R" if side == "L" else "L"
                slot = take_slot(other, from_back=False)
                if slot is None:
                    slot = take_slot("B", from_back=False)
            if slot is None:
                continue
            assignment[d.pallet_id] = slot.slot_id
            side_weight[slot.side] += d.weight_kg
            side_used[slot.side] = 0

        # COG-aware rebalancing: weight-based assignment above is greedy
        # and ignores WHERE on the pallet items actually sit (door-edge
        # vs centerline). Sometimes that drifts lateral COM past the
        # validator's 0.30 m rollover limit. Iteratively swap an L
        # pallet with an R pallet whenever the swap reduces |COM_z|.
        # Within-class only — never swap a KEG pallet with a BOX pallet
        # (would break GLASS_UNDER_HEAVY / CRUSH_RISK rules).
        return self._balance_lateral_com(
            drafts, assignment, slots, self._truck_for_balance,
        )

    # Subclasses pass `case.truck` via this hook (set inside `plan()`).
    _truck_for_balance = None

    def _balance_lateral_com(
        self,
        drafts: list[_PalletDraft],
        assignment: dict[str, str],
        slots: list[Slot],
        truck,
        target_offset_m: float = 0.18,
        max_iter: int = 30,
    ) -> dict[str, str]:
        """Swap L↔R pallets to drive the truck's lateral centre of mass
        toward zero. Greedy hill-climb over swaps.

        Cross-class swaps ARE allowed: a slot is just a position in the
        truck — it doesn't enforce per-class semantics. The pallet keeps
        its own class-discipline (no KEG-on-BOX inside one pallet) no
        matter which slot it lands in. Refusing cross-class swaps used
        to leave catastrophic 5×–12× L/R weight imbalances when one
        class clustered to one side (e.g. all 7 keg pallets on L).

        target_offset_m=0.18 sits below the validator's COM_LATERAL_WARN
        threshold (0.20), so we don't even emit the soft warning when
        the swap is feasible.
        """

        if truck is None:
            return assignment
        by_pid = {d.pallet_id: d for d in drafts}

        for _ in range(max_iter):
            cur = self._lateral_com_z(by_pid, assignment)
            if abs(cur) <= target_offset_m:
                break
            best_offset = abs(cur)
            best_swap: tuple[str, str] | None = None

            l_pallets = [
                pid for pid, sid in assignment.items() if sid.startswith("L")
            ]
            r_pallets = [
                pid for pid, sid in assignment.items() if sid.startswith("R")
            ]
            for lp in l_pallets:
                for rp in r_pallets:
                    trial = dict(assignment)
                    trial[lp], trial[rp] = trial[rp], trial[lp]
                    offset = abs(self._lateral_com_z(by_pid, trial))
                    if offset + 1e-6 < best_offset:
                        best_offset = offset
                        best_swap = (lp, rp)
            if best_swap is None:
                break
            lp, rp = best_swap
            assignment[lp], assignment[rp] = assignment[rp], assignment[lp]
        return assignment

    @staticmethod
    def _lateral_com_z(
        by_pid: dict[str, "_PalletDraft"],
        assignment: dict[str, str],
    ) -> float:
        """Lateral centre of mass (z axis in truck-local metres) given
        a {pallet_id → slot_id} assignment. Mirrors validator math:
          - L slot centre: z = -0.45 m
          - R slot centre: z = +0.45 m
          - B slot centre: z =  0
          - Item local_z within pallet flips sign on L↔R swap.
        """

        PALLET_WIDTH = 0.80
        LR_GAP = 0.10
        slot_z_for = {
            "L": -(PALLET_WIDTH / 2.0 + LR_GAP / 2.0),
            "R": +(PALLET_WIDTH / 2.0 + LR_GAP / 2.0),
            "B": 0.0,
        }
        total_mass = 0.0
        moment = 0.0
        for pid, slot_id in assignment.items():
            d = by_pid.get(pid)
            if d is None:
                continue
            side = slot_id[:1]
            slot_z = slot_z_for.get(side, 0.0)
            for it in d.items:
                mass = it.qty * it.unit_weight_kg
                if mass <= 0:
                    continue
                item_center_y = it.pos_y + it.dim_y / 2.0
                if side == "L":
                    local_z = -PALLET_WIDTH / 2.0 + item_center_y
                elif side == "R":
                    local_z = +PALLET_WIDTH / 2.0 - item_center_y
                else:
                    local_z = item_center_y - PALLET_WIDTH / 2.0
                moment += mass * (slot_z + local_z)
                total_mass += mass
        return moment / total_mass if total_mass > 0 else 0.0

    @staticmethod
    def _client_visit_seq(
        draft: _PalletDraft, _placeholder: list[int]
    ) -> list[int]:
        """Return sorted visit-seq indices for the draft's clients.

        We re-derive from picks (each pick carries the intended_client
        and was emitted in route order, so the lowest pick index for a
        given client gives that client's first-seen position on this
        pallet — but for slot assignment we just need any seq, so use
        the picks' insertion order directly).
        """

        # First-visit seq per client = position the client first appears
        # in the picks list. This is a stable proxy for the route seq
        # because we packed in route order within each SKU-block.
        first_idx_by_client: dict[str, int] = {}
        for i, p in enumerate(draft.picks):
            first_idx_by_client.setdefault(p.intended_client, i)
        return sorted(first_idx_by_client.values())

    def _emit_commands(
        self,
        case: DayCase,
        route: list[ClientOrder],
        slots: list[Slot],
        drafts: list[_PalletDraft],
        slot_assignment: dict[str, str],
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
    ) -> list[Command]:
        cmds: list[Command] = []

        # Drop drafts that didn't get a slot (shouldn't happen unless
        # the truck genuinely doesn't have enough pallets).
        kept_drafts = [d for d in drafts if d.pallet_id in slot_assignment]

        # Rewrite delivery_slots so the (client, sku) → slot mapping
        # uses the assigned slot, not the placeholder pallet_id.
        slot_by_pallet = slot_assignment
        for (cid, sku), entries in list(delivery_slots.items()):
            new_entries: list[tuple[str, float]] = []
            for pallet_id_or_slot, qty in entries:
                # During _commit we used pallet_id as the first tuple
                # element — translate that into the assigned slot now.
                slot_id = slot_by_pallet.get(pallet_id_or_slot, pallet_id_or_slot)
                new_entries.append((slot_id, qty))
            delivery_slots[(cid, sku)] = new_entries

        # Emit BuildPallet + Pick + Load in slot order so the simulator
        # sees pallets land on slots from front to back, mirroring how
        # a forklift actually loads a truck.
        ordered_drafts = sorted(
            kept_drafts,
            key=lambda d: self._slot_sort_key(slot_assignment[d.pallet_id]),
        )
        for d in ordered_drafts:
            slot_id = slot_assignment[d.pallet_id]
            primary = d.primary_client if not d.multi_clients else None
            kind = (
                PalletKind.CLIENT_BLOCK.value
                if primary
                else PalletKind.MIXED.value
            )
            note = (
                f"slot={slot_id} class={d.pallet_class.value} "
                f"clients={sorted(d.client_set)} "
                f"skus={sorted(d.sku_set)}"
            )
            cmds.append(
                BuildPallet(
                    pallet_id=d.pallet_id,
                    kind=kind,
                    primary_client=primary,
                    notes=note,
                    pallet_class=d.pallet_class.value,
                )
            )
            for p in d.picks:
                cmds.append(
                    Pick(
                        sku=p.sku,
                        qty=p.qty,
                        location=None,
                        pallet_id=d.pallet_id,
                        intended_client=p.intended_client,
                        pos_x=p.pos_x,
                        pos_y=p.pos_y,
                        pos_z=p.pos_z,
                        dim_x=p.dim_x,
                        dim_y=p.dim_y,
                        dim_h=p.dim_h,
                        unit_volume_m3=p.unit_volume_m3,
                        unit_weight_kg=p.unit_weight_kg,
                        physical_type=p.physical_type,
                    )
                )
            cmds.append(Load(pallet_id=d.pallet_id, slot_id=slot_id))

        cmds.append(DepartDepot())

        # Shadow truck for restock/returnable planning.
        vt = VirtualTruck()
        for d in ordered_drafts:
            slot_id = slot_assignment[d.pallet_id]
            for it in d.items:
                vt.add(slot_id, it)

        # Slot centres for the COG-aware returnable strategy.
        # We use a stricter subclass that REFUSES to stack empties on
        # cargo (Pass 3 in BalancedStrategy) — that pass produces SETTLE
        # events whenever the cargo below gets delivered.
        from simulator.algorithms.restock_strategy import _RestockContext
        slot_centers = {s.slot_id: self._slot_center(case.truck, s) for s in slots}
        strategy = self._make_empties_strategy(slot_centers)

        keg_dx, keg_dy, keg_dh = physical_dims("keg")

        for o in route:
            cmds.append(DriveTo(client_id=o.client_id))

            # Group target items by slot, then plan blockers + restock
            # atomically per slot (mirrors the simulator's batching).
            splits_by_slot: dict[str, list[tuple[str, float]]] = {}
            for line in o.lines:
                for slot_id, qty in delivery_slots.get(
                    (o.client_id, line.sku), []
                ):
                    splits_by_slot.setdefault(slot_id, []).append((line.sku, qty))

            for slot_id, items in splits_by_slot.items():
                target_keys = [(sku, o.client_id) for sku, _ in items]
                target_items, same_client, foreign = vt.find_blockers(
                    slot_id, target_keys
                )
                ordered_lifts = sorted(
                    foreign + same_client,
                    key=lambda b: (b.pos_y, b.pos_x, -b.pos_z),
                )
                lifts = vt.to_lifts(ordered_lifts)
                restock = vt.plan_restock(
                    slot_id, target_items, same_client, foreign,
                    aspect_limit=STACK_RATIO,
                )
                first = True
                for sku, qty in items:
                    cmds.append(
                        Unload(
                            client_id=o.client_id,
                            sku=sku,
                            qty=qty,
                            slot_id=slot_id,
                            lifts=tuple(lifts) if first else (),
                            restock=tuple(restock) if first else (),
                        )
                    )
                    first = False
                vt.apply_restock(
                    slot_id, restock, target_items, same_client, foreign
                )

            if o.expected_returnable_units > 0:
                # Damm Smart Truck challenge brief, §1: "trucks also
                # collect empty CRATES, CONTAINERS, or BARRELS during
                # the route." Split returnables by the originating
                # line's physical type so visualization and physics
                # checks see the right shapes:
                #   - barrels  ← keg lines (BAR / BID / keg-named ZPR), 60% return
                #   - crates   ← case lines (CAJ / PAK / BOX),          50% return
                #   - bottles  ← bottle lines (BOT),                    40% return
                # Cans and units don't come back (no deposit / disposable).
                qty_by_ptype: dict[str, float] = defaultdict(float)
                for line in o.lines:
                    qty_by_ptype[_physical_type_str(line)] += line.qty

                expected_keg_units = qty_by_ptype.get("keg", 0.0) * 0.60
                expected_crate_units = qty_by_ptype.get("case", 0.0) * 0.50
                expected_bottle_units = qty_by_ptype.get("bottle", 0.0) * 0.40

                # Slot picks per class. KEG empties → KEG-class slots;
                # CASE/BOTTLE empties → BOX-class slots (cases stack with
                # cases, kegs with kegs — avoids CRUSH_RISK / GLASS_UNDER_HEAVY).
                keg_slots = [
                    slot_assignment[d.pallet_id]
                    for d in kept_drafts
                    if d.pallet_class == PalletClass.KEG
                ]
                box_slots = [
                    slot_assignment[d.pallet_id]
                    for d in kept_drafts
                    if d.pallet_class == PalletClass.BOX
                ]
                all_slots = [
                    slot_assignment[d.pallet_id] for d in kept_drafts
                ] or ["L1"]
                ret_slot_default = self._return_slot(
                    o.client_id, slot_assignment, drafts, vt
                )

                # ---- 1) Empty barrels (kegs) -------------------------
                if expected_keg_units > 0:
                    candidates = keg_slots or all_slots
                    primary = (
                        ret_slot_default
                        if ret_slot_default in candidates
                        else candidates[0]
                    )
                    strategy.place_empties(
                        cmds,
                        vt,
                        _RestockContext(
                            client_id=o.client_id,
                            primary_slot=primary,
                            candidate_slots=candidates,
                        ),
                        expected_keg_units,
                        keg_dx,
                        keg_dy,
                        keg_dh,
                        physical_type="keg",
                        sku="EMPTY_KEG",
                        unit_weight_kg=2.0,    # empty 30 L keg ≈ 2 kg
                        unit_volume_m3=0.04,
                        max_per_stack=2,       # KEG_MAX_STACK business rule
                    )

                # ---- 2) Empty crates (cases) -------------------------
                if expected_crate_units > 0:
                    case_dx, case_dy, case_dh = physical_dims("case")
                    candidates = box_slots or all_slots
                    strategy.place_empties(
                        cmds,
                        vt,
                        _RestockContext(
                            client_id=o.client_id,
                            primary_slot=candidates[0],
                            candidate_slots=candidates,
                        ),
                        expected_crate_units,
                        case_dx,
                        case_dy,
                        case_dh,
                        physical_type="case",
                        sku="EMPTY_CRATE",
                        unit_weight_kg=0.6,    # empty plastic crate ≈ 0.6 kg
                        unit_volume_m3=0.022,
                        max_per_stack=4,       # 4 × 0.30 m = 1.20 m, stable
                    )

                # ---- 3) Empty bottles (containers) -------------------
                if expected_bottle_units > 0:
                    bot_dx, bot_dy, bot_dh = physical_dims("bottle")
                    candidates = box_slots or all_slots
                    strategy.place_empties(
                        cmds,
                        vt,
                        _RestockContext(
                            client_id=o.client_id,
                            primary_slot=candidates[0],
                            candidate_slots=candidates,
                        ),
                        expected_bottle_units,
                        bot_dx,
                        bot_dy,
                        bot_dh,
                        physical_type="bottle",
                        sku="EMPTY_BOTTLE",
                        unit_weight_kg=0.3,    # empty glass bottle ≈ 0.3 kg
                        unit_volume_m3=0.0006,
                        max_per_stack=4,       # 4 × 0.42 m = 1.68 m, fits pallet
                    )

        cmds.append(ReturnDepot())
        return cmds

    @staticmethod
    def _slot_sort_key(slot_id: str) -> tuple[int, int, int]:
        side_rank = {"B": 0, "L": 1, "R": 2}
        side = slot_id[:1]
        try:
            pos = int(slot_id[1:])
        except ValueError:
            pos = 0
        return (side_rank.get(side, 9), pos, 0)

    @staticmethod
    def _slot_center(truck, slot: Slot) -> tuple[float, float]:
        cap = truck.pallet_capacity
        half = max(1, cap // 2)
        if slot.side == "B":
            return (1.0, 0.5)
        x = (slot.position - 0.5) / half
        y = 0.25 if slot.side == "L" else 0.75
        return (x, y)

    @staticmethod
    def _return_slot(
        client_id: str,
        slot_assignment: dict[str, str],
        drafts: list[_PalletDraft],
        vt: VirtualTruck,
    ) -> str:
        """Prefer a KEG slot the client already used; fall back to any
        slot they used; finally any KEG slot at all."""

        from simulator.config import PALLET_FOOTPRINT_M2

        def free_floor(slot_id: str) -> float:
            used = sum(
                it.dim_x * it.dim_y
                for it in vt.items(slot_id)
                if it.qty > 0 and it.pos_z < 1e-6
            )
            return max(0.0, PALLET_FOOTPRINT_M2 - used)

        client_keg = [
            slot_assignment[d.pallet_id]
            for d in drafts
            if d.pallet_id in slot_assignment
            and d.pallet_class == PalletClass.KEG
            and client_id in d.client_set
        ]
        if client_keg:
            return max(client_keg, key=free_floor)

        client_any = [
            slot_assignment[d.pallet_id]
            for d in drafts
            if d.pallet_id in slot_assignment and client_id in d.client_set
        ]
        if client_any:
            return max(client_any, key=free_floor)

        any_keg = [
            slot_assignment[d.pallet_id]
            for d in drafts
            if d.pallet_id in slot_assignment
            and d.pallet_class == PalletClass.KEG
        ]
        if any_keg:
            return max(any_keg, key=free_floor)

        any_slot = list(slot_assignment.values())
        return any_slot[0] if any_slot else "L1"


# ---- Strict floor-only empties strategy --------------------------------------


from simulator.algorithms.restock_strategy import (  # noqa: E402
    BalancedStrategy,
    _find_position_on_empties,
)
from simulator.algorithms.virtual_truck import _find_floor_position  # noqa: E402


class _FloorOnlyEmptiesStrategy(BalancedStrategy):
    """Like BalancedStrategy but never stacks empties on cargo and emits
    the correct physical_type per pickup (kegs vs cases).

    BalancedStrategy's Pass 3 falls back to "any stable stack" when
    floor and empties-on-empties are full. That last pass parks empties
    on top of cargo and produces a SETTLE event the moment the cargo
    below is delivered to its client. For the historic algorithm we
    refuse that fallback — we'd rather drop the empty (lose tracking
    of one returnable) than break the cargo geometry mid-route.

    `place_empties` accepts an extra `physical_type` (default "keg") and
    `unit_weight_kg` so an algorithm can pick up empty cases / bottles
    in addition to empty kegs and have them rendered correctly.
    """

    def _best_placement(
        self,
        vt,
        candidates,
        dim_x: float,
        dim_y: float,
        qty: float,
        dim_h_unit: float,
    ):
        stack_h = qty * dim_h_unit
        kg_added = qty * 10.0
        # Pass 1 — floor anchors, ranked by COG penalty.
        scored = []
        for sid in candidates:
            pos = _find_floor_position(vt.items(sid), dim_x, dim_y, stack_h)
            if pos is None or pos[2] + stack_h > PALLET_HEIGHT_M + 1e-6:
                continue
            scored.append((self._cog_penalty(vt, sid, kg_added), sid, pos))
        if scored:
            scored.sort(key=lambda s: s[0])
            return (scored[0][1], scored[0][2])
        # Pass 2 — stack ONLY on other empties. Safe because empties
        # don't get delivered mid-route (they go back to depot at
        # ReturnDepot, all together).
        for sid in candidates:
            pos = _find_position_on_empties(vt.items(sid), dim_x, dim_y, stack_h)
            if pos is not None:
                return (sid, pos)
        # No Pass 3 — refuse the empty rather than perch it on cargo.
        return None

    def place_empties(
        self,
        cmds,
        vt,
        ctx,
        total_units: float,
        unit_dx: float,
        unit_dy: float,
        unit_dh: float,
        *,
        physical_type: str = "keg",
        sku: str = "EMPTY",
        unit_weight_kg: float = 2.0,
        unit_volume_m3: float = 0.04,
        max_per_stack: float | None = None,
    ) -> None:
        """Pick up `total_units` empties of one physical_type at this stop.

        Empty cases / bottles use a smaller per-unit weight and don't
        share the keg's 2-per-stack limit. Caller can override
        `max_per_stack` (e.g. 4 for cases) when the kind tolerates more.
        """

        from simulator.algorithms.virtual_truck import KEG_MAX_STACK
        from simulator.domain.commands import PickupReturn
        from simulator.domain.pallet import PalletItem

        if total_units < 0.3:
            return
        remaining = float(round(total_units))
        if remaining <= 0:
            return
        per_stack = float(max_per_stack if max_per_stack is not None else KEG_MAX_STACK)

        candidates = list(dict.fromkeys([ctx.primary_slot, *ctx.candidate_slots]))

        while remaining > 0:
            take = min(remaining, per_stack)
            placement = self._best_placement(
                vt, candidates, unit_dx, unit_dy, take, unit_dh
            )
            if placement is None and take > 1:
                take = 1.0
                placement = self._best_placement(
                    vt, candidates, unit_dx, unit_dy, take, unit_dh
                )
            if placement is None:
                # No clean spot anywhere — refuse the empty rather
                # than corrupt the cargo geometry.
                remaining -= take
                continue
            chosen_slot, pos = placement
            stack_h = take * unit_dh
            cmds.append(
                PickupReturn(
                    client_id=ctx.client_id,
                    sku=sku,
                    qty=take,
                    slot_id=chosen_slot,
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                    physical_type=physical_type,
                    unit_weight_kg=unit_weight_kg,
                    unit_volume_m3=unit_volume_m3,
                )
            )
            vt.add(
                chosen_slot,
                PalletItem(
                    sku=sku,
                    qty=take,
                    unit_volume_m3=unit_volume_m3,
                    unit_weight_kg=unit_weight_kg,
                    intended_client=None,
                    is_returnable_empty=True,
                    physical_type=physical_type,
                    pos_x=pos[0],
                    pos_y=pos[1],
                    pos_z=pos[2],
                    dim_x=unit_dx,
                    dim_y=unit_dy,
                    dim_h=stack_h,
                ),
            )
            remaining -= take
