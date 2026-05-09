"""Balanced loader: reverse-delivery order + COG balancing + client compactness.

Pipeline:
  1. Route clients with nearest-neighbor + 2-opt.
  2. Process clients in REVERSE delivery order — last visit is loaded first
     and lands on the back-most pallet slot, so the first delivery is at the
     door.
  3. Within a client, items are sorted by (effective weight desc, footprint
     desc, height desc, sku) so heavy/wide items become stack bases.
  4. Each delivery line is split into physically realistic chunks (stack
     height never exceeds pallet height; kegs capped at 2 high).
  5. For every chunk, candidate slots are generated and scored:
        balance      (resulting truck center-of-gravity vs. ideal)
      + max_height   (resulting stack height ratio)
      + layer        (vertical layer penalty)
      + center_dist  (slot center vs. ideal)
      + locality     (penalize new slot / new stack for same client)
     The lowest-score candidate wins.
  6. Pallets accumulate items until weight, height or footprint runs out;
     KEG and BOX classes never share a pallet.

Output: a Plan whose commands the existing Simulator can execute end-to-end
(BuildPallet → Pick … → Load → Depart → DriveTo/Unload/PickupReturn → Return).
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
KEG_MAX_STACK = 2  # business rule from the loading spec
# A stack must satisfy `height ≤ STACK_RATIO × min(footprint side)` so it
# doesn't topple in transit. Matches the validator's STACK_RATIO_ERROR.
STACK_RATIO = 3.5
IDEAL_X = 0.52
IDEAL_Y = 0.50

# Balance is split X/Y because L/R imbalance is far more dangerous for the
# truck than front/back imbalance — Y carries 4x the weight. The penalty is
# quadratic so the score bites harder as we drift from the ideal.
W_BALANCE_X = 8.0
W_BALANCE_Y = 32.0
W_HEIGHT = 4.0
W_LAYER = 2.0
W_CENTER = 1.0
# Lower than before (was 6.0) so the loader spreads a heavy client across
# slots when needed for balance instead of glueing everything to one slot.
W_NEW_SLOT = 2.5
W_NEW_STACK = 1.5

_BBOX_EPS = 1e-6


def _find_position_safe(items, chunk):
    """Strict packing with progressive fallbacks. Returns (pos, tier).
    Heavy items (>5 kg/unit) NEVER cross the no-crush guarantee — if
    no non-crushing anchor is available in a slot, that slot returns
    None and the algorithm tries another slot (or an empty one).
    Light items keep the older relaxation path because the crush
    rule doesn't apply to them anyway."""

    base = dict(
        dim_x=chunk.unit_dim_x,
        dim_y=chunk.unit_dim_y,
        dim_h=chunk.stack_h,
        enforce_pallet_height=True,
        aspect_limit=STACK_RATIO,
        unit_weight_kg=chunk.unit_weight_kg,
    )

    is_heavy = chunk.unit_weight_kg >= 5.0

    # 1. Strict — require ≥50% support and no crush.
    pos = find_position(items, **base, require_support=True, avoid_crush=True)
    if pos is not None:
        return pos, 1
    # 2. Relax support to "at least touching" — but KEEP no-crush for
    # heavy chunks. For light chunks crush is irrelevant.
    pos = find_position(
        items, **base,
        require_support=True,
        min_support_fraction=0.001,
        avoid_crush=is_heavy,
        prefer_max_support=True,
    )
    if pos is not None:
        return pos, 2
    # 3. Light items only — relax support to any positive coverage.
    # Heavy items refuse a slot rather than risk crush.
    if not is_heavy:
        pos = find_position(
            items, **base,
            require_support=True,
            min_support_fraction=0.001,
            avoid_crush=False,
            prefer_max_support=True,
        )
        if pos is not None:
            return pos, 3
    return None, 0


# ---- Internal types ----------------------------------------------------------


@dataclass
class _LoadUnit:
    """One physical chunk pending placement (a vertical stack of identical units)."""

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
    is_returnable: bool

    @property
    def stack_h(self) -> float:
        return self.qty * self.unit_dim_h

    @property
    def footprint(self) -> float:
        return self.unit_dim_x * self.unit_dim_y

    @property
    def weight_kg(self) -> float:
        return self.qty * self.unit_weight_kg

    @property
    def is_keg(self) -> bool:
        return self.physical_type == "keg"

    @property
    def pallet_class(self) -> PalletClass:
        # Decide by physical type, not UMA — some SKUs are kegs
        # (e.g. ED30 BARRIL) but carry UMA=ZPR which sku_class_for_uma
        # would classify as BOX. Mixing them on the same pallet is what
        # caused CRUSH_RISK errors in the validator.
        return PalletClass.KEG if self.physical_type == "keg" else PalletClass.BOX


@dataclass
class _PlannedPick:
    """A Pick the algorithm will emit, with all geometry already computed."""

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
class _SlotState:
    """Mutable shadow of a pallet slot, used to plan placements."""

    slot_id: str
    slot: Slot
    pallet_id: str | None = None
    pallet_class: PalletClass | None = None
    primary_client: str | None = None
    has_multi_clients: bool = False
    items: list[PalletItem] = field(default_factory=list)
    client_set: set[str] = field(default_factory=set)
    picks: list[_PlannedPick] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.items

    @property
    def weight_kg(self) -> float:
        return sum(it.qty * it.unit_weight_kg for it in self.items)

    @property
    def loaded_height_m(self) -> float:
        return max((it.top_z for it in self.items), default=0.0)


# ---- Algorithm ---------------------------------------------------------------


class BalancedLoader(Algorithm):
    name = "balanced"
    description = (
        "Reverse-delivery loader with center-of-gravity balancing, client "
        "compactness, and 3D bin-packing. Last visit goes back-most; first "
        "visit sits at the door."
    )

    def plan(self, case: DayCase, clients: Clients, network: Network) -> Plan:
        rationale: list[str] = []

        route = self._route(case, clients, network)
        route = self._two_opt(route, case, clients, network, max_passes=2)
        rationale.append(
            "Route: nearest-neighbor + 2-opt. Visit order: "
            f"{[o.client_id for o in route]}"
        )

        slots = list(build_slots(case.truck))
        slot_states: dict[str, _SlotState] = {
            s.slot_id: _SlotState(slot_id=s.slot_id, slot=s) for s in slots
        }

        # Dedicate ONE slot for empties (returnables) — but only if
        # the truck has enough room. When orders fill ≥80% of the
        # remaining slots' nominal capacity, reserving a slot would
        # force cargo drops; in that case we share the slot.
        empties_slot = self._choose_empties_slot(slots)
        if empties_slot is not None:
            # Estimate cargo volume vs available cargo capacity if
            # we kept the slot reserved.
            cargo_volume = 0.0
            for o in case.orders:
                for line in o.lines:
                    cargo_volume += line.qty * line.unit_volume_m3
            from simulator.config import PALLET_VOLUME_M3
            avail_with_reserve = (len(slots) - 1) * PALLET_VOLUME_M3
            if avail_with_reserve > 0 and cargo_volume / avail_with_reserve > 0.80:
                rationale.append(
                    f"Cargo volume {cargo_volume:.1f} m³ ≥ 80% of {avail_with_reserve:.1f} m³ "
                    f"with reserve — releasing empties slot for cargo."
                )
                empties_slot = None
            else:
                rationale.append(
                    f"Reserved slot {empties_slot} for returnable empties "
                    "— cargo is never loaded here."
                )

        loading_order = list(reversed(route))
        rationale.append(
            "Loading: reverse delivery order — last visit is loaded first."
        )

        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)

        next_id = [0]

        def new_pid(slot_id: str) -> str:
            next_id[0] += 1
            return f"PB{next_id[0]:02d}-{slot_id}"

        overflow: list[tuple[str, str, float, str]] = []

        for client_order in loading_order:
            units = self._make_load_units(client_order)
            units.sort(
                key=lambda u: (
                    -u.weight_kg,
                    -u.footprint,
                    -u.unit_dim_h,
                    u.sku,
                )
            )
            for unit in units:
                for chunk in self._split_chunks(unit):
                    # 1st pass: try to fit without touching the empties
                    # slot. 2nd pass (if 1st failed): allow the empties
                    # slot too — better to ship the cargo than reserve
                    # space we won't use. Empties get a fallback slot
                    # at pickup time.
                    target = self._pick_best_slot(
                        chunk, slot_states, case.truck,
                        forbidden=empties_slot,
                    )
                    if target is None and empties_slot is not None:
                        target = self._pick_best_slot(
                            chunk, slot_states, case.truck, forbidden=None
                        )
                    if target is None:
                        overflow.append(
                            (chunk.client_id, chunk.sku, chunk.qty, "no fit")
                        )
                        continue
                    self._place_chunk(target, chunk, delivery_slots, new_pid)

        used_slots = sum(1 for s in slot_states.values() if not s.is_empty)
        rationale.append(
            f"Used {used_slots}/{len(slots)} slots; truck weight "
            f"{sum(s.weight_kg for s in slot_states.values()):.0f} kg."
        )
        if overflow:
            rationale.append(
                f"OVERFLOW: {len(overflow)} chunks did not fit (dropped at delivery)."
            )

        cmds = self._emit_commands(
            case, route, slots, slot_states, delivery_slots,
            empties_slot=empties_slot,
        )

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
            best: ClientOrder | None = None
            best_km = float("inf")
            for o in remaining:
                c = clients.get(o.client_id)
                d = network.leg(loc, (c.lat, c.lon)).distance_km
                if d < best_km:
                    best_km, best = d, o
            assert best is not None
            ordered.append(best)
            remaining.remove(best)
            c = clients.get(best.client_id)
            loc = (c.lat, c.lon)
        return ordered

    def _two_opt(
        self,
        order: list[ClientOrder],
        case: DayCase,
        clients: Clients,
        network: Network,
        max_passes: int,
    ) -> list[ClientOrder]:
        def total_km(seq: list[ClientOrder]) -> float:
            loc = (case.depot.lat, case.depot.lon)
            total = 0.0
            for o in seq:
                c = clients.get(o.client_id)
                total += network.leg(loc, (c.lat, c.lon)).distance_km
                loc = (c.lat, c.lon)
            total += network.leg(loc, (case.depot.lat, case.depot.lon)).distance_km
            return total

        best = order
        best_km = total_km(best)
        for _ in range(max_passes):
            improved = False
            for i in range(len(best) - 1):
                for j in range(i + 1, len(best)):
                    cand = best[:i] + list(reversed(best[i:j + 1])) + best[j + 1:]
                    km = total_km(cand)
                    if km + 1e-6 < best_km:
                        best, best_km = cand, km
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        return best

    # ---- Load-unit construction ----

    def _make_load_units(self, client_order: ClientOrder) -> list[_LoadUnit]:
        units: list[_LoadUnit] = []
        for line in client_order.lines:
            ptype = (
                line.physical_type.value
                if hasattr(line.physical_type, "value")
                else str(line.physical_type)
            )
            if (
                line.dim_source == "data"
                and line.dim_x_m > 0
                and line.dim_y_m > 0
                and line.dim_h_m > 0
            ):
                dx, dy, dh = line.dim_x_m, line.dim_y_m, line.dim_h_m
            else:
                dx, dy, dh = physical_dims(ptype)
            units.append(
                _LoadUnit(
                    sku=line.sku,
                    client_id=client_order.client_id,
                    qty=float(line.qty),
                    unit_dim_x=dx,
                    unit_dim_y=dy,
                    unit_dim_h=dh,
                    unit_weight_kg=line.unit_weight_kg,
                    unit_volume_m3=line.unit_volume_m3,
                    physical_type=ptype,
                    uma=line.uma,
                    is_returnable=line.is_returnable,
                )
            )
        return units

    def _split_chunks(self, unit: _LoadUnit) -> list[_LoadUnit]:
        """Split into chunks where each chunk fits on a pallet AND stays
        within the stack-stability ratio so we never plan a narrow tower."""

        if unit.qty <= 0:
            return []
        if unit.is_keg:
            chunk_size = KEG_MAX_STACK
        else:
            # Pallet-height cap.
            by_pallet_h = max(
                1, int(PALLET_HEIGHT_M / max(unit.unit_dim_h, 1e-3))
            )
            # Stability cap: stack height ≤ STACK_RATIO × narrow side.
            narrow = max(
                1e-3, min(unit.unit_dim_x, unit.unit_dim_y)
            )
            by_stability = max(
                1, int((STACK_RATIO * narrow) / max(unit.unit_dim_h, 1e-3))
            )
            chunk_size = min(by_pallet_h, by_stability)
        chunks: list[_LoadUnit] = []
        remaining = unit.qty
        while remaining > 0:
            take = min(remaining, float(chunk_size))
            chunks.append(
                _LoadUnit(
                    sku=unit.sku,
                    client_id=unit.client_id,
                    qty=take,
                    unit_dim_x=unit.unit_dim_x,
                    unit_dim_y=unit.unit_dim_y,
                    unit_dim_h=unit.unit_dim_h,
                    unit_weight_kg=unit.unit_weight_kg,
                    unit_volume_m3=unit.unit_volume_m3,
                    physical_type=unit.physical_type,
                    uma=unit.uma,
                    is_returnable=unit.is_returnable,
                )
            )
            remaining -= take
        return chunks

    # ---- Placement ----

    def _pick_best_slot(
        self,
        chunk: _LoadUnit,
        slot_states: dict[str, _SlotState],
        truck,
        forbidden: str | None = None,
    ) -> _SlotState | None:
        best: tuple[float, _SlotState] | None = None
        for state in slot_states.values():
            if forbidden is not None and state.slot_id == forbidden:
                continue
            evaluated = self._evaluate_candidate(chunk, state, slot_states, truck)
            if evaluated is None:
                continue
            score = evaluated
            if best is None or score < best[0]:
                best = (score, state)
        return best[1] if best else None

    @staticmethod
    def _choose_empties_slot(slots) -> str | None:
        """Pick a single slot to dedicate exclusively to returnable
        empties. Strategy: prefer the back ('B') slot if the truck
        has one; otherwise pick the deepest right-side slot
        ('Rmax_pos'). Falls back to the deepest slot of any side.
        Returns None if there's nothing to dedicate (single-slot
        truck — extremely rare)."""

        if not slots or len(slots) <= 1:
            return None
        # Back slot first.
        for s in slots:
            if s.side == "B":
                return s.slot_id
        # Deepest right-side slot.
        right_slots = [s for s in slots if s.side == "R"]
        if right_slots:
            return max(right_slots, key=lambda s: s.position).slot_id
        # Last resort — deepest slot regardless of side.
        return max(slots, key=lambda s: s.position).slot_id

    def _evaluate_candidate(
        self,
        chunk: _LoadUnit,
        state: _SlotState,
        slot_states: dict[str, _SlotState],
        truck,
    ) -> float | None:
        if state.is_empty:
            if chunk.weight_kg > PALLET_MAX_WEIGHT_KG:
                return None
            pos, tier = _find_position_safe([], chunk)
            if pos is None:
                return None
            opens_stack = True
        else:
            if (
                state.pallet_class is not None
                and state.pallet_class != chunk.pallet_class
            ):
                return None
            if state.weight_kg + chunk.weight_kg > PALLET_MAX_WEIGHT_KG:
                return None
            pos, tier = _find_position_safe(state.items, chunk)
            if pos is None:
                return None
            opens_stack = self._is_new_stack(state.items, pos)
        return self._score(chunk, state, pos, slot_states, truck, opens_stack, tier)

    @staticmethod
    def _is_new_stack(items: list[PalletItem], pos: tuple[float, float, float]) -> bool:
        if pos[2] < 1e-6:
            return True
        eps = 1e-4
        for it in items:
            if (
                abs(it.top_z - pos[2]) < eps
                and abs(it.pos_x - pos[0]) < eps
                and abs(it.pos_y - pos[1]) < eps
            ):
                return False
        return True

    def _score(
        self,
        chunk: _LoadUnit,
        state: _SlotState,
        pos: tuple[float, float, float],
        slot_states: dict[str, _SlotState],
        truck,
        opens_stack: bool,
        tier: int,
    ) -> float:
        total_w = 0.0
        sx = 0.0
        sy = 0.0
        for s in slot_states.values():
            w = s.weight_kg + (chunk.weight_kg if s.slot_id == state.slot_id else 0.0)
            if w <= 0:
                continue
            cx, cy = self._slot_center(truck, s.slot)
            sx += cx * w
            sy += cy * w
            total_w += w
        if total_w > 0:
            sx /= total_w
            sy /= total_w
        else:
            sx, sy = IDEAL_X, IDEAL_Y
        # Quadratic — small drifts cost little, large drifts cost a lot.
        dx = sx - IDEAL_X
        dy = sy - IDEAL_Y
        balance_pen = W_BALANCE_X * dx * dx + W_BALANCE_Y * dy * dy

        new_height = max(state.loaded_height_m, pos[2] + chunk.stack_h)
        height_ratio = new_height / PALLET_HEIGHT_M
        layer_penalty = pos[2] / PALLET_HEIGHT_M

        cx, cy = self._slot_center(truck, state.slot)
        center_dist = math.hypot(cx - IDEAL_X, cy - IDEAL_Y)

        if state.is_empty:
            locality = W_NEW_SLOT
        elif chunk.client_id in state.client_set:
            locality = W_NEW_STACK if opens_stack else 0.0
        else:
            locality = W_NEW_SLOT

        # Tier penalty — strongly prefer slots where a strict-valid
        # placement exists. Higher tier = more relaxed constraints.
        tier_penalty = 0.0
        if tier == 2:
            tier_penalty = 50.0
        elif tier == 3:
            tier_penalty = 200.0
        elif tier == 4:
            tier_penalty = 1000.0  # very last resort

        return (
            balance_pen
            + W_HEIGHT * height_ratio
            + W_LAYER * layer_penalty
            + W_CENTER * center_dist
            + locality
            + tier_penalty
        )

    @staticmethod
    def _slot_center(truck, slot: Slot) -> tuple[float, float]:
        """Map a slot to normalized (x, y) ∈ [0, 1]^2.

        x: 0 = door (front), 1 = back wall.
        y: 0.25 = left side, 0.75 = right side, 0.5 = center (B / van).
        """

        cap = truck.pallet_capacity
        half = max(1, cap // 2)
        if slot.side == "B":
            return (1.0, 0.5)
        x = (slot.position - 0.5) / half
        y = 0.25 if slot.side == "L" else 0.75
        return (x, y)

    def _place_chunk(
        self,
        state: _SlotState,
        chunk: _LoadUnit,
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
        new_pid,
    ) -> None:
        if state.pallet_id is None:
            state.pallet_id = new_pid(state.slot_id)
            state.primary_client = chunk.client_id
            state.pallet_class = chunk.pallet_class
        elif state.primary_client != chunk.client_id:
            state.has_multi_clients = True

        state.client_set.add(chunk.client_id)

        pos, _tier = _find_position_safe(state.items, chunk)
        if pos is None:
            # _evaluate_candidate already screened this slot, so we
            # shouldn't get here. If we do, refuse the placement instead
            # of falling back to a tower.
            return

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
        state.items.append(item)
        state.picks.append(
            _PlannedPick(
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
        delivery_slots[(chunk.client_id, chunk.sku)].append(
            (state.slot_id, chunk.qty)
        )

    # ---- Command emission ----

    def _emit_commands(
        self,
        case: DayCase,
        route: list[ClientOrder],
        slots: list[Slot],
        slot_states: dict[str, _SlotState],
        delivery_slots: dict[tuple[str, str], list[tuple[str, float]]],
        empties_slot: str | None = None,
    ) -> list[Command]:
        cmds: list[Command] = []

        for slot in slots:
            state = slot_states[slot.slot_id]
            if state.is_empty or state.pallet_id is None:
                continue
            primary = state.primary_client if not state.has_multi_clients else None
            kind = (
                PalletKind.CLIENT_BLOCK.value
                if primary
                else PalletKind.MIXED.value
            )
            note = (
                f"slot={slot.slot_id} "
                f"clients={sorted(state.client_set)} "
                f"class={state.pallet_class.value if state.pallet_class else '?'}"
            )
            cmds.append(
                BuildPallet(
                    pallet_id=state.pallet_id,
                    kind=kind,
                    primary_client=primary,
                    notes=note,
                    pallet_class=(
                        state.pallet_class.value if state.pallet_class else None
                    ),
                )
            )
            for p in state.picks:
                cmds.append(
                    Pick(
                        sku=p.sku,
                        qty=p.qty,
                        location=None,
                        pallet_id=state.pallet_id,
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
            cmds.append(
                Load(pallet_id=state.pallet_id, slot_id=slot.slot_id)
            )

        cmds.append(DepartDepot())

        # Maintain a shadow truck while we walk the plan so PickupReturn
        # anchors land on whatever is still on the pallet at pickup time.
        vt = VirtualTruck()
        for slot in slots:
            for it in slot_states[slot.slot_id].items:
                vt.add(slot.slot_id, it)

        keg_dx, keg_dy, keg_dh = physical_dims("keg")

        for o in route:
            cmds.append(DriveTo(client_id=o.client_id))
            # Group splits by slot so we can plan blockers + restock
            # for the whole stop atomically (mirrors how the simulator
            # batches unloads per slot).
            splits_by_slot: dict[str, list[tuple[str, float]]] = {}
            for line in o.lines:
                for slot_id, qty in delivery_slots.get((o.client_id, line.sku), []):
                    splits_by_slot.setdefault(slot_id, []).append((line.sku, qty))

            for slot_id, items in splits_by_slot.items():
                target_keys = [(sku, o.client_id) for sku, _ in items]
                target_items, same_client, foreign = vt.find_blockers(
                    slot_id, target_keys
                )
                # Lift order mirrors the simulator's ranking
                # (edge-first, then top-down) so positions match up.
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
                # Empties always land in the dedicated empties slot.
                # That slot has no cargo (reserved at plan time), so
                # the floor is always available and the simulator's
                # snap/settle paths almost never trigger.
                if empties_slot is not None:
                    ret_slot = empties_slot
                    # Empties slot is the primary target. If high-
                    # volume days forced cargo into it, also offer
                    # other keg-compatible slots as fallback so we
                    # don't drop empties.
                    empties_state = slot_states[empties_slot]
                    candidates = [empties_slot]
                    if not empties_state.is_empty:
                        candidates.extend(
                            s.slot_id for s in slots
                            if s.slot_id != empties_slot
                            and slot_states[s.slot_id].pallet_class
                            in (None, PalletClass.KEG)
                        )
                else:
                    ret_slot = self._return_slot(o.client_id, slot_states, vt)
                    keg_compatible = [
                        s.slot_id for s in slots
                        if slot_states[s.slot_id].pallet_class
                        in (None, PalletClass.KEG)
                    ]
                    candidates = keg_compatible or [s.slot_id for s in slots]
                    if ret_slot not in candidates:
                        ret_slot = candidates[0]
                # COG-aware strategy: among floor-eligible candidates,
                # pick the one that keeps the truck centre of mass
                # closest to (0.52, 0.50).
                from simulator.algorithms.restock_strategy import (
                    BalancedStrategy,
                    _RestockContext,
                )
                slot_centers = {
                    s.slot_id: self._slot_center(case.truck, s)
                    for s in slots
                }
                strategy = BalancedStrategy(slot_centers)
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
        slot_states: dict[str, _SlotState],
        vt: VirtualTruck,
    ) -> str:
        """Pick the slot for empties.

        Strategy: among slots that already saw this client, prefer the one
        with the most free floor footprint **right now** (i.e. after the
        deliveries already executed at this stop). Falls back to any
        client-touched slot, then any non-empty slot, then L1.
        """

        from simulator.config import PALLET_FOOTPRINT_M2

        def free_floor(slot_id: str) -> float:
            used = sum(
                it.dim_x * it.dim_y
                for it in vt.items(slot_id)
                if it.qty > 0 and it.pos_z < 1e-6
            )
            return max(0.0, PALLET_FOOTPRINT_M2 - used)

        client_keg_slots = [
            s.slot_id
            for s in slot_states.values()
            if s.pallet_class == PalletClass.KEG and client_id in s.client_set
        ]
        if client_keg_slots:
            return max(client_keg_slots, key=free_floor)

        client_slots = [
            s.slot_id for s in slot_states.values() if client_id in s.client_set
        ]
        if client_slots:
            return max(client_slots, key=free_floor)

        for state in slot_states.values():
            if not state.is_empty:
                return state.slot_id
        return "L1"
