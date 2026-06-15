import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

import { useIsDesktop } from "../hooks/useBreakpoint";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { MapView } from "../map/MapView";
import { useScoring } from "../results/useScoring";
import { useUrlState } from "../state/useUrlState";
import { BottomSheet } from "./BottomSheet";
import { Coachmarks, hasSeenTour } from "./Coachmarks";
import { DrawFab } from "./DrawFab";
import { DrawToolbar } from "./DrawToolbar";
import { ResultsPanel } from "./ResultsPanel";
import { ShortcutSheet } from "./ShortcutSheet";
import { Sidebar } from "./Sidebar";
import { ThemeToggle } from "./ThemeToggle";

const PANELS_LS_KEY = "geo.panels.v1";
const LEFT_MIN = 240;
const LEFT_MAX = 520;
const RIGHT_MIN = 280;
const RIGHT_MAX = 560;
const KEY_STEP = 16;
const MIN_MAP_PX = 320; // keep the side panels from crowding the map down to nothing

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** The widest the named panel may be without leaving the map < MIN_MAP_PX (viewport-aware). */
function maxFor(side: "left" | "right", other: number): number {
  const [lo, hi] = side === "left" ? [LEFT_MIN, LEFT_MAX] : [RIGHT_MIN, RIGHT_MAX];
  const viewportMax = (typeof window !== "undefined" ? window.innerWidth : hi + other + MIN_MAP_PX) - other - MIN_MAP_PX;
  return Math.max(lo, Math.min(hi, viewportMax));
}

function loadWidths(): { left: number; right: number } {
  let left = 320;
  let right = 360;
  try {
    const raw = JSON.parse(localStorage.getItem(PANELS_LS_KEY) ?? "null");
    if (raw && typeof raw.left === "number" && typeof raw.right === "number") {
      left = raw.left;
      right = raw.right;
    }
  } catch {
    /* ignore malformed persisted state */
  }
  left = clamp(left, LEFT_MIN, LEFT_MAX);
  right = clamp(right, RIGHT_MIN, RIGHT_MAX);
  // If a previously-saved width is too wide for the current (narrower) window, shrink both so the
  // map keeps at least MIN_MAP_PX.
  if (typeof window !== "undefined" && left + right > window.innerWidth - MIN_MAP_PX) {
    const avail = Math.max(LEFT_MIN + RIGHT_MIN, window.innerWidth - MIN_MAP_PX);
    const scale = avail / (left + right);
    left = clamp(Math.floor(left * scale), LEFT_MIN, LEFT_MAX);
    right = clamp(Math.floor(right * scale), RIGHT_MIN, RIGHT_MAX);
  }
  return { left, right };
}

/**
 * Responsive application shell.
 *
 * - Desktop (>= 768px): 3-pane CSS grid — left controls / center map / right results — with
 *   draggable, keyboard-resizable, localStorage-persisted side panels (GEO-30).
 * - Mobile (< 768px): full-screen map with a draggable bottom-sheet for controls/results.
 *
 * Desktop keyboard shortcuts + the "?" help sheet are mounted here (GEO-30). The drawing toolbar
 * (GEO-23) floats over the map pane in both layouts.
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

  const [widths, setWidths] = useState(loadWidths);
  const drag = useRef<{ side: "left" | "right"; startX: number; startW: number } | null>(null);

  const persist = useCallback((next: { left: number; right: number }) => {
    try {
      localStorage.setItem(PANELS_LS_KEY, JSON.stringify(next));
    } catch {
      /* storage may be unavailable (private mode) — resizing still works for the session */
    }
  }, []);

  const onHandleDown = (side: "left" | "right") => (e: PointerEvent<HTMLDivElement>) => {
    drag.current = { side, startX: e.clientX, startW: side === "left" ? widths.left : widths.right };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none"; // dragging the handle must not select panel text
  };
  const onHandleMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    setWidths((prev) =>
      d.side === "left"
        ? { ...prev, left: clamp(d.startW + dx, LEFT_MIN, maxFor("left", prev.right)) }
        : { ...prev, right: clamp(d.startW - dx, RIGHT_MIN, maxFor("right", prev.left)) },
    );
  };
  const onHandleUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    document.body.style.userSelect = "";
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setWidths((w) => {
      persist(w);
      return w;
    });
  };
  const onHandleKey = (side: "left" | "right") => (e: KeyboardEvent<HTMLDivElement>) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (dir === 0) return;
    e.preventDefault();
    setWidths((prev) => {
      const next =
        side === "left"
          ? { ...prev, left: clamp(prev.left + dir * KEY_STEP, LEFT_MIN, maxFor("left", prev.right)) }
          : { ...prev, right: clamp(prev.right - dir * KEY_STEP, RIGHT_MIN, maxFor("right", prev.left)) };
      persist(next);
      return next;
    });
  };

  if (isDesktop) {
    const style: CSSProperties = {
      gridTemplateColumns: `${widths.left}px 6px minmax(0, 1fr) 6px ${widths.right}px`,
    };
    return (
      <>
        <div className="shell shell--desktop" style={style}>
          <aside className="pane pane--left">
            <Sidebar onStartTour={startTour} />
          </aside>
          <div
            className="resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize controls panel"
            aria-valuenow={Math.round(widths.left)}
            aria-valuemin={LEFT_MIN}
            aria-valuemax={LEFT_MAX}
            tabIndex={0}
            onPointerDown={onHandleDown("left")}
            onPointerMove={onHandleMove}
            onPointerUp={onHandleUp}
            onPointerCancel={onHandleUp}
            onKeyDown={onHandleKey("left")}
          />
          <main className="pane pane--map">
            <MapView />
            <DrawToolbar />
          </main>
          <div
            className="resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize results panel"
            aria-valuenow={Math.round(widths.right)}
            aria-valuemin={RIGHT_MIN}
            aria-valuemax={RIGHT_MAX}
            tabIndex={0}
            onPointerDown={onHandleDown("right")}
            onPointerMove={onHandleMove}
            onPointerUp={onHandleUp}
            onPointerCancel={onHandleUp}
            onKeyDown={onHandleKey("right")}
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
        <DrawToolbar />
      </main>
      <div className="floating-toolbar">
        <ThemeToggle />
      </div>
      <BottomSheet>
        <Sidebar onStartTour={startTour} />
        <ResultsPanel />
      </BottomSheet>
      <DrawFab />
      <Coachmarks open={tourOpen} onClose={closeTour} />
    </div>
  );
}
