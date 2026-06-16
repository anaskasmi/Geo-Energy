import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";

import { AgentPanel } from "../agent/AgentPanel";
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
const AGENT_LS_KEY = "geo.agentpanel.v1";
const RIGHT_MIN = 300;
const RIGHT_MAX = 560;
const LEFT_MIN = 300;
const LEFT_MAX = 600;
const KEY_STEP = 16;
const MIN_MAP_PX = 360; // keep the side panels from crowding the map down to nothing

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** Widest the results panel may be while leaving the map ≥ MIN_MAP_PX (viewport-aware). */
function maxRight(): number {
  const viewport = typeof window !== "undefined" ? window.innerWidth : RIGHT_MAX + MIN_MAP_PX;
  return Math.max(RIGHT_MIN, Math.min(RIGHT_MAX, viewport - MIN_MAP_PX));
}

/** Widest the agent panel may be, accounting for the (right) results panel + a minimum map. */
function maxLeft(rightWidth: number): number {
  const viewport = typeof window !== "undefined" ? window.innerWidth : LEFT_MAX + RIGHT_MAX + MIN_MAP_PX;
  return Math.max(LEFT_MIN, Math.min(LEFT_MAX, viewport - rightWidth - MIN_MAP_PX - 12));
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

function loadAgent(): { width: number; open: boolean } {
  let width = 380;
  let open = true;
  try {
    const raw = JSON.parse(localStorage.getItem(AGENT_LS_KEY) ?? "null");
    if (raw) {
      if (typeof raw.width === "number") width = raw.width;
      if (typeof raw.open === "boolean") open = raw.open;
    }
  } catch {
    /* ignore malformed persisted state */
  }
  return { width: clamp(width, LEFT_MIN, LEFT_MAX), open };
}

/**
 * Responsive application shell — map-first (design philosophy: "separate things, keep each
 * minimal"). The drawing tools + control popovers float on the map in the TopBar; the Assistant now
 * lives in a first-class DOCKED LEFT panel (GEO-40), and the Results panel docks right.
 *
 * - Desktop (>= 768px): up to 3 columns — Assistant / map / Results — each with a draggable,
 *   keyboard-resizable, localStorage-persisted divider. The Assistant panel is closable (toolbar
 *   ✨ toggles it; its header ⟨ closes it) and its open state + width persist.
 * - Mobile (< 768px): full-screen map with a draggable bottom sheet (Results); the Assistant opens
 *   as a full-screen overlay.
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
  const initialAgent = useRef(loadAgent());
  const [agentOpen, setAgentOpen] = useState(initialAgent.current.open);
  const [leftWidth, setLeftWidth] = useState(initialAgent.current.width);
  const drag = useRef<{ startX: number; startW: number } | null>(null);
  const leftDrag = useRef<{ startX: number; startW: number } | null>(null);

  const persist = useCallback((right: number) => {
    try {
      localStorage.setItem(PANEL_LS_KEY, JSON.stringify({ right }));
    } catch {
      /* storage may be unavailable (private mode) — resizing still works for the session */
    }
  }, []);
  const persistAgent = useCallback((open: boolean, width: number) => {
    try {
      localStorage.setItem(AGENT_LS_KEY, JSON.stringify({ open, width }));
    } catch {
      /* storage may be unavailable — toggling still works for the session */
    }
  }, []);

  const toggleAgent = useCallback(() => {
    setAgentOpen((o) => {
      const next = !o;
      persistAgent(next, leftWidth);
      return next;
    });
  }, [leftWidth, persistAgent]);
  const closeAgent = useCallback(() => {
    setAgentOpen(false);
    persistAgent(false, leftWidth);
  }, [leftWidth, persistAgent]);

  // ── Right (results) divider ──
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

  // ── Left (assistant) divider — dragging right widens it ──
  const onLeftDown = (e: PointerEvent<HTMLDivElement>) => {
    leftDrag.current = { startX: e.clientX, startW: leftWidth };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
  };
  const onLeftMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!leftDrag.current) return;
    const dx = e.clientX - leftDrag.current.startX;
    setLeftWidth(clamp(leftDrag.current.startW + dx, LEFT_MIN, maxLeft(rightWidth)));
  };
  const onLeftUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!leftDrag.current) return;
    leftDrag.current = null;
    document.body.style.userSelect = "";
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setLeftWidth((w) => {
      persistAgent(true, w);
      return w;
    });
  };
  const onLeftKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (dir === 0) return;
    e.preventDefault();
    setLeftWidth((prev) => {
      const next = clamp(prev + dir * KEY_STEP, LEFT_MIN, maxLeft(rightWidth));
      persistAgent(true, next);
      return next;
    });
  };

  if (isDesktop) {
    const showAgent = agentOpen;
    const style: CSSProperties = {
      gridTemplateColumns: showAgent
        ? `${leftWidth}px 6px minmax(0, 1fr) 6px ${rightWidth}px`
        : `minmax(0, 1fr) 6px ${rightWidth}px`,
    };
    return (
      <>
        <div className="shell shell--desktop" style={style}>
          {showAgent && (
            <>
              <aside className="pane pane--left">
                <AgentPanel onClose={closeAgent} />
              </aside>
              <div
                className="resize-handle"
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize assistant panel"
                aria-valuenow={Math.round(leftWidth)}
                aria-valuemin={LEFT_MIN}
                aria-valuemax={LEFT_MAX}
                tabIndex={0}
                onPointerDown={onLeftDown}
                onPointerMove={onLeftMove}
                onPointerUp={onLeftUp}
                onPointerCancel={onLeftUp}
                onKeyDown={onLeftKey}
              />
            </>
          )}
          <main className="pane pane--map">
            <MapView />
            <TopBar onStartTour={startTour} onToggleAgent={toggleAgent} agentOpen={agentOpen} />
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
        <TopBar onStartTour={startTour} onToggleAgent={toggleAgent} agentOpen={agentOpen} />
      </main>
      <BottomSheet>
        <ResultsPanel />
      </BottomSheet>
      {agentOpen && (
        <div className="agent-overlay" role="dialog" aria-label="Assistant">
          <AgentPanel onClose={closeAgent} />
        </div>
      )}
      <Coachmarks open={tourOpen} onClose={closeTour} />
    </div>
  );
}
