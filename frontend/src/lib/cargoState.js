// Pure derivation of cargo state at any given step index.
//
// Each PHYSICAL box = one entry in `boxes`. A PalletItem with stack_size=N
// expands into N boxes, one per discrete level. Events from the simulator
// are now per-box (qty=1, with explicit `level`), so we match by exact
// (slot_id, col_x, col_y, level, sku, intended_client).
//
// Output:
//   {
//     boxes: [
//       { id, slot_id, side, pallet_class, layout,
//         col_x, col_y, level, sku, qty,
//         intended_client, is_returnable_empty,
//         status: 'in_pallet' | 'in_hands' | 'delivered',
//         history: [...] }
//     ],
//     palletsBySlot: { slot_id → { pallet_class, layout, side } },
//     currentStage, pendingStage, idx, total,
//   }

// Legacy cell sizes used by Python's PalletItem.col_x/col_y/bottom_level
// derivation. Kept in sync with simulator/domain/pallet.py.
const _LEGACY_CELL_X_M = 0.30;
const _LEGACY_CELL_Y_M = 0.27;
const _LEGACY_CELL_H_M = 0.30;

function boxKey(slotId, colX, colY, level, sku, intendedClient) {
  return `${slotId}|${colX}|${colY}|${level}|${sku}|${intendedClient ?? ''}`;
}

function pickEventLevel(detail) {
  if (detail?.level !== undefined && detail?.level !== null) return Number(detail.level);
  if (detail?.bottom_level !== undefined && detail?.bottom_level !== null) return Number(detail.bottom_level);
  return null;
}

function findBox(boxes, slotId, colX, colY, level, sku, intendedClient, status, posX, posY, posZ) {
  // Prefer continuous-pos matching when the simulator gave us pos_*.
  // Several small items can share the same legacy (col_x, col_y, level)
  // cell, so col-based matching mis-targets and the next event finds
  // the wrong physical box (the visible "overlap" comes from this).
  if (posX !== undefined && posX !== null) {
    const eps = 0.005;
    for (const b of boxes) {
      if (
        b.status === status &&
        b.slot_id === slotId &&
        b.sku === sku &&
        Math.abs((b.pos_x ?? 0) - posX) < eps &&
        Math.abs((b.pos_y ?? 0) - posY) < eps &&
        Math.abs((b.pos_z ?? 0) - posZ) < eps &&
        (intendedClient === undefined || intendedClient === null || b.intended_client === intendedClient)
      ) {
        return b;
      }
    }
    // Fallback: ignore intended_client.
    for (const b of boxes) {
      if (
        b.status === status &&
        b.slot_id === slotId &&
        b.sku === sku &&
        Math.abs((b.pos_x ?? 0) - posX) < eps &&
        Math.abs((b.pos_y ?? 0) - posY) < eps &&
        Math.abs((b.pos_z ?? 0) - posZ) < eps
      ) {
        return b;
      }
    }
  }
  // Legacy col-based matching (kept for events that don't carry pos_*).
  for (const b of boxes) {
    if (
      b.status === status &&
      b.slot_id === slotId &&
      b.col_x === colX &&
      b.col_y === colY &&
      b.level === level &&
      b.sku === sku &&
      (intendedClient === undefined || intendedClient === null || b.intended_client === intendedClient)
    ) {
      return b;
    }
  }
  // Fallback: ignore intended_client
  for (const b of boxes) {
    if (
      b.status === status &&
      b.slot_id === slotId &&
      b.col_x === colX &&
      b.col_y === colY &&
      b.level === level &&
      b.sku === sku
    ) {
      return b;
    }
  }
  // Looser fallback: any box at that column/level/sku regardless of status
  for (const b of boxes) {
    if (
      b.slot_id === slotId &&
      b.col_x === colX &&
      b.col_y === colY &&
      b.level === level &&
      b.sku === sku &&
      b.status !== 'delivered'
    ) {
      return b;
    }
  }
  return null;
}

export function flattenStages(stops = []) {
  const flat = [];
  stops.forEach((stop, stopIdx) => {
    (stop.stages || []).forEach((stg) => {
      flat.push({
        ...stg,
        stop_idx: stopIdx,
        stop_client_id: stop.client_id,
        stop_client_name: stop.name,
        stop_visit_seq: stop.visit_seq,
      });
    });
  });
  flat.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  return flat;
}

export function buildInitialBoxes(initialCargo = []) {
  const boxes = [];
  const palletsBySlot = {};
  let id = 0;
  for (const slotEntry of initialCargo) {
    palletsBySlot[slotEntry.slot_id] = {
      side: slotEntry.side,
      pallet_class: slotEntry.pallet?.pallet_class || null,
      layout: slotEntry.pallet?.layout || null,
      pallet_id: slotEntry.pallet?.pallet_id || null,
    };
    if (!slotEntry.pallet) continue;
    const items = slotEntry.pallet.items || [];
    for (const it of items) {
      const stackSize = Math.max(1, it.stack_size || 1);
      // Per-physical-box: split a stack of N units into N cubes stacked
      // vertically by (it.dim_h / N). Continuous pos lets us render exactly
      // where the bin-packer placed each item.
      const slice_h = stackSize > 0 ? (it.dim_h ?? 0) / stackSize : 0;
      for (let i = 0; i < stackSize; i++) {
        boxes.push({
          id: `b${id++}`,
          slot_id: slotEntry.slot_id,
          side: slotEntry.side,
          pallet_class: slotEntry.pallet.pallet_class,
          layout: slotEntry.pallet.layout,
          // Legacy discrete coords (kept for stage-event matching).
          col_x: it.col_x,
          col_y: it.col_y,
          level: it.bottom_level + i,
          // Continuous coords for rendering — i-th physical unit in stack.
          pos_x: it.pos_x ?? 0,
          pos_y: it.pos_y ?? 0,
          pos_z: (it.pos_z ?? 0) + i * slice_h,
          dim_x: it.dim_x ?? 0.20,
          dim_y: it.dim_y ?? 0.20,
          dim_h: slice_h || (it.dim_h ?? 0.24),
          sku: it.sku,
          qty: stackSize > 0 ? it.qty / stackSize : it.qty,
          unit_weight_kg: it.unit_weight_kg ?? 0,
          intended_client: it.intended_client,
          is_returnable_empty: it.is_returnable_empty,
          physical_type: it.physical_type || 'unit',
          stack_member_idx: i,
          stack_member_total: stackSize,
          source_sku: it.sku,
          status: 'in_pallet',
          history: [],
        });
      }
    }
  }
  return { boxes, palletsBySlot };
}

// Build maps for "what happens to this lifted box":
//   replaceMap:  lift_seq → matching BLOCKER_REPLACE seq (foreign blocker)
//   deliveryMap: lift_seq → matching TARGET_TAKE seq (same-client opportunistic)
export function buildLiftReplaceMap(stages) {
  const replaceMap = new Map();
  const deliveryMap = new Map();
  const open = [];
  for (const s of stages) {
    const d = s.detail || {};
    const lvl = pickEventLevel(d);
    if (s.kind === 'BLOCKER_LIFT') {
      open.push({
        seq: s.seq,
        key: boxKey(d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client),
      });
    } else if (s.kind === 'BLOCKER_REPLACE') {
      const key = boxKey(d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client);
      for (let i = open.length - 1; i >= 0; i--) {
        if (open[i].key === key) {
          replaceMap.set(open[i].seq, s.seq);
          open.splice(i, 1);
          break;
        }
      }
    } else if (s.kind === 'TARGET_TAKE' && d.opportunistic) {
      const key = boxKey(d.slot_id, d.col_x, d.col_y, lvl, d.sku, undefined);
      for (let i = open.length - 1; i >= 0; i--) {
        if (open[i].key === key || open[i].key.startsWith(`${d.slot_id}|${d.col_x}|${d.col_y}|${lvl}|${d.sku}|`)) {
          deliveryMap.set(open[i].seq, s.seq);
          open.splice(i, 1);
          break;
        }
      }
    }
  }
  return { replaceMap, deliveryMap };
}

export function cargoStateAt(initialCargo, stages, idx) {
  const { boxes, palletsBySlot } = buildInitialBoxes(initialCargo);
  let nextId = boxes.length;
  const upTo = Math.max(0, Math.min(idx, stages.length));

  for (let i = 0; i < upTo; i++) {
    const stg = stages[i];
    if (!stg) continue;
    const d = stg.detail || {};
    const lvl = pickEventLevel(d);

    if (stg.kind === 'BLOCKER_LIFT') {
      const box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client, 'in_pallet', d.pos_x, d.pos_y, d.pos_z);
      if (box) {
        box.status = 'in_hands';
        box.history.push({ step_idx: i, kind: 'lifted', t_min: stg.t_min, time_min: stg.time_min });
      }
    } else if (stg.kind === 'BLOCKER_REPLACE') {
      // Match by ORIGINAL pos using `from_pos_*` from the event.
      // `box.pos_*` still holds the lift-time location after BLOCKER_LIFT.
      // Without from_pos matching, two blockers sharing (sku, client)
      // would get their destinations swapped, producing visible overlap.
      let box = null;
      const fromX = d.from_pos_x;
      const fromY = d.from_pos_y;
      const fromZ = d.from_pos_z;
      if (fromX !== undefined && fromX !== null) {
        const eps = 0.005;
        for (const b of boxes) {
          if (
            b.status === 'in_hands' &&
            b.slot_id === d.slot_id &&
            b.sku === d.sku &&
            Math.abs((b.pos_x ?? 0) - fromX) < eps &&
            Math.abs((b.pos_y ?? 0) - fromY) < eps &&
            Math.abs((b.pos_z ?? 0) - fromZ) < eps &&
            (d.intended_client === undefined || d.intended_client === null || b.intended_client === d.intended_client)
          ) {
            box = b;
            break;
          }
        }
      }
      if (!box) {
        // Fallback when from_pos isn't carried (legacy events): pick
        // the first matching in_hands box. Order-dependent, may
        // mis-pair when multiple blockers share (sku, client).
        for (const b of boxes) {
          if (
            b.status === 'in_hands' &&
            b.slot_id === d.slot_id &&
            b.sku === d.sku &&
            (d.intended_client === undefined || d.intended_client === null || b.intended_client === d.intended_client)
          ) {
            box = b;
            break;
          }
        }
      }
      if (!box) {
        // Legacy column-based fallback.
        box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client, 'in_hands');
      }
      if (box) {
        box.status = 'in_pallet';
        // The simulator carries the algorithm's restock pos in the
        // BLOCKER_REPLACE event — update the box so it lands at its
        // new spot. Without this it stays at the legacy lift coords
        // and visually keeps floating.
        if (d.pos_x !== undefined && d.pos_x !== null) box.pos_x = d.pos_x;
        if (d.pos_y !== undefined && d.pos_y !== null) box.pos_y = d.pos_y;
        if (d.pos_z !== undefined && d.pos_z !== null) box.pos_z = d.pos_z;
        if (d.dim_x !== undefined && d.dim_x !== null) box.dim_x = d.dim_x;
        if (d.dim_y !== undefined && d.dim_y !== null) box.dim_y = d.dim_y;
        if (d.dim_h !== undefined && d.dim_h !== null) box.dim_h = d.dim_h;
        // Recompute legacy discrete coords from the new pos so the
        // NEXT lift / take / replace event finds this box (events
        // carry recalculated col_x/col_y/level matching the new pos).
        if (box.pos_x !== undefined) {
          box.col_x = Math.max(0, Math.floor(box.pos_x / _LEGACY_CELL_X_M));
        }
        if (box.pos_y !== undefined) {
          box.col_y = Math.max(0, Math.floor(box.pos_y / _LEGACY_CELL_Y_M));
        }
        if (box.pos_z !== undefined) {
          box.level = Math.max(0, Math.floor(box.pos_z / _LEGACY_CELL_H_M));
        }
        box.history.push({ step_idx: i, kind: 'replaced', t_min: stg.t_min, time_min: stg.time_min });
      }
    } else if (stg.kind === 'TARGET_TAKE') {
      // Try in_pallet first (regular target), fall back to in_hands (opportunistic same-client delivery)
      let box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, undefined, 'in_pallet', d.pos_x, d.pos_y, d.pos_z);
      if (!box) {
        box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, undefined, 'in_hands', d.pos_x, d.pos_y, d.pos_z);
      }
      if (box) {
        box.status = 'delivered';
        box.history.push({
          step_idx: i,
          kind: d.opportunistic ? 'delivered_opportunistic' : 'delivered',
          t_min: stg.t_min,
          time_min: stg.time_min,
        });
      }
    } else if (stg.kind === 'SETTLE') {
      // The simulator detected a box left floating after a delivery
      // removed its supporter and dropped it to a clean anchor. Find
      // the stale box (still at from_pos) and update its position.
      // Without this the visualizer keeps rendering the keg at its
      // pre-settle pos — visible as "100% intersection" with whatever
      // is now at that anchor.
      const fromX = d.from_pos_x;
      const fromY = d.from_pos_y;
      const fromZ = d.from_pos_z;
      let box = null;
      const eps = 0.005;
      for (const b of boxes) {
        if (
          b.status === 'in_pallet' &&
          b.slot_id === d.slot_id &&
          b.sku === d.sku &&
          Math.abs((b.pos_x ?? 0) - fromX) < eps &&
          Math.abs((b.pos_y ?? 0) - fromY) < eps &&
          Math.abs((b.pos_z ?? 0) - fromZ) < eps
        ) {
          box = b;
          break;
        }
      }
      if (box) {
        if (d.pos_x !== undefined) box.pos_x = d.pos_x;
        if (d.pos_y !== undefined) box.pos_y = d.pos_y;
        if (d.pos_z !== undefined) box.pos_z = d.pos_z;
        if (d.dim_x !== undefined) box.dim_x = d.dim_x;
        if (d.dim_y !== undefined) box.dim_y = d.dim_y;
        if (d.dim_h !== undefined) box.dim_h = d.dim_h;
        // Recompute legacy discrete coords so future events match.
        box.col_x = Math.max(0, Math.floor((box.pos_x ?? 0) / _LEGACY_CELL_X_M));
        box.col_y = Math.max(0, Math.floor((box.pos_y ?? 0) / _LEGACY_CELL_Y_M));
        box.level = Math.max(0, Math.floor((box.pos_z ?? 0) / _LEGACY_CELL_H_M));
        box.history.push({ step_idx: i, kind: 'settled', t_min: stg.t_min, time_min: stg.time_min });
      }
    } else if (stg.kind === 'PICKUP_RETURN') {
      const slotMeta = palletsBySlot[d.slot_id];
      if (slotMeta) {
        const qty = Math.max(1, Math.ceil(d.qty || 1));
        // The algorithm sends exact placement (pos_*, dim_*) for the
        // whole stack. Render it as `qty` cubes piled vertically at
        // that anchor instead of dropping all of them at (0,0,0).
        const stackPosX = d.pos_x ?? 0;
        const stackPosY = d.pos_y ?? 0;
        const stackPosZ = d.pos_z ?? 0;
        const stackDimX = d.dim_x ?? 0.40;
        const stackDimY = d.dim_y ?? 0.40;
        const stackDimH = d.dim_h ?? qty * 0.65;
        const sliceH = qty > 0 ? stackDimH / qty : stackDimH;
        for (let k = 0; k < qty; k++) {
          boxes.push({
            id: `b${nextId++}`,
            slot_id: d.slot_id,
            side: slotMeta.side,
            pallet_class: 'keg',
            layout: slotMeta.layout || { cols_x: 2, cols_y: 2, max_level: 4 },
            col_x: 0,
            col_y: 0,
            level: k,
            // Continuous coords from the algorithm — k-th keg sits
            // (k * sliceH) above the stack base.
            pos_x: stackPosX,
            pos_y: stackPosY,
            pos_z: stackPosZ + k * sliceH,
            dim_x: stackDimX,
            dim_y: stackDimY,
            dim_h: sliceH,
            sku: d.sku || 'EMPTY',
            qty: 1,
            // Empty kegs ≈ 2 kg, empty crates ≈ 0.6 kg, bottles ≈ 0.3 kg.
            // Picked up from the event when present, otherwise default
            // to keg weight.
            unit_weight_kg: d.unit_weight_kg ?? 2.0,
            intended_client: null,
            is_returnable_empty: true,
            physical_type: d.physical_type || 'keg',
            stack_member_idx: k,
            stack_member_total: qty,
            source_sku: d.sku || 'EMPTY',
            status: 'in_pallet',
            history: [{ step_idx: i, kind: 'picked', t_min: stg.t_min, time_min: stg.time_min }],
          });
        }
      }
    }
  }

  return {
    boxes,
    palletsBySlot,
    currentStage: upTo > 0 ? stages[upTo - 1] : null,
    pendingStage: upTo < stages.length ? stages[upTo] : null,
    idx: upTo,
    total: stages.length,
  };
}


// ---- Centre-of-mass computation ------------------------------------------
//
// Mirrors the validator's lateral / longitudinal / vertical COM math (see
// simulator/validation/validator.py::_center_of_mass) so the live readout
// in the playback agrees with the post-hoc PLAN REJECTED warnings.
//
// Truck coordinate convention:
//   X — front/back (along the truck length). Positive = back.
//   Z — left/right (lateral). Positive = right side. Rollover risk axis.
//   Y — height above the truck floor.
//
// Thresholds (matching validator constants):
//   COM_LATERAL_WARN_M   = 0.20   → "watch sharp turns"
//   COM_LATERAL_ERROR_M  = 0.30   → rollover risk

const _PALLET_LEN_M = 1.20;
const _PALLET_WIDTH_M = 0.80;
const _SLOT_GAP_X_M = 0.06;
const _LR_GAP_M = 0.10;

export const COM_LATERAL_WARN_M = 0.20;
export const COM_LATERAL_ERROR_M = 0.30;
export const COM_LONGITUDINAL_WARN_M = 0.50;
export const COM_HIGH_WARN_M = 1.20;

function _slotWorldXZ(slotId, maxSidePos) {
  const m = /^([LRB])(\d+)$/.exec(slotId);
  if (!m) return null;
  const side = m[1];
  const pos = parseInt(m[2], 10);
  const totalLength = maxSidePos * (_PALLET_LEN_M + _SLOT_GAP_X_M);
  const startX = -totalLength / 2 + (_PALLET_LEN_M + _SLOT_GAP_X_M) / 2;
  const x = startX + (pos - 1) * (_PALLET_LEN_M + _SLOT_GAP_X_M);
  if (side === 'L') return { x, z: -(_PALLET_WIDTH_M / 2 + _LR_GAP_M / 2) };
  if (side === 'R') return { x, z: +(_PALLET_WIDTH_M / 2 + _LR_GAP_M / 2) };
  // Back row — sticks out behind the body, on the centerline.
  const backX = totalLength / 2 + _PALLET_LEN_M / 2 + 0.10;
  return { x: backX, z: 0 };
}

function _maxSidePos(boxes) {
  let max = 1;
  for (const b of boxes) {
    const m = /^([LRB])(\d+)$/.exec(b.slot_id || '');
    if (!m || m[1] === 'B') continue;
    const p = parseInt(m[2], 10);
    if (p > max) max = p;
  }
  return max;
}

/**
 * Compute the truck's centre of mass from the live box list.
 * Returns metres in truck-local frame:
 *   - lateral_z:    +right / -left  (rollover axis)
 *   - longitudinal_x: +back / -front (axle balance)
 *   - vertical_y:   above the floor (top-heavy axis)
 *
 * Each box's own pos_y/pos_z within its pallet shifts it from the slot
 * centre. Boxes in the driver's hands (status !== 'in_pallet') are
 * skipped — they're transient and don't load the truck axles.
 */
export function computeCenterOfMass(boxes = []) {
  const maxPos = _maxSidePos(boxes);
  let totalMass = 0;
  let mx = 0;
  let mz = 0;
  let my = 0;
  for (const b of boxes) {
    if (b.status !== 'in_pallet') continue;
    const mass = (b.qty ?? 0) * (b.unit_weight_kg ?? 0);
    if (mass <= 0) continue;
    const slot = _slotWorldXZ(b.slot_id, maxPos);
    if (!slot) continue;
    const side = (b.slot_id || '')[0];
    // Box centre within its pallet (local coords from the bin-packer).
    const localCx = (b.pos_x ?? 0) + (b.dim_x ?? 0) / 2 - _PALLET_LEN_M / 2;
    const localCyPallet = (b.pos_y ?? 0) + (b.dim_y ?? 0) / 2;
    let localZ;
    if (side === 'L') {
      localZ = -_PALLET_WIDTH_M / 2 + localCyPallet;
    } else if (side === 'R') {
      localZ = +_PALLET_WIDTH_M / 2 - localCyPallet;
    } else {
      localZ = localCyPallet - _PALLET_WIDTH_M / 2;
    }
    const worldX = slot.x + localCx;
    const worldZ = slot.z + localZ;
    const worldY = (b.pos_z ?? 0) + (b.dim_h ?? 0) / 2;
    mx += worldX * mass;
    mz += worldZ * mass;
    my += worldY * mass;
    totalMass += mass;
  }
  if (totalMass <= 0) {
    return {
      total_kg: 0,
      lateral_z: 0,
      longitudinal_x: 0,
      vertical_y: 0,
    };
  }
  return {
    total_kg: totalMass,
    lateral_z: mz / totalMass,
    longitudinal_x: mx / totalMass,
    vertical_y: my / totalMass,
  };
}

/**
 * Mass per slot (one entry per pallet position) for the per-slot
 * axle-balance grid. Returns:
 *   {
 *     bySlot:  { L1: 1060, L2: 200, ..., R3: 503, ... },
 *     maxPos:  N (positions per L/R side; T6=3, T8=4),
 *   }
 *
 * Items in the driver's hands (status !== 'in_pallet') are skipped.
 */
export function computeSlotMass(boxes = []) {
  const maxPos = _maxSidePos(boxes);
  const bySlot = {};
  for (const b of boxes) {
    if (b.status !== 'in_pallet') continue;
    const mass = (b.qty ?? 0) * (b.unit_weight_kg ?? 0);
    if (mass <= 0) continue;
    const slotId = b.slot_id || '';
    if (!slotId) continue;
    bySlot[slotId] = (bySlot[slotId] || 0) + mass;
  }
  return { bySlot, maxPos };
}
