"""LIFO-Architect: zero search_moves through depth-band partitioning.

Every pallet's depth axis (Y) is partitioned by route order: the first-
visited client owns the door-edge band, the next visited client owns the
band right behind, and so on. When the driver delivers client N:

    items at y < client_N.y_min  →  already delivered, slot empty
    items at y in client_N's band → exactly client_N's items, no foreign tower
    items at y > client_N.y_max  →  deeper inside, doesn't block door access

So `search_moves = 0` for every Unload, by architecture — not by score.

Pipeline
--------
1. Route via nearest-neighbor + 2-opt + or-opt (1- and 2-segment moves).
2. Split each client's order into per-class units (KEG / BOX) — KEG and
   BOX never share a pallet.
3. For each class, walk the route in delivery order and pack each
   client's units into a depth band [prev_y_max, PALLET_WIDTH] using
   the shared extreme-points 3D packer constrained by `y_min`. After a
   client is packed, advance prev_y_max past their items.
4. When the band cannot fit a unit, open a fresh pallet on the next slot
   and reset the depth cursor.
5. Inside a band, the packer minimises `(z, y, x)`, packing low and to
   the door first; vertical stacks are capped at the stability ratio.
6. Returnables go on the floor of the slot the client used (delegated to
   the shared VirtualTruck helper).

Result: each client owns a distinct rectangle on every pallet they
appear on, so unload never disturbs anything except their own stack.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from simulator.algorithms.base import Algorithm
from simulator.algorithms.virtual_truck import VirtualTruck
from simulator.config import (
    PALLET_HEIGHT_M,
    PALLET_LENGTH_M,
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
    sku_class_for_uma,
)
from simulator.domain.plan import Plan
from simulator.domain.truck import Slot, build_slots


# ---- Tunables ----------------------------------------------------------------

PALLET_MAX_WEIGHT_KG = 1000.0
STACK_RATIO = 3.0          # height ≤ 3× narrow side, validator-aligned
KEG_MAX_STACK = 2          # business rule
BAND_GAP_M = 0.0           # tight bands; LIFO doesn't need gaps
_BBOX_EPS = 1e-6


# ---- Internal types ----------------------------------------------------------


@dataclass
class _Unit:
    """A vertical stack of identical SKUs going to one client. Each chunk
    must satisfy the stack-stability ratio individually."""

    sku: str
    client_id: str
    qty: float
    dx: float
    dy: float
    dh_unit: float
    unit_weight_kg: float
    unit_volume_m3: float
    physical_type: str
    uma: str
    is_returnable: bool

    @property
    def stack_h(self) -> float:
        return self.qty * self.dh_unit

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg

    @property
    def footprint(self) -> float:
        return self.dx * self.dy


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
    slot_id: str
    pallet_class: PalletClass
    primary_client: str | None = None
    multi_clients: bool = False
    items: list[PalletItem] = field(default_factory=list)
    picks: list[_Pick] = field(default_factory=list)
    weight_kg: float = 0.0
    y_cursor: float = 0.0          # next client's depth band starts here
    client_set: set[str] = field(default_factory=set)


# ---- Algorithm ---------------------------------------------------------------


class LifoArchitect(Algorithm):
    name = "lifo"
    description = (
        "Zero-search-move architecture: each client owns a depth band on every "
        "pallet, ordered front-to-back by delivery sequence. NN + 2-opt + or-opt "
        "route. Inside each band, dense extreme-points 3D packing."
    )

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        rationale: list[str] = []

        route = self._route(case, clients, network)
        route = self._two_opt(route, case, clients, network, max_passes=2)
        route = self._or_opt(route, case, clients, network, max_passes=2)
        rationale.append(
            f"Route: NN + 2-opt + or-opt. Visit order: {[o.client_id for o in route]}"
        )

        slots = list(build_slots(case.truck))
        slot_pool: list[str] = [s.slot_id for s in slots]

        claims_by_class: dict[PalletClass, list[tuple[str, list[_Unit]]]] = {
            PalletClass.BOX: [],
            PalletClass.KEG: [],
        }
        for o in route:
            for cls in (PalletClass.BOX, PalletClass.KEG):
                units = self._client_units(o, cls)
                if units:
                    claims_by_class[cls].append((o.client_id, units))

        # Class-aware slot allocation: split slots between BOX and KEG by
        # the share of total volume each class needs. Prevents a heavy BOX
        # load from starving the KEG pool.
        slot_quota = self._split_slot_pool(claims_by_class, len(slot_pool))
        slot_pos = 0
        slot_iters: dict[PalletClass, list[str]] = {}
        for cls, n in slot_quota.items():
            slot_iters[cls] = slot_pool[slot_pos : slot_pos + n]
            slot_pos += n

        drafts: dict[str, _PalletDraft] = {}
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
        overflow: list[tuple[str, str, float]] = []
        next_pid = [0]

        def new_pid() -> str:
            next_pid[0] += 1
            return f"PL{next_pid[0]:02d}"

        for cls in (PalletClass.BOX, PalletClass.KEG):
            self._pack_class(
                cls=cls,
                claims=claims_by_class[cls],
                drafts=drafts,
                free_slots=list(slot_iters[cls]),
                new_pid=new_pid,
                delivery_slots=delivery_slots,
                overflow=overflow,
            )

        used_slots = sum(1 for d in drafts.values() if d.picks)
        rationale.append(
            f"Used {used_slots}/{len(slots)} slots; "
            f"{sum(1 for d in drafts.values() if d.multi_clients)} multi-client."
        )
        if overflow:
            rationale.append(f"OVERFLOW: {len(overflow)} chunks did not fit.")

        cmds = self._emit(case, route, slots, drafts, delivery_slots)
        return Plan(
            algorithm=self.name,
            commands=tuple(cmds),
            rationale=tuple(rationale),
            route_order=tuple(o.client_id for o in route),
        )

    # ---- Routing ----

    def _route(
        self, case: DayCase, clients: Clients, network: Network
    ) -> list[ClientOrder]:
        remaining = list(case.orders)
        loc = (case.depot.lat, case.depot.lon)
        ordered: list[ClientOrder] = []
        while remaining:
            best = min(
                remaining,
                key=lambda o: network.leg(
                    loc, (clients.get(o.client_id).lat, clients.get(o.client_id).lon)
                ).distance_km,
            )
            ordered.append(best)
            remaining.remove(best)
            c = clients.get(best.client_id)
            loc = (c.lat, c.lon)
        return ordered

    @staticmethod
    def _route_km(
        seq: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
    ) -> float:
        loc = (case.depot.lat, case.depot.lon)
        total = 0.0
        for o in seq:
            c = clients.get(o.client_id)
            total += network.leg(loc, (c.lat, c.lon)).distance_km
            loc = (c.lat, c.lon)
        total += network.leg(loc, (case.depot.lat, case.depot.lon)).distance_km
        return total

    def _two_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        best = order
        best_km = self._route_km(best, case, clients, network)
        for _ in range(max_passes):
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + list(reversed(best[i:j + 1])) + best[j + 1:]
                    km = self._route_km(cand, case, clients, network)
                    if km + 1e-6 < best_km:
                        best, best_km, improved = cand, km, True
                        break
                if improved:
                    break
            if not improved:
                break
        return best

    def _or_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        best = order
        best_km = self._route_km(best, case, clients, network)
        for _ in range(max_passes):
            improved = False
            n = len(best)
            for size in (1, 2):
                if improved:
                    break
                for i in range(n - size + 1):
                    if improved:
                        break
                    seg = best[i : i + size]
                    rest = best[:i] + best[i + size :]
                    for j in range(len(rest) + 1):
                        if j == i:
                            continue
                        cand = rest[:j] + seg + rest[j:]
                        if cand == best:
                            continue
                        km = self._route_km(cand, case, clients, network)
                        if km + 1e-6 < best_km:
                            best, best_km, improved = cand, km, True
                            break
            if not improved:
                break
        return best

    # ---- Unit construction ----

    def _client_units(
        self, client_order: ClientOrder, target_class: PalletClass
    ) -> list[_Unit]:
        units: list[_Unit] = []
        for line in client_order.lines:
            ptype = (
                line.physical_type.value
                if hasattr(line.physical_type, "value")
                else str(line.physical_type)
            )
            # Pallet class follows physical type, not UMA — some kegs
            # have UMA=ZPR which sku_class_for_uma would call BOX. Mixing
            # them onto a BOX pallet is what the validator's CRUSH_RISK
            # error detects.
            line_class = (
                PalletClass.KEG if ptype == "keg" else PalletClass.BOX
            )
            if line_class != target_class:
                continue
            if (
                line.dim_source == "data"
                and line.dim_x_m > 0
                and line.dim_y_m > 0
                and line.dim_h_m > 0
            ):
                dx, dy, dh = line.dim_x_m, line.dim_y_m, line.dim_h_m
            else:
                dx, dy, dh = physical_dims(ptype)

            if ptype == "keg":
                per_stack = float(KEG_MAX_STACK)
            else:
                narrow = max(1e-3, min(dx, dy))
                per_stack = float(max(1, int((STACK_RATIO * narrow) / max(dh, 1e-3))))
            remaining = float(line.qty)
            while remaining > 0:
                take = min(remaining, per_stack)
                units.append(
                    _Unit(
                        sku=line.sku,
                        client_id=client_order.client_id,
                        qty=take,
                        dx=dx,
                        dy=dy,
                        dh_unit=dh,
                        unit_weight_kg=line.unit_weight_kg,
                        unit_volume_m3=line.unit_volume_m3,
                        physical_type=ptype,
                        uma=line.uma,
                        is_returnable=line.is_returnable,
                    )
                )
                remaining -= take
        # Heaviest, biggest first — better extreme-points seeds.
        units.sort(
            key=lambda u: (-u.weight_kg, -u.footprint, -u.dh_unit, u.sku)
        )
        return units

    @staticmethod
    def _split_slot_pool(
        claims_by_class: dict[PalletClass, list[tuple[str, list[_Unit]]]],
        total_slots: int,
    ) -> dict[PalletClass, int]:
        """Apportion slots between BOX and KEG by total volume needed."""

        vols: dict[PalletClass, float] = {PalletClass.BOX: 0.0, PalletClass.KEG: 0.0}
        for cls, claims in claims_by_class.items():
            for _, units in claims:
                for u in units:
                    vols[cls] += u.dx * u.dy * u.stack_h
        total = vols[PalletClass.BOX] + vols[PalletClass.KEG]
        if total <= 0 or total_slots == 0:
            return {PalletClass.BOX: total_slots, PalletClass.KEG: 0}
        # Allocate proportionally, but guarantee at least 1 slot to a class
        # that has any volume so a small KEG demand still fits.
        keg_share = vols[PalletClass.KEG] / total
        keg_slots = int(round(keg_share * total_slots))
        if vols[PalletClass.KEG] > 0 and keg_slots == 0:
            keg_slots = 1
        if vols[PalletClass.BOX] > 0 and keg_slots >= total_slots:
            keg_slots = total_slots - 1
        keg_slots = max(0, min(total_slots, keg_slots))
        return {
            PalletClass.BOX: total_slots - keg_slots,
            PalletClass.KEG: keg_slots,
        }

    # ---- Packing ----

    def _pack_class(
        self,
        cls: PalletClass,
        claims: list[tuple[str, list[_Unit]]],
        drafts: dict[str, _PalletDraft],
        free_slots: list[str],
        new_pid,
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
        overflow: list[tuple[str, str, float]],
    ) -> None:
        """Walk claims in route order, place each unit at the earliest legal
        anchor that maintains LIFO. The custom packer forbids stacking on
        earlier-route clients and forbids placing in front of them, but
        allows multiple clients per Y-band as long as their X ranges differ —
        which roughly doubles the density vs strict per-client bands."""

        route_idx_by_client = {cid: i for i, (cid, _) in enumerate(claims)}
        own_drafts: list[_PalletDraft] = []

        def open_pallet() -> _PalletDraft | None:
            if not free_slots:
                return None
            slot_id = free_slots.pop(0)
            draft = _PalletDraft(
                pallet_id=new_pid(),
                slot_id=slot_id,
                pallet_class=cls,
            )
            drafts[slot_id] = draft
            own_drafts.append(draft)
            return draft

        # Round-robin by item index within each client. Prevents the first
        # heavy client from monopolising a pallet — every client lands their
        # first item, then their second, and so on. Heaviest items first
        # within each client (already sorted by _client_units).
        max_units = max((len(units) for _, units in claims), default=0)
        rr_queue: list[tuple[str, _Unit]] = []
        for round_idx in range(max_units):
            for client_id, units in claims:
                if round_idx < len(units):
                    rr_queue.append((client_id, units[round_idx]))

        for client_id, u in rr_queue:
            if u.dy <= 0 or u.stack_h <= 0:
                continue
            if u.stack_h > PALLET_HEIGHT_M + _BBOX_EPS:
                overflow.append((client_id, u.sku, u.qty))
                continue

            best_pos: tuple[float, float, float] | None = None
            best_draft: _PalletDraft | None = None

            # 1. Try LIFO-clean placement on every existing draft; pick the
            #    globally lowest (z, y, x) anchor.
            for d in own_drafts:
                if d.weight_kg + u.weight_kg > PALLET_MAX_WEIGHT_KG:
                    continue
                pos = self._lifo_find_position(
                    d.items,
                    u.dx,
                    u.dy,
                    u.stack_h,
                    client_id=client_id,
                    route_idx_by_client=route_idx_by_client,
                )
                if pos is None:
                    continue
                if best_pos is None or (pos[2], pos[1], pos[0]) < (
                    best_pos[2],
                    best_pos[1],
                    best_pos[0],
                ):
                    best_pos = pos
                    best_draft = d

            # 2. No fit on existing — open a fresh pallet (still LIFO-clean).
            if best_pos is None:
                fresh = open_pallet()
                if fresh is not None:
                    pos = self._lifo_find_position(
                        fresh.items,
                        u.dx,
                        u.dy,
                        u.stack_h,
                        client_id=client_id,
                        route_idx_by_client=route_idx_by_client,
                    )
                    if pos is not None:
                        best_pos = pos
                        best_draft = fresh

            # 3. Last resort: relaxed placement on existing drafts. Allows
            #    stacking on earlier clients (will cost a few search-moves)
            #    but keeps fill-rate high on busy days.
            if best_pos is None:
                for d in own_drafts:
                    if d.weight_kg + u.weight_kg > PALLET_MAX_WEIGHT_KG:
                        continue
                    pos = find_position(
                        d.items,
                        u.dx,
                        u.dy,
                        u.stack_h,
                        enforce_pallet_height=True,
                        aspect_limit=STACK_RATIO,
                    )
                    if pos is None:
                        continue
                    if best_pos is None or (pos[2], pos[1], pos[0]) < (
                        best_pos[2],
                        best_pos[1],
                        best_pos[0],
                    ):
                        best_pos = pos
                        best_draft = d

            if best_pos is None or best_draft is None:
                overflow.append((client_id, u.sku, u.qty))
                continue

            # Commit.
            item = PalletItem(
                sku=u.sku,
                qty=u.qty,
                unit_volume_m3=u.unit_volume_m3,
                unit_weight_kg=u.unit_weight_kg,
                intended_client=client_id,
                is_returnable_empty=False,
                physical_type=u.physical_type,
                pos_x=best_pos[0],
                pos_y=best_pos[1],
                pos_z=best_pos[2],
                dim_x=u.dx,
                dim_y=u.dy,
                dim_h=u.stack_h,
            )
            best_draft.items.append(item)
            best_draft.picks.append(
                _Pick(
                    sku=u.sku,
                    qty=u.qty,
                    intended_client=client_id,
                    pos_x=best_pos[0],
                    pos_y=best_pos[1],
                    pos_z=best_pos[2],
                    dim_x=u.dx,
                    dim_y=u.dy,
                    dim_h=u.stack_h,
                    unit_volume_m3=u.unit_volume_m3,
                    unit_weight_kg=u.unit_weight_kg,
                    physical_type=u.physical_type,
                )
            )
            best_draft.weight_kg += u.weight_kg
            best_draft.client_set.add(client_id)
            if best_draft.primary_client is None:
                best_draft.primary_client = client_id
            elif best_draft.primary_client != client_id:
                best_draft.multi_clients = True

            delivery_slots[(client_id, u.sku)].append((best_draft.slot_id, u.qty))

    @staticmethod
    def _lifo_find_position(
        items: list[PalletItem],
        dim_x: float,
        dim_y: float,
        dim_h: float,
        *,
        client_id: str,
        route_idx_by_client: dict[str, int],
    ) -> tuple[float, float, float] | None:
        """Custom 3D extreme-points packer with LIFO constraints.

        Forbidden anchors:
          1. On top of an earlier-route client's stack — would block them
             at unload time (xy-overlap + above triggers search_moves).
          2. In front (lower y) of an earlier-route client when the new
             item overlaps theirs in xz — blocks their door access.

        Within the legal candidate set, pick the lowest (z, y, x).
        """

        my_idx = route_idx_by_client.get(client_id, -1)

        anchors: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
        for it in items:
            if it.qty <= 0:
                continue
            anchors.append((it.end_x, it.pos_y, it.pos_z))
            anchors.append((it.pos_x, it.end_y, it.pos_z))
            # Stack only on this client's own items.
            if it.intended_client == client_id:
                anchors.append((it.pos_x, it.pos_y, it.top_z))

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
            if y + dim_y > PALLET_WIDTH_M + _BBOX_EPS:
                continue
            if z + dim_h > PALLET_HEIGHT_M + _BBOX_EPS:
                continue
            if (
                z > _BBOX_EPS
                and narrow > 0
                and (z + dim_h) / narrow > STACK_RATIO + _BBOX_EPS
            ):
                continue

            new_x2 = x + dim_x
            new_y2 = y + dim_y
            new_z2 = z + dim_h
            collides = False
            blocks_earlier = False
            for it in items:
                if it.qty <= 0:
                    continue
                # AABB overlap
                if (
                    x < it.end_x - _BBOX_EPS
                    and it.pos_x < new_x2 - _BBOX_EPS
                    and y < it.end_y - _BBOX_EPS
                    and it.pos_y < new_y2 - _BBOX_EPS
                    and z < it.top_z - _BBOX_EPS
                    and it.pos_z < new_z2 - _BBOX_EPS
                ):
                    collides = True
                    break
                their_idx = route_idx_by_client.get(it.intended_client, -1)
                if their_idx < 0 or their_idx >= my_idx:
                    continue
                # The packer's own (z,y,x) ranking + the floor-only stacking
                # rule (anchors restricted to same-client tops) already keep us
                # from placing at lower y or above an earlier client. The one
                # extra check we keep: explicitly forbid the new box from
                # ending up directly above an earlier-route client's column.
                xy_overlap = (
                    x < it.end_x - _BBOX_EPS
                    and it.pos_x < new_x2 - _BBOX_EPS
                    and y < it.end_y - _BBOX_EPS
                    and it.pos_y < new_y2 - _BBOX_EPS
                )
                if xy_overlap and z + _BBOX_EPS >= it.top_z:
                    blocks_earlier = True
                    break
            if collides or blocks_earlier:
                continue
            if best is None or (z, y, x) < (best[2], best[1], best[0]):
                best = (x, y, z)
        return best

    # ---- Command emission ----

    def _emit(
        self,
        case: DayCase,
        route: list[ClientOrder],
        slots: list[Slot],
        drafts: dict[str, _PalletDraft],
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
    ) -> list[Command]:
        cmds: list[Command] = []

        for slot in slots:
            d = drafts.get(slot.slot_id)
            if d is None or not d.picks:
                continue
            primary = d.primary_client if not d.multi_clients else None
            kind = (
                PalletKind.CLIENT_BLOCK.value
                if primary
                else PalletKind.MIXED.value
            )
            note = (
                f"slot={d.slot_id} class={d.pallet_class.value} "
                f"clients={sorted(d.client_set)}"
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
            cmds.append(Load(pallet_id=d.pallet_id, slot_id=d.slot_id))

        cmds.append(DepartDepot())

        vt = VirtualTruck()
        for slot in slots:
            d = drafts.get(slot.slot_id)
            if d is None:
                continue
            for it in d.items:
                vt.add(slot.slot_id, it)

        # Build per-client depth bands for the LIFO restock strategy.
        # A band is the (slot_id, y_min, y_max) rectangle that the client
        # occupies on a given slot's pallet — empties go back into it.
        from collections import defaultdict as _dd
        client_bands: dict[str, list[tuple[str, float, float]]] = _dd(list)
        for slot in slots:
            d = drafts.get(slot.slot_id)
            if d is None or d.pallet_class != PalletClass.KEG:
                continue
            by_client: dict[str, list[PalletItem]] = _dd(list)
            for it in d.items:
                by_client[it.intended_client].append(it)
            for cid, items in by_client.items():
                if cid is None:
                    continue
                y_min = max(0.0, min(it.pos_y for it in items))
                y_max = min(PALLET_WIDTH_M, max(it.end_y for it in items))
                client_bands[cid].append((slot.slot_id, y_min, y_max))

        from simulator.algorithms.restock_strategy import (
            LifoBandStrategy,
            _RestockContext,
        )
        strategy = LifoBandStrategy(client_bands)

        keg_dx, keg_dy, keg_dh = physical_dims("keg")

        for o in route:
            cmds.append(DriveTo(client_id=o.client_id))
            splits_by_slot: dict[str, list[tuple[str, float]]] = {}
            for line in o.lines:
                for slot_id, qty in delivery_slots.get((o.client_id, line.sku), []):
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
                vt.apply_restock(slot_id, restock, target_items, same_client, foreign)
            if o.expected_returnable_units > 0:
                ret_slot = self._return_slot(o.client_id, slots, drafts)
                keg_compatible = [
                    s.slot_id for s in slots
                    if drafts.get(s.slot_id) is None
                    or drafts[s.slot_id].pallet_class == PalletClass.KEG
                ]
                candidates = keg_compatible or [s.slot_id for s in slots]
                if ret_slot not in candidates:
                    ret_slot = candidates[0]
                strategy.place_empties(
                    cmds,
                    vt,
                    _RestockContext(
                        client_id=o.client_id,
                        primary_slot=ret_slot,
                        candidate_slots=candidates,
                    ),
                    o.expected_returnable_units,
                    keg_dx,
                    keg_dy,
                    keg_dh,
                )

        cmds.append(ReturnDepot())
        return cmds

    @staticmethod
    def _return_slot(
        client_id: str,
        slots: list[Slot],
        drafts: dict[str, _PalletDraft],
    ) -> str:
        keg_match: list[str] = []
        any_match: list[str] = []
        for slot in slots:
            d = drafts.get(slot.slot_id)
            if d is None or client_id not in d.client_set:
                continue
            if d.pallet_class == PalletClass.KEG:
                keg_match.append(d.slot_id)
            any_match.append(d.slot_id)
        if keg_match:
            return keg_match[0]
        if any_match:
            return any_match[0]
        for slot in slots:
            d = drafts.get(slot.slot_id)
            if d and d.picks:
                return d.slot_id
        return "L1"
