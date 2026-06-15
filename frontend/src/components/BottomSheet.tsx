import { useCallback, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

type Snap = "peek" | "half" | "full";

/** Sheet height (as a fraction of viewport height) at each snap point. */
const SNAP_HEIGHT_VH: Record<Snap, number> = {
  peek: 14,
  half: 48,
  full: 90,
};

/** Drag distance (px) past which we move to the next snap on release. */
const SNAP_THRESHOLD_PX = 48;

const ORDER: Snap[] = ["peek", "half", "full"];

function nextSnap(current: Snap, direction: "up" | "down"): Snap {
  const idx = ORDER.indexOf(current);
  const nextIdx = direction === "up" ? idx + 1 : idx - 1;
  return ORDER[Math.min(ORDER.length - 1, Math.max(0, nextIdx))];
}

/**
 * Mobile draggable/expandable bottom-sheet skeleton. Three snap points (peek/half/full).
 * Drag the handle to resize; release snaps to the nearest sensible point. This is a
 * scaffold for the mobile results/controls surface (full content lands with scoring).
 */
export function BottomSheet({ children }: { children: ReactNode }) {
  const [snap, setSnap] = useState<Snap>("half");
  const [dragDelta, setDragDelta] = useState(0);
  const dragging = useRef(false);
  const startY = useRef(0);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    startY.current = event.clientY;
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    setDragDelta(event.clientY - startY.current);
  }, []);

  const endDrag = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
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
    },
    [],
  );

  // While dragging, follow the finger downward (clamped); upward expansion is handled by
  // the snap on release so the sheet never detaches from the bottom.
  const liveOffset = dragging.current ? Math.max(0, dragDelta) : 0;

  return (
    <section
      className="bottom-sheet"
      data-snap={snap}
      data-dragging={dragging.current || undefined}
      style={{
        height: `${SNAP_HEIGHT_VH[snap]}vh`,
        transform: liveOffset ? `translateY(${liveOffset}px)` : undefined,
      }}
      aria-label="Results panel"
    >
      <div
        className="bottom-sheet__handle"
        role="button"
        tabIndex={0}
        aria-label="Drag to resize results panel"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <span className="bottom-sheet__grip" />
      </div>
      <div className="bottom-sheet__content">{children}</div>
    </section>
  );
}
