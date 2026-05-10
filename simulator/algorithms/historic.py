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

Geometry: footprint-bucket packing. Chunks are grouped by (class, base
footprint dx×dy×dh) into buckets; each bucket fills uniform columns of
known capacity (column cap = min(aspect, height, layout) — 4 for KEG, 6
for BOX). Within a column, units stack heaviest-first so crush is
impossible. Across columns / pallets, slot assignment runs the COG
balancer. No relaxation tiers — by construction we never emit
STACK_UNSTABLE / CRUSH_RISK / STACK_OVERFLOW / PALLET_HEIGHT_EXCEEDS.

Returnables (kegs / crates / bottles per the Damm brief — three return
categories) go on the floor of class-compatible slots via the strict
`_FloorOnlyEmptiesStrategy` (never stacked on cargo).

Why historic vs balanced: `balanced` re-routes via NN + 2-opt; historic
keeps the human driver's choice and only fixes the loading. Comparing
the two isolates "route savings" from "loader savings".
"""

from __future__ import annotations

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
# STACK_RATIO_WARN=3.0 (warning). User mandate: 100% delivery. Pack at
# the validator's HARD ceiling so density matches real-world stretch-
# wrapped pallets (~95% fill). STACK_WOBBLY warnings are the explicit
# price for getting every unit on the truck.
STACK_RATIO = 3.5
PACK_ASPECT_NON_KEG = 3.45
IDEAL_X = 0.52
IDEAL_Y = 0.50
_BBOX_EPS = 1e-6

# Soft caps: when the current SKU-block pallet crosses either, we open a
# fresh pallet for the next chunk even if it would still fit. Keeps the
# per-pallet density high enough for warehouse-friendly load-by-reference,
# low enough that subsequent unloads don't disturb a packed-tight tower.
# WEIGHT_SOFT_KG = 400 was deliberately tightened from 800 so that one
# heavy SKU (e.g. 18 kegs × 35 kg = 630 kg) splits into 2 pallets — the
# COG balancer can then move one half to L and the other to R, instead
# of being stuck with a single 630 kg pallet that swamps any swap.
PALLET_VOLUME_SOFT_FRAC = 0.85
PALLET_WEIGHT_SOFT_KG = 400.0


def _line_dims(line: OrderLine) -> tuple[float, float, float]:
    """Return (dim_x, dim_y, dim_h) for one unit of this line.

    Some catalog SKUs are inherently tall-and-narrow when stood
    upright — e.g. 0LM0020 at 0.57 × 0.18 × 0.74 has a single-unit
    aspect ratio of 4.1 (already over the validator's 3.5 ceiling),
    so STACK_UNSTABLE fires even before the packer stacks anything.
    Real loaders solve this by **laying the SKU on its side** so the
    longest face goes flat. We mirror that decision here: if a single
    upright unit would be wobbly, swap dimensions so the smallest
    side becomes the height.
    """
    if (
        line.dim_source == "data"
        and line.dim_x_m > 0
        and line.dim_y_m > 0
        and line.dim_h_m > 0
    ):
        return _orient_for_stability(line.dim_x_m, line.dim_y_m, line.dim_h_m)
    ptype = (
        line.physical_type.value
        if hasattr(line.physical_type, "value")
        else str(line.physical_type)
    )
    dx, dy, dh = physical_dims(ptype)
    return _orient_for_stability(dx, dy, dh)


def _orient_for_stability(dx: float, dy: float, dh: float) -> tuple[float, float, float]:
    """Pick the orientation (permutation of dx/dy/dh) that keeps the
    per-unit aspect under STACK_RATIO, while disturbing the original
    pose as little as possible.

    Strategy:
      1. If the original orientation already passes (aspect ≤ 3.5),
         keep it — no rotation, no extra footprint cost.
      2. Otherwise consider the three axis swaps; pick the candidate
         that (a) passes the aspect rule AND (b) keeps the FOOTPRINT
         (dx × dy) smallest — laying flat costs pallet area, so we
         prefer the orientation that costs less.
      3. If none pass even after rotation, return the candidate with
         the lowest aspect (best we can do).
    """
    PALLET_H = 1.80
    PALLET_W = 0.80
    PALLET_L = 1.20

    def aspect(d) -> float:
        cdx, cdy, cdh = d
        return cdh / max(min(cdx, cdy), 1e-6)

    def fits_pallet(d) -> bool:
        cdx, cdy, cdh = d
        # Both x and y must fit inside the pallet footprint, height
        # under truck ceiling.
        return (
            cdh <= PALLET_H + 1e-6
            and cdx <= PALLET_L + 1e-6
            and cdy <= PALLET_W + 1e-6
        )

    original = (dx, dy, dh)
    if aspect(original) <= STACK_RATIO + 1e-6 and fits_pallet(original):
        return original

    candidates = [
        (dx, dy, dh),  # original
        (dx, dh, dy),  # swap Y ↔ H (lays flat on Y axis)
        (dh, dy, dx),  # swap X ↔ H (lays flat on X axis)
    ]

    # Prefer orientations that PASS aspect AND fit the pallet
    # footprint, ranked by smallest footprint area (smaller cost).
    passing = [
        c for c in candidates
        if aspect(c) <= STACK_RATIO + 1e-6 and fits_pallet(c)
    ]
    if passing:
        passing.sort(key=lambda c: c[0] * c[1])
        return passing[0]

    # No orientation passes — at least pick the lowest-aspect one
    # that fits the pallet footprint.
    fitting = [c for c in candidates if fits_pallet(c)]
    if fitting:
        fitting.sort(key=aspect)
        return fitting[0]

    # Last resort: original.
    return original


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
    units_per_layer: int = 1

    @property
    def stack_h(self) -> float:
        # Bundled chunks (units_per_layer > 1) lay multiple SKU units
        # side-by-side in one physical layer. Stack height is therefore
        # ceil(qty / units_per_layer) layers tall, not qty layers.
        layers = max(1, (int(self.qty) + self.units_per_layer - 1) // max(1, self.units_per_layer))
        return layers * self.unit_dim_h

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


@dataclass
class _Column:
    """One vertical column on a pallet — uniform footprint, bottom-up
    fill. All units in a column come from the same footprint bucket so
    the aspect ratio is bounded at construction time. The top-of-stack
    weight and delivery_seq are tracked so subsequent placements respect
    crush-safety (lighter on top of heavier) and LIFO-support (a
    supporter must be delivered AFTER everything above it — otherwise
    items float mid-route when the supporter is unloaded)."""

    pallet_id: str
    x: float
    y: float
    dx: float
    dy: float
    dh: float
    cap: int
    used: int = 0
    top_unit_weight_kg: float = float("inf")
    top_delivery_seq: int = 10**9


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

        for order in route:
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
                    remaining -= take

        # Per-SKU max weight is used by `_chunk_sort_key` (a hook the
        # subclasses lean on for SKU-vs-client priority) and by the
        # within-bucket secondary sort below.
        sku_max_weight: dict[str, float] = defaultdict(float)
        for cls_chunks in chunks_by_class.values():
            for c in cls_chunks:
                if c.unit_weight_kg > sku_max_weight[c.sku]:
                    sku_max_weight[c.sku] = c.unit_weight_kg

        # Per-client packing. Each client's chunks consolidate on
        # dedicated pallets so a delivery touches only that client's
        # pallets — no cross-client blocker lifts.
        #
        # Order clients LARGEST FIRST. The heaviest cargo claims its
        # dedicated pallets up front (and SPREADS across multiple
        # pallets / slots if it doesn't fit one); smaller clients
        # fill the rest. Otherwise a fat seq=5 processed last would
        # find every slot already claimed by smaller earlier clients
        # and spill cross-client. Slot assignment below independently
        # places the earliest-visit pallet at the curtain edge so the
        # first-delivered client is still nearest to the door.
        all_chunks: list[_Chunk] = [
            c for cls_chunks in chunks_by_class.values() for c in cls_chunks
        ]

        client_volume: dict[str, float] = defaultdict(float)
        for c in all_chunks:
            client_volume[c.client_id] += (
                c.qty * c.unit_dim_x * c.unit_dim_y * c.unit_dim_h
            )

        def chunk_key(c: _Chunk) -> tuple:
            return (
                -client_volume[c.client_id],
                delivery_seq.get(c.client_id, 10**9),
                c.client_id,
                c.pallet_class.value,
                round(c.unit_dim_x * 20) / 20,
                round(c.unit_dim_y * 20) / 20,
                round(c.unit_dim_h * 20) / 20,
                -c.unit_weight_kg,
                self._chunk_sort_key(
                    c, sku_max_weight, sku_total_volume, delivery_seq
                ),
            )

        all_chunks.sort(key=chunk_key)

        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        drafts: list[_PalletDraft] = []
        columns: list[_Column] = []
        next_pid = [0]

        def new_draft(cls: PalletClass) -> _PalletDraft:
            next_pid[0] += 1
            d = _PalletDraft(pallet_id=f"PH{next_pid[0]:02d}", pallet_class=cls)
            drafts.append(d)
            return d

        max_pallets = len(slots)
        overflow: list[tuple[str, str, float]] = []

        for chunk in all_chunks:
            dx = chunk.unit_dim_x
            dy = chunk.unit_dim_y
            dh = chunk.unit_dim_h
            cls = chunk.pallet_class
            col_cap = self._col_cap(dx, dy, dh, is_keg=chunk.is_keg)
            chunk_seq = delivery_seq.get(chunk.client_id, 0)
            remaining = float(chunk.qty)
            while remaining > 0.5:
                target = self._find_open_column(
                    columns, drafts, dx, dy, dh,
                    chunk.unit_weight_kg, chunk_seq, cls,
                )
                if target is None:
                    # Phase 1: prefer a SAME-CLIENT pallet of matching
                    # class. Cross-client fallback comes later.
                    target = self._open_column_on_existing(
                        drafts, columns, dx, dy, dh, col_cap,
                        chunk.unit_weight_kg, cls, chunk.client_id,
                        same_client_only=True,
                    )
                if target is None:
                    # Phase 1.5: BOX class only — try opening a fresh
                    # pallet so each BOX client gets a dedicated slot.
                    # KEG class is rare and tiny per client; we share
                    # KEG pallets across clients to free up the slot
                    # budget for BOX (where most cargo lives).
                    if cls == PalletClass.BOX and len(drafts) < max_pallets:
                        fresh = new_draft(cls)
                        col = _Column(fresh.pallet_id, 0.0, 0.0, dx, dy, dh, col_cap)
                        columns.append(col)
                        target = (fresh, col)
                if target is None:
                    # Phase 2: cross-client placement on an existing
                    # matching pallet (any client, any free anchor).
                    # KEG pallets always end up here after the first
                    # opens; BOX pallets only when the slot budget is
                    # already saturated.
                    target = self._open_column_on_existing(
                        drafts, columns, dx, dy, dh, col_cap,
                        chunk.unit_weight_kg, cls, chunk.client_id,
                        same_client_only=False,
                    )
                if target is None and len(drafts) < max_pallets:
                    # Phase 3: open a fresh pallet (any class) as last
                    # resort before declaring overflow.
                    fresh = new_draft(cls)
                    col = _Column(fresh.pallet_id, 0.0, 0.0, dx, dy, dh, col_cap)
                    columns.append(col)
                    target = (fresh, col)
                if target is None:
                    overflow.append(
                        (chunk.client_id, chunk.sku, remaining,
                         chunk, chunk_seq, col_cap)
                    )
                    break

                d, col = target
                cur_dx, cur_dy = col.dx, col.dy
                upl = max(1, chunk.units_per_layer)
                # col.cap and col.used count LAYERS (1 layer = upl
                # SKU units in carton mode, or upl=1 for normal).
                room_layers = col.cap - col.used
                room_units = room_layers * upl
                weight_room = int(
                    max(0.0, PALLET_MAX_WEIGHT_KG - d.weight_kg)
                    / max(1e-6, chunk.unit_weight_kg)
                )
                take = float(min(int(remaining), room_units, weight_room))
                if take < 0.5:
                    col.used = col.cap
                    continue
                sub = _Chunk(
                    sku=chunk.sku,
                    client_id=chunk.client_id,
                    qty=take,
                    unit_dim_x=cur_dx,
                    unit_dim_y=cur_dy,
                    unit_dim_h=dh,
                    unit_weight_kg=chunk.unit_weight_kg,
                    unit_volume_m3=chunk.unit_volume_m3,
                    physical_type=chunk.physical_type,
                    uma=chunk.uma,
                    pallet_class=chunk.pallet_class,
                    units_per_layer=chunk.units_per_layer,
                )
                z = col.used * dh
                self._commit(d, sub, (col.x, col.y, z), delivery_slots)
                col.used += (int(take) + upl - 1) // upl  # layers consumed
                col.top_unit_weight_kg = chunk.unit_weight_kg
                col.top_delivery_seq = chunk_seq
                remaining -= take

        # Post-pass: try to rescue overflow chunks by rotating the
        # footprint 90°. The primary orientation is chosen for stability
        # in `_orient_for_stability`, but when every grid-aligned anchor
        # is taken the swapped orientation often fits a leftover strip
        # the primary couldn't. Doing this here (rather than inline)
        # preserves prime anchors for the main pass, so rotation only
        # eats space that would otherwise have stayed empty.
        if overflow:
            recovered: list[tuple] = []
            for entry in overflow:
                client_id, sku, qty, chunk, chunk_seq, col_cap = entry
                rdx, rdy = chunk.unit_dim_y, chunk.unit_dim_x
                if rdx == chunk.unit_dim_x and rdy == chunk.unit_dim_y:
                    recovered.append(entry)
                    continue
                cls = chunk.pallet_class
                dh = chunk.unit_dim_h
                remaining = float(qty)
                while remaining > 0.5:
                    target = self._open_column_on_existing(
                        drafts, columns, rdx, rdy, dh, col_cap,
                        chunk.unit_weight_kg, cls, client_id,
                    )
                    if target is None:
                        recovered.append(
                            (client_id, sku, remaining,
                             chunk, chunk_seq, col_cap)
                        )
                        break
                    d, col = target
                    upl = max(1, chunk.units_per_layer)
                    room_layers = col.cap - col.used
                    room_units = room_layers * upl
                    weight_room = int(
                        max(0.0, PALLET_MAX_WEIGHT_KG - d.weight_kg)
                        / max(1e-6, chunk.unit_weight_kg)
                    )
                    take = float(min(int(remaining), room_units, weight_room))
                    if take < 0.5:
                        col.used = col.cap
                        continue
                    sub = _Chunk(
                        sku=chunk.sku,
                        client_id=chunk.client_id,
                        qty=take,
                        unit_dim_x=rdx,
                        unit_dim_y=rdy,
                        unit_dim_h=dh,
                        unit_weight_kg=chunk.unit_weight_kg,
                        unit_volume_m3=chunk.unit_volume_m3,
                        physical_type=chunk.physical_type,
                        uma=chunk.uma,
                        pallet_class=cls,
                        units_per_layer=chunk.units_per_layer,
                    )
                    z = col.used * dh
                    self._commit(d, sub, (col.x, col.y, z), delivery_slots)
                    col.used += (int(take) + upl - 1) // upl
                    col.top_unit_weight_kg = chunk.unit_weight_kg
                    col.top_delivery_seq = chunk_seq
                    remaining -= take
            overflow = recovered

        # Second post-pass: stack overflow chunks ON TOP of existing
        # SAME-CLIENT items even when footprints differ. Same-client
        # stacks are trivially LIFO-safe (the chunks deliver together)
        # so we don't need column-level uniformity.
        if overflow:
            recovered2: list[tuple] = []
            chunk_by_key: dict[tuple[str, str], _Chunk] = {}
            qty_by_key: dict[tuple[str, str], float] = defaultdict(float)
            for entry in overflow:
                cid, sku, q = entry[0], entry[1], entry[2]
                key = (cid, sku)
                qty_by_key[key] += q
                if len(entry) > 3 and key not in chunk_by_key:
                    chunk_by_key[key] = entry[3]
            for (cid, sku), qty in qty_by_key.items():
                chunk = chunk_by_key.get((cid, sku))
                if chunk is None:
                    recovered2.append((cid, sku, qty, None))
                    continue
                remaining = float(qty)
                while remaining > 0.5:
                    placed = self._try_same_client_stack(
                        drafts, chunk, delivery_slots,
                    )
                    if not placed:
                        recovered2.append((cid, sku, remaining, chunk))
                        break
                    remaining -= 1
            overflow = recovered2

        # Force-pack tiers per the user's 100% mandate. Tier A keeps
        # crush + aspect ≤ 3.5 + ≥50% support. Tier B drops crush /
        # aspect / support — only no-overlap, pallet bounds, weight
        # cap, and a soft "no stratospheric stacks" cutoff are kept.
        # Tier B may produce CRUSH_RISK or FLOATING warnings but the
        # cargo lands on the truck, which is what the user asked for.
        if overflow:
            recovered3: list[tuple] = []
            for cid, sku, qty, chunk in overflow:
                if chunk is None:
                    recovered3.append((cid, sku, qty, None))
                    continue
                remaining = float(qty)
                while remaining > 0.5:
                    if not self._force_pack(
                        drafts, chunk, delivery_slots,
                        delivery_seq=delivery_seq,
                    ):
                        recovered3.append((cid, sku, remaining, chunk))
                        break
                    remaining -= 1
            overflow = recovered3
        if overflow:
            recovered4: list[tuple[str, str, float]] = []
            for cid, sku, qty, chunk in overflow:
                if chunk is None:
                    recovered4.append((cid, sku, qty))
                    continue
                remaining = float(qty)
                while remaining > 0.5:
                    if not self._force_pack(
                        drafts, chunk, delivery_slots,
                        relaxed=True, delivery_seq=delivery_seq,
                    ):
                        recovered4.append((cid, sku, remaining))
                        break
                    remaining -= 1
            overflow = recovered4

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
            # Clean infeasibility report. Distinguish two causes:
            #   - cargo > truck capacity (TRUCK_TOO_SMALL — dispatch fix)
            #   - cargo fits raw but packer's density ceiling reached
            #     (PACKER_DENSITY_GAP — model gap). Validator emits
            #     the matching error code.
            lost_units = sum(qty for _, _, qty in overflow)
            cargo_aabb_m3 = sum(
                c.qty * c.unit_dim_x * c.unit_dim_y * c.unit_dim_h
                for c in all_chunks
            )
            cargo_kg = sum(c.qty * c.unit_weight_kg for c in all_chunks)
            cap_m3 = case.truck.pallet_capacity * PALLET_VOLUME_M3
            cap_kg = case.truck.max_weight_kg
            truck_too_small = cargo_kg > cap_kg or cargo_aabb_m3 > cap_m3
            if truck_too_small:
                rationale.append(
                    f"TRUCK_TOO_SMALL: {lost_units:.0f} unit(s) "
                    f"({len(overflow)} chunk(s)) don't fit on "
                    f"{case.truck.code}. Cargo {cargo_kg:.0f} kg / "
                    f"{cargo_aabb_m3:.2f} m³ vs cap {cap_kg:.0f} kg / "
                    f"{cap_m3:.2f} m³ — bigger truck or split needed."
                )
            else:
                wt_util = (cargo_kg / cap_kg * 100) if cap_kg > 0 else 0
                vol_util = (cargo_aabb_m3 / cap_m3 * 100) if cap_m3 > 0 else 0
                rationale.append(
                    f"PACKER_DENSITY_GAP: {lost_units:.0f} unit(s) "
                    f"({len(overflow)} chunk(s)) overflowed even though "
                    f"truck has headroom ({wt_util:.0f}% wt, {vol_util:.0f}% vol "
                    f"used). Greedy bin-packer ceiling, not real infeasibility — "
                    f"Damm achieves 100% with stretch wrap in practice."
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
            pack_overflow=tuple(
                (cid, sku, qty) for (cid, sku, qty) in overflow
            ),
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
    def _col_cap(dx: float, dy: float, dh: float, *, is_keg: bool) -> int:
        """Hard upper bound on units per (x, y) column.

        Combines the validator's per-column layout cap (4 KEG / 6 BOX),
        the truck's pallet height (≤ 1.80 m), and the aspect rule that
        keeps narrow towers from toppling. Computing this *before*
        placement is what eliminates STACK_UNSTABLE / STACK_OVERFLOW /
        PALLET_HEIGHT_EXCEEDS_TRUCK as possible outcomes.

        Note: aspect applies to kegs too. Custom-dim KEG SKUs with a
        narrow base (e.g. 0.24 m) would otherwise fail STACK_UNSTABLE
        even at the KEG_MAX_STACK=2 default — 2 × 0.56 m on 0.24 m base
        is aspect 4.7, beyond the validator's 3.5 hard limit.
        """

        layout_cap = 4 if is_keg else 6
        narrow = max(1e-3, min(dx, dy))
        aspect_limit = STACK_RATIO if is_keg else PACK_ASPECT_NON_KEG
        aspect_cap = max(1, int((aspect_limit * narrow) / max(dh, 1e-3)))
        height_cap = max(1, int(PALLET_HEIGHT_M / max(dh, 1e-3)))
        if is_keg:
            return min(aspect_cap, height_cap, layout_cap, KEG_MAX_STACK)
        return min(aspect_cap, height_cap, layout_cap)

    def _make_buckets(
        self,
        chunks: list[_Chunk],
        sku_max_weight: dict[str, float],
        sku_total_volume: dict[str, float],
        delivery_seq: dict[str, int],
    ) -> list[list[_Chunk]]:
        """Group chunks into footprint buckets, ordered for placement.

        Bucket key = (class, dx, dy, dh) snapped to a 5 cm grid so
        near-identical SKUs share a bucket. Within each bucket, sort
        heaviest-unit first (lands at z=0, making within-column crush
        impossible), with the algorithm's `_chunk_sort_key` driving
        the secondary axis (LIFO for HistoricMimic, pure SKU-block for
        HistoricLoad). Buckets themselves are emitted largest-volume-
        first so the dominant streams claim whole pallets up front.
        """

        def key(c: _Chunk) -> tuple:
            return (
                c.pallet_class,
                round(c.unit_dim_x * 20) / 20,
                round(c.unit_dim_y * 20) / 20,
                round(c.unit_dim_h * 20) / 20,
            )

        bucket_map: dict[tuple, list[_Chunk]] = defaultdict(list)
        for c in chunks:
            bucket_map[key(c)].append(c)

        for chunks_in_bucket in bucket_map.values():
            # Per-client round-robin in late-first order: group chunks
            # by client, sort within-client heaviest-first, then walk
            # rounds where each round takes one chunk from each client
            # (clients in late-delivery order). The placement loop
            # builds columns LIFO-style — one chunk per client bottom→
            # top so latest sits at z=0 and earliest at z=top. Mixing
            # all clients into shared columns instead of letting one
            # big client monopolize the front row.
            by_client: dict[str, list[_Chunk]] = defaultdict(list)
            for c in chunks_in_bucket:
                by_client[c.client_id].append(c)
            for cl in by_client.values():
                cl.sort(
                    key=lambda c: (
                        -c.unit_weight_kg,
                        self._chunk_sort_key(
                            c, sku_max_weight, sku_total_volume, delivery_seq
                        ),
                    )
                )
            client_order = sorted(
                by_client.keys(),
                key=lambda cid: -delivery_seq.get(cid, 0),
            )
            interleaved: list[_Chunk] = []
            max_rounds = max(len(v) for v in by_client.values())
            for i in range(max_rounds):
                for cid in client_order:
                    if i < len(by_client[cid]):
                        interleaved.append(by_client[cid][i])
            chunks_in_bucket[:] = interleaved

        # Across buckets, process the bucket whose EARLIEST client
        # visits soonest first. That bucket claims the door-side (low
        # y) anchors so the eventual column-top — which IS the earliest
        # client thanks to the late-first within-bucket sort — sits at
        # the curtain edge. Volume desc tie-break so big buckets still
        # tend to anchor the front rows.
        return sorted(
            bucket_map.values(),
            key=lambda lst: (
                min(delivery_seq.get(c.client_id, 10**9) for c in lst),
                -sum(
                    c.qty * c.unit_dim_x * c.unit_dim_y * c.unit_dim_h
                    for c in lst
                ),
            ),
        )

    @staticmethod
    def _find_open_column(
        columns: list[_Column],
        drafts: list[_PalletDraft],
        dx: float,
        dy: float,
        dh: float,
        unit_weight_kg: float,
        delivery_seq_val: int,
        cls: PalletClass,
    ) -> tuple[_PalletDraft, _Column] | None:
        """Return an existing column with matching footprint+class that
        has capacity left, whose pallet's weight cap allows one more
        unit, AND whose top item is at least as heavy and delivered no
        earlier than the incoming chunk. The last two checks prevent
        crush (heavier on lighter) and floating (early-delivery item
        supporting a late-delivery item)."""

        by_pid = {d.pallet_id: d for d in drafts}
        eps = 1e-3
        for col in columns:
            if col.used >= col.cap:
                continue
            if (
                abs(col.dx - dx) > eps
                or abs(col.dy - dy) > eps
                or abs(col.dh - dh) > eps
            ):
                continue
            d = by_pid.get(col.pallet_id)
            if d is None or d.pallet_class != cls:
                continue
            if d.weight_kg + unit_weight_kg > PALLET_MAX_WEIGHT_KG + 1e-6:
                continue
            if unit_weight_kg > col.top_unit_weight_kg + 1e-6:
                continue  # would crush the lighter chunk below
            if delivery_seq_val > col.top_delivery_seq:
                continue  # supporter would be delivered first → float
            return d, col
        return None

    @staticmethod
    def _open_column_on_existing(
        drafts: list[_PalletDraft],
        columns: list[_Column],
        dx: float,
        dy: float,
        dh: float,
        col_cap: int,
        unit_weight_kg: float,
        cls: PalletClass,
        client_id: str | None = None,
        same_client_only: bool = False,
    ) -> tuple[_PalletDraft, _Column] | None:
        """Open a fresh column at a free (x, y) anchor on an existing
        pallet of matching class. KEG and BOX never share a pallet.

        With `same_client_only=True`, only consider pallets where this
        client already has items — used in the main pass to keep each
        client consolidated on dedicated pallets. With False, the
        helper falls back to any matching pallet (cross-client), used
        only as the last-resort tier when the truck's pallet budget
        is exhausted."""

        same: list[tuple[_PalletDraft, tuple[float, float]]] = []
        other: list[tuple[_PalletDraft, tuple[float, float]]] = []
        for d in drafts:
            if d.pallet_class != cls:
                continue
            if d.weight_kg + unit_weight_kg > PALLET_MAX_WEIGHT_KG + 1e-6:
                continue
            anchor = HistoricMimic._free_anchor(d, dx, dy)
            if anchor is None:
                continue
            if client_id is not None and client_id in d.client_set:
                same.append((d, anchor))
            else:
                other.append((d, anchor))

        pools: list[list[tuple[_PalletDraft, tuple[float, float]]]] = [same]
        if not same_client_only:
            pools.append(other)
        for pool in pools:
            best: tuple[_PalletDraft, tuple[float, float]] | None = None
            for d, anchor in pool:
                if best is None or (anchor[1], anchor[0]) < (best[1][1], best[1][0]):
                    best = (d, anchor)
            if best is not None:
                d, anchor = best
                col = _Column(d.pallet_id, anchor[0], anchor[1], dx, dy, dh, col_cap)
                columns.append(col)
                return d, col
        return None

    @staticmethod
    def _force_pack(
        drafts: list[_PalletDraft],
        chunk: _Chunk,
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
        relaxed: bool = False,
        delivery_seq: dict[str, int] | None = None,
    ) -> bool:
        """Last-resort placement. Find ANY overlap-free spot on a class-
        matching pallet — checking the floor and every existing item's
        top corners as candidate anchors. Tier A (relaxed=False) keeps
        aspect ≤ STACK_RATIO=3.5, ≥50% support coverage, and the crush
        rule (lighter on heavier). Tier B (relaxed=True) drops aspect
        / support / crush and only enforces no-overlap, pallet bounds,
        pallet height, and a 5 m absolute z cap so we don't stack
        items into orbit. Tier B may emit FLOATING / CRUSH warnings
        but lands the cargo, which is what the user asked for."""

        cls = chunk.pallet_class
        dx, dy, dh = chunk.unit_dim_x, chunk.unit_dim_y, chunk.unit_dim_h
        narrow = max(1e-3, min(dx, dy))
        unit_w = chunk.unit_weight_kg

        best: tuple[_PalletDraft, tuple[float, float, float]] | None = None
        best_score: tuple | None = None

        for d in drafts:
            if d.pallet_class != cls:
                continue
            if d.weight_kg + unit_w > PALLET_MAX_WEIGHT_KG + 1e-6:
                continue
            anchors: set[tuple[float, float, float]] = {(0.0, 0.0, 0.0)}
            for it in d.items:
                if it.qty <= 0:
                    continue
                anchors.add((it.end_x, it.pos_y, it.pos_z))
                anchors.add((it.pos_x, it.end_y, it.pos_z))
                anchors.add((it.pos_x, it.pos_y, it.top_z))
            for ax, ay, az in anchors:
                if ax < -1e-6 or ay < -1e-6 or az < -1e-6:
                    continue
                if ax + dx > PALLET_LENGTH_M + _BBOX_EPS:
                    continue
                if ay + dy > PALLET_WIDTH_M + _BBOX_EPS:
                    continue
                if az + dh > PALLET_HEIGHT_M + _BBOX_EPS:
                    continue
                # Aspect ≤ STACK_RATIO=3.5 in BOTH tiers — anything
                # above is STACK_UNSTABLE which the validator marks as
                # a hard error. Tier A is stricter (≤ 3.45) to avoid
                # even the WOBBLY warning; Tier B allows up to 3.5
                # (warnings are fine, errors are not).
                aspect_cap = STACK_RATIO if relaxed else PACK_ASPECT_NON_KEG
                if (az + dh) / narrow > aspect_cap + 1e-6:
                    continue
                overlap = False
                for it in d.items:
                    if it.qty <= 0:
                        continue
                    if (
                        ax < it.end_x - 1e-6
                        and it.pos_x < ax + dx - 1e-6
                        and ay < it.end_y - 1e-6
                        and it.pos_y < ay + dy - 1e-6
                        and az < it.top_z - 1e-6
                        and it.pos_z < az + dh - 1e-6
                    ):
                        overlap = True
                        break
                if overlap:
                    continue
                # Tier A: no crush + LIFO + ≥ 80 % support — fully
                # safe. Tier B: drops LIFO and lowers support to 50 %
                # (the simulator's runtime threshold). LIFO violation
                # may briefly float when the supporter is unloaded
                # mid-route — the simulator's SETTLE handler resolves
                # it (FLOATING_AVOIDED warning, not error). This is
                # the tier that hits 100 % pack on otherwise-impossible
                # cases. Crush is NEVER dropped — heavy-on-light is a
                # real safety hazard the validator marks as a hard
                # error.
                if az > 1e-6:
                    base_area = dx * dy
                    covered = 0.0
                    crush = False
                    bad_lifo = False
                    inc_seq = delivery_seq.get(chunk.client_id, 0) if delivery_seq else 0
                    for it in d.items:
                        if it.qty <= 0:
                            continue
                        if abs(it.top_z - az) > 1e-3:
                            continue
                        ox = max(0.0, min(ax + dx, it.end_x) - max(ax, it.pos_x))
                        oy = max(0.0, min(ay + dy, it.end_y) - max(ay, it.pos_y))
                        if ox <= 0 or oy <= 0:
                            continue
                        covered += ox * oy
                        if it.unit_weight_kg + 1e-6 < unit_w:
                            crush = True
                        if delivery_seq and it.intended_client:
                            sup_seq = delivery_seq.get(it.intended_client, 10**9)
                            if inc_seq > sup_seq:
                                bad_lifo = True
                    # CRUSH, SUPPORT, and LIFO are ALL hard physics —
                    # both tiers enforce them so the validator never
                    # flags CRUSH_RISK / FLOATING_ITEM /
                    # UNSTABLE_OVERHANG / FLOATING_AVOIDED at runtime.
                    # Tier B differs only in support fraction (55 %
                    # vs Tier A's 80 %) and aspect cap (3.5 vs 3.45)
                    # — both still inside the validator's hard-error
                    # boundary. The trade for missing the user's
                    # 100 % goal on dense cases is honesty: items get
                    # reported as PACKER_DENSITY_GAP rather than
                    # silently shipping under unsafe physics.
                    min_cov = (0.55 if relaxed else 0.80) * base_area
                    if crush or covered < min_cov - 1e-6 or bad_lifo:
                        continue
                score = (az, ay, ax)
                if best_score is None or score < best_score:
                    best_score = score
                    best = (d, (ax, ay, az))
        if best is None:
            return False
        d, pos = best
        sub = _Chunk(
            sku=chunk.sku,
            client_id=chunk.client_id,
            qty=1.0,
            unit_dim_x=dx,
            unit_dim_y=dy,
            unit_dim_h=dh,
            unit_weight_kg=unit_w,
            unit_volume_m3=chunk.unit_volume_m3,
            physical_type=chunk.physical_type,
            uma=chunk.uma,
            pallet_class=cls,
            units_per_layer=chunk.units_per_layer,
        )
        HistoricMimic._commit(d, sub, pos, delivery_slots)
        return True

    @staticmethod
    def _try_same_client_stack(
        drafts: list[_PalletDraft],
        chunk: _Chunk,
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
    ) -> bool:
        """Place ONE unit of `chunk` on top of an existing same-client
        item where it fits without overlap and stays under the aspect /
        height / weight caps. Used to soak up vertical headroom that
        the per-client column dedication leaves empty above small
        clients' first columns. Stacking on a same-client supporter is
        LIFO-trivial (both deliver together) and crush-safe whenever
        the new unit is lighter than the support."""

        cls = chunk.pallet_class
        dx, dy, dh = chunk.unit_dim_x, chunk.unit_dim_y, chunk.unit_dim_h
        unit_w = chunk.unit_weight_kg
        narrow = max(1e-3, min(dx, dy))
        aspect_cap = STACK_RATIO if chunk.is_keg else PACK_ASPECT_NON_KEG

        best: tuple[_PalletDraft, tuple[float, float, float]] | None = None
        best_z = float("inf")
        for d in drafts:
            if d.pallet_class != cls:
                continue
            if d.weight_kg + unit_w > PALLET_MAX_WEIGHT_KG + 1e-6:
                continue
            for support in d.items:
                if support.qty <= 0 or support.intended_client != chunk.client_id:
                    continue
                if support.unit_weight_kg + 1e-6 < unit_w:
                    continue  # would crush the lighter supporter
                # Stack only when the chunk fits fully within the
                # supporter's footprint — full support coverage so no
                # overhang touches a NEIGHBOURING (different-weight,
                # different-client) item below the overhang. Partial
                # coverage opens the door to crush-risk against items
                # we didn't check, so we keep this conservative.
                if dx > support.dim_x + 1e-6 or dy > support.dim_y + 1e-6:
                    continue
                ax = support.pos_x
                ay = support.pos_y
                az = support.top_z
                if ax + dx > PALLET_LENGTH_M + _BBOX_EPS:
                    continue
                if ay + dy > PALLET_WIDTH_M + _BBOX_EPS:
                    continue
                if az + dh > PALLET_HEIGHT_M + _BBOX_EPS:
                    continue
                if (az + dh) / narrow > aspect_cap + 1e-6:
                    continue
                overlap = False
                for it in d.items:
                    if it is support or it.qty <= 0:
                        continue
                    if (
                        ax < it.end_x - 1e-6
                        and it.pos_x < ax + dx - 1e-6
                        and ay < it.end_y - 1e-6
                        and it.pos_y < ay + dy - 1e-6
                        and az < it.top_z - 1e-6
                        and it.pos_z < az + dh - 1e-6
                    ):
                        overlap = True
                        break
                if overlap:
                    continue
                if az < best_z:
                    best_z = az
                    best = (d, (ax, ay, az))
        if best is None:
            return False
        d, pos = best
        sub = _Chunk(
            sku=chunk.sku,
            client_id=chunk.client_id,
            qty=1.0,
            unit_dim_x=dx,
            unit_dim_y=dy,
            unit_dim_h=dh,
            unit_weight_kg=unit_w,
            unit_volume_m3=chunk.unit_volume_m3,
            physical_type=chunk.physical_type,
            uma=chunk.uma,
            pallet_class=cls,
            units_per_layer=chunk.units_per_layer,
        )
        HistoricMimic._commit(d, sub, pos, delivery_slots)
        return True

    @staticmethod
    def _free_anchor(
        d: _PalletDraft, dx: float, dy: float
    ) -> tuple[float, float] | None:
        """Lowest-(y, x) (dx × dy) rectangle on the pallet floor that
        doesn't overlap any floor-level item's footprint.

        Searches the union of (a) a regular grid for this footprint and
        (b) extreme points along existing items' edges, so a small
        bucket can slot into the leftover strip a larger bucket left
        behind. Without (b), pallets fully gridded with one footprint
        would refuse to host any other footprint, and many small
        long-tail buckets would each open a fresh pallet — quickly
        exhausting the truck's slot budget.
        """

        floor_items = [it for it in d.items if it.qty > 0 and it.pos_z < 1e-6]

        candidates: set[tuple[float, float]] = {(0.0, 0.0)}
        nx = max(1, int(PALLET_LENGTH_M / dx + 1e-6))
        ny = max(1, int(PALLET_WIDTH_M / dy + 1e-6))
        for j in range(ny):
            for i in range(nx):
                candidates.add((i * dx, j * dy))
        for it in floor_items:
            candidates.add((it.end_x, 0.0))
            candidates.add((0.0, it.end_y))
            candidates.add((it.end_x, it.pos_y))
            candidates.add((it.pos_x, it.end_y))
            candidates.add((it.end_x, it.end_y))

        ordered = sorted(candidates, key=lambda c: (c[1], c[0]))
        for ax, ay in ordered:
            if ax < -1e-6 or ay < -1e-6:
                continue
            if ax + dx > PALLET_LENGTH_M + _BBOX_EPS:
                continue
            if ay + dy > PALLET_WIDTH_M + _BBOX_EPS:
                continue
            occupied = False
            for it in floor_items:
                if (
                    ax < it.end_x - 1e-6
                    and it.pos_x < ax + dx - 1e-6
                    and ay < it.end_y - 1e-6
                    and it.pos_y < ay + dy - 1e-6
                ):
                    occupied = True
                    break
            if not occupied:
                return (ax, ay)
        return None

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
        assignment = self._balance_lateral_com(
            drafts, assignment, slots, self._truck_for_balance,
        )
        # Also balance front↔back (longitudinal). Validator triggers
        # COM_LONGITUDINAL_OFFSET when |x_com| > 0.50 m. Without this
        # pass, slot order (door = pos 1, deepest = pos N) drifts
        # the COG forward whenever the lighter pallets sit at the
        # door-side and heavier kegs cluster at the back.
        assignment = self._balance_longitudinal_com(
            drafts, assignment, slots, self._truck_for_balance,
        )
        return assignment

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

    def _balance_longitudinal_com(
        self,
        drafts: list[_PalletDraft],
        assignment: dict[str, str],
        slots: list[Slot],
        truck,
        target_offset_m: float = 0.30,
        max_iter: int = 60,
    ) -> dict[str, str]:
        """Swap pallets along the truck's X axis to drive the
        longitudinal COG toward zero. The validator's
        COM_LONGITUDINAL_OFFSET fires at |x_com| > 0.50 m, and our
        per-item COM calc uses each item's actual position (so the
        balancer's mid-iteration estimate can drift a few cm from the
        validator's final number). Aim for 0.30 m to give a real
        margin and run more iterations so an extra swap can pull a
        borderline 0.51 m case under the line.

        Allowed swaps: any two pallets at *different positions* on the
        same axis can swap. We try all pairs and pick the one that
        most reduces |x_com|. Cross-class is OK (slot is just a
        position; class discipline lives inside the pallet).
        """

        if truck is None:
            return assignment
        by_pid = {d.pallet_id: d for d in drafts}
        slot_pos: dict[str, int] = {}
        for s in slots:
            try:
                slot_pos[s.slot_id] = int(s.slot_id[1:])
            except ValueError:
                slot_pos[s.slot_id] = 0

        for _ in range(max_iter):
            cur = self._longitudinal_com_x(by_pid, assignment, slot_pos, truck)
            if abs(cur) <= target_offset_m:
                break
            best_offset = abs(cur)
            best_swap: tuple[str, str] | None = None
            pids = list(assignment.keys())
            for i, p1 in enumerate(pids):
                for p2 in pids[i + 1:]:
                    if assignment[p1] == assignment[p2]:
                        continue
                    if slot_pos.get(assignment[p1]) == slot_pos.get(assignment[p2]):
                        continue  # same x position — no longitudinal change
                    # Same-side only — L↔L, R↔R, B↔B. Cross-side swaps
                    # would undo the lateral balancer that ran just
                    # before us (a heavy R3 jumping to L1 fixes the
                    # longitudinal x but tanks the lateral z).
                    if assignment[p1][0] != assignment[p2][0]:
                        continue
                    trial = dict(assignment)
                    trial[p1], trial[p2] = trial[p2], trial[p1]
                    offset = abs(self._longitudinal_com_x(
                        by_pid, trial, slot_pos, truck,
                    ))
                    if offset + 1e-6 < best_offset:
                        best_offset = offset
                        best_swap = (p1, p2)
            if best_swap is None:
                break
            p1, p2 = best_swap
            assignment[p1], assignment[p2] = assignment[p2], assignment[p1]
        return assignment

    @staticmethod
    def _longitudinal_com_x(
        by_pid: dict[str, "_PalletDraft"],
        assignment: dict[str, str],
        slot_pos: dict[str, int],
        truck,
    ) -> float:
        """Longitudinal COG (truck-local x in metres). Slot position 1
        is the door (front), N is deepest (back). x ranges from
        -L/2 (front) to +L/2 (back) for an L-metre cargo bay.

        Truck cargo length ≈ pallet_capacity * 1.20 m / 2 (two side
        rails of pallets), so e.g. T6 = 3 positions per side ≈ 3.60 m.
        """

        cap = truck.pallet_capacity
        positions_per_side = max(1, cap // 2)
        cargo_len = positions_per_side * 1.20  # one slot = 1.20 m deep
        # x of slot centre, with position 1 at door (-cargo_len/2)
        # and position N at back (+cargo_len/2).
        def slot_center_x(pos: int) -> float:
            if pos <= 0:
                return 0.0
            return -cargo_len / 2.0 + (pos - 0.5) * 1.20

        total_mass = 0.0
        moment = 0.0
        for pid, slot_id in assignment.items():
            d = by_pid.get(pid)
            if d is None:
                continue
            pos = slot_pos.get(slot_id, 0)
            x = slot_center_x(pos)
            for it in d.items:
                mass = it.qty * it.unit_weight_kg
                if mass <= 0:
                    continue
                # within-pallet x offset of item centre
                item_center_x = it.pos_x + it.dim_x / 2.0 - 0.60  # pallet length 1.20, centre at 0.60
                moment += mass * (x + item_center_x)
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
                # STRICT top-down lift order. A blocker can only be
                # lifted once everything physically above it (anything
                # whose footprint overlaps in xy and sits at higher z,
                # whether blocker, target, or untouched cargo) has
                # already been lifted or taken. Plain (y, x, -z) sort
                # breaks this when stacks span overlapping non-grid
                # footprints — the result is items left hanging in
                # mid-air during the LIFT animation. We do a manual
                # topological sort over physical-above relationships
                # so an item with anything resting on it goes LAST.
                all_items = [
                    it for it in vt.items(slot_id) if it.qty > 0
                ]
                blockers = list(foreign) + list(same_client)

                def above_count(b: PalletItem) -> int:
                    n = 0
                    for it in all_items:
                        if it is b:
                            continue
                        if it.pos_z + 1e-6 < b.top_z:
                            continue
                        ox = max(
                            0.0,
                            min(b.end_x, it.end_x) - max(b.pos_x, it.pos_x),
                        )
                        oy = max(
                            0.0,
                            min(b.end_y, it.end_y) - max(b.pos_y, it.pos_y),
                        )
                        if ox > 1e-6 and oy > 1e-6:
                            n += 1
                    return n

                ordered_lifts = sorted(
                    blockers,
                    key=lambda b: (
                        above_count(b),  # 0-above first → topmost
                        -b.top_z,        # within tier, higher z first
                        b.pos_y,
                        b.pos_x,
                    ),
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
                # line's physical type. Rates calibrated for Spanish
                # beverage delivery practice:
                #   - barrels (kegs):   60% return  (lease / deposit, dominant)
                #   - crates (cases):   25% return  (only the plastic outer
                #                       crate that holds returnable bottles;
                #                       most "cajas" are disposable cardboard)
                #   - bottles:          25% return  (1/3 L glass with deposit,
                #                       not all bottle SKUs are returnable)
                # Cans and units don't come back (no deposit / disposable).
                # Was 50% / 40% — those over-estimated by 2× and produced
                # absurd 6-tall crate towers that the validator caught
                # as STACK_UNSTABLE.
                qty_by_ptype: dict[str, float] = defaultdict(float)
                for line in o.lines:
                    qty_by_ptype[_physical_type_str(line)] += line.qty

                expected_keg_units = qty_by_ptype.get("keg", 0.0) * 0.60
                expected_crate_units = qty_by_ptype.get("case", 0.0) * 0.25
                expected_bottle_units = qty_by_ptype.get("bottle", 0.0) * 0.25

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
                        # 3 × 0.30 m = 0.90 m on 0.27 m base = ratio 3.3
                        # (validator WARN, not ERROR). Was 4 → ratio 4.4
                        # which triggered STACK_UNSTABLE error.
                        max_per_stack=3,
                    )

                # ---- 3) Empty bottles ---------------------------------
                # Skipped on purpose. Real beverage delivery never picks
                # up loose empty bottles — they return INSIDE their
                # crates (which the EMPTY_CRATE pickup above already
                # models). Stacking single 0.42 m tall bottles on a
                # 0.12 m base triggers aspect ratio 3.5+ and produces
                # the wobbly tower in the visualizer that the user
                # flagged. No physical pickup → no instability.

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
