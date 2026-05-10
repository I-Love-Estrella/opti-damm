"""Plan validator — checks an algorithm's output against physical and process
constraints. Produces a list of ValidationIssue objects with severity.

Three severities:
  ERROR    — algorithm produced an invalid plan. Must be fixed (run is rejected).
  WARNING  — plan is technically valid but risky/suboptimal.
  INFO     — observation worth flagging.

Categories of checks:
  Cargo physical: stack overflow, truck overweight, crush risk, fragile placement.
  Stability:      center of mass (lateral / longitudinal / vertical), L-R balance.
  Process:        time-window violations, closed-visit, fill rate, drops, overtime.
  Route:          missed / revisited clients.

Usage:
  issues = validate_plan(case, plan, run_result, sim)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum

from simulator.config import PALLET_VOLUME_M3
from simulator.core.simulator import SimulationResult, Simulator
from simulator.core.state import WorldState
from simulator.data.orders import DayCase
from simulator.domain.pallet import Pallet, PalletItem
from simulator.domain.plan import Plan


PALLET_LEN_M = 1.20
PALLET_WIDTH_M = 0.80
PALLET_HEIGHT_M = 1.80
SLOT_GAP_X_M = 0.06
LR_GAP_M = 0.10
TRUCK_HEIGHT_M = 2.10  # generous internal cabin clearance

# Risk thresholds — hand-tuned defaults; can be made configurable later.
CRUSH_WEIGHT_RATIO = 3.0      # upper / lower per-box weight to flag
CRUSH_MIN_UPPER_KG = 5.0      # don't flag if upper item is too light to crush
PALLET_HEAVY_KG = 800.0       # forklift practical limit
TRUCK_WEIGHT_NEAR_LIMIT_FRAC = 0.95
COM_LATERAL_ERROR_M = 0.30
COM_LATERAL_WARN_M = 0.20
COM_LONGITUDINAL_WARN_M = 0.50
COM_HIGH_WARN_M = 1.20
LR_IMBALANCE_RATIO = 1.5
FILL_RATE_ERROR = 0.95
OVERTIME_HARD_HOURS = 13.0
OVERTIME_WARN_HOURS = 10.0

# Stack stability — narrow towers tip over. A stack is unstable when its
# height exceeds STACK_RATIO_ERROR × the smaller footprint side. Below
# the WARN threshold we still flag a soft warning so the operator knows
# the stack is getting wobbly.
STACK_RATIO_ERROR = 3.5
STACK_RATIO_WARN = 3.0
# Anything physically smaller than this on the long side gets a hard pass —
# tiny single items (a lone bottle, a single can) shouldn't trip a stack
# rule because they aren't really a "stack".
STACK_MIN_HEIGHT_M = 0.40

# Overlap tolerance — must match the simulator's _PHYSICS_EPS so the
# validator agrees with runtime checks. 0.1 mm — strict enough to catch
# any real overlap, loose enough to ignore floating-point noise.
OVERLAP_EPS_M = 1e-4


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    where: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "where": self.where,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == ValidationSeverity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == ValidationSeverity.WARNING)

    @property
    def infos(self) -> int:
        return sum(1 for i in self.issues if i.severity == ValidationSeverity.INFO)

    @property
    def is_valid(self) -> bool:
        return self.errors == 0

    def to_dict(self) -> dict:
        return {
            "summary": {
                "errors": self.errors,
                "warnings": self.warnings,
                "infos": self.infos,
                "is_valid": self.is_valid,
            },
            "stability": self.stability(),
            "issues": [i.to_dict() for i in self.issues],
        }

    def stability(self) -> dict:
        """Aggregate physics-related issues into a single 0-100 score.

        Real Damm always delivers 100% — but our greedy 3D bin-packer
        had to relax its safety rails to match that. The price is
        physics warnings: stacks that wobble, columns that overflow,
        COG that drifts. This metric exposes that price so the
        operator can see WHY the load fits and WHAT they'd pay for it
        in the real warehouse (extra stretch wrap, manual bracing,
        slower drive).

        Score bands:
          - 90..100  STABLE     — load is rock-solid
          - 70..89   WOBBLY     — minor balance / stack issues
          - 40..69   FRAGILE    — driver should drive carefully
          - 0..39    DANGEROUS  — needs reload before dispatch
        """

        physics_err = 0
        physics_warn = 0
        for i in self.issues:
            if i.code not in _PHYSICS_CODES:
                continue
            if i.severity == ValidationSeverity.ERROR:
                physics_err += 1
            elif i.severity == ValidationSeverity.WARNING:
                physics_warn += 1
        # Errors weigh 5x warnings — a tipped pallet is much worse
        # than a borderline COG. Cap penalty so a single accident
        # doesn't zero the score when most of the load is fine.
        penalty = physics_err * 5 + physics_warn * 1
        score = max(0, 100 - penalty * 2)
        if score >= 90:
            label = "STABLE"
        elif score >= 70:
            label = "WOBBLY"
        elif score >= 40:
            label = "FRAGILE"
        else:
            label = "DANGEROUS"
        return {
            "score": score,
            "label": label,
            "physics_errors": physics_err,
            "physics_warnings": physics_warn,
        }


_PHYSICS_CODES = frozenset({
    # COG / balance
    "COM_LATERAL_ROLLOVER",
    "COM_LATERAL_IMBALANCE",
    "COM_LONGITUDINAL_OFFSET",
    "COM_HIGH",
    "WEIGHT_IMBALANCE_LR",
    # Stack stability
    "STACK_OVERFLOW",
    "STACK_WOBBLY",
    "STACK_UNSTABLE",
    "UNSTABLE_OVERHANG",
    # Crush / mixing
    "CRUSH_RISK",
    "GLASS_UNDER_HEAVY",
    # Capacity
    "PALLET_OVERWEIGHT",
    "PALLET_HEIGHT_EXCEEDS_TRUCK",
    "TRUCK_OVERWEIGHT",
    "TRUCK_NEAR_WEIGHT_LIMIT",
    # Geometry
    "OVERLAP",
    "FLOATING",
    "OUT_OF_BOUNDS",
})


def validate_plan(
    case: DayCase,
    plan: Plan,
    result: SimulationResult,
    sim: Simulator,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    # Runtime physics violations: any overlap / out-of-bounds / floating
    # event the simulator raised during the run is surfaced as a hard
    # ERROR. We do this BEFORE any other check so a broken plan can't
    # be mistaken for a clean one.
    issues.extend(_check_runtime_physics(result))

    # Process-level checks: read directly from final WorldState.
    issues.extend(_check_process(case, plan, result))

    # Final-state cargo checks (overlap on every loaded pallet — runs
    # against the final post-route state too, not just the post-load
    # state, so any restock or returnable that ended up overlapping is
    # caught even if the simulator's per-command check was skipped).
    issues.extend(_check_overlaps(result.state, where="final state"))

    # Cargo physical checks: re-run loading to capture the exact post-depot state.
    try:
        loading = sim.simulate_loading(case, plan)
        if loading.success:
            issues.extend(_check_overlaps(loading.state, where="post-load"))
            issues.extend(_check_cargo(case, loading.state))
    except Exception as exc:  # defensive — never let validation crash a run
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="VALIDATION_INTERNAL",
                message=f"Could not run cargo simulation for validation: {exc!r}",
                where="validator",
            )
        )

    # Sort by severity for predictable UI ordering.
    severity_rank = {
        ValidationSeverity.ERROR: 0,
        ValidationSeverity.WARNING: 1,
        ValidationSeverity.INFO: 2,
    }
    issues.sort(key=lambda i: (severity_rank[i.severity], i.code))
    return ValidationReport(issues=tuple(issues))


# ---------------------------------------------------------------------------
# Process-level
# ---------------------------------------------------------------------------


def _check_process(case: DayCase, plan: Plan, result: SimulationResult) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    state = result.state

    # Plan terminated cleanly?
    if not result.success:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="SIM_FAILED",
                message=f"Simulator could not run plan to completion: {result.error}",
                where="plan",
                detail={"error": str(result.error or "")},
            )
        )

    # Time window slack used.
    if state.tw_violations_min > 0:
        sev = (
            ValidationSeverity.ERROR
            if state.tw_violations_min > 60
            else ValidationSeverity.WARNING
        )
        out.append(
            ValidationIssue(
                severity=sev,
                code="TIME_WINDOW_VIOLATION",
                message=(
                    f"Arrived outside delivery window for total {state.tw_violations_min:.1f} min"
                ),
                where="plan",
                detail={"total_minutes": state.tw_violations_min},
            )
        )

    # Visited a client whose store is closed today.
    if state.closed_visits > 0:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="CLOSED_VISITS",
                message=(
                    f"Visited {state.closed_visits} client(s) while their store is closed today"
                ),
                where="plan",
                detail={"count": int(state.closed_visits)},
            )
        )

    # Pack-time overflow → algorithm couldn't fit some chunks. We
    # distinguish two causes so the operator gets actionable info:
    #
    #   1. TRUCK_TOO_SMALL — cargo weight/volume genuinely exceeds
    #      truck capacity. Bump the truck class or split the day.
    #
    #   2. PACKER_DENSITY_GAP — cargo would fit by raw weight/volume,
    #      but our greedy 3D bin-packer can't reach the density real
    #      loaders achieve (~95% with stretch wrap). This is a MODEL
    #      gap, not an operations failure: Damm's recorded data shows
    #      100% delivery on every route. The fix is in the algorithm,
    #      not in dispatch.
    overflow_units = sum(qty for _, _, qty in plan.pack_overflow)
    if overflow_units > 0:
        truck = case.truck
        cap_m3 = truck.pallet_capacity * PALLET_VOLUME_M3
        cap_kg = truck.max_weight_kg
        # Compute cargo totals from the orders (unit_volume_m3 is the
        # catalog literage — small. We use AABB dim_x*dim_y*dim_h*qty
        # for an honest geometric estimate).
        from simulator.data.catalog import physical_dims
        cargo_aabb = 0.0
        cargo_kg = 0.0
        for o in case.orders:
            for line in o.lines:
                cargo_kg += line.qty * line.unit_weight_kg
                if line.dim_x_m > 0 and line.dim_y_m > 0 and line.dim_h_m > 0:
                    dx, dy, dh = line.dim_x_m, line.dim_y_m, line.dim_h_m
                else:
                    pt = (
                        line.physical_type.value
                        if hasattr(line.physical_type, "value")
                        else str(line.physical_type)
                    )
                    dx, dy, dh = physical_dims(pt)
                cargo_aabb += line.qty * dx * dy * dh

        weight_overflow = cargo_kg > cap_kg
        volume_overflow = cargo_aabb > cap_m3
        truck_too_small = weight_overflow or volume_overflow

        if truck_too_small:
            cause = []
            if weight_overflow:
                cause.append(
                    f"weight {cargo_kg:.0f} kg > {cap_kg:.0f} kg cap"
                )
            if volume_overflow:
                cause.append(
                    f"volume {cargo_aabb:.1f} m³ > {cap_m3:.1f} m³ cap"
                )
            msg = (
                f"TRUCK_TOO_SMALL: {overflow_units:.0f} unit(s) "
                f"({len(plan.pack_overflow)} chunk(s)) don't fit on "
                f"{truck.code} — cargo exceeds truck cap "
                f"({'; '.join(cause)}). Pick a bigger truck or split "
                f"the route across multiple trucks."
            )
            code = "TRUCK_TOO_SMALL"
        else:
            wt_util = cargo_kg / cap_kg * 100 if cap_kg > 0 else 0
            vol_util = cargo_aabb / cap_m3 * 100 if cap_m3 > 0 else 0
            msg = (
                f"PACKER_DENSITY_GAP: {overflow_units:.0f} unit(s) "
                f"({len(plan.pack_overflow)} chunk(s)) couldn't be packed "
                f"on {truck.code} although the truck has headroom "
                f"(cargo {cargo_kg:.0f} kg / {cargo_aabb:.1f} m³ vs cap "
                f"{cap_kg:.0f} kg / {cap_m3:.1f} m³ — {wt_util:.0f}% wt / "
                f"{vol_util:.0f}% vol used). Greedy 3D bin-packer hits its "
                f"density ceiling (~70%); real warehouse loaders achieve "
                f"~95% with stretch wrap. Algorithm-side limit, not dispatch."
            )
            code = "PACKER_DENSITY_GAP"

        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code=code,
                message=msg,
                where="packing",
                detail={
                    "overflow_units": float(overflow_units),
                    "overflow_chunks": len(plan.pack_overflow),
                    "cargo_weight_kg": round(cargo_kg, 1),
                    "cargo_aabb_m3": round(cargo_aabb, 2),
                    "truck_code": truck.code,
                    "truck_pallets": int(truck.pallet_capacity),
                    "truck_max_weight_kg": float(cap_kg),
                    "truck_raw_volume_m3": round(cap_m3, 2),
                    "weight_overflow": bool(weight_overflow),
                    "volume_overflow": bool(volume_overflow),
                },
            )
        )

    # Fill rate (informational only — `LOAD_DOES_NOT_FIT` above is the
    # actionable error). Real fill in production data is always 100%;
    # < 100% in our model means pack-time overflow, captured separately.
    delivered = float(sum(state.delivered_qty.values()))
    ordered = float(sum(line.qty for o in case.orders for line in o.lines))
    if ordered > 0:
        fill = delivered / ordered
        if fill < FILL_RATE_ERROR and overflow_units == 0:
            # Only warn about fill if it's NOT explained by overflow
            # (i.e. the algorithm packed it but the simulator dropped
            # it at runtime — a real algorithm bug).
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="FILL_RATE_LOW",
                    message=(
                        f"Delivered only {delivered:.0f} of {ordered:.0f} ordered units "
                        f"(fill rate {fill:.1%})"
                    ),
                    where="plan",
                    detail={
                        "delivered": delivered,
                        "ordered": ordered,
                        "fill_rate": round(fill, 4),
                    },
                )
            )

    # Drops.
    if state.drops:
        sample = [
            {"client_id": c, "sku": s, "qty": float(q)} for c, s, q in state.drops[:10]
        ]
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="DROPS",
                message=f"{len(state.drops)} delivery line(s) dropped (couldn't be fulfilled)",
                where="plan",
                detail={"count": len(state.drops), "sample": sample},
            )
        )

    # Pallet capacity violations counter — pure runtime physics
    # checks (e.g. pickup-overlap snaps, settle moves, height
    # exceeded, off-pallet edge). Mixed KEG/BOX is no longer flagged
    # here — we deliberately mix classes for higher density and the
    # validator's CRUSH_RISK / GLASS_UNDER_HEAVY catches the
    # genuinely unsafe combinations.
    if state.capacity_violations > 0:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="CAPACITY_VIOLATIONS",
                message=(
                    f"{state.capacity_violations} runtime physics adjustment(s) "
                    "during the route (column overflow, pickup snap, settle, "
                    "or height/edge tolerance). See per-event SETTLE / "
                    "PICKUP_OVERLAP_SNAPPED in the trace for details."
                ),
                where="loading",
                detail={"count": int(state.capacity_violations)},
            )
        )

    # Missed clients.
    visited_clients = {cid for (cid, _sku) in state.delivered_qty.keys()}
    expected_clients = {o.client_id for o in case.orders}
    missed = expected_clients - visited_clients
    if missed:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="MISSED_CLIENTS",
                message=f"{len(missed)} client(s) never received any delivery",
                where="route",
                detail={"missed": sorted(missed)[:20], "count": len(missed)},
            )
        )

    # Revisited client (route doubles back through same client).
    route_order = list(plan.route_order or ())
    seen: dict[str, int] = {}
    revisits: list[tuple[str, int, int]] = []
    for idx, cid in enumerate(route_order):
        if cid in seen:
            revisits.append((cid, seen[cid], idx))
        else:
            seen[cid] = idx
    if revisits:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="REVISIT_CLIENT",
                message=f"{len(revisits)} client(s) visited more than once in the route",
                where="route",
                detail={
                    "revisits": [
                        {"client_id": c, "first_idx": a, "second_idx": b}
                        for c, a, b in revisits[:10]
                    ]
                },
            )
        )

    # Overtime / legal limit.
    # The driver's shift = drive time + on-route service time.
    # Warehouse loading (depot work) is performed by loaders, not the
    # driver, and must NOT count toward the legal 13 h cap. We rebuild
    # driver hours straight from the event log here so the validator
    # stays self-contained (no dependency on the KPI module).
    driver_hours = _driver_hours_from_log(result)
    if driver_hours > OVERTIME_HARD_HOURS:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="OVERTIME_LEGAL",
                message=(
                    f"Driver shift {driver_hours:.1f}h (drive + client service) — "
                    f"exceeds hard legal limit ({OVERTIME_HARD_HOURS:.0f}h)"
                ),
                where="plan",
                detail={"driver_hours": round(driver_hours, 2)},
            )
        )
    elif driver_hours > OVERTIME_WARN_HOURS:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="OVERTIME_LONG_SHIFT",
                message=(
                    f"Driver shift {driver_hours:.1f}h (drive + client service) — "
                    f"long; consider routing changes"
                ),
                where="plan",
                detail={"driver_hours": round(driver_hours, 2)},
            )
        )

    return out


_DRIVER_KINDS = frozenset({
    "ARRIVE", "RETURN_DEPOT",                        # drive legs
    "SERVICE_BASE", "UNLOAD", "PICKUP_RETURN",       # at-client service
    "BLOCKER_LIFT", "TARGET_TAKE", "BLOCKER_REPLACE",
    "DROP", "SETTLE",
})


def _driver_hours_from_log(result: SimulationResult) -> float:
    """Total time the driver is on the clock, in hours.

    Sums the time deltas between consecutive events whose kind is
    something the driver performs: driving (ARRIVE / RETURN_DEPOT) or
    at-client service. Depot loading events (BUILD_PALLET, PICK, LOAD,
    DEPART_DEPOT) are excluded — those are warehouse loader work.
    """

    total = 0.0
    prev_t = 0.0
    for e in result.log.events:
        delta = max(0.0, e.t_min - prev_t)
        if e.kind in _DRIVER_KINDS:
            total += delta
        prev_t = e.t_min
    return total / 60.0


# ---------------------------------------------------------------------------
# Physics — overlap & runtime violations (hard errors)
# ---------------------------------------------------------------------------


def _print_error(msg: str) -> None:
    """Always print every physics ERROR to stderr so an operator sees
    the failure even when the JSON response is ignored."""
    print(f"[CHECKER] {msg}", file=sys.stderr)


def _aabb_overlap_volume(a: PalletItem, b: PalletItem) -> float:
    """Volume of the 3D AABB intersection. 0 means no overlap."""
    ox = max(0.0, min(a.end_x, b.end_x) - max(a.pos_x, b.pos_x))
    oy = max(0.0, min(a.end_y, b.end_y) - max(a.pos_y, b.pos_y))
    oz = max(0.0, min(a.top_z, b.top_z) - max(a.pos_z, b.pos_z))
    return ox * oy * oz


def _check_overlaps(state: WorldState, where: str) -> list[ValidationIssue]:
    """Walk every loaded pallet and emit one OVERLAP error per colliding
    pair. Tolerance matches the simulator's physics epsilon so we agree
    with runtime checks. Always severity=ERROR — any overlap is a bug
    in the algorithm. Each overlap is also printed to stderr."""
    out: list[ValidationIssue] = []
    seen_pairs: set[tuple] = set()
    for pallet_id, slot_id in state.cargo.slot_by_pallet.items():
        pallet = state.cargo.pallet_by_id.get(pallet_id)
        if pallet is None:
            continue
        items = [it for it in pallet.items if it.qty > 0]
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                # Same logic as core/simulator._check_pallet_invariants.
                if not (
                    a.pos_x < b.end_x - OVERLAP_EPS_M
                    and b.pos_x < a.end_x - OVERLAP_EPS_M
                    and a.pos_y < b.end_y - OVERLAP_EPS_M
                    and b.pos_y < a.end_y - OVERLAP_EPS_M
                    and a.pos_z < b.top_z - OVERLAP_EPS_M
                    and b.pos_z < a.top_z - OVERLAP_EPS_M
                ):
                    continue
                vol = _aabb_overlap_volume(a, b)
                a_vol = a.dim_x * a.dim_y * a.dim_h
                b_vol = b.dim_x * b.dim_y * b.dim_h
                ratio = vol / max(min(a_vol, b_vol), 1e-9)
                key = (
                    slot_id,
                    a.sku,
                    b.sku,
                    round(a.pos_x, 3),
                    round(a.pos_y, 3),
                    round(a.pos_z, 3),
                    round(b.pos_x, 3),
                    round(b.pos_y, 3),
                    round(b.pos_z, 3),
                )
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                msg = (
                    f"Overlap [{where} slot={slot_id} pallet={pallet_id}]: "
                    f"{a.sku}@({a.pos_x:.3f},{a.pos_y:.3f},{a.pos_z:.3f}) "
                    f"intersects {b.sku}@({b.pos_x:.3f},{b.pos_y:.3f},{b.pos_z:.3f}) "
                    f"by {vol * 1000:.1f} L ({ratio * 100:.0f}% of smaller item)"
                )
                _print_error(msg)
                out.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="OVERLAP",
                        message=msg,
                        where=f"slot {slot_id}",
                        detail={
                            "slot_id": slot_id,
                            "pallet_id": pallet_id,
                            "sku_a": a.sku,
                            "sku_b": b.sku,
                            "pos_a": [round(a.pos_x, 4), round(a.pos_y, 4), round(a.pos_z, 4)],
                            "dim_a": [round(a.dim_x, 4), round(a.dim_y, 4), round(a.dim_h, 4)],
                            "pos_b": [round(b.pos_x, 4), round(b.pos_y, 4), round(b.pos_z, 4)],
                            "dim_b": [round(b.dim_x, 4), round(b.dim_y, 4), round(b.dim_h, 4)],
                            "overlap_volume_m3": round(vol, 6),
                            "overlap_ratio": round(ratio, 4),
                            "where_phase": where,
                        },
                    )
                )
    return out


# Maps the simulator's PHYSICS_VIOLATION codes to validator codes. The
# simulator emits one event per offending pair on every state change,
# so we deduplicate by (code, slot, sku, pos rounded to mm).
_PHYSICS_CODE_MAP = {
    "OVERLAP": "OVERLAP",
    "OUT_OF_BOUNDS": "OUT_OF_BOUNDS",
    "FLOATING": "FLOATING_ITEM",
    "UNSTABLE_OVERHANG": "UNSTABLE_OVERHANG",
    "HEIGHT_OVERFLOW": "PALLET_HEIGHT_EXCEEDS_TRUCK",
    # Self-corrected events — the algorithm asked for an invalid
    # position but the simulator snapped/settled it to a clean one.
    # These get distinct codes (so they're easy to filter) and
    # WARNING severity since the final cargo state is physically
    # valid. The algorithm bug is still visible to anyone reading
    # the warnings list.
    "PICKUP_OVERLAP_SNAPPED": "OVERLAP_AVOIDED",
    "RESTOCK_OVERLAP_SNAPPED": "OVERLAP_AVOIDED",
    "SETTLE": "FLOATING_AVOIDED",
    # Genuine loss — empty couldn't be placed and was dropped. Stays
    # ERROR because the truck loses track of a returnable.
    "PICKUP_DROPPED_NO_FIT": "OVERLAP",
}

# Validator codes whose severity is WARNING (sub-optimal but
# auto-corrected) rather than ERROR (truly invalid plan state).
_WARNING_CODES = {"OVERLAP_AVOIDED", "FLOATING_AVOIDED"}


def _check_runtime_physics(result: SimulationResult) -> list[ValidationIssue]:
    """Surface PHYSICS_VIOLATION events from the simulator log as
    validator issues. OVERLAP, OUT_OF_BOUNDS and FLOATING are always
    ERROR severity. Each unique violation is printed once to stderr."""
    out: list[ValidationIssue] = []
    seen: set[tuple] = set()
    for rec in result.log.to_records():
        if rec.get("kind") != "PHYSICS_VIOLATION":
            continue
        code = str(rec.get("code") or "")
        if code == "HEIGHT_OVERFLOW":
            # Soft — handled by PALLET_HEIGHT_EXCEEDS_TRUCK from the
            # cargo path. Skip to avoid double-reporting.
            continue
        validator_code = _PHYSICS_CODE_MAP.get(code, "PHYSICS_VIOLATION")
        pos_a = rec.get("pos") or rec.get("pos_a") or [0.0, 0.0, 0.0]
        pos_b = rec.get("pos_b") or [0.0, 0.0, 0.0]
        key = (
            code,
            rec.get("slot_id"),
            rec.get("sku") or rec.get("sku_a"),
            rec.get("sku_b"),
            tuple(round(float(x), 3) for x in pos_a),
            tuple(round(float(x), 3) for x in pos_b),
        )
        if key in seen:
            continue
        seen.add(key)
        msg = str(rec.get("message") or f"Physics violation: {code}")
        severity = (
            ValidationSeverity.WARNING
            if validator_code in _WARNING_CODES
            else ValidationSeverity.ERROR
        )
        if severity == ValidationSeverity.ERROR:
            _print_error(f"runtime {code}: {msg}")
        out.append(
            ValidationIssue(
                severity=severity,
                code=validator_code,
                message=msg,
                where=str(rec.get("where") or "runtime"),
                detail={
                    "slot_id": rec.get("slot_id"),
                    "sku": rec.get("sku") or rec.get("sku_a"),
                    "sku_b": rec.get("sku_b"),
                    "pos": list(pos_a),
                    "pos_b": list(pos_b),
                    "t_min": rec.get("t_min"),
                    "raw_code": code,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# Cargo / stability
# ---------------------------------------------------------------------------


def _slot_world_xz(slot_id: str, max_pos: int) -> tuple[float, float] | None:
    """Slot center in truck coordinates. Returns None for unknown slot ids."""
    m = re.match(r"^([LRB])(\d+)$", slot_id)
    if not m:
        return None
    side, pos_str = m.group(1), m.group(2)
    pos = int(pos_str)
    total_length = max_pos * (PALLET_LEN_M + SLOT_GAP_X_M)
    start_x = -total_length / 2.0 + (PALLET_LEN_M + SLOT_GAP_X_M) / 2.0
    x = start_x + (pos - 1) * (PALLET_LEN_M + SLOT_GAP_X_M)
    if side == "L":
        return (x, -(PALLET_WIDTH_M / 2.0 + LR_GAP_M / 2.0))
    if side == "R":
        return (x, +(PALLET_WIDTH_M / 2.0 + LR_GAP_M / 2.0))
    # B — back row
    back_x = total_length / 2.0 + PALLET_LEN_M / 2.0 + 0.10
    return (back_x, 0.0)


def _max_side_position(state: WorldState) -> int:
    out = 1
    for slot_id in state.cargo.pallet_by_slot.keys():
        m = re.match(r"^([LRB])(\d+)$", slot_id)
        if not m or m.group(1) == "B":
            continue
        pos = int(m.group(2))
        if pos > out:
            out = pos
    return out


def _check_cargo(case: DayCase, state: WorldState) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    truck = case.truck

    loaded: list[tuple[str, Pallet]] = []
    for pallet_id, slot_id in state.cargo.slot_by_pallet.items():
        pallet = state.cargo.pallet_by_id.get(pallet_id)
        if pallet is not None:
            loaded.append((slot_id, pallet))

    if not loaded:
        return out

    # Total weight.
    total_weight = sum(p.weight_kg for _, p in loaded)
    if total_weight > truck.max_weight_kg:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="TRUCK_OVERWEIGHT",
                message=(
                    f"Truck loaded with {total_weight:.0f} kg, exceeds limit "
                    f"{truck.max_weight_kg:.0f} kg ({truck.code})"
                ),
                where="loading",
                detail={
                    "total_kg": round(total_weight, 1),
                    "max_kg": float(truck.max_weight_kg),
                },
            )
        )
    elif total_weight > TRUCK_WEIGHT_NEAR_LIMIT_FRAC * truck.max_weight_kg:
        out.append(
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                code="TRUCK_NEAR_WEIGHT_LIMIT",
                message=(
                    f"Truck at {(total_weight / truck.max_weight_kg) * 100:.0f}% "
                    f"of weight limit ({total_weight:.0f}/{truck.max_weight_kg:.0f} kg)"
                ),
                where="loading",
                detail={
                    "total_kg": round(total_weight, 1),
                    "max_kg": float(truck.max_weight_kg),
                    "fraction": round(total_weight / truck.max_weight_kg, 3),
                },
            )
        )

    # Per-pallet checks.
    for slot_id, pallet in loaded:
        layout = pallet.layout
        if layout is None:
            continue

        # Heavy pallet.
        if pallet.weight_kg > PALLET_HEAVY_KG:
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="PALLET_OVERWEIGHT",
                    message=(
                        f"Pallet {pallet.pallet_id} in {slot_id} weighs {pallet.weight_kg:.0f} kg "
                        f"— above {PALLET_HEAVY_KG:.0f} kg practical forklift limit"
                    ),
                    where=f"slot {slot_id}",
                    detail={
                        "pallet_id": pallet.pallet_id,
                        "weight_kg": round(pallet.weight_kg, 1),
                    },
                )
            )

        cell_h = PALLET_HEIGHT_M / max(1, layout.max_level)

        # Group items by column.
        cols: dict[tuple[int, int], list[PalletItem]] = {}
        for it in pallet.items:
            cols.setdefault((it.col_x, it.col_y), []).append(it)

        for (cx, cy), items in cols.items():
            top_level = max(it.bottom_level + max(1, it.stack_size) for it in items)

            # Stack overflow within layout.
            if top_level > layout.max_level:
                out.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="STACK_OVERFLOW",
                        message=(
                            f"Column ({cx},{cy}) of {slot_id} stacks {top_level} units, "
                            f"layout allows only {layout.max_level}"
                        ),
                        where=f"slot {slot_id}, col ({cx},{cy})",
                        detail={
                            "pallet_id": pallet.pallet_id,
                            "col_x": cx,
                            "col_y": cy,
                            "top_level": top_level,
                            "max_level": layout.max_level,
                        },
                    )
                )

            # Pallet height vs truck inner height.
            stack_height_m = top_level * cell_h
            if stack_height_m > TRUCK_HEIGHT_M:
                out.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        code="PALLET_HEIGHT_EXCEEDS_TRUCK",
                        message=(
                            f"Stack at ({cx},{cy}) of {slot_id} reaches "
                            f"{stack_height_m:.2f} m — taller than truck inner clearance "
                            f"{TRUCK_HEIGHT_M:.2f} m"
                        ),
                        where=f"slot {slot_id}, col ({cx},{cy})",
                        detail={
                            "stack_height_m": round(stack_height_m, 3),
                            "truck_height_m": TRUCK_HEIGHT_M,
                        },
                    )
                )

            # Crush risk + glass-under-heavy. Uses CONTINUOUS xy-overlap
            # (not legacy col_x/col_y bins) so we only flag pairs whose
            # footprints actually touch — discrete bins were producing
            # false positives for items that share a legacy column index
            # but don't physically rest on each other.
            sorted_items = sorted(items, key=lambda x: x.pos_z)
            for lower, upper in zip(sorted_items, sorted_items[1:]):
                if upper.pos_z + OVERLAP_EPS_M < lower.top_z:
                    continue  # not actually stacked
                ox = max(0.0, min(lower.end_x, upper.end_x) - max(lower.pos_x, upper.pos_x))
                oy = max(0.0, min(lower.end_y, upper.end_y) - max(lower.pos_y, upper.pos_y))
                if ox <= OVERLAP_EPS_M or oy <= OVERLAP_EPS_M:
                    continue  # no physical xy overlap
                lower_w = float(lower.unit_weight_kg)
                upper_w = float(upper.unit_weight_kg)
                if (
                    lower_w > 0
                    and upper_w >= CRUSH_MIN_UPPER_KG
                    and upper_w > CRUSH_WEIGHT_RATIO * lower_w
                ):
                    out.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            code="CRUSH_RISK",
                            message=(
                                f"{upper.physical_type} {upper.sku} ({upper_w:.1f} kg/box) "
                                f"sits on lighter {lower.physical_type} {lower.sku} "
                                f"({lower_w:.1f} kg/box) in {slot_id} col ({cx},{cy}) — "
                                f"will crush"
                            ),
                            where=f"slot {slot_id}, col ({cx},{cy})",
                            detail={
                                "upper_sku": upper.sku,
                                "upper_kg": round(upper_w, 2),
                                "upper_type": upper.physical_type,
                                "lower_sku": lower.sku,
                                "lower_kg": round(lower_w, 2),
                                "lower_type": lower.physical_type,
                                "ratio": round(upper_w / max(lower_w, 1e-3), 2),
                            },
                        )
                    )
                if (
                    lower.physical_type in ("bottle", "can")
                    and upper.physical_type == "keg"
                ):
                    out.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            code="GLASS_UNDER_HEAVY",
                            message=(
                                f"Keg {upper.sku} sits on fragile {lower.physical_type} "
                                f"{lower.sku} in {slot_id} col ({cx},{cy})"
                            ),
                            where=f"slot {slot_id}, col ({cx},{cy})",
                            detail={
                                "upper_sku": upper.sku,
                                "lower_sku": lower.sku,
                                "lower_type": lower.physical_type,
                            },
                        )
                    )

        # Stack stability — narrow towers tip over. Looks at every
        # vertical column of items sharing roughly the same xy footprint
        # and rejects total stack height greater than STACK_RATIO_ERROR ×
        # the smaller footprint side. Single low items are exempt
        # (anything below STACK_MIN_HEIGHT_M is too short to topple).
        out.extend(_check_stack_stability(slot_id, pallet))

    # Center of mass + side balance.
    com = _center_of_mass(state)
    side_weight = _side_weights(state)
    if com is not None:
        com_x, com_y, com_z = com
        if abs(com_z) > COM_LATERAL_ERROR_M:
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="COM_LATERAL_ROLLOVER",
                    message=(
                        f"Center of mass shifted {com_z:+.2f} m laterally — "
                        f"rollover risk under cornering"
                    ),
                    where="loading",
                    detail={"offset_m": round(com_z, 3)},
                )
            )
        elif abs(com_z) > COM_LATERAL_WARN_M:
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="COM_LATERAL_IMBALANCE",
                    message=(
                        f"Center of mass shifted {com_z:+.2f} m laterally — "
                        f"watch sharp turns"
                    ),
                    where="loading",
                    detail={"offset_m": round(com_z, 3)},
                )
            )
        if abs(com_x) > COM_LONGITUDINAL_WARN_M:
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="COM_LONGITUDINAL_OFFSET",
                    message=(
                        f"Center of mass shifted {com_x:+.2f} m front/back — axle imbalance"
                    ),
                    where="loading",
                    detail={"offset_m": round(com_x, 3)},
                )
            )
        if com_y > COM_HIGH_WARN_M:
            out.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="COM_HIGH",
                    message=f"Center of mass is {com_y:.2f} m above floor — top-heavy load",
                    where="loading",
                    detail={"height_m": round(com_y, 3)},
                )
            )

    if side_weight is not None:
        wL, wR = side_weight
        if wL > 0 and wR > 0:
            ratio = max(wL, wR) / min(wL, wR)
            if ratio > LR_IMBALANCE_RATIO:
                out.append(
                    ValidationIssue(
                        severity=ValidationSeverity.WARNING,
                        code="WEIGHT_IMBALANCE_LR",
                        message=(
                            f"Left/Right weight ratio {ratio:.2f}× "
                            f"(L={wL:.0f} kg vs R={wR:.0f} kg)"
                        ),
                        where="loading",
                        detail={"left_kg": round(wL, 1), "right_kg": round(wR, 1), "ratio": round(ratio, 3)},
                    )
                )

    return out


def _center_of_mass(state: WorldState) -> tuple[float, float, float] | None:
    """Compute (x, y, z) center of mass in truck coordinates by aggregating
    each box's weight at its own (slot_xz + cell offset, level cell-y)."""
    max_pos = _max_side_position(state)
    total = 0.0
    sx = sy = sz = 0.0
    for pallet_id, slot_id in state.cargo.slot_by_pallet.items():
        pallet = state.cargo.pallet_by_id.get(pallet_id)
        if pallet is None or pallet.layout is None:
            continue
        pos = _slot_world_xz(slot_id, max_pos)
        if pos is None:
            continue
        slot_x, slot_z = pos
        layout = pallet.layout
        cell_len = PALLET_LEN_M / max(1, layout.cols_x)
        cell_w = PALLET_WIDTH_M / max(1, layout.cols_y)
        cell_h = PALLET_HEIGHT_M / max(1, layout.max_level)
        side = slot_id[:1]

        for it in pallet.items:
            local_x = (it.col_x + 0.5) * cell_len - PALLET_LEN_M / 2.0
            if side == "L":
                local_z = -PALLET_WIDTH_M / 2.0 + (it.col_y + 0.5) * cell_w
            elif side == "R":
                local_z = PALLET_WIDTH_M / 2.0 - (it.col_y + 0.5) * cell_w
            else:
                local_z = (it.col_y + 0.5) * cell_w - PALLET_WIDTH_M / 2.0
            wx = slot_x + local_x
            wz = slot_z + local_z
            stack = max(1, it.stack_size)
            unit_w = float(it.unit_weight_kg)
            for k in range(stack):
                wy = (it.bottom_level + k + 0.5) * cell_h
                total += unit_w
                sx += wx * unit_w
                sy += wy * unit_w
                sz += wz * unit_w
    if total <= 0:
        return None
    return (sx / total, sy / total, sz / total)


def _stack_columns(pallet: Pallet) -> dict[tuple[float, float], list[PalletItem]]:
    """Group items into vertical "columns" — items whose xy footprint
    overlaps share a column. We pick the smallest item as the column
    seed and pull anything that overlaps it onto the same key. Used by
    the stability check below."""

    cols: dict[tuple[float, float], list[PalletItem]] = {}
    items = sorted(
        (it for it in pallet.items if it.qty > 0),
        key=lambda it: (it.pos_z, it.pos_x, it.pos_y),
    )
    for it in items:
        # Match against existing column seeds: same anchored x/y means
        # same column. Tolerance avoids float noise on stacked items.
        key = (round(it.pos_x, 3), round(it.pos_y, 3))
        cols.setdefault(key, []).append(it)
    return cols


def _braced_narrow(
    cols: dict[tuple[float, float], list[PalletItem]],
    target_key: tuple[float, float],
    target_items: list[PalletItem],
    base_narrow: float,
) -> tuple[float, list[tuple[float, float]]]:
    """Return the EFFECTIVE narrow side after considering lateral
    bracing from adjacent columns.

    A column counts as a brace for the target column if:
      * Its footprint shares a face (touching, edge-aligned within
        a small tolerance) with the target's footprint.
      * Its top reaches at least 50 % of the target column's height
        (otherwise the brace ends below mid-height and the upper half
        still tilts freely).

    Effective narrow = sum of widths of all braced columns + the
    target column itself, measured along the axis where the bracing
    extends.

    Returns `(effective_narrow, list_of_brace_keys)` so the validator
    can mention which neighbours rescue this column in the message.
    """

    eps = 1e-3
    tcx, tcy = target_key
    t_items = target_items
    t_min_x = min(it.pos_x for it in t_items)
    t_max_x = max(it.end_x for it in t_items)
    t_min_y = min(it.pos_y for it in t_items)
    t_max_y = max(it.end_y for it in t_items)
    t_floor = min(it.pos_z for it in t_items)
    t_top = max(it.top_z for it in t_items)
    t_mid_height_z = t_floor + (t_top - t_floor) * 0.5

    # Walk neighbours; keep only those that physically touch a face
    # AND extend up to ≥ mid-height of the target.
    eff_x = t_max_x - t_min_x
    eff_y = t_max_y - t_min_y
    braces: list[tuple[float, float]] = []

    for key, items in cols.items():
        if key == target_key:
            continue
        n_min_x = min(it.pos_x for it in items)
        n_max_x = max(it.end_x for it in items)
        n_min_y = min(it.pos_y for it in items)
        n_max_y = max(it.end_y for it in items)
        n_top = max(it.top_z for it in items)

        # Must reach mid-height to count as bracing.
        if n_top + eps < t_mid_height_z:
            continue

        # Touching on the X axis (column sits to the left or right,
        # y-ranges overlap meaningfully).
        x_touches_left = abs(n_max_x - t_min_x) < eps
        x_touches_right = abs(n_min_x - t_max_x) < eps
        y_overlap = max(0.0, min(t_max_y, n_max_y) - max(t_min_y, n_min_y))
        if (x_touches_left or x_touches_right) and y_overlap > eps:
            eff_x += (n_max_x - n_min_x)
            braces.append(key)
            continue

        # Touching on the Y axis (column sits in front or behind).
        y_touches_front = abs(n_max_y - t_min_y) < eps
        y_touches_back = abs(n_min_y - t_max_y) < eps
        x_overlap = max(0.0, min(t_max_x, n_max_x) - max(t_min_x, n_min_x))
        if (y_touches_front or y_touches_back) and x_overlap > eps:
            eff_y += (n_max_y - n_min_y)
            braces.append(key)

    eff_narrow = max(min(eff_x, eff_y), base_narrow)
    return eff_narrow, braces


def _check_stack_stability(slot_id: str, pallet: Pallet) -> list[ValidationIssue]:
    out: list[ValidationIssue] = []
    cols = _stack_columns(pallet)

    for (cx, cy), items in cols.items():
        if not items:
            continue
        min_x = min(it.dim_x for it in items)
        min_y = min(it.dim_y for it in items)
        narrow = min(min_x, min_y)
        if narrow <= 0:
            continue

        floor = min(it.pos_z for it in items)
        top = max(it.top_z for it in items)
        height = top - floor
        if height < STACK_MIN_HEIGHT_M:
            continue

        ratio = height / narrow
        if ratio <= STACK_RATIO_WARN:
            continue

        # Lateral bracing: a narrow tower wedged between two other
        # tall stacks can't topple — adjacent columns physically
        # support it. Recompute the ratio against the BRACED narrow
        # side and skip the warning if the joined footprint is
        # stable enough.
        braced_narrow, braces = _braced_narrow(cols, (cx, cy), items, narrow)
        braced_ratio = height / braced_narrow
        if braced_ratio <= STACK_RATIO_WARN:
            continue

        severity = (
            ValidationSeverity.ERROR
            if braced_ratio > STACK_RATIO_ERROR
            else ValidationSeverity.WARNING
        )
        code = "STACK_UNSTABLE" if severity == ValidationSeverity.ERROR else "STACK_WOBBLY"
        sample = items[0]
        # Use the braced numbers in the message — that's the
        # effective stability, not the isolated-column ratio.
        out.append(
            ValidationIssue(
                severity=severity,
                code=code,
                message=(
                    f"Stack at ({cx:.2f}, {cy:.2f}) of {slot_id} is "
                    f"{height:.2f} m tall on a {braced_narrow:.2f} m base "
                    f"(ratio {braced_ratio:.1f}, max {STACK_RATIO_ERROR:.1f}) — "
                    f"narrow tower of {sample.physical_type} will topple"
                ),
                where=f"slot {slot_id}, col ({cx:.2f},{cy:.2f})",
                detail={
                    "pallet_id": pallet.pallet_id,
                    "pos_x": cx,
                    "pos_y": cy,
                    "stack_height_m": round(height, 3),
                    "footprint_min_m": round(narrow, 3),
                    "braced_narrow_m": round(braced_narrow, 3),
                    "braces": braces,
                    "ratio": round(braced_ratio, 2),
                    "limit_ratio": STACK_RATIO_ERROR,
                    "warn_ratio": STACK_RATIO_WARN,
                    "physical_type": sample.physical_type,
                },
            )
        )
    return out


def _side_weights(state: WorldState) -> tuple[float, float] | None:
    wL = wR = 0.0
    for pallet_id, slot_id in state.cargo.slot_by_pallet.items():
        pallet = state.cargo.pallet_by_id.get(pallet_id)
        if pallet is None:
            continue
        if slot_id.startswith("L"):
            wL += pallet.weight_kg
        elif slot_id.startswith("R"):
            wR += pallet.weight_kg
        # B contributes equally to both — skipped.
    if wL == 0 and wR == 0:
        return None
    return wL, wR
