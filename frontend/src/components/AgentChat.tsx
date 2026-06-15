import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { SUGGESTIONS } from "../agent/mockAgent";
import { useAgentChat } from "../agent/useAgentChat";
import type { ParcelRef } from "../agent/types";
import { useMapStore } from "../map/useMapStore";

/**
 * Agent chat panel (GEO-27): type a request → the assistant streams a reply and its "tool calls"
 * update the map + ranked list (via the real scoring pipeline). Suggested-prompt chips, clickable
 * parcel references, and graceful degradation when scoring is unavailable. The live `/api/agent`
 * (Gemini via Pydantic AI) lands in GEO-21; this is built against that mocked contract.
 */
export function AgentChat() {
  const { messages, send } = useAgentChat();
  const { setSelected, flyTo } = useMapStore();
  const [input, setInput] = useState("");
  const logRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
  }, [messages]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
    setInput("");
  };

  const openRef = (r: ParcelRef) => {
    setSelected({ id: r.id, apn: r.apn, acres: null });
    if (r.centroid) flyTo(r.centroid);
  };

  return (
    <div className="agent-chat">
      <div
        className="agent-chat__log"
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-busy={messages.some((m) => m.streaming)}
        aria-label="Assistant conversation"
      >
        {messages.map((m) => (
          <div key={m.id} className={`chat-msg chat-msg--${m.role}`}>
            <p className="chat-msg__text">
              {m.text}
              {m.streaming && <span className="chat-cursor" aria-hidden="true" />}
            </p>
            {m.refs && m.refs.length > 0 && (
              <div className="chat-refs">
                {m.refs.map((r) => (
                  <button key={String(r.id)} type="button" className="chat-ref" onClick={() => openRef(r)}>
                    {r.apn ?? `Parcel ${r.id}`} · {r.score.toFixed(0)}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="chat-chips">
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="chat-chip" onClick={() => send(s)}>
            {s}
          </button>
        ))}
      </div>

      <form className="chat-input" onSubmit={submit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. best solar sites near Mojave"
          aria-label="Message the assistant"
        />
        <button type="submit" disabled={!input.trim()}>
          Send
        </button>
      </form>

      <p className="chat-note">
        Demo — the live agent (Gemini via <code>/api/agent</code>) lands in GEO-21; its tool calls already drive the
        real map &amp; list.
      </p>
    </div>
  );
}
