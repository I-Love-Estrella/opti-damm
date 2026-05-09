'use client';

import { useEffect, useRef } from 'react';

export default function CopilotPanel({ messages, prompts, onPrompt, isTyping, sysLog }) {
  const streamRef = useRef(null);

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

  return (
    <div className="panel copilot-panel">
      <div className="panel-head copilot-head">
        <div className="panel-title">
          <span className="panel-index">03</span>
          Co-pilot <span className="sparkle">✦</span>
          <span className="panel-code">CLD-AGENT · v0.4</span>
        </div>
        <div className="panel-readout">
          <span className="ro-row">CTX <strong>RTE-A · TRK-04</strong></span>
          <span className="ro-row ro-dim">REASONING ON</span>
        </div>
      </div>

      <div className="sys-log">
        <div className="sl-head">
          <span>SYSTEM LOG</span>
          <span>{sysLog.length} EVT</span>
        </div>
        <div className="sl-feed">
          {sysLog.slice(-3).map((e, i) => (
            <div key={i} className="sl-row">
              <span className="sl-time">{e.t}</span>
              <span className={`sl-tag ${e.level || 'info'}`}>{e.tag}</span>
              <span className="sl-msg">{e.msg}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="stream" ref={streamRef}>
        {messages.map((m, i) => {
          if (m.kind === "alert") {
            return <div key={i} className="msg alert">⚠ {m.text}</div>;
          }
          return (
            <div key={i} className={`msg ${m.kind}`}>
              <div dangerouslySetInnerHTML={{ __html: m.text }} />
            </div>
          );
        })}
        {isTyping && (
          <div className="msg claude">
            <span className="typing"><span></span><span></span><span></span></span>
          </div>
        )}
      </div>

      <div className="prompts">
        {prompts.map(p => (
          <button
            key={p.id}
            className={`prompt-btn ${p.alert ? 'alert-prompt' : ''}`}
            disabled={p.disabled}
            onClick={() => onPrompt(p)}
          >
            {p.alert ? '⚠ ' : ''}{p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
