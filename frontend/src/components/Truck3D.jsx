'use client';

import { Canvas } from '@react-three/fiber';
import { OrbitControls, Edges, Html } from '@react-three/drei';
import { Component, useMemo, useState } from 'react';
import * as THREE from 'three';
import { PHYSICAL_TYPES, typeMeta } from '@/lib/physicalType';

const PALLET_LEN = 1.2;
const PALLET_WIDTH = 0.8;
const PALLET_HEIGHT = 1.8;
const PALLET_THICKNESS = 0.12;
const SLOT_GAP_X = 0.06;
const TRUCK_WALL_PAD = 0.10;
const TRUCK_FLOOR_THICKNESS = 0.05;

// Per-type physical height in metres. X/Z stay cell-based (no overlap with
// neighbouring columns), but height makes the type visible at a glance:
// kegs are tall, cans are short, bulk is double-tall.
const TYPE_HEIGHT_M = {
  keg:    0.65,   // 30L / 50L beer keg — tallest of the regular cargo
  case:   0.30,   // beer case 24×33CL
  bottle: 0.42,   // standalone large bottle
  can:    0.16,   // single can — very short
  bulk:   1.50,   // whole pallet item — almost truck-tall
  weight: 0.30,   // weight bag/box
  unit:   0.24,   // generic unit
};

function typeHeight(t) {
  return TYPE_HEIGHT_M[t] ?? TYPE_HEIGHT_M.unit;
}

function clientColor(id) {
  if (!id) return '#6e6e6e';
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const hue = (h * 137.508) % 360;
  return `hsl(${hue.toFixed(1)}, 65%, 60%)`;
}

function slotIndex(slotId) {
  const m = /^([LRB])(\d+)$/.exec(slotId);
  if (!m) return { side: 'L', pos: 1 };
  return { side: m[1], pos: parseInt(m[2], 10) };
}

function slotWorldCenter(slotId, totalSidePos, backOnly = false) {
  const { side, pos } = slotIndex(slotId);
  // Back-loaded van (V3): every slot is a B-slot in a single in-truck
  // column, laid out along the X axis like normal lateral pallets.
  if (backOnly) {
    const totalLength = totalSidePos * (PALLET_LEN + SLOT_GAP_X);
    const startX = -totalLength / 2 + (PALLET_LEN + SLOT_GAP_X) / 2;
    const x = startX + (pos - 1) * (PALLET_LEN + SLOT_GAP_X);
    return { x, y: 0, z: 0 };
  }
  const totalLength = totalSidePos * (PALLET_LEN + SLOT_GAP_X);
  const startX = -totalLength / 2 + (PALLET_LEN + SLOT_GAP_X) / 2;
  const x = startX + (pos - 1) * (PALLET_LEN + SLOT_GAP_X);
  const halfWidth = PALLET_WIDTH / 2;
  if (side === 'L') {
    return { x, y: 0, z: -(halfWidth + 0.05) };
  }
  if (side === 'R') {
    return { x, y: 0, z: +(halfWidth + 0.05) };
  }
  const backX = totalSidePos * (PALLET_LEN + SLOT_GAP_X) / 2 + PALLET_LEN / 2 + 0.1;
  return { x: backX, y: 0, z: 0 };
}

function cellWorld(palletCenter, side, layout, colX, colY, level) {
  const cellLen = PALLET_LEN / layout.cols_x;
  const cellWidth = PALLET_WIDTH / layout.cols_y;
  const cellH = PALLET_HEIGHT / layout.max_level;
  const palletFloorY = PALLET_THICKNESS;
  const localX = (colX + 0.5) * cellLen - PALLET_LEN / 2;
  let localZ = 0;
  if (side === 'L') {
    localZ = -PALLET_WIDTH / 2 + (colY + 0.5) * cellWidth;
  } else if (side === 'R') {
    localZ = PALLET_WIDTH / 2 - (colY + 0.5) * cellWidth;
  } else {
    localZ = (colY + 0.5) * cellWidth - PALLET_WIDTH / 2;
  }
  const localY = palletFloorY + (level + 0.5) * cellH;
  return {
    x: palletCenter.x + localX,
    y: palletCenter.y + localY,
    z: palletCenter.z + localZ,
    cellLen,
    cellWidth,
    cellH,
  };
}

// Letter textures — one per type code, cached so we never rebuild canvases.
// Texture is a square with a transparent background and a single black glyph;
// the body colour comes from the material's `color` so the same texture works
// for every cube of the same type.
const _letterTextureCache = new Map();
function getLetterTexture(code) {
  if (_letterTextureCache.has(code)) return _letterTextureCache.get(code);
  const SIZE = 256;
  const canvas = (typeof document !== 'undefined') ? document.createElement('canvas') : null;
  if (!canvas) return null;
  canvas.width = SIZE;
  canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  // White background — multiplies with material.color, so the body keeps its
  // colour everywhere except inside the glyph.
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, SIZE, SIZE);
  ctx.fillStyle = '#000000';
  ctx.font = 'bold 200px ui-monospace, "SFMono-Regular", Menlo, monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(code, SIZE / 2, SIZE / 2 + 8);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  _letterTextureCache.set(code, tex);
  return tex;
}

function CargoBox({
  position,
  size,
  color,
  opacity = 1,
  emissive,
  isCurrentEvent,
  isHovered,
  isSelected,
  typeCode,
  typeColor,
  showLabel,
  onPointerOver,
  onPointerOut,
  onPointerDown,
}) {
  // Edge / emissive priority: selected > hovered > current event > default.
  let edgeColor = '#000000';
  let edgeWidth = 1.5;
  let activeEmissive = emissive || null;
  let emissiveIntensity = emissive ? 0.45 : 0;
  if (isSelected) {
    edgeColor = '#ffffff';
    edgeWidth = 3.5;
    activeEmissive = '#ffffff';
    emissiveIntensity = 0.55;
  } else if (isHovered) {
    edgeColor = '#fc0';
    edgeWidth = 2.5;
    activeEmissive = '#665500';
    emissiveIntensity = 0.4;
  } else if (isCurrentEvent) {
    edgeColor = '#ffaa00';
    edgeWidth = 2.0;
    activeEmissive = '#ffaa00';
    emissiveIntensity = 0.5;
  }

  // Letter is baked into the face texture so it appears on the cube surface
  // itself. Faces 0/+X, 1/-X, 4/+Z, 5/-Z and 2/+Y get the letter; 3/-Y (bottom)
  // stays clean.
  const letterTex = showLabel && typeCode ? getLetterTexture(typeCode) : null;
  const matBase = {
    color,
    transparent: opacity < 1,
    opacity,
    emissive: activeEmissive || '#000000',
    emissiveIntensity,
    roughness: 0.6,
    metalness: 0.05,
  };

  const meshKey = `${typeCode || 'x'}-${letterTex ? 'on' : 'off'}`;

  return (
    <group position={[position.x, position.y, position.z]}>
      <mesh
        key={meshKey}
        onPointerOver={onPointerOver}
        onPointerOut={onPointerOut}
        onPointerDown={onPointerDown}
      >
        <boxGeometry args={[size.len, size.h, size.width]} />
        {/* +X */}
        <meshStandardMaterial attach="material-0" {...matBase} map={letterTex || null} />
        {/* -X */}
        <meshStandardMaterial attach="material-1" {...matBase} map={letterTex || null} />
        {/* +Y (top) */}
        <meshStandardMaterial attach="material-2" {...matBase} map={letterTex || null} />
        {/* -Y (bottom) — no letter */}
        <meshStandardMaterial attach="material-3" {...matBase} />
        {/* +Z */}
        <meshStandardMaterial attach="material-4" {...matBase} map={letterTex || null} />
        {/* -Z */}
        <meshStandardMaterial attach="material-5" {...matBase} map={letterTex || null} />
        <Edges color={edgeColor} threshold={1} lineWidth={edgeWidth} />
      </mesh>
    </group>
  );
}

function PalletPlatform({ center, side, layout, slotId }) {
  return (
    <group position={[center.x, center.y, center.z]}>
      <mesh position={[0, PALLET_THICKNESS / 2, 0]}>
        <boxGeometry args={[PALLET_LEN, PALLET_THICKNESS, PALLET_WIDTH]} />
        <meshStandardMaterial color="#5a4a36" roughness={0.95} />
        <Edges color="#1a1a1a" />
      </mesh>
      {(side === 'L' || side === 'R') && (
        <mesh
          position={[
            0,
            PALLET_THICKNESS + 0.001,
            side === 'L' ? -PALLET_WIDTH / 2 + 0.01 : PALLET_WIDTH / 2 - 0.01,
          ]}
        >
          <boxGeometry args={[PALLET_LEN, 0.005, 0.02]} />
          <meshBasicMaterial color="#fc0" />
        </mesh>
      )}
      <Html position={[-PALLET_LEN / 2 - 0.06, PALLET_THICKNESS + 0.01, 0]} center distanceFactor={5} style={{ pointerEvents: 'none' }}>
        <div className="r3d-slot-label">{slotId}</div>
      </Html>
    </group>
  );
}

function TruckShell({ totalSidePos, backOnly = false }) {
  const truckLength = totalSidePos * (PALLET_LEN + SLOT_GAP_X) + 2 * TRUCK_WALL_PAD;
  // Back-loaded van (V3) has a single pallet column — narrow body.
  // Standard side-curtain trucks (T6/T8) have two columns plus the
  // 0.05 m gap on each side.
  const truckWidth = backOnly
    ? PALLET_WIDTH + 2 * TRUCK_WALL_PAD
    : 2 * (PALLET_WIDTH + 0.05) + 2 * TRUCK_WALL_PAD;
  const truckHeight = PALLET_HEIGHT + 0.2;

  return (
    <group>
      <mesh position={[0, -TRUCK_FLOOR_THICKNESS / 2, 0]}>
        <boxGeometry args={[truckLength, TRUCK_FLOOR_THICKNESS, truckWidth]} />
        <meshStandardMaterial color="#1a1a1a" roughness={0.85} />
      </mesh>
      <mesh position={[0, truckHeight / 2, 0]}>
        <boxGeometry args={[truckLength, truckHeight, truckWidth]} />
        <meshBasicMaterial color="#fc0" wireframe transparent opacity={0.3} />
      </mesh>
    </group>
  );
}

function BoxDetailsCard({ box, pinned, onClose }) {
  const tmeta = typeMeta(box.physical_type);
  const last = box.history?.[box.history.length - 1];
  const fields = [
    ['SKU', box.sku],
    ['Type', `${tmeta.code} · ${tmeta.label}`],
    ['Quantity (per box)', Number(box.qty).toFixed(2)],
    ['Intended client', box.intended_client || '—'],
    ['Slot', box.slot_id],
    ['Column', `(${box.col_x}, ${box.col_y})`],
    ['Level', box.level],
    ['Status', box.status],
    ['Returnable empty', box.is_returnable_empty ? 'yes' : 'no'],
    ['Stack member', `${(box.stack_member_idx ?? 0) + 1} / ${box.stack_member_total ?? 1}`],
  ];

  return (
    <div className={`truck3d-details${pinned ? ' truck3d-details-pinned' : ''}`}>
      <div className="td-head">
        <span className="td-tag" style={{ background: tmeta.color }}>{tmeta.code}</span>
        <span className="td-title">{box.sku}</span>
        <span className="td-pin">{pinned ? 'pinned' : 'hover'}</span>
        {pinned && (
          <button className="td-close" onClick={onClose} type="button" title="Close">
            ✕
          </button>
        )}
      </div>
      <div className="td-body">
        {fields.map(([k, v]) => (
          <div className="td-row" key={k}>
            <span className="td-key">{k}</span>
            <span className="td-val">{String(v)}</span>
          </div>
        ))}
        {last && (
          <div className="td-row">
            <span className="td-key">Last event</span>
            <span className="td-val">
              {last.kind} @ step {last.step_idx + 1} (+{(last.time_min || 0).toFixed(2)}m)
            </span>
          </div>
        )}
      </div>
    </div>
  );
}


class CanvasErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, message: '' };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, message: error?.message || String(error) };
  }
  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('Truck3D render error:', error, info);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="truck3d-error">
          <div>3D scene failed to render.</div>
          <code>{this.state.message}</code>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function Truck3D({
  truck,
  palletsBySlot = {},
  boxes = [],
  highlightSeq,
  height = 460,
}) {
  const [showTypeLabels, setShowTypeLabels] = useState(true);
  const [hoveredId, setHoveredId] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const activeId = selectedId || hoveredId;
  const activeBox = useMemo(
    () => boxes.find((b) => b.id === activeId) || null,
    [boxes, activeId],
  );

  // Back-loaded van: every available slot is a B-slot. The truck spec
  // declares this via `sides=("B",)` (V3 in the Damm fleet). We detect
  // it from the actual slot ids so the renderer stays decoupled from
  // the spec format.
  const backOnly = useMemo(() => {
    const ids = Object.keys(palletsBySlot);
    if (ids.length === 0) return (truck?.sides?.length === 1 && truck.sides[0] === 'B');
    return ids.every((sid) => slotIndex(sid).side === 'B');
  }, [palletsBySlot, truck]);

  const totalSidePos = useMemo(() => {
    let max = 0;
    for (const slotId of Object.keys(palletsBySlot)) {
      const { side, pos } = slotIndex(slotId);
      // Back-only trucks lay every pallet along the X axis; otherwise
      // only L/R columns set the truck length and B sits behind.
      if (backOnly || side !== 'B') {
        if (pos > max) max = pos;
      }
    }
    return Math.max(max, 1);
  }, [palletsBySlot, backOnly]);

  const slotCenters = useMemo(() => {
    const out = {};
    for (const slotId of Object.keys(palletsBySlot)) {
      out[slotId] = slotWorldCenter(slotId, totalSidePos, backOnly);
    }
    return out;
  }, [palletsBySlot, totalSidePos, backOnly]);

  // In-hands cargo: group identical items so the row above the truck doesn't
  // turn into a jittered cloud. Key = type + full/empty + client + sku, so
  // distinct items stay visually separable while duplicates collapse to one
  // representative cube with an "× N" badge.
  const inHandsGroups = useMemo(() => {
    const groups = new Map();
    for (const b of boxes) {
      if (b.status === 'delivered' || b.status === 'in_pallet') continue;
      const key = [
        b.physical_type || 'unit',
        b.is_returnable_empty ? 'E' : 'F',
        b.intended_client || '',
        b.sku || '',
      ].join('|');
      let g = groups.get(key);
      if (!g) {
        g = {
          key,
          type: b.physical_type,
          isEmpty: !!b.is_returnable_empty,
          client: b.intended_client,
          sku: b.sku,
          rep: b,
          items: [],
        };
        groups.set(key, g);
      }
      g.items.push(b);
    }
    return Array.from(groups.values()).sort((a, b) =>
      (a.type || '').localeCompare(b.type || '') || (a.sku || '').localeCompare(b.sku || ''),
    );
  }, [boxes]);

  const inHandsLayout = useMemo(() => {
    const truckLength =
      totalSidePos * (PALLET_LEN + SLOT_GAP_X) + 2 * TRUCK_WALL_PAD;
    const spacing = 0.65;
    const totalWidth = Math.max((inHandsGroups.length - 1) * spacing, 0);
    const startX = -totalWidth / 2;
    const rowY = PALLET_HEIGHT + 0.85;
    const rowZ = 0;
    return inHandsGroups.map((_, i) => ({
      x: startX + i * spacing,
      y: rowY,
      z: rowZ,
      truckLength,
    }));
  }, [inHandsGroups, totalSidePos]);



  return (
    <div className="truck3d-wrap" style={{ height }}>
      <CanvasErrorBoundary>
        <Canvas
          shadows={false}
          camera={{ position: [3.5, 3.0, 4.5], fov: 45 }}
          gl={{ antialias: true, alpha: false }}
          onCreated={({ gl }) => {
            gl.setClearColor('#0a0a0a', 1);
          }}
        >
          <ambientLight intensity={0.55} />
          <directionalLight position={[6, 8, 4]} intensity={1.0} />
          <directionalLight position={[-6, 6, -4]} intensity={0.4} color="#88aaff" />

          <TruckShell totalSidePos={totalSidePos} backOnly={backOnly} />

          {Object.entries(palletsBySlot).map(([slotId, meta]) => (
            <PalletPlatform
              key={slotId}
              center={slotCenters[slotId]}
              side={meta.side}
              layout={meta.layout}
              slotId={slotId}
            />
          ))}

          {boxes.map((b) => {
            const center = slotCenters[b.slot_id];
            if (!center || !b.layout) return null;
            if (b.status !== 'in_pallet') return null;

            // Continuous physical placement straight from the bin-packer.
            // Convert pallet-local (px, py, pz) into world-space, taking the
            // pallet's position and L/R orientation (col_y=0 is the door edge).
            const cubeSize = {
              len: (b.dim_x ?? 0.20) * 0.99,
              width: (b.dim_y ?? 0.20) * 0.99,
              h: (b.dim_h ?? 0.24) * 0.99,
            };
            const px = b.pos_x ?? 0;
            const py = b.pos_y ?? 0;
            const pz = b.pos_z ?? 0;
            const worldX = center.x + (px + (b.dim_x ?? 0) / 2 - PALLET_LEN / 2);
            const worldZ_local = py + (b.dim_y ?? 0) / 2 - PALLET_WIDTH / 2;
            const worldZ = center.z + (b.side === 'R' ? -worldZ_local : worldZ_local);
            const worldY = center.y + PALLET_THICKNESS + pz + (b.dim_h ?? 0) / 2;

            const baseColor = b.is_returnable_empty ? '#aa66ff' : clientColor(b.intended_client);
            const baseOpacity = b.is_returnable_empty ? 0.45 : 1;
            const isCurrentEvent =
              highlightSeq !== undefined &&
              b.history?.length > 0 &&
              b.history[b.history.length - 1].step_idx === highlightSeq;

            const tmeta = typeMeta(b.physical_type);
            return (
              <CargoBox
                key={b.id}
                position={{ x: worldX, y: worldY, z: worldZ }}
                size={cubeSize}
                color={baseColor}
                opacity={baseOpacity}
                emissive={isCurrentEvent ? '#ffaa00' : null}
                isCurrentEvent={isCurrentEvent}
                isHovered={hoveredId === b.id}
                isSelected={selectedId === b.id}
                typeCode={tmeta.code}
                typeColor={tmeta.color}
                showLabel={showTypeLabels}
                onPointerOver={(e) => {
                  e.stopPropagation();
                  setHoveredId(b.id);
                }}
                onPointerOut={(e) => {
                  e.stopPropagation();
                  setHoveredId((prev) => (prev === b.id ? null : prev));
                }}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  setSelectedId((prev) => (prev === b.id ? null : b.id));
                }}
              />
            );
          })}

          {inHandsGroups.map((group, gi) => {
            const pos = inHandsLayout[gi];
            const rep = group.rep;
            const cubeSize = {
              len: (rep.dim_x ?? 0.20) * 0.99,
              width: (rep.dim_y ?? 0.20) * 0.99,
              h: (rep.dim_h ?? 0.24) * 0.99,
            };
            const color = group.isEmpty
              ? '#aa66ff'
              : clientColor(group.client);
            const groupOpacity = group.isEmpty ? 0.5 : 0.92;
            const tmeta = typeMeta(group.type);
            const isCurrentEvent =
              highlightSeq !== undefined &&
              group.items.some(
                (b) =>
                  b.history?.length > 0 &&
                  b.history[b.history.length - 1].step_idx === highlightSeq,
              );
            const isHovered = group.items.some((b) => b.id === hoveredId);
            const isSelected = group.items.some((b) => b.id === selectedId);
            const repId = rep.id;

            return (
              <group key={group.key} position={[pos.x, pos.y, pos.z]}>
                <CargoBox
                  position={{ x: 0, y: 0, z: 0 }}
                  size={cubeSize}
                  color={color}
                  opacity={groupOpacity}
                  emissive={isCurrentEvent ? '#ffaa00' : '#552200'}
                  isCurrentEvent={isCurrentEvent}
                  isHovered={isHovered}
                  isSelected={isSelected}
                  typeCode={tmeta.code}
                  typeColor={tmeta.color}
                  showLabel={showTypeLabels}
                  onPointerOver={(e) => {
                    e.stopPropagation();
                    setHoveredId(repId);
                  }}
                  onPointerOut={(e) => {
                    e.stopPropagation();
                    setHoveredId((prev) => (prev === repId ? null : prev));
                  }}
                  onPointerDown={(e) => {
                    e.stopPropagation();
                    setSelectedId((prev) => (prev === repId ? null : repId));
                  }}
                />
                <Html
                  position={[cubeSize.len / 2 + 0.08, cubeSize.h / 2 + 0.08, 0]}
                  center
                  distanceFactor={5}
                  style={{ pointerEvents: 'none' }}
                >
                  <div className="r3d-count-badge">
                    ×{group.items.length}
                  </div>
                </Html>
              </group>
            );
          })}

          <gridHelper args={[20, 40, '#222', '#111']} position={[0, -TRUCK_FLOOR_THICKNESS, 0]} />
          <axesHelper args={[1.5]} />

          <OrbitControls
            enableDamping
            dampingFactor={0.1}
            maxPolarAngle={Math.PI / 2.1}
            minDistance={2}
            maxDistance={15}
            target={[0, 0.6, 0]}
          />
        </Canvas>
      </CanvasErrorBoundary>
      {activeBox && (
        <BoxDetailsCard
          box={activeBox}
          pinned={!!selectedId}
          onClose={() => {
            setSelectedId(null);
            setHoveredId(null);
          }}
        />
      )}
      <div className="truck3d-toplabels">
        <button
          type="button"
          onClick={() => setShowTypeLabels((v) => !v)}
          className="truck3d-flag"
          aria-pressed={!showTypeLabels}
          title={showTypeLabels ? 'Raise flag — hide type badges' : 'Lower flag — show type badges'}
        >
          <span className="flag-icon">{showTypeLabels ? '🏳️' : '🚩'}</span>
          <span>{showTypeLabels ? 'badges visible' : 'badges hidden'}</span>
        </button>
        <div className="truck3d-typekey">
          {Object.entries(PHYSICAL_TYPES).map(([k, m]) => (
            <span className="chip" key={k}>
              <span className="dot" style={{ background: m.color }}>{m.code}</span>
              {m.label}
            </span>
          ))}
        </div>
      </div>
      <div className="truck3d-legend">
        <span>Drag to rotate · scroll to zoom · right-drag to pan</span>
        <span><span className="sw" style={{ background: '#fc0' }} /> floating boxes = in driver's hands</span>
      </div>
    </div>
  );
}
