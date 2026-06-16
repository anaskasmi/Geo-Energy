import Lottie from "lottie-react";

import animationData from "../assets/lottie/ai-thumbnail.json";

/**
 * Animated assistant avatar (GEO-40) — the looping Lottie orb cloned from the aero-qalis assistant.
 * Used in the panel header and beside assistant messages. Purely decorative (aria-hidden); pause the
 * loop when the user prefers reduced motion.
 */
export function BotAvatar({ size = 28 }: { size?: number }) {
  const reduce =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  return (
    <div className="bot-avatar" style={{ width: size, height: size }} aria-hidden="true">
      <Lottie animationData={animationData} loop={!reduce} autoplay={!reduce} style={{ width: size, height: size }} />
    </div>
  );
}
