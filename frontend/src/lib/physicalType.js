// Physical packaging type taxonomy (mirrors simulator/data/catalog.py).
// Used across the 3D scene, action log and step card to render type badges.

export const PHYSICAL_TYPES = {
  keg:    { code: 'K', label: 'keg',     en: 'keg',     color: '#ffae42', icon: 'K' },
  case:   { code: 'C', label: 'case',    en: 'case',    color: '#42c8ff', icon: 'C' },
  bottle: { code: 'B', label: 'bottle',  en: 'bottle',  color: '#9bff7c', icon: 'B' },
  can:    { code: 'N', label: 'can',     en: 'can',     color: '#ff7c9b', icon: 'N' },
  bulk:   { code: 'P', label: 'pallet',  en: 'pallet',  color: '#c060ff', icon: 'P' },
  weight: { code: 'W', label: 'weight',  en: 'weight',  color: '#ffd633', icon: 'W' },
  unit:   { code: 'U', label: 'unit',    en: 'unit',    color: '#888888', icon: 'U' },
};

export function typeMeta(t) {
  return PHYSICAL_TYPES[t] || PHYSICAL_TYPES.unit;
}

export function typeCode(t) {
  return (PHYSICAL_TYPES[t] || PHYSICAL_TYPES.unit).code;
}

export function typeLabel(t) {
  return (PHYSICAL_TYPES[t] || PHYSICAL_TYPES.unit).label;
}
