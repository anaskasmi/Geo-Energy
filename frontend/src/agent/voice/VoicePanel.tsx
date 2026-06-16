import { useEffect, useRef } from "react";
import { MicOff } from "lucide-react";

import { Icon } from "../../components/Icon";
import { VoiceOrb } from "./VoiceOrb";
import type { VoiceState, VoiceTranscript } from "./voiceTypes";

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "Tap to talk",
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  error: "Voice unavailable",
};

/**
 * Full-panel voice UI (GEO-40): the animated orb + a live state label, a running transcript, an
 * optional error, and a single "End voice" control. Shown in place of the chat body while a realtime
 * session is active.
 */
export function VoicePanel({
  state,
  transcripts,
  error,
  onStop,
}: {
  state: VoiceState;
  transcripts: VoiceTranscript[];
  error: string | null;
  onStop: () => void;
}) {
  const logRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcripts]);

  return (
    <div className="voice-panel">
      <div className="voice-panel__stage">
        <VoiceOrb state={state} />
        <p className="voice-panel__state">{STATE_LABEL[state]}</p>
        {error && <p className="voice-panel__error">{error}</p>}
      </div>

      {transcripts.length > 0 && (
        <div className="voice-panel__log" ref={logRef} aria-live="polite">
          {transcripts.map((t) => (
            <p key={t.id} className={`voice-line voice-line--${t.role}`}>
              {t.text}
            </p>
          ))}
        </div>
      )}

      <button type="button" className="voice-panel__stop" onClick={onStop}>
        <Icon icon={MicOff} size={16} />
        End voice
      </button>
    </div>
  );
}
