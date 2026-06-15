import { useCallback, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

type Snap = "peek" | "half" | "full";

/** Sheet height at each snap point — `dvh` (not `vh`) so it doesn't overflow mobile Safari's
 *  dynamic toolbar; peek is a fixed px so it always clears the safe-area home indicator. */
const SNAP_HEIGHT: Record<Snap, string> = {
  peek: "var(--sheet-peek)",
  half: "var(--sheet-half)",
  full: "var(--sheet-full)",
};

/** Spoken detent name for the aria-live announcement. */
const SNAP_LABEL: Record<Snap, string> = { peek: "collapsed", half: "half open", full: "expanded" };

/** Drag distance (px) past which a release moves to the next snap (and which marks a gesture as a
 *  drag rather than a tap). */
const SNAP_THRESHOLD_PX = 48;

const ORDER: Snap[] = ["peek", "half", "full"];

function nextSnap(current: Snap, direction: "up" | "down"): Snap {
  const idx = ORDER.indexOf(current);
  const nextIdx = direction === "up" ? idx + 1 : idx - 1;
  return ORDER[Math.min(ORDER.length - 1, Math.max(0, nextIdx))];
}

/**
 * Mobile draggable/expandable bottom-sheet. Three snap points (peek/half/full). It's a
 * NONMODAL, persistent surface — no scrim, no focus trap — so the map stays interactive behind it.
 *
 * Accessibility (design system §6, fixes the prior keyboard-trap bug): the grip is a real
 * `<button>`, so it satisfies WCAG 2.1.1 (keyboard operable) AND 2.5.7 (a single-pointer / keyboard
 * alternative to the drag gesture). Pointer drag resizes continuously and snaps on release; a plain
 * tap or Enter/Space cycles peek → half → full → peek. `aria-expanded` + a polite live region
 * announce the detent. `touch-action` lives on the handle only (the scrollable content owns pan-y),
 * so dragging the handle never fights the list scroll.
 */
export function BottomSheet({ children }: { children: ReactNode }) {
  const [snap, setSnap] = useState<Snap>("half");
  const [dragDelta, setDragDelta] = useState(0);
  const dragging = useRef(false);
  const didDrag = useRef(false);
  const startY = useRef(0);

  const cycle = useCallback(() => {
    setSnap((current) => ORDER[(ORDER.indexOf(current) + 1) % ORDER.length]);
  }, []);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    dragging.current = true;
    didDrag.current = false;
    startY.current = event.clientY;
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!dragging.current) return;
    const delta = event.clientY - startY.current;
    if (Math.abs(delta) > 6) didDrag.current = true; // past the slop → it's a drag, not a tap
    setDragDelta(delta);
  }, []);

  const endDrag = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!dragging.current) return;
    dragging.current = false;
    const delta = event.clientY - startY.current;
    if (Math.abs(delta) > SNAP_THRESHOLD_PX) {
      setSnap((current) => nextSnap(current, delta < 0 ? "up" : "down"));
    }
    setDragDelta(0);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  // Native buttons fire onClick on pointer-tap AND on keyboard Enter/Space — that's our drag
  // alternative. A pointer DRAG also emits a click on release, so swallow that one.
  const onClick = useCallback(() => {
    if (didDrag.current) {
      didDrag.current = false;
      return;
    }
    cycle();
  }, [cycle]);

  // While dragging, follow the finger downward (clamped); upward expansion snaps on release so
  // the sheet never detaches from the bottom.
  const liveOffset = dragging.current ? Math.max(0, dragDelta) : 0;

  return (
    <section
      className="bottom-sheet"
      data-snap={snap}
      data-dragging={dragging.current || undefined}
      style={{
        height: SNAP_HEIGHT[snap],
        transform: liveOffset ? `translateY(${liveOffset}px)` : undefined,
      }}
      aria-label="Results panel"
    >
      <button
        type="button"
        className="bottom-sheet__handle"
        aria-label={snap === "full" ? "Collapse results panel" : "Expand results panel"}
        aria-expanded={snap !== "peek"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClick={onClick}
      >
        <span className="bottom-sheet__grip" />
      </button>
      <p className="visually-hidden" role="status" aria-live="polite">
        Results panel {SNAP_LABEL[snap]}
      </p>
      <div className="bottom-sheet__content">{children}</div>
    </section>
  );
}
