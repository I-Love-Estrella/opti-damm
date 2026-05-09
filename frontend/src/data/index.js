export const STOPS = [
  { id: 1, n: 1, code: "S-01", name: "Los Teresitos",         neighborhood: "Montcada i Reixac", nbCode: "MIR", window: "08:30 – 09:30", pallets: 1, priority: false, status: "completed", eta: "08:42", latlng: [41.4836, 2.1886] },
  { id: 2, n: 2, code: "S-02", name: "Viena Granollers",      neighborhood: "Granollers",        nbCode: "GRA", window: "09:00 – 10:30", pallets: 2, priority: false, status: "completed", eta: "09:38", latlng: [41.6083, 2.2875] },
  { id: 3, n: 3, code: "S-03", name: "Frankfurt Leo Boeck",   neighborhood: "Granollers",        nbCode: "GRA", window: "10:30 – 11:00", pallets: 1, priority: false, status: "completed", eta: "10:24", latlng: [41.6107, 2.2910] },
  { id: 4, n: 4, code: "S-04", name: "Cafeteria Pradals",     neighborhood: "Vic",               nbCode: "VIC", window: "11:00 – 12:30", pallets: 1, priority: false, status: "current",   eta: "11:06", latlng: [41.8863, 2.2544] },
  { id: 5, n: 5, code: "S-05", name: "Area Truck Shell",      neighborhood: "Vic",               nbCode: "VIC", window: "12:00 – 13:00", pallets: 1, priority: false, status: "upcoming",  eta: "12:14", latlng: [41.8821, 2.2490] },
  { id: 6, n: 6, code: "S-06", name: "Area Vic BP",           neighborhood: "Vic",               nbCode: "VIC", window: "13:00 – 14:00", pallets: 1, priority: false, status: "upcoming",  eta: "13:08", latlng: [41.8779, 2.2503] },
  { id: 7, n: 7, code: "S-07", name: "Hospital de Manlleu",   neighborhood: "Manlleu",           nbCode: "MAN", window: "14:00 – 15:00", pallets: 1, priority: true,  status: "upcoming",  eta: "14:18", latlng: [41.8741, 2.2847] },
];

export const WAREHOUSE = { name: "DDI Mollet", code: "DEPOT", latlng: [41.5400, 2.2107] };

export const ZONES = [
  { id: "mollet-cb",   label: "MOLLET CAN BORRELL",   code: "DD13100001" },
  { id: "mollet-pl",   label: "MOLLET PLANA LLADÓ",   code: "DD13100002" },
  { id: "mollet-bo",   label: "MOLLET BARRI OLIVA",   code: "DD13100003" },
  { id: "granollers",  label: "GRANOLLERS",            code: "DD13100008" },
  { id: "vic",         label: "VIC",                   code: "DD13100045" },
  { id: "manlleu",     label: "MANLLEU",               code: "DD13100058" },
  { id: "montcada",    label: "MONTCADA I REIXAC",     code: "DD13100043" },
];

export const PALLETS_T8 = {
  reference: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 2, code: "P-03", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 3, code: "P-04", sku: "AGV", stop: 6, ret: false, client: "Area Vic BP",      wt: 240 },
    { idx: 4, code: "P-05", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
    { idx: 5, code: "P-06", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
    { idx: 6, code: "P-07", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
    { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,             wt: 0 },
  ],
  client: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 2, code: "P-03", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
    { idx: 3, code: "P-04", sku: "AGV", stop: 6, ret: false, client: "Area Vic BP",      wt: 240 },
    { idx: 4, code: "P-05", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
    { idx: 5, code: "P-06", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 6, code: "P-07", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
    { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,             wt: 0 },
  ],
  hybrid: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 2, code: "P-03", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 3, code: "P-04", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
    { idx: 4, code: "P-05", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
    { idx: 5, code: "P-06", sku: "AGV", stop: 6, ret: false, client: "Area Vic BP",      wt: 240 },
    { idx: 6, code: "P-07", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
    { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,             wt: 0 },
  ],
};

export const PALLETS_T6 = {
  reference: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 2, code: "P-03", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 3, code: "P-04", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
    { idx: 4, code: "P-05", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
    { idx: 5, code: "P-06", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
  ],
  client: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 2, code: "P-03", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
    { idx: 3, code: "P-04", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
    { idx: 4, code: "P-05", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 5, code: "P-06", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
  ],
  hybrid: [
    { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cafeteria Pradals", wt: 320 },
    { idx: 1, code: "P-02", sku: "AGV", stop: 5, ret: true,  client: "Area Truck Shell", wt: 240 },
    { idx: 2, code: "P-03", sku: "EST", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 320 },
    { idx: 3, code: "P-04", sku: "MAL", stop: 5, ret: true,  client: "Area Truck Shell", wt: 305 },
    { idx: 4, code: "P-05", sku: "VOL", stop: 7, ret: true,  client: "Hospital de Manlleu", wt: 410 },
    { idx: 5, code: "P-06", sku: "VOL", stop: 6, ret: false, client: "Area Vic BP",      wt: 285 },
  ],
};

export const STOP_TONE = {
  4: "1",
  5: "2",
  6: "4",
  7: "5",
};

export const SKU_TONE = {
  EST: "1",
  VOL: "3",
  MAL: "4",
  MOR: "5",
  AGV: "2",
};

export const CLIENT_ORDERS = {
  "Cafeteria Pradals":    [["EST 33CL × 24", "1 pallet"]],
  "Area Truck Shell":     [["AGV 1.5L × 12", "1 pallet"], ["MAL 75CL × 6", "1 pallet"]],
  "Area Vic BP":          [["AGV 1.5L × 12", "1 pallet"], ["VOL 33CL × 24", "1 pallet"]],
  "Hospital de Manlleu":  [["EST 33CL × 24", "1 pallet"], ["VOL 33CL × 24", "1 pallet"]],
  "Los Teresitos":        [["EST 33CL × 24", "1 pallet"]],
  "Viena Granollers":     [["EST 33CL × 24", "1 pallet"], ["AGV 1.5L × 12", "1 pallet"]],
  "Frankfurt Leo Boeck":  [["VOL 33CL × 24", "1 pallet"]],
};

export const TRUCK_TYPES = {
  T6: { code: "T6", name: "6-Pallet Truck", capacity: 6, cols: 2, maxKg: 6000, fleet: 11 },
  T8: { code: "T8", name: "8-Pallet Truck", capacity: 8, cols: 2, maxKg: 8000, fleet: 4 },
};
