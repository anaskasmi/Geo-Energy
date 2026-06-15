import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

import { useIsDesktop } from "../hooks/useBreakpoint";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { MapView } from "../map/MapView";
import { useScoring } from "../results/useScoring";
import { useUrlState } from "../state/useUrlState";
import { BottomSheet } from "./BottomSheet";
import { Coachmarks, hasSeenTour } from "./Coachmarks";
import { ResultsPanel } from "./ResultsPanel";
import { ShortcutSheet } from "./ShortcutSheet";
import { TopBar } from "./TopBar";

const PANEL_LS_KEY = "geo.panel.v1";
const RIGHT_MIN = 300;
const RIGHT_MAX = 560;
const KEY_STEP = 16;
const MIN_MAP_PX = 360; // keep the results panel from crowding the map down to nothing

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** Widest the results panel may be while leaving the map ≥ MIN_MAP_PX (viewport-aware). */
function maxRight(): number {
  const viewport = typeof window !== "undefined" ? window.innerWidth : RIGHT_MAX + MIN_MAP_PX;
  return Math.max(RIGHT_MIN, Math.min(RIGHT_MAX, viewport - MIN_MAP_PX));
}

function loadRight(): number {
  let right = 360;
  try {
    const raw = JSON.parse(localStorage.getItem(PANEL_LS_KEY) ?? "null");
    if (raw && typeof raw.right === "number") right = raw.right;
  } catch {
    /* ignore malformed persisted state */
  }
  return clamp(right, RIGHT_MIN, maxRight());
}

/**
 * Responsive application shell — map-first (design philosophy: "separate things, keep each
 * minimal"). Every control lives on the map in the floating TopBar (drawing tools + single-purpose
 * popovers); the only docked surface is the Results panel.
 *
 * - Desktop (>= 768px): 2-pane CSS grid — map / results — with a draggable, keyboard-resizable,
 *   localStorage-persisted results panel.
 * - Mobile (< 768px): full-screen map with a draggable bottom sheet that shows the results only.
 */
export function AppShell() {
  const isDesktop = useIsDesktop();
  useScoring(); // debounced score of the drawn polygon (GEO-24); mounted once here
  useUrlState(); // hydrate from + mirror to the URL hash (GEO-31); mounted once here
  const [helpOpen, setHelpOpen] = useState(false);
  useKeyboardShortcuts(useCallback(() => setHelpOpen((o) => !o), []));

  // First-run onboarding (GEO-32): open the tour once, after the shell has painted.
  const [tourOpen, setTourOpen] = useState(false);
  useEffect(() => {
    if (!hasSeenTour()) {
      const timer = window.setTimeout(() => setTourOpen(true), 600);
      return () => window.clearTimeout(timer);
    }
  }, []);
  const startTour = useCallback(() => setTourOpen(true), []);
  const closeTour = useCallback(() => setTourOpen(false), []);

  const [rightWidth, setRightWidth] = useState(loadRight);
  const drag = useRef<{ startX: number; startW: number } | null>(null);

  const persist = useCallback((right: number) => {
    try {
      localStorage.setItem(PANEL_LS_KEY, JSON.stringify({ right }));
    } catch {
      /* storage may be unavailable (private mode) — resizing still works for the session */
    }
  }, []);

  const onHandleDown = (e: PointerEvent<HTMLDivElement>) => {
    drag.current = { startX: e.clientX, startW: rightWidth };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none"; // dragging the handle must not select panel text
  };
  const onHandleMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.startX;
    setRightWidth(clamp(drag.current.startW - dx, RIGHT_MIN, maxRight()));
  };
  const onHandleUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    document.body.style.userSelect = "";
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setRightWidth((w) => {
      persist(w);
      return w;
    });
  };
  const onHandleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (dir === 0) return;
    e.preventDefault();
    setRightWidth((prev) => {
      const next = clamp(prev - dir * KEY_STEP, RIGHT_MIN, maxRight());
      persist(next);
      return next;
    });
  };

  if (isDesktop) {
    const style: CSSProperties = {
      gridTemplateColumns: `minmax(0, 1fr) 6px ${rightWidth}px`,
    };
    return (
      <>
        <div className="shell shell--desktop" style={style}>
          <main className="pane pane--map">
            <MapView />
            <TopBar onStartTour={startTour} />
          </main>
          <div
            className="resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize results panel"
            aria-valuenow={Math.round(rightWidth)}
            aria-valuemin={RIGHT_MIN}
            aria-valuemax={RIGHT_MAX}
            tabIndex={0}
            onPointerDown={onHandleDown}
            onPointerMove={onHandleMove}
            onPointerUp={onHandleUp}
            onPointerCancel={onHandleUp}
            onKeyDown={onHandleKey}
          />
          <aside className="pane pane--right">
            <ResultsPanel />
          </aside>
        </div>
        <ShortcutSheet open={helpOpen} onClose={() => setHelpOpen(false)} />
        <Coachmarks open={tourOpen} onClose={closeTour} />
      </>
    );
  }

  return (
    <div className="shell shell--mobile">
      <main className="pane pane--map">
        <MapView />
        <TopBar onStartTour={startTour} />
      </main>
      <BottomSheet>
        <ResultsPanel />
      </BottomSheet>
      <Coachmarks open={tourOpen} onClose={closeTour} />
    </div>
  );
}
