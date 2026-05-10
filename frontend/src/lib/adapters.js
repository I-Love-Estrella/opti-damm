// Transforms the simulator /api/run response into the shapes the existing
// console components (MapPanel, TruckPanel, MetricsBar) already accept.

const SKU_NAMES = {
  EST: 'Estrella 33cl',
  VOL: 'Voll-Damm 33cl',
  MAL: 'Malquerida 75cl',
  MOR: 'Moritz 33cl',
  AGV: 'Agua 1.5L',
};

function fmtClock(arriveMin) {
  if (arriveMin == null || Number.isNaN(arriveMin)) return '—';
  // Treat arrive_t_min as minutes-from-midnight; clamp to 24h.
  const total = Math.max(0, Math.round(arriveMin));
  const hh = String(Math.floor(total / 60) % 24).padStart(2, '0');
  const mm = String(total % 60).padStart(2, '0');
  return `${hh}:${mm}`;
}

function shortSku(name) {
  if (!name) return null;
  const upper = String(name).toUpperCase();
  for (const code of Object.keys(SKU_NAMES)) {
    if (upper.startsWith(code)) return code;
  }
  return upper.slice(0, 3);
}

export function adaptDepot(apiResponse) {
  const d = apiResponse?.depot;
  if (!d) return null;
  return {
    name: d.name,
    code: 'DEPOT',
    latlng: [d.lat, d.lon],
  };
}

export function adaptStops(apiResponse) {
  const stops = apiResponse?.stops || [];
  const palletsByClient = new Map();
  for (const slot of apiResponse?.initial_cargo || []) {
    const p = slot?.pallet;
    if (!p?.primary_client) continue;
    palletsByClient.set(p.primary_client, (palletsByClient.get(p.primary_client) || 0) + 1);
  }

  return stops.map((s) => ({
    id: s.visit_seq,
    n: s.visit_seq,
    visit_seq: s.visit_seq,
    client_id: s.client_id,
    code: `S-${String(s.visit_seq).padStart(2, '0')}`,
    name: s.name,
    neighborhood: s.city || '',
    nbCode: (s.cp || '').slice(0, 3) || '—',
    window: '—',
    pallets: palletsByClient.get(s.client_id) || 0,
    priority: false,
    status: 'planned',
    eta: fmtClock(s.arrive_t_min),
    latlng: [s.lat, s.lon],
  }));
}

export function adaptPallets(apiResponse) {
  const cargo = apiResponse?.initial_cargo || [];
  const stops = apiResponse?.stops || [];
  const seqByClient = new Map(stops.map((s) => [s.client_id, s.visit_seq]));
  const nameByClient = new Map(stops.map((s) => [s.client_id, s.name]));

  return cargo.map((slot, idx) => {
    const p = slot?.pallet;
    if (!p) {
      return {
        idx,
        code: slot?.slot_id || `P-${idx + 1}`,
        sku: null,
        stop: null,
        ret: false,
        client: null,
        wt: 0,
        items: [],
      };
    }
    const items = p.items || [];
    const primaryItem = items.find((it) => it.intended_client === p.primary_client) || items[0];

    const skuTotals = new Map();
    for (const it of items) {
      const code = shortSku(it.sku);
      if (!code) continue;
      skuTotals.set(code, (skuTotals.get(code) || 0) + it.qty);
    }
    const breakdown = Array.from(skuTotals.entries())
      .map(([sku, qty]) => ({ sku, qty: Math.round(qty) }))
      .sort((a, b) => b.qty - a.qty);

    return {
      idx,
      code: slot.slot_id,
      sku: shortSku(primaryItem?.sku),
      stop: seqByClient.get(p.primary_client) ?? null,
      ret: items.some((it) => it.is_returnable_empty),
      client: nameByClient.get(p.primary_client) || p.primary_client,
      wt: Math.round(p.weight_kg || 0),
      items: breakdown,
    };
  });
}

export function adaptMetrics(apiResponse) {
  const k = apiResponse?.kpis || {};
  return {
    total_km: k.total_km ?? 0,
    total_minutes: k.total_minutes ?? 0,
    total_cost_eur: k.total_cost_eur ?? 0,
    co2_kg: k.co2_kg ?? 0,
    search_moves: k.search_moves ?? 0,
    fill_rate: k.fill_rate ?? 0,
    all: k,
  };
}

export function adaptTruck(apiResponse) {
  const t = apiResponse?.truck;
  if (!t) return null;
  const cap = t.pallet_capacity || 0;
  return {
    code: t.code,
    capacity: cap,
    cols: cap >= 8 ? 2 : 2,
    maxKg: t.max_weight_kg,
    sides: t.sides || [],
  };
}
