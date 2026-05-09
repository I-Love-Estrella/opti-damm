'use client';

import { useEffect, useRef, useState } from 'react';
import MarkdownMessage from './MarkdownMessage';

export default function CopilotPanel({ messages, onAsk, isTyping, sysLog }) {
  const streamRef = useRef(null);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [messages, isTyping]);

  function handleSubmit(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || isTyping) return;
    setDraft('');
    onAsk(text);
  }

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
            return (
              <div key={i} className="msg alert">
                <span aria-hidden="true">⚠ </span>
                <MarkdownMessage text={m.text} />
              </div>
            );
          }
          return (
            <div key={i} className={`msg ${m.kind}`}>
              {m.kind === 'claude' ? (
                <MarkdownMessage text={m.text} />
              ) : (
                <div className="plain-message">{m.text}</div>
              )}
            </div>
          );
        })}
        {isTyping && (
          <div className="msg claude">
            <span className="typing"><span></span><span></span><span></span></span>
          </div>
        )}
      </div>

      <form className="copilot-input" onSubmit={handleSubmit}>
        <input
          value={draft}
          disabled={isTyping}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about this route, client, load, or simulation..."
          aria-label="Ask copilot"
        />
        <button type="submit" disabled={isTyping || !draft.trim()} title="Send">↵</button>
      </form>
    </div>
  );
}
