'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

const KIND_TAGS = {
  ARRIVE:           { tag: 'ARRIVE  ', cls: 'al-arrive'  },
  SERVICE_BASE:     { tag: 'SERVICE ', cls: 'al-service' },
  BLOCKER_LIFT:     { tag: 'LIFT    ', cls: 'al-lift'    },
  TARGET_TAKE:      { tag: 'TAKE    ', cls: 'al-take'    },
  BLOCKER_REPLACE:  { tag: 'REPLACE ', cls: 'al-replace' },
  UNLOAD:           { tag: 'UNLOAD  ', cls: 'al-unload'  },
  DROP:             { tag: 'DROP    ', cls: 'al-drop'    },
  PICKUP_RETURN:    { tag: 'PICKUP  ', cls: 'al-pickup'  },
};

const ACTION_KINDS = new Set(['BLOCKER_LIFT', 'TARGET_TAKE', 'BLOCKER_REPLACE', 'PICKUP_RETURN', 'DROP']);

function fmtSim(min) {
  if (min === undefined || min === null) return '       ';
  const total = Math.max(0, min);
  const h = Math.floor(total / 60);
  const m = Math.floor(total % 60);
  const s = Math.round((total - Math.floor(total)) * 60);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function fmtDelta(min) {
  if (!min || min < 0.001) return '+0.00s';
  if (min >= 1) {
    return `+${min.toFixed(2)}m`;
  }
  return `+${(min * 60).toFixed(2)}s`;
}

function buildLine(stg, idx) {
  const meta = KIND_TAGS[stg.kind] || { tag: stg.kind.padEnd(8, ' '), cls: '' };
  const idxStr = `#${String(idx + 1).padStart(4, ' ')}`;
  const sim = fmtSim(stg.t_min);
  const delta = fmtDelta(stg.time_min || 0).padStart(8, ' ');
  const stop = stg.stop_visit_seq !== undefined ? `[s${stg.stop_visit_seq}]` : '[--]';
  return `${idxStr}  ${sim}  ${delta}  ${stop} ${meta.tag} ${stg.description || ''}`;
}

export default function ActionLog({
  stages = [],
  idx = 0,
  onSelectIdx,
}) {
  const [filterActions, setFilterActions] = useState(false);
  const [stickToCurrent, setStickToCurrent] = useState(true);
  const containerRef = useRef(null);
  const rowRefs = useRef({});

  const visible = useMemo(() => {
    if (!filterActions) return stages.map((s, i) => ({ s, i }));
    return stages
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => ACTION_KINDS.has(s.kind));
  }, [stages, filterActions]);

  const currentIdx = Math.max(0, Math.min(idx - 1, stages.length - 1));

  useEffect(() => {
    if (!stickToCurrent) return;
    const node = rowRefs.current[currentIdx];
    if (node && containerRef.current) {
      const c = containerRef.current;
      const offsetTop = node.offsetTop - c.offsetTop;
      const target = offsetTop - c.clientHeight / 2 + node.clientHeight / 2;
      c.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
    }
  }, [currentIdx, stickToCurrent]);

  function copyAll() {
    const text = visible.map(({ s, i }) => buildLine(s, i)).join('\n');
    navigator.clipboard?.writeText(text);
  }

  function copyAround() {
    const startIdx = Math.max(0, currentIdx - 9);
    const endIdx = Math.min(stages.length - 1, currentIdx + 10);
    const text = stages
      .slice(startIdx, endIdx + 1)
      .map((s, k) => buildLine(s, startIdx + k))
      .join('\n');
    navigator.clipboard?.writeText(text);
  }

  return (
    <div className="action-log">
      <div className="al-head">
        <span className="al-title">Action log · {stages.length} stages</span>
        <label className="al-toggle">
          <input
            type="checkbox"
            checked={filterActions}
            onChange={(e) => setFilterActions(e.target.checked)}
          />
          actions only
        </label>
        <label className="al-toggle">
          <input
            type="checkbox"
            checked={stickToCurrent}
            onChange={(e) => setStickToCurrent(e.target.checked)}
          />
          follow current
        </label>
        <button className="al-btn" onClick={copyAround} title="Copy 20 stages around the current step">
          Copy ±10
        </button>
        <button className="al-btn" onClick={copyAll} title="Copy all visible lines">
          Copy {filterActions ? 'filtered' : 'all'}
        </button>
      </div>
      <div className="al-body" ref={containerRef}>
        {visible.length === 0 ? (
          <div className="al-empty">No matching actions.</div>
        ) : (
          visible.map(({ s, i }) => {
            const meta = KIND_TAGS[s.kind] || { cls: '' };
            const isCur = i === currentIdx;
            return (
              <div
                key={i}
                ref={(el) => { rowRefs.current[i] = el; }}
                className={`al-row ${meta.cls}${isCur ? ' al-current' : ''}`}
                onClick={() => onSelectIdx?.(i + 1)}
                title="Click to jump to this stage"
              >
                {buildLine(s, i)}
              </div>
            );
          })
        )}
      </div>
      <div className="al-foot">
        Click a row to jump · select text and Cmd/Ctrl-C to copy a range
      </div>
    </div>
  );
}
