import { useCallback, useEffect, useRef, useState } from "react";

import { fetchRealtimeSession, OPENAI_REALTIME_CALLS_URL, reportRealtimeUsage } from "./realtimeClient";
import type { UseVoiceModeOptions, VoiceState, VoiceTranscript } from "./voiceTypes";

/** Shown when the per-IP voice budget is spent (GEO-44); mirrors the server's text-agent message. */
const LIMIT_MESSAGE = "Sorry, you've reached the usage limit allowed.";

/**
 * OpenAI Realtime voice mode over WebRTC (GEO-40).
 *
 * Lifecycle: `start()` mints a short-lived ephemeral secret from our backend, opens a peer
 * connection to OpenAI, streams the mic up and plays the model's voice back (audio never touches our
 * server), and opens a data channel for events. We translate the realtime event stream into a small
 * UI surface — a coarse `state` (connecting/listening/thinking/speaking), live `transcripts`, and an
 * `error` — and dispatch the model's function calls to the supplied `tools` so the voice agent can
 * actually drive the map. `stop()` tears everything down. Self-healing: a denied mic, a missing key,
 * or any transport failure resolves to a clean `error` state instead of throwing.
 */
export function useVoiceMode(options: UseVoiceModeOptions) {
  const [state, setState] = useState<VoiceState>("idle");
  const [transcripts, setTranscripts] = useState<VoiceTranscript[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Set when the per-IP voice budget is spent (GEO-44); the panel renders it as a banner.
  const [limitNotice, setLimitNotice] = useState<string | null>(null);

  // Latest options without re-subscribing the connection (avoids stale tool closures mid-session).
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const asstIdRef = useRef<string | null>(null); // current assistant transcript being streamed
  const idRef = useRef(0);
  const newId = () => `v${(idRef.current += 1)}`;
  // Monotonic attempt counter. start() claims a generation; teardown() bumps it to CANCEL any
  // in-flight start() — so a getUserMedia/SDP step that resolves AFTER the user hit "End voice"
  // releases the mic it just acquired instead of leaking a live device behind an idle UI.
  const genRef = useRef(0);

  const teardown = useCallback(() => {
    genRef.current += 1; // invalidate any start() still in flight
    dcRef.current?.close();
    dcRef.current = null;
    pcRef.current?.getSenders().forEach((s) => s.track?.stop());
    pcRef.current?.close();
    pcRef.current = null;
    micRef.current?.getTracks().forEach((t) => t.stop());
    micRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
      audioRef.current = null;
    }
    asstIdRef.current = null;
  }, []);

  const stop = useCallback(() => {
    teardown();
    setState("idle");
  }, [teardown]);

  // Append a transcript line; for streamed assistant text, update the in-flight line in place.
  const pushUser = useCallback((text: string) => {
    if (!text.trim()) return;
    setTranscripts((prev) => [...prev, { id: `vu${Date.now()}`, role: "user", text }]);
  }, []);
  const appendAssistant = useCallback((delta: string) => {
    setTranscripts((prev) => {
      const id = asstIdRef.current;
      const last = prev[prev.length - 1];
      if (id && last && last.id === id) {
        return [...prev.slice(0, -1), { ...last, text: last.text + delta }];
      }
      const fresh = { id: id ?? newId(), role: "assistant" as const, text: delta };
      asstIdRef.current = fresh.id;
      return [...prev, fresh];
    });
  }, []);

  const handleEvent = useCallback(
    async (evt: Record<string, unknown>) => {
      const type = String(evt.type ?? "");
      const dc = dcRef.current;
      switch (type) {
        case "input_audio_buffer.speech_started":
          setState("listening");
          break;
        case "input_audio_buffer.speech_stopped":
          setState("thinking");
          break;
        case "response.created":
          asstIdRef.current = null; // a new assistant turn starts a fresh transcript line
          setState("thinking");
          break;
        case "response.output_audio.delta":
          setState("speaking");
          break;
        case "response.output_audio_transcript.delta":
          appendAssistant(String(evt.delta ?? ""));
          break;
        case "conversation.item.input_audio_transcription.completed":
          pushUser(String(evt.transcript ?? ""));
          break;
        case "response.done": {
          // Report this turn's token usage so the backend accrues it to the per-IP voice budget
          // (GEO-44). If that tips the IP over the cap, stop the session and show the banner.
          const usage = (evt.response as { usage?: unknown } | undefined)?.usage;
          if (usage) {
            void reportRealtimeUsage(usage).then((status) => {
              if (status?.limitReached) {
                setLimitNotice(LIMIT_MESSAGE);
                teardown();
                setState("idle");
              }
            });
          }
          // Turn finished — go back to a calm listening state (semantic VAD keeps the mic hot).
          setState("listening");
          break;
        }
        case "response.function_call_arguments.done": {
          // The model wants to act. Run the matching tool locally, return its result, ask for a reply.
          const name = String(evt.name ?? "");
          const callId = String(evt.call_id ?? "");
          let args: Record<string, unknown> = {};
          try {
            args = JSON.parse(String(evt.arguments ?? "{}")) as Record<string, unknown>;
          } catch {
            /* malformed args → empty object; the tool can decide what to do */
          }
          const tool = optionsRef.current.tools.find((t) => t.name === name);
          let output: unknown;
          try {
            output = tool ? await tool.execute(args) : { error: `unknown tool: ${name}` };
          } catch {
            output = { error: "the tool failed to run" };
          }
          dc?.send(
            JSON.stringify({
              type: "conversation.item.create",
              item: { type: "function_call_output", call_id: callId, output: JSON.stringify(output) },
            }),
          );
          dc?.send(JSON.stringify({ type: "response.create" }));
          break;
        }
        case "error": {
          const message = (evt.error as { message?: string } | undefined)?.message;
          if (message) setError(message);
          break;
        }
        default:
          break;
      }
    },
    [appendAssistant, pushUser, teardown],
  );

  const start = useCallback(async () => {
    if (pcRef.current) return; // already connecting/active
    const gen = (genRef.current += 1); // claim this attempt; teardown() bumps gen to cancel it
    const cancelled = () => gen !== genRef.current; // true once stop()/teardown() superseded us
    setError(null);
    setLimitNotice(null);
    setTranscripts([]);
    setState("connecting");

    const session = await fetchRealtimeSession();
    if (cancelled()) return; // ended while minting the session
    if (!session.configured) {
      setError("Voice mode needs an OpenAI API key. Add OPENAI_API_KEY to the server's .env.");
      setState("error");
      return;
    }
    if (session.limitReached) {
      // Per-IP voice budget already spent — don't mint another billable session (GEO-44).
      setLimitNotice(session.error ?? LIMIT_MESSAGE);
      setState("idle");
      return;
    }
    if (session.error || !session.value) {
      setError(session.error ?? "Couldn't start a voice session.");
      setState("error");
      return;
    }

    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      if (!cancelled()) {
        setError("Microphone access was blocked. Allow the mic to use voice mode.");
        setState("error");
      }
      return;
    }
    if (cancelled()) {
      // The user ended voice while the mic was being acquired — release the device immediately,
      // otherwise it stays hot with no connection to stop it (the reported leak).
      mic.getTracks().forEach((t) => t.stop());
      return;
    }
    micRef.current = mic;

    const pc = new RTCPeerConnection();
    pcRef.current = pc;

    // Play the model's voice. Created on a user gesture (the mic click) so autoplay is allowed.
    const audio = new Audio();
    audio.autoplay = true;
    audioRef.current = audio;
    pc.ontrack = (e) => {
      audio.srcObject = e.streams[0];
    };

    pc.addTrack(mic.getTracks()[0], mic);

    const dc = pc.createDataChannel("oai-events");
    dcRef.current = dc;
    dc.onmessage = (e) => {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(e.data as string) as Record<string, unknown>;
      } catch {
        return;
      }
      void handleEvent(data);
    };
    dc.onopen = () => {
      // Configure the live session: persona, server-side turn detection, input transcription, and the
      // tools the model may call. Voice/model are already fixed by the minted ephemeral secret.
      dc.send(
        JSON.stringify({
          type: "session.update",
          session: {
            type: "realtime",
            instructions: optionsRef.current.instructions,
            audio: {
              input: {
                turn_detection: { type: "semantic_vad" },
                transcription: { model: "gpt-4o-mini-transcribe" },
              },
            },
            tools: optionsRef.current.tools.map((t) => ({
              type: "function",
              name: t.name,
              description: t.description,
              parameters: t.parameters,
            })),
            tool_choice: "auto",
          },
        }),
      );
      setState("listening");
    };

    try {
      const offer = await pc.createOffer();
      if (cancelled()) return; // teardown() already closed pc + released the mic
      await pc.setLocalDescription(offer);
      if (cancelled()) return;
      const answer = await fetch(OPENAI_REALTIME_CALLS_URL, {
        method: "POST",
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${session.value}`,
          "Content-Type": "application/sdp",
        },
      });
      if (cancelled()) return;
      if (!answer.ok) {
        throw new Error(`SDP exchange failed (${answer.status})`);
      }
      const sdp = await answer.text();
      if (cancelled()) return;
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch {
      if (cancelled()) return; // a stop() during the handshake already cleaned up
      teardown();
      setError("Couldn't connect the voice session. Please try again.");
      setState("error");
    }
  }, [handleEvent, teardown]);

  // Always release the mic + connection if the component using voice mode unmounts.
  useEffect(() => () => teardown(), [teardown]);

  return {
    state,
    transcripts,
    error,
    limitNotice,
    dismissLimit: () => setLimitNotice(null),
    isActive: state !== "idle" && state !== "error",
    start,
    stop,
  };
}
