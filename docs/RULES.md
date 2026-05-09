# Damm Simulator — Validation Rules

Two checker layers. Different lifecycles, different consequences.

| Layer | When it runs | Failure mode | Source |
|---|---|---|---|
| **Transport rules** | After the run finishes (post-hoc) | Issues with `severity=error/warning/info` returned to the user | `simulator/validation/validator.py` |
| **Placement rules** | During simulation, after every state-changing command | Either `_CommandError` (strict mode) or `capacity_violations++` (default) | `simulator/core/simulator.py::_check_pallet_invariants` |

Transport rules answer "can the truck legally and safely depart?". Placement rules answer "did the algorithm produce a physically coherent state?".

---

## 1. Transport rules

Run by `validate_plan(case, plan, run_result, sim)` once the simulation is complete. They look at the final `WorldState` and the event log. Each rule emits one or more `ValidationIssue` records. Three severities:

| Severity | Meaning | UI behavior |
|---|---|---|
| **ERROR** | The truck cannot leave the depot like this | Plan REJECTED |
| **WARNING** | Risky / suboptimal but technically allowed | Highlighted in yellow |
| **INFO** | Observation, no action required | Listed informationally |

Tunable thresholds (edit at the top of `validator.py`):

```
CRUSH_WEIGHT_RATIO       = 3.0      # upper / lower per-box weight to flag
CRUSH_MIN_UPPER_KG       = 5.0      # too-light upper item is harmless
PALLET_HEAVY_KG          = 800.0    # forklift practical limit
TRUCK_WEIGHT_NEAR_LIMIT  = 0.95     # fraction of max weight
COM_LATERAL_ERROR_M      = 0.30     # rollover risk
COM_LATERAL_WARN_M       = 0.20
COM_LONGITUDINAL_WARN_M  = 0.50     # axle imbalance
COM_HIGH_WARN_M          = 1.20     # top-heavy load
LR_IMBALANCE_RATIO       = 1.5      # left/right weight ratio
FILL_RATE_ERROR          = 0.95     # min delivered fraction
OVERTIME_HARD_HOURS      = 13.0     # legal driver shift cap
OVERTIME_WARN_HOURS      = 10.0
STACK_RATIO_ERROR        = 3.5      # narrow-tower aspect cap (height / min side)
STACK_RATIO_WARN         = 3.0
STACK_MIN_HEIGHT_M       = 0.40     # below this, stacks are exempt
```

### 1.1 Cargo physics

| Code | Severity | Trigger | Why |
|---|---|---|---|
| `TRUCK_OVERWEIGHT` | ERROR | `total_kg > truck.max_weight_kg` | Vehicle illegally overloaded |
| `TRUCK_NEAR_WEIGHT_LIMIT` | WARNING | `total_kg > 0.95 × max_weight_kg` | One missing scale measurement away from a fine |
| `PALLET_OVERWEIGHT` | WARNING | `pallet.weight_kg > 800 kg` | Forklift cannot lift, manual handling injuries |
| `STACK_OVERFLOW` | ERROR | Discrete column count > `layout.max_level` | Legacy 4-level cap on the per-class layout |
| `PALLET_HEIGHT_EXCEEDS_TRUCK` | ERROR | `stack_height_m > 2.10 m` | Hits the truck cabin ceiling |
| `STACK_UNSTABLE` | ERROR | `stack_height / min_footprint_side > 3.5` | Narrow tower of small items will topple |
| `STACK_WOBBLY` | WARNING | Same ratio in `(3.0, 3.5]` | Borderline — fine if driving carefully |
| `CRUSH_RISK` | ERROR | Upper item's per-box weight > `3.0 ×` lower's, and upper ≥ 5 kg | Heavy keg on a light case will crush it |
| `GLASS_UNDER_HEAVY` | WARNING | `bottle/can` directly under `keg` | Glass bottles + 50 kg keg = breakage risk |

### 1.2 Truck stability

| Code | Severity | Trigger | Why |
|---|---|---|---|
| `COM_LATERAL_ROLLOVER` | ERROR | \|center-of-mass lateral offset\| > 0.30 m | Truck will roll over in a sharp turn |
| `COM_LATERAL_IMBALANCE` | WARNING | \|offset\| in `(0.20, 0.30]` m | Drive carefully on roundabouts |
| `COM_LONGITUDINAL_OFFSET` | WARNING | \|offset\| > 0.50 m front-to-back | One axle takes too much load → tire wear / brake imbalance |
| `COM_HIGH` | WARNING | COM height > 1.20 m above floor | Top-heavy, sways at speed |
| `WEIGHT_IMBALANCE_LR` | WARNING | `max(L, R) / min(L, R) > 1.5` | Suspension wear, asymmetric handling |

### 1.3 Process & route

| Code | Severity | Trigger | Why |
|---|---|---|---|
| `TIME_WINDOW_VIOLATION` | ERROR / WARNING | Arrived after the client's window closed | Client refuses delivery |
| `CLOSED_VISITS` | ERROR | Visited a client whose store was closed today | Wasted trip |
| `FILL_RATE_LOW` | ERROR | `delivered / ordered < 0.95` | Customer SLA breach |
| `DROPS` | ERROR | Items the simulator failed to hand over (target not on pallet) | Lost orders |
| `MISSED_CLIENTS` | ERROR | A client in `case.orders` never got a `DriveTo` | Forgotten stop |
| `REVISIT_CLIENT` | WARNING | Same client visited twice in the route | Inefficient routing |
| `OVERTIME_LEGAL` | ERROR | Total shift > 13 h | Illegal under EU driver-rest rules |
| `OVERTIME_LONG_SHIFT` | WARNING | Shift in `(10, 13]` h | Extra labor cost |
| `CAPACITY_VIOLATIONS` | WARNING | Counter > 0 from runtime placement-rule layer | Forwarded from layer 2 |
| `SIM_FAILED` | ERROR | Simulator threw `_CommandError` | Plan crashed mid-run |
| `VALIDATION_INTERNAL` | WARNING | Validator itself raised | Bug in the validator |

### 1.4 Reading a validation report

```python
from simulator.validation import validate_plan
report = validate_plan(case, plan, result, sim)
if not report.is_valid:
    for issue in report.issues:
        if issue.severity.value == "error":
            print(issue.code, issue.where, issue.message)
```

The frontend's `ValidationPanel` renders the same data as a colored list with a `PLAN REJECTED` banner if `errors > 0`.

---

## 2. Placement rules (algorithm-side)

These run in real time inside the simulator. They guarantee the **physical state** the algorithm hands to the simulator is consistent: no overlap, no items outside the pallet, no levitating boxes.

The algorithm owns geometry. The simulator just executes Pick / Load / Unload / PickupReturn commands using the `pos_*` and `dim_*` the algorithm chose. These rules check that those choices respect physics.

### 2.1 Modes

| Mode | Constructor | Behavior on violation |
|---|---|---|
| **Soft** (default) | `Simulator(clients, network)` | Floating / overlap → `state.capacity_violations++`. Out-of-pallet remains a hard error. Run completes; user sees nothing pop up but `capacity_violations` reflects reality |
| **Strict** | `Simulator(clients, network, strict_physics=True)` | All five rules hard-stop the simulation with `_CommandError` |
| **API** | `POST /api/run {"strict_physics": true}` | Per-request opt-in |

`replay`, `nearest`, and similar baseline algorithms break overlap/floating rules under load — soft mode lets you still see their KPIs. `balanced` and `lifo` should pass strict on routine cases.

### 2.2 When checks run

Triggered by the dispatcher in `Simulator._dispatch` after every state-changing command:

- after every `Pick` (item added to a staging pallet)
- after every `Load` (pallet moved to a slot)
- after every Unload **batch** (one client × one slot — i.e. after Phase 1 lift, Phase 2 target take, Phase 3 same-client take, Phase 4 restock all completed)
- after every `PickupReturn` (empties added)

Pallet-internal mid-Unload states (e.g. between target take and blocker restock) are NOT checked because items are transiently absent from the pallet by design.

### 2.3 The five rules

#### R1 — `pos_x / pos_y / pos_z ≥ 0`
Item cannot have a negative coordinate inside a pallet's local frame. Any negative value indicates the algorithm computed a position from broken inputs (e.g. a non-existent item pulled into a centering offset).

| Severity | Always ERROR |
| Threshold | `pos_* < -1 mm` |
| Message | `{sku} pos_x={value} is negative (item slipped off the door edge)` |

#### R2 — `end_x ≤ 1.20 m`, `end_y ≤ 0.80 m`
Item must fit inside the **horizontal** pallet footprint (length × width).

| Severity | Always ERROR |
| Threshold | `pos_x + dim_x > PALLET_LENGTH_M (1.20) + 1 mm` or same for Y |
| Message | `{sku} end_x={value} exceeds pallet length 1.20 m` |

#### R3 — `top_z ≤ 1.80 m` (height overflow)
Item's top must not exceed the pallet height envelope. The truck cabin is 2.10 m, so a 5 cm overflow on a 1.80 m pallet is survivable — kept as a soft warning even in strict mode.

| Severity | Always WARNING (counter only) |
| Threshold | `pos_z + dim_h > 1.80 m + 1 mm` |
| Effect | `state.capacity_violations++`, surfaces via the validator's `CAPACITY_VIOLATIONS` rule |

#### R4 — No 3D AABB overlap
No two items on the same pallet may share volume. Two items overlap iff their full 3D AABBs intersect (with a 1 mm tolerance for floating-point noise).

| Severity | Strict: ERROR. Soft: counter |
| Threshold | All six axes overlap simultaneously, gap < 1 mm |
| Message | `{sku_a}@(x,y,z) overlaps {sku_b}@(x,y,z) — two items occupy the same volume` |

#### R5 — Floating support
An item with `pos_z > 0` must rest on at least one supporting item directly below: another item with `top_z = this.pos_z` (within 1 mm) and overlapping XY footprint.

Items at `pos_z = 0` are exempt — they sit on the pallet floor, always supported.

| Severity | Strict: ERROR. Soft: counter |
| Threshold | `pos_z ≥ 1 mm` AND no supporter found |
| Message | `{sku}@(x,y,z) is floating — no item directly below provides support` |

### 2.4 Tunables

```python
# simulator/core/simulator.py
_PALLET_LENGTH_M = 1.20
_PALLET_WIDTH_M  = 0.80
_PALLET_HEIGHT_M = 1.80
_PHYSICS_EPS     = 1e-3   # 1 mm float-error tolerance
```

Edit `_PHYSICS_EPS` if you see false positives from accumulated rounding (algorithms with deep stack chains). Don't go above 5 mm — you'll start hiding real overlaps.

### 2.5 Constants used by the algorithms themselves

These are the values algorithms enforce **before** emitting a command, so the runtime checks above never fire on a well-behaved algorithm.

| Constant | Value | Where | Meaning |
|---|---|---|---|
| `PALLET_MAX_WEIGHT_KG` | 1000.0 | `balanced.py`, `lifo.py` | Per-pallet weight cap for chunk placement |
| `KEG_MAX_STACK` | 2 | all algorithms | Business rule: kegs stack at most 2 high |
| `STACK_RATIO` | 3.0 (lifo) / 3.5 (balanced) | each algorithm | `stack_height / min_footprint_side` cap, matches the validator's `STACK_RATIO_ERROR` |
| `IDEAL_X` / `IDEAL_Y` | 0.52 / 0.50 | `balanced.py`, `BalancedStrategy` | Target normalized truck COG |
| `W_BALANCE_X` / `W_BALANCE_Y` | 8.0 / 32.0 | `balanced.py` | COG penalty weights (Y is 4× because L/R imbalance is more dangerous) |
| `W_NEW_SLOT` / `W_NEW_STACK` | 2.5 / 1.5 | `balanced.py` | Locality penalties for client compactness |

---

## 3. Cheat sheet for diagnosing a failure

| What you see | Likely cause | Where to look |
|---|---|---|
| "PLAN REJECTED" with COM_LATERAL_ROLLOVER | Algorithm dumped all KEG slots on one side | `BalancedStrategy._cog_penalty`, `balanced._score` weights |
| "PLAN REJECTED" with STACK_UNSTABLE | A chunk emitted with `qty × dh > 3.5 × min(dx, dy)` | `_split_chunks` chunk_size cap; `find_position(aspect_limit=...)` |
| "PLAN REJECTED" with CRUSH_RISK | Heavy keg on light case — class mixing | `chunk.pallet_class` (must use `physical_type`, not UMA) |
| Strict run errors with "is floating" | Algorithm's `find_position` returned a top anchor without a supporter | Usually caused by removing an item without re-running placement; see `Phase 4` restock logic |
| Strict run errors with "occupies the same volume" | Two restock entries in the same FIFO queue dropped onto colliding positions | `VirtualTruck.plan_restock` order vs `_collect_restock_static` queue draining order |
| `capacity_violations` huge in `replay` | Expected — `replay` is the baseline, not stability-aware | Switch to `balanced` / `lifo` for stable plans |

---

## 4. Adding a new rule

**Transport rule** (post-hoc, validator):
1. Add a constant near the top of `validator.py`.
2. Add the check inside the right `_check_*` function (cargo, stability, process).
3. Append a `ValidationIssue` with a unique `code`.
4. Update this document.

**Placement rule** (runtime, simulator):
1. Add a check inside `_check_pallet_invariants`.
2. Decide whether it's always ERROR (R1, R2) or strict-vs-soft (R4, R5).
3. Wire counters via `st.capacity_violations += 1` for the soft case.
4. Update this document.

The two layers do not call each other — keep it that way. Transport rules read the final state; placement rules read the running state. Mixing them produces phantom errors.
