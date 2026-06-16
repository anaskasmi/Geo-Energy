import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { GripVertical } from "lucide-react";

import { DrawToolbar } from "./DrawToolbar";
import { Icon } from "./Icon";
import { MapControls } from "./MapControls";

const LS_KEY = "geo.topbar.pos.v1";
const MARGIN = 8; // keep this much gap between the floating bar and the map edges
const KEY_STEP = 16; // arrow-key nudge distance

interface Pos {
  x: number;
  y: number;
}

function loadPos(): Pos | null {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) ?? "null");
    if (raw && typeof raw.x === "number" && typeof raw.y === "number") return raw;
  } catch {
    /* ignore malformed persisted state */
  }
  return null;
}

/**
 * Floating top bar over the map. Left: drawing tools. Right: single-purpose control popovers.
 *
 * Draggable (GEO-26+): by default it's docked full-width at the top; grab the grip to DETACH it
 * into a content-width floating bar you can drop anywhere on the map. Position is clamped to the
 * map pane and persisted to localStorage. Double-click the grip (or press Esc while it's focused)
 * to re-dock; arrow keys nudge it for keyboard users. Only the grip starts a drag, so every button
 * stays clickable and the map underneath is untouched.
 */
export function TopBar({
  onStartTour,
  onToggleAgent,
  agentOpen,
}: {
  onStartTour?: () => void;
  onToggleAgent?: () => void;
  agentOpen?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<Pos | null>(loadPos);
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);

  const persist = useCallback((p: Pos | null) => {
    try {
      if (p) localStorage.setItem(LS_KEY, JSON.stringify(p));
      else localStorage.removeItem(LS_KEY);
    } catch {
      /* storage unavailable (private mode) — dragging still works for the session */
    }
  }, []);

  // Keep (x, y) inside the map pane (the toolbar's offset parent), leaving a small margin.
  const clamp = useCallback((x: number, y: number): Pos => {
    const el = ref.current;
    const pane = el?.offsetParent as HTMLElement | null;
    if (!el || !pane) return { x, y };
    const maxX = Math.max(MARGIN, pane.clientWidth - el.offsetWidth - MARGIN);
    const maxY = Math.max(MARGIN, pane.clientHeight - el.offsetHeight - MARGIN);
    return { x: Math.min(maxX, Math.max(MARGIN, x)), y: Math.min(maxY, Math.max(MARGIN, y)) };
  }, []);

  const onPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const el = ref.current;
    const pane = el?.offsetParent as HTMLElement | null;
    if (!el || !pane) return;
    const rect = el.getBoundingClientRect();
    const paneRect = pane.getBoundingClientRect();
    // Detach at the current visual position so the bar doesn't jump when it shrinks to content width.
    setPos(clamp(rect.left - paneRect.left, rect.top - paneRect.top));
    dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.style.userSelect = "none";
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!dragRef.current) return;
    const el = ref.current;
    const pane = el?.offsetParent as HTMLElement | null;
    if (!el || !pane) return;
    const paneRect = pane.getBoundingClientRect();
    setPos(clamp(e.clientX - paneRect.left - dragRef.current.dx, e.clientY - paneRect.top - dragRef.current.dy));
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    document.body.style.userSelect = "";
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setPos((p) => {
      persist(p);
      return p;
    });
  };

  const redock = useCallback(() => {
    setPos(null);
    persist(null);
  }, [persist]);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Escape" || e.key === "Delete" || e.key === "Backspace") {
      if (pos) {
        e.preventDefault();
        redock();
      }
      return;
    }
    const deltas: Record<string, [number, number]> = {
      ArrowUp: [0, -KEY_STEP],
      ArrowDown: [0, KEY_STEP],
      ArrowLeft: [-KEY_STEP, 0],
      ArrowRight: [KEY_STEP, 0],
    };
    const d = deltas[e.key];
    if (!d) return;
    e.preventDefault();
    const el = ref.current;
    const pane = el?.offsetParent as HTMLElement | null;
    if (!el || !pane) return;
    const rect = el.getBoundingClientRect();
    const paneRect = pane.getBoundingClientRect();
    const base = pos ?? { x: rect.left - paneRect.left, y: rect.top - paneRect.top };
    const next = clamp(base.x + d[0], base.y + d[1]);
    setPos(next);
    persist(next);
  };

  // Pull a persisted/edge-hugging position back into view when the viewport (pane) resizes.
  useEffect(() => {
    if (!pos) return;
    const onResize = () => setPos((p) => (p ? clamp(p.x, p.y) : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [pos, clamp]);

  const style: CSSProperties | undefined = pos
    ? { left: `${pos.x}px`, top: `${pos.y}px`, right: "auto" }
    : undefined;

  return (
    <div
      ref={ref}
      className={pos ? "topbar overlay-panel topbar--floating" : "topbar overlay-panel"}
      style={style}
    >
      <button
        type="button"
        className="topbar__grip"
        aria-label={pos ? "Move toolbar — double-click or Esc to re-dock" : "Move toolbar"}
        title={pos ? "Drag to move · double-click to re-dock" : "Drag to move"}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={redock}
        onKeyDown={onKeyDown}
      >
        <Icon icon={GripVertical} size={16} />
      </button>
      <DrawToolbar />
      <MapControls onStartTour={onStartTour} onToggleAgent={onToggleAgent} agentOpen={agentOpen} />
    </div>
  );
}
