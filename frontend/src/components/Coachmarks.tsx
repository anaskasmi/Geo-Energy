import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { X } from "lucide-react";

import { Icon } from "./Icon";

/**
 * First-run onboarding coachmarks (GEO-32 #7): a dismissible, keyboard-accessible guided
 * sequence (draw an area → read the scores → open a parcel). Non-blocking — it floats over the
 * map as a card and never inerts the app. A "seen" flag persists in localStorage; the sidebar's
 * "Take a tour" button replays it. Honors reduced-motion via the global CSS rule (no JS animation).
 */
const SEEN_KEY = "geo.onboarding.seen.v1";

const STEPS = [
  {
    title: "1. Draw a search area",
    body: "Use the Draw tool at the top of the map (or press D), then click to outline an area. Parcels inside are scored automatically.",
  },
  {
    title: "2. Read the scores",
    body: "Ranked parcels appear in the results panel, colored by suitability. Sort, filter, and compare up to three at once.",
  },
  {
    title: "3. Open a parcel",
    body: "Select a parcel — on the map or in the list — to see its full scoring breakdown, then export or share it.",
  },
];

export function hasSeenTour(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return true; // storage blocked → treat as seen so we never nag on every load
  }
}

function markTourSeen(): void {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* storage unavailable — the in-session state still closes the tour */
  }
}

export function Coachmarks({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const primaryRef = useRef<HTMLButtonElement | null>(null);

  // Reset to the first step each time the tour opens; focus the primary action. Capture the
  // element that opened the tour and restore focus to it on close/unmount, so keyboard/SR users
  // aren't dropped onto <body> (a11y — WCAG focus management). For the first-run auto-open the
  // opener is typically <body>, making the restore a harmless no-op.
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    setStep(0);
    return () => opener?.focus?.();
  }, [open]);
  useEffect(() => {
    if (open) primaryRef.current?.focus();
  }, [open, step]);

  if (!open) return null;

  const last = step === STEPS.length - 1;
  const close = () => {
    markTourSeen();
    onClose();
  };
  const next = () => (last ? close() : setStep((s) => s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      next();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      back();
    }
  };

  const current = STEPS[step];
  return (
    <div
      className="coachmark"
      role="dialog"
      aria-modal="false"
      aria-label="Getting started"
      onKeyDown={onKeyDown}
    >
      <div className="coachmark__head">
        <span className="coachmark__step">{`Step ${step + 1} of ${STEPS.length}`}</span>
        <button type="button" className="coachmark__close" aria-label="Dismiss tour" onClick={close}>
          <Icon icon={X} size={16} />
        </button>
      </div>
      <h2 className="coachmark__title">{current.title}</h2>
      <p className="coachmark__body">{current.body}</p>
      <div className="coachmark__foot">
        <button type="button" className="coachmark__skip" onClick={close}>
          {last ? "Close" : "Skip"}
        </button>
        <div className="coachmark__nav">
          {step > 0 && (
            <button type="button" className="panel-btn" onClick={back}>
              Back
            </button>
          )}
          <button type="button" className="panel-btn panel-btn--primary" ref={primaryRef} onClick={next}>
            {last ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
