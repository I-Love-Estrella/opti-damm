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
            return (
              <div key={sampleKey} className={`vp-group ${meta.cls}`}>
                <button
                  className="vp-group-head"
                  onClick={() => setShowCode(expanded ? null : sampleKey)}
                >
                  <span className="vp-icon">{meta.icon}</span>
                  <CodeBadge code={g.code} />
                  <span className="vp-msg">
                    {g.count > 1 ? `${g.count}× ` : ''}
                    {g.message}
                  </span>
                  <span className="vp-toggle">{expanded ? '−' : '+'}</span>
                </button>
                {expanded && (
                  <div className="vp-group-body">
                    {(visibleIndividual || g.samples).map((s, i) => (
                      <IssueRow key={`${g.code}-${i}`} issue={s} />
                    ))}
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
