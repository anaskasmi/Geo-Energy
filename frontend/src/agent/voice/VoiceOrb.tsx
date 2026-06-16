import type { VoiceState } from "./voiceTypes";

/**
 * Animated voice orb (GEO-40), adapted from the aero-qalis assistant to the geo-energy design
 * tokens. The orb's motion encodes the realtime session state: a slow breathe at rest, a spin while
 * connecting, a bounce while the user speaks, a shimmer while the model thinks, and a quick pulse
 * while it speaks. All motion is CSS (see `.voice-orb*` in components.css) and respects
 * prefers-reduced-motion.
 */
export function VoiceOrb({ state, size = 132 }: { state: VoiceState; size?: number }) {
  return (
    <div className={`voice-orb voice-orb--${state}`} style={{ width: size, height: size }}>
      <span className="voice-orb__glow" aria-hidden="true" />
      <span className="voice-orb__core" aria-hidden="true">
        <span className="voice-orb__highlight" />
        <span className="voice-orb__shimmer" />
      </span>
    </div>
  );
}
