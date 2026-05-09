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

function boxKey(slotId, colX, colY, level, sku, intendedClient) {
  return `${slotId}|${colX}|${colY}|${level}|${sku}|${intendedClient ?? ''}`;
}

function pickEventLevel(detail) {
  if (detail?.level !== undefined && detail?.level !== null) return Number(detail.level);
  if (detail?.bottom_level !== undefined && detail?.bottom_level !== null) return Number(detail.bottom_level);
  return null;
}

function findBox(boxes, slotId, colX, colY, level, sku, intendedClient, status) {
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
      for (let i = 0; i < stackSize; i++) {
        boxes.push({
          id: `b${id++}`,
          slot_id: slotEntry.slot_id,
          side: slotEntry.side,
          pallet_class: slotEntry.pallet.pallet_class,
          layout: slotEntry.pallet.layout,
          col_x: it.col_x,
          col_y: it.col_y,
          level: it.bottom_level + i,
          sku: it.sku,
          // qty per physical box ≈ qty / stackSize (informational only)
          qty: stackSize > 0 ? it.qty / stackSize : it.qty,
          intended_client: it.intended_client,
          is_returnable_empty: it.is_returnable_empty,
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
      const box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client, 'in_pallet');
      if (box) {
        box.status = 'in_hands';
        box.history.push({ step_idx: i, kind: 'lifted', t_min: stg.t_min, time_min: stg.time_min });
      }
    } else if (stg.kind === 'BLOCKER_REPLACE') {
      const box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, d.intended_client, 'in_hands');
      if (box) {
        box.status = 'in_pallet';
        box.history.push({ step_idx: i, kind: 'replaced', t_min: stg.t_min, time_min: stg.time_min });
      }
    } else if (stg.kind === 'TARGET_TAKE') {
      // Try in_pallet first (regular target), fall back to in_hands (opportunistic same-client delivery)
      let box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, undefined, 'in_pallet');
      if (!box) {
        box = findBox(boxes, d.slot_id, d.col_x, d.col_y, lvl, d.sku, undefined, 'in_hands');
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
    } else if (stg.kind === 'PICKUP_RETURN') {
      const slotMeta = palletsBySlot[d.slot_id];
      if (slotMeta) {
        const qty = Math.max(1, Math.ceil(d.qty || 1));
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
            sku: d.sku || 'EMPTY',
            qty: 1,
            intended_client: null,
            is_returnable_empty: true,
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
