import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import Lottie from "lottie-react";
import { Mic, PanelLeftClose, Send, Sparkles } from "lucide-react";

import { apiClient } from "../api/client";
import type { UseCase } from "../api/client";
import { useMapStore } from "../map/useMapStore";
import { PLACE_LABELS, resolvePlace } from "../map/places";
import emptyAnimation from "../assets/lottie/empty-chat.json";
import { Icon } from "../components/Icon";
import { PHASE_LABELS, SUGGESTIONS } from "./agentClient";
import { BotAvatar } from "./BotAvatar";
import { Markdown } from "./Markdown";
import type { ParcelRef } from "./types";
import { useAgentChat } from "./useAgentChat";
import { VoicePanel } from "./voice/VoicePanel";
import { useVoiceMode } from "./voice/useVoiceMode";
import type { VoiceTool } from "./voice/voiceTypes";

const VOICE_INSTRUCTIONS = `You are the voice assistant for a renewable-energy site-selection app focused on Kern County, California. You help users find good parcels for utility-scale solar farms and data centers.

Voice rules:
- Keep replies to 1-3 short sentences. Be conversational and direct.
- Never read raw tables, lists, coordinates, or long numbers aloud — the screen shows those. Say the insight: how many strong sites, roughly where, and the standout parcel.
- Reference the screen naturally, e.g. "I've put the top sites on the map."
- No markdown, bullet points, or special characters — your words are spoken aloud.

When the user asks to find, score, or rank sites, call find_sites with the place and use_case. When they only want to look at an area, call focus_map.
Places you cover: ${PLACE_LABELS.join(", ")}. If they name somewhere else, say you only cover Kern County for now.`;

/**
 * Docked Agent panel (GEO-40). Replaces the old toolbar popover with a first-class left-rail surface
 * cloned from the aero-qalis assistant and restyled to the geo-energy tokens: a Lottie-avatar header,
 * a streaming chat body (the live `/api/agent` SSE — Gemini) with an animated empty state + typing
 * indicator, suggested-prompt chips, and a composer footer. The mic opens an OpenAI Realtime VOICE
 * session whose function calls drive the same map + results list.
 */
export function AgentPanel({ onClose }: { onClose?: () => void }) {
  const { messages, send, busy } = useAgentChat();
  const { setSelected, flyTo, setScoreResult, setScoreStatus, setUseCase } = useMapStore();
  const [input, setInput] = useState("");
  const logRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);

  // Tools the voice agent may call — they run locally and drive the real map + results list, then
  // hand a compact summary back to the model so it can speak the takeaway.
  const tools = useMemo<VoiceTool[]>(() => {
    const findSites: VoiceTool = {
      name: "find_sites",
      description:
        "Score and rank parcels for a renewable project near a Kern County place, then show them on the map and results list.",
      parameters: {
        type: "object",
        properties: {
          place: { type: "string", description: "A Kern County place, e.g. Mojave, Bakersfield, Tehachapi." },
          use_case: {
            type: "string",
            enum: ["utility_solar", "data_center"],
            description: "What to site. Defaults to utility_solar.",
          },
        },
        required: ["place"],
      },
      execute: async (args) => {
        const place = String(args.place ?? "");
        const useCase: UseCase = args.use_case === "data_center" ? "data_center" : "utility_solar";
        const resolved = resolvePlace(place);
        if (!resolved) return { error: `No data for "${place}".`, known_places: PLACE_LABELS };
        setUseCase(useCase);
        setScoreStatus("scoring");
        try {
          const fc = await apiClient.score({ geometry: resolved.geometry, use_case: useCase });
          setScoreResult(fc);
          setScoreStatus("done");
          flyTo(resolved.center, 10);
          const feats = fc.features ?? [];
          return {
            place: resolved.label,
            use_case: useCase,
            count: feats.length,
            top: feats.slice(0, 3).map((f) => ({
              apn: f.properties.apn,
              score: Math.round(f.properties.score),
            })),
          };
        } catch {
          setScoreStatus("error", "Scoring failed.");
          return { error: "Scoring failed for that area." };
        }
      },
    };
    const focusMap: VoiceTool = {
      name: "focus_map",
      description: "Pan and zoom the map to a Kern County place without scoring.",
      parameters: {
        type: "object",
        properties: { place: { type: "string", description: "A Kern County place name." } },
        required: ["place"],
      },
      execute: async (args) => {
        const resolved = resolvePlace(String(args.place ?? ""));
        if (!resolved) return { error: "Unknown place.", known_places: PLACE_LABELS };
        flyTo(resolved.center, 11);
        return { ok: true, place: resolved.label };
      },
    };
    return [findSites, focusMap];
  }, [flyTo, setScoreResult, setScoreStatus, setUseCase]);

  const voice = useVoiceMode({ instructions: VOICE_INSTRUCTIONS, tools });
  const showVoice = voice.state !== "idle";

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
  }, [messages]);

  // Auto-grow the composer up to a few lines, then scroll inside it.
  const autosize = useCallback(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`;
  }, []);
  useEffect(autosize, [input, autosize]);

  const doSend = () => {
    if (!input.trim() || busy) return;
    send(input);
    setInput("");
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    doSend();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  };

  const openRef = (r: ParcelRef) => {
    setSelected({ id: r.id, apn: r.apn, acres: null });
    if (r.centroid) flyTo(r.centroid);
  };

  const isEmpty = messages.length <= 1;

  return (
    <section className="agent-panel" aria-label="Assistant">
      <header className="agent-panel__head">
        <div className="agent-panel__id">
          <BotAvatar size={28} />
          <div>
            <h2 className="agent-panel__title">Assistant</h2>
            <p className="agent-panel__subtitle">Kern County siting</p>
          </div>
        </div>
        {onClose && (
          <button type="button" className="agent-panel__close" aria-label="Close assistant" onClick={onClose}>
            <Icon icon={PanelLeftClose} size={18} />
          </button>
        )}
      </header>

      {showVoice ? (
        <VoicePanel state={voice.state} transcripts={voice.transcripts} error={voice.error} onStop={voice.stop} />
      ) : (
        <>
          <div
            className="agent-panel__body"
            ref={logRef}
            role="log"
            aria-live="polite"
            aria-busy={messages.some((m) => m.streaming)}
            aria-label="Assistant conversation"
          >
            {isEmpty ? (
              <div className="agent-empty">
                <Lottie
                  animationData={emptyAnimation}
                  loop
                  autoplay
                  className="agent-empty__art"
                  aria-hidden="true"
                />
                <p className="agent-empty__text">{messages[0]?.text}</p>
              </div>
            ) : (
              messages.map((m) => (
                <div key={m.id} className={`chat-msg chat-msg--${m.role}`}>
                  {m.role === "assistant" && <BotAvatar size={22} />}
                  <div className="chat-msg__col">
                    {m.phase && <p className="chat-msg__phase">{PHASE_LABELS[m.phase] ?? "Working…"}</p>}
                    {m.role === "assistant" && m.streaming && !m.text && !m.phase ? (
                      <TypingDots />
                    ) : m.role === "assistant" && !m.streaming ? (
                      <div className="chat-msg__text chat-md">
                        <Markdown text={m.text} />
                      </div>
                    ) : (
                      <p className="chat-msg__text">
                        {m.text}
                        {m.streaming && <span className="chat-cursor" aria-hidden="true" />}
                      </p>
                    )}
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
                </div>
              ))
            )}
          </div>

          <div className="chat-chips">
            {SUGGESTIONS.map((s) => (
              <button key={s} type="button" className="chat-chip" onClick={() => send(s)} disabled={busy}>
                {s}
              </button>
            ))}
          </div>

          <form className="agent-composer" onSubmit={submit}>
            <button
              type="button"
              className="agent-composer__mic"
              aria-label="Start voice mode"
              title="Talk to the assistant"
              onClick={() => voice.start()}
            >
              <Icon icon={Mic} size={18} />
            </button>
            <textarea
              ref={taRef}
              className="agent-composer__input"
              value={input}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask, or tap the mic to talk…"
              aria-label="Message the assistant"
              disabled={busy}
            />
            <button
              type="submit"
              className="agent-composer__send"
              aria-label="Send"
              disabled={!input.trim() || busy}
            >
              <Icon icon={busy ? Sparkles : Send} size={18} />
            </button>
          </form>

          <p className="chat-note">
            Runs local geospatial tools and updates the map &amp; list. Answers come from a model — verify
            before acting.
          </p>
        </>
      )}
    </section>
  );
}

function TypingDots() {
  return (
    <div className="typing-dots" aria-label="Assistant is thinking">
      <span className="typing-dot" />
      <span className="typing-dot" />
      <span className="typing-dot" />
    </div>
  );
}
