export const STOPS = [
  { id: 1, n: 1, code: "S-01", name: "Bar La Plata",        neighborhood: "Born",       nbCode: "BOR", window: "08:30 – 09:30", pallets: 1, priority: false, status: "completed", eta: "08:42", latlng: [41.3819, 2.1834] },
  { id: 2, n: 2, code: "S-02", name: "Bodega Joan",         neighborhood: "Gràcia",     nbCode: "GRA", window: "09:00 – 10:30", pallets: 2, priority: false, status: "completed", eta: "09:38", latlng: [41.4015, 2.1571] },
  { id: 3, n: 3, code: "S-03", name: "Cervecería Catalana", neighborhood: "Eixample",   nbCode: "EIX", window: "10:00 – 11:00", pallets: 1, priority: false, status: "completed", eta: "10:24", latlng: [41.3936, 2.1612] },
  { id: 4, n: 4, code: "S-04", name: "Cal Pep",             neighborhood: "Born",       nbCode: "BOR", window: "11:00 – 12:30", pallets: 1, priority: false, status: "current",   eta: "11:06", latlng: [41.3838, 2.1836] },
  { id: 5, n: 5, code: "S-05", name: "Quimet & Quimet",     neighborhood: "Poble Sec",  nbCode: "PSE", window: "12:00 – 13:00", pallets: 1, priority: false, status: "upcoming",  eta: "12:14", latlng: [41.3750, 2.1654] },
  { id: 6, n: 6, code: "S-06", name: "Tickets Bar",         neighborhood: "Sant Antoni",nbCode: "STA", window: "13:00 – 14:00", pallets: 1, priority: false, status: "upcoming",  eta: "13:08", latlng: [41.3761, 2.1583] },
  { id: 7, n: 7, code: "S-07", name: "El Xampanyet",        neighborhood: "Born",       nbCode: "BOR", window: "14:00 – 15:00", pallets: 1, priority: true,  status: "upcoming",  eta: "14:18", latlng: [41.3848, 2.1820] },
];

export const WAREHOUSE = { name: "Mollet Depot", code: "DEPOT", latlng: [41.5424, 2.2126] };

export const NEIGHBORHOODS = [
  { id: "gracia",   label: "GRÀCIA",     cx: 50, cy: 36, points: "40,28 56,26 62,32 60,42 50,46 42,44 38,36" },
  { id: "eixample", label: "EIXAMPLE",   cx: 46, cy: 58, points: "32,48 60,46 64,54 62,66 50,68 36,66 28,58" },
  { id: "born",     label: "BORN",       cx: 64, cy: 70, points: "58,62 72,62 74,72 70,78 60,76 56,68", alt: true },
  { id: "raval",    label: "RAVAL",      cx: 50, cy: 72, points: "42,66 56,68 56,76 50,82 42,80 38,72", alt: true },
  { id: "poble",    label: "POBLE SEC",  cx: 32, cy: 78, points: "22,72 40,72 42,80 36,86 26,86 20,80", alt: true },
  { id: "stan",     label: "SANT ANTONI",cx: 36, cy: 64, points: "28,58 42,60 42,68 32,70 26,66" },
  { id: "barceloneta", label: "BARCELONETA", cx: 78, cy: 80, points: "72,76 84,74 86,82 82,86 74,84", alt: true },
];

export const PALLETS_BY_REFERENCE = [
  { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cal Pep",         wt: 320 },
  { idx: 1, code: "P-02", sku: "EST", stop: 7, ret: true,  client: "El Xampanyet",    wt: 320 },
  { idx: 2, code: "P-03", sku: "VOL", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 285 },
  { idx: 3, code: "P-04", sku: "VOL", stop: 6, ret: false, client: "Tickets Bar",     wt: 285 },
  { idx: 4, code: "P-05", sku: "MAL", stop: 7, ret: true,  client: "El Xampanyet",    wt: 410 },
  { idx: 5, code: "P-06", sku: "MOR", stop: 6, ret: false, client: "Tickets Bar",     wt: 305 },
  { idx: 6, code: "P-07", sku: "AGV", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 240 },
  { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,           wt: 0 },
];

export const PALLETS_BY_CLIENT = [
  { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cal Pep",         wt: 320 },
  { idx: 1, code: "P-02", sku: "VOL", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 285 },
  { idx: 2, code: "P-03", sku: "AGV", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 240 },
  { idx: 3, code: "P-04", sku: "VOL", stop: 6, ret: false, client: "Tickets Bar",     wt: 285 },
  { idx: 4, code: "P-05", sku: "MOR", stop: 6, ret: false, client: "Tickets Bar",     wt: 305 },
  { idx: 5, code: "P-06", sku: "EST", stop: 7, ret: true,  client: "El Xampanyet",    wt: 320 },
  { idx: 6, code: "P-07", sku: "MAL", stop: 7, ret: true,  client: "El Xampanyet",    wt: 410 },
  { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,           wt: 0 },
];

export const PALLETS_HYBRID = [
  { idx: 0, code: "P-01", sku: "EST", stop: 4, ret: true,  client: "Cal Pep",         wt: 320 },
  { idx: 1, code: "P-02", sku: "VOL", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 285 },
  { idx: 2, code: "P-03", sku: "EST", stop: 7, ret: true,  client: "El Xampanyet",    wt: 320 },
  { idx: 3, code: "P-04", sku: "AGV", stop: 5, ret: true,  client: "Quimet & Quimet", wt: 240 },
  { idx: 4, code: "P-05", sku: "MAL", stop: 7, ret: true,  client: "El Xampanyet",    wt: 410 },
  { idx: 5, code: "P-06", sku: "VOL", stop: 6, ret: false, client: "Tickets Bar",     wt: 285 },
  { idx: 6, code: "P-07", sku: "MOR", stop: 6, ret: false, client: "Tickets Bar",     wt: 305 },
  { idx: 7, code: "P-08", sku: null,  stop: null, ret: false, client: null,           wt: 0 },
];

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
  "Cal Pep":           [["EST 33CL × 24", "1 pallet"]],
  "Quimet & Quimet":   [["VOL 33CL × 24", "1 pallet"], ["AGV 1.5L × 12", "1 pallet"]],
  "Tickets Bar":       [["VOL 33CL × 24", "1 pallet"], ["MOR 33CL × 24", "1 pallet"]],
  "El Xampanyet":      [["EST 33CL × 24", "1 pallet"], ["MAL 75CL × 6", "1 pallet"]],
  "Bar La Plata":      [["EST 33CL × 24", "1 pallet"]],
  "Bodega Joan":       [["EST 33CL × 24", "1 pallet"], ["VOL 33CL × 24", "1 pallet"]],
  "Cervecería Catalana": [["EST 33CL × 24", "1 pallet"]],
};
