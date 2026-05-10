'use client';

import { useMemo, useState } from 'react';

const SEVERITY_META = {
  error:   { label: 'ERROR',   icon: '✕', cls: 'vp-error'   },
  warning: { label: 'WARNING', icon: '!', cls: 'vp-warning' },
  info:    { label: 'INFO',    icon: 'i', cls: 'vp-info'    },
};

const SEVERITY_ORDER = ['error', 'warning', 'info'];

function CodeBadge({ code }) {
  return <span className="vp-code">{code}</span>;
}

const STABILITY_CLS = {
  STABLE:    'vp-stab-stable',
  WOBBLY:    'vp-stab-wobbly',
  FRAGILE:   'vp-stab-fragile',
  DANGEROUS: 'vp-stab-dangerous',
};

function StabilityBadge({ stability }) {
  const { score, label, physics_errors, physics_warnings } = stability;
  const cls = STABILITY_CLS[label] || 'vp-stab-stable';
  const total = physics_errors + physics_warnings;
  const tooltip = total === 0
    ? 'Load passes every physics check.'
    : `${physics_errors} physics error(s), ${physics_warnings} physics warning(s). ` +
      'Greedy 3D packer was relaxed to fit 100% — this score is the price.';
  return (
    <span className={`vp-stab ${cls}`} title={tooltip}>
      <span className="vp-stab-label">{label}</span>
      <span className="vp-stab-score">{score}%</span>
      {total > 0 && (
        <span className="vp-stab-detail">
          {physics_errors > 0 && <em>×{physics_errors} err</em>}
          {physics_warnings > 0 && <em>×{physics_warnings} warn</em>}
        </span>
      )}
    </span>
  );
}

function IssueRow({ issue }) {
  const meta = SEVERITY_META[issue.severity] || SEVERITY_META.info;
  const [open, setOpen] = useState(false);
  const hasDetail = issue.detail && Object.keys(issue.detail).length > 0;
  return (
    <div className={`vp-row ${meta.cls}`}>
      <button className="vp-row-head" onClick={() => hasDetail && setOpen((o) => !o)}>
        <span className="vp-icon">{meta.icon}</span>
        <CodeBadge code={issue.code} />
        <span className="vp-msg">{issue.message}</span>
        {issue.where && <span className="vp-where">{issue.where}</span>}
        {hasDetail && <span className="vp-toggle">{open ? '−' : '+'}</span>}
      </button>
      {open && hasDetail && (
        <pre className="vp-detail">{JSON.stringify(issue.detail, null, 2)}</pre>
      )}
    </div>
  );
}

export default function ValidationPanel({ validation }) {
  const [filterSeverity, setFilterSeverity] = useState(null);
  const [showCode, setShowCode] = useState(null);

  const summary = validation?.summary || { errors: 0, warnings: 0, infos: 0, is_valid: true };
  const issues = validation?.issues || [];
  const stability = validation?.stability || null;

  // Group issues by code so we can collapse repetitions.
  const grouped = useMemo(() => {
    const map = new Map();
    for (const i of issues) {
      const key = `${i.severity}::${i.code}`;
      const e = map.get(key) || { ...i, count: 0, samples: [] };
      e.count += 1;
      if (e.samples.length < 5) e.samples.push(i);
      map.set(key, e);
    }
    return Array.from(map.values()).sort((a, b) => {
      const sa = SEVERITY_ORDER.indexOf(a.severity);
      const sb = SEVERITY_ORDER.indexOf(b.severity);
      if (sa !== sb) return sa - sb;
      return b.count - a.count;
    });
  }, [issues]);

  const filtered = useMemo(() => {
    let out = grouped;
    if (filterSeverity) out = out.filter((g) => g.severity === filterSeverity);
    return out;
  }, [grouped, filterSeverity]);

  const visibleIndividual = useMemo(() => {
    if (!showCode) return null;
    return issues.filter((i) => `${i.severity}::${i.code}` === showCode);
  }, [issues, showCode]);

  return (
    <div className={`validation-panel ${summary.is_valid ? 'vp-valid' : 'vp-invalid'}`}>
      <div className="vp-head">
        <span className={`vp-status ${summary.is_valid ? 'vp-status-valid' : 'vp-status-invalid'}`}>
          {summary.is_valid ? 'PLAN VALID' : 'PLAN REJECTED'}
        </span>
        {stability && (
          <StabilityBadge stability={stability} />
        )}
        <span className="vp-counts">
          <button
            className={`vp-count vp-count-error${filterSeverity === 'error' ? ' active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'error' ? null : 'error')}
            disabled={summary.errors === 0}
          >
            {summary.errors} errors
          </button>
          <button
            className={`vp-count vp-count-warning${filterSeverity === 'warning' ? ' active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'warning' ? null : 'warning')}
            disabled={summary.warnings === 0}
          >
            {summary.warnings} warnings
          </button>
          <button
            className={`vp-count vp-count-info${filterSeverity === 'info' ? ' active' : ''}`}
            onClick={() => setFilterSeverity(filterSeverity === 'info' ? null : 'info')}
            disabled={summary.infos === 0}
          >
            {summary.infos} info
          </button>
          {filterSeverity && (
            <button className="vp-clear" onClick={() => setFilterSeverity(null)}>
              clear filter
            </button>
          )}
        </span>
      </div>

      {summary.is_valid && summary.warnings === 0 && summary.infos === 0 && (
        <div className="vp-clean">
          ✓ No issues found. Plan respects all physical and process constraints.
        </div>
      )}

      {filtered.length > 0 && (
        <div className="vp-groups">
          {filtered.map((g) => {
            const meta = SEVERITY_META[g.severity];
            const sampleKey = `${g.severity}::${g.code}`;
            const expanded = showCode === sampleKey;
            const instances = visibleIndividual || g.samples;
            const hasDetail =
              g.count > 1 ||
              (g.detail && Object.keys(g.detail).length > 0) ||
              g.where;
            return (
              <div key={sampleKey} className={`vp-group ${meta.cls}`}>
                <button
                  className="vp-group-head"
                  onClick={() =>
                    hasDetail && setShowCode(expanded ? null : sampleKey)
                  }
                  disabled={!hasDetail}
                >
                  <span className="vp-icon">{meta.icon}</span>
                  <CodeBadge code={g.code} />
                  <span className="vp-msg">
                    {g.count > 1 ? `${g.count}× ` : ''}
                    {g.message}
                  </span>
                  {hasDetail && (
                    <span className="vp-toggle">{expanded ? '−' : '+'}</span>
                  )}
                </button>
                {expanded && hasDetail && (
                  <div className="vp-group-body">
                    {g.count === 1 ? (
                      // Single instance: don't duplicate the message —
                      // just show `where` + detail JSON below the parent.
                      <div className="vp-single-detail">
                        {g.where && (
                          <div className="vp-where-line">
                            <span className="vp-where">{g.where}</span>
                          </div>
                        )}
                        {g.detail && Object.keys(g.detail).length > 0 && (
                          <pre className="vp-detail">
                            {JSON.stringify(g.detail, null, 2)}
                          </pre>
                        )}
                      </div>
                    ) : (
                      // Multiple instances: show each one as a compact
                      // row (where + detail). Suppress the message — the
                      // parent already says it.
                      instances.map((s, i) => (
                        <div
                          key={`${g.code}-${i}`}
                          className={`vp-row ${meta.cls} vp-instance`}
                        >
                          {s.where && (
                            <div className="vp-where-line">
                              <span className="vp-where">{s.where}</span>
                            </div>
                          )}
                          {s.detail && Object.keys(s.detail).length > 0 && (
                            <pre className="vp-detail">
                              {JSON.stringify(s.detail, null, 2)}
                            </pre>
                          )}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
