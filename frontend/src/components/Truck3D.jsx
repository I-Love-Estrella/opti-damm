'use client';

import { Canvas } from '@react-three/fiber';
import { OrbitControls, Edges, Html } from '@react-three/drei';
import { Component, useMemo } from 'react';

const PALLET_LEN = 1.2;
const PALLET_WIDTH = 0.8;
const PALLET_HEIGHT = 1.8;
const PALLET_THICKNESS = 0.12;
const SLOT_GAP_X = 0.06;
const TRUCK_WALL_PAD = 0.10;
const TRUCK_FLOOR_THICKNESS = 0.05;

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

function slotWorldCenter(slotId, totalSidePos) {
  const { side, pos } = slotIndex(slotId);
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

function CargoBox({ position, size, color, opacity = 1, emissive, isHovered, label }) {
  return (
    <group position={[position.x, position.y, position.z]}>
      <mesh>
        <boxGeometry args={[size.len, size.h, size.width]} />
        <meshStandardMaterial
          color={color}
          transparent={opacity < 1}
          opacity={opacity}
          emissive={emissive || '#000000'}
          emissiveIntensity={emissive ? 0.45 : 0}
          roughness={0.6}
          metalness={0.05}
        />
        <Edges color={isHovered ? '#ffffff' : '#000000'} threshold={1} lineWidth={1.5} />
      </mesh>
      {label && (
        <Html
          position={[0, size.h / 2 + 0.06, 0]}
          center
          distanceFactor={6}
          style={{ pointerEvents: 'none' }}
        >
          <div className="r3d-label">{label}</div>
        </Html>
      )}
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

function TruckShell({ totalSidePos }) {
  const truckLength = totalSidePos * (PALLET_LEN + SLOT_GAP_X) + 2 * TRUCK_WALL_PAD;
  const truckWidth = 2 * (PALLET_WIDTH + 0.05) + 2 * TRUCK_WALL_PAD;
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
  const totalSidePos = useMemo(() => {
    let max = 0;
    for (const slotId of Object.keys(palletsBySlot)) {
      const { side, pos } = slotIndex(slotId);
      if (side !== 'B' && pos > max) max = pos;
    }
    return Math.max(max, 1);
  }, [palletsBySlot]);

  const slotCenters = useMemo(() => {
    const out = {};
    for (const slotId of Object.keys(palletsBySlot)) {
      out[slotId] = slotWorldCenter(slotId, totalSidePos);
    }
    return out;
  }, [palletsBySlot, totalSidePos]);

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

          <TruckShell totalSidePos={totalSidePos} />

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
            if (b.status === 'delivered') return null;

            const cell = cellWorld(center, b.side, b.layout, b.col_x, b.col_y, b.level);
            const cubeSize = {
              len: cell.cellLen * 0.99,
              width: cell.cellWidth * 0.99,
              h: cell.cellH * 0.99,
            };

            const baseColor = b.is_returnable_empty ? '#aa66ff' : clientColor(b.intended_client);
            const isCurrentEvent =
              highlightSeq !== undefined &&
              b.history?.length > 0 &&
              b.history[b.history.length - 1].step_idx === highlightSeq;

            let position;
            let opacity = 1;
            let emissive = isCurrentEvent ? '#ffaa00' : null;

            if (b.status === 'in_pallet') {
              position = { x: cell.x, y: cell.y, z: cell.z };
            } else {
              // in_hands — float above truck, jittered per box id
              const idHash = parseInt(b.id.replace(/\D/g, ''), 10) || 0;
              const xJ = ((idHash * 17) % 200 - 100) / 200;
              const zJ = ((idHash * 23) % 200 - 100) / 200;
              position = {
                x: cell.x + xJ * 0.4,
                y: PALLET_HEIGHT + 0.5 + ((idHash * 11) % 70) / 100,
                z:
                  cell.z +
                  (b.side === 'L' ? -1.2 : b.side === 'R' ? 1.2 : 0) +
                  zJ * 0.3,
              };
              opacity = 0.92;
              emissive = emissive || '#552200';
            }

            return (
              <CargoBox
                key={b.id}
                position={position}
                size={cubeSize}
                color={baseColor}
                opacity={opacity}
                emissive={emissive}
                isHovered={isCurrentEvent}
              />
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
      <div className="truck3d-legend">
        <span>Drag to rotate · scroll to zoom · right-drag to pan</span>
        <span><span className="sw" style={{ background: '#fc0' }} /> floating boxes = in driver's hands</span>
      </div>
    </div>
  );
}
