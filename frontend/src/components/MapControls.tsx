import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Compass, Gauge, Layers, Settings, Share2, Sparkles, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { BasemapControl } from "./BasemapControl";
import { Icon } from "./Icon";
import { LayerControl } from "./LayerControl";
import { ScoringControl } from "./ScoringControl";
import { ShareControl } from "./ShareControl";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Floating map controls (design philosophy: "separate things, keep each minimal"). A row of
 * single-purpose icon buttons sits on the right of the top bar. The Assistant button TOGGLES the
 * docked left Agent panel (GEO-40); the rest each open ONE minimal popover for their concern —
 * Scoring, Layers, Share & save, Settings (basemap + help) — plus a compact light/dark theme toggle.
 * Only one popover is open at a time.
 *
 * Edge-aware popovers (GEO-40): the bar is draggable, so a popover that always dropped down-right
 * would spill off-screen once the bar is near the bottom or a side. Placement is measured against the
 * viewport when the popover opens (and on resize) — it flips UP when there's no room below, and
 * aligns to whichever horizontal side keeps it on-screen.
 *
 * Each popover is a non-blocking `role="dialog"` (the map stays interactive behind it): Esc and the
 * close button dismiss it AND return focus to the trigger; a click outside dismisses it without
 * stealing focus. Opening focuses the panel for keyboard / screen-reader users.
 */
type PanelId = "scoring" | "layers" | "share" | "settings";

interface ControlDef {
  id: PanelId;
  label: string;
  icon: LucideIcon;
}

const CONTROLS: ControlDef[] = [
  { id: "scoring", label: "Scoring", icon: Gauge },
  { id: "layers", label: "Layers", icon: Layers },
  { id: "share", label: "Share & save", icon: Share2 },
  { id: "settings", label: "Settings", icon: Settings },
];

/** Popover width cap — kept in sync with the `.map-panel` CSS so placement math is accurate. */
const PANEL_W = 360;
const EDGE_MARGIN = 8;

interface Placement {
  v: "down" | "up";
  h: "right" | "left";
}

export function MapControls({
  onStartTour,
  onToggleAgent,
  agentOpen = false,
}: {
  onStartTour?: () => void;
  onToggleAgent?: () => void;
  agentOpen?: boolean;
}) {
  const [open, setOpen] = useState<PanelId | null>(null);
  const [placement, setPlacement] = useState<Placement>({ v: "down", h: "right" });
  const rootRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);

  const close = useCallback((returnFocus = false) => {
    setOpen(null);
    if (returnFocus) openerRef.current?.focus();
    openerRef.current = null;
  }, []);

  const toggle = useCallback((id: PanelId, event: MouseEvent<HTMLButtonElement>) => {
    const trigger = event.currentTarget;
    setOpen((current) => {
      if (current === id) return null;
      openerRef.current = trigger;
      return id;
    });
  }, []);

  // Decide where the popover opens so it stays within the viewport, no matter where the (draggable)
  // bar sits. Measured against the trigger-cluster rect: flip UP when the panel won't fit below, and
  // align to the horizontal side that keeps it on-screen. Runs in a layout effect (before paint) so
  // there's no visible jump from the default down-right placement.
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const root = rootRef.current;
      if (!root) return;
      const r = root.getBoundingClientRect();
      const panel = root.querySelector<HTMLElement>(".map-panel");
      const pw = panel?.offsetWidth ?? PANEL_W;
      const ph = panel?.offsetHeight ?? 360;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      // Horizontal: default right-align (panel's right edge at the cluster's right, growing left).
      // Flip to left-align if growing left would clip the viewport's left edge — but only if
      // left-align actually fits better (avoids clipping the right edge on a narrow viewport).
      let h: "right" | "left" = "right";
      if (r.right - pw < EDGE_MARGIN && r.left + pw <= vw - EDGE_MARGIN) h = "left";
      // Vertical: default down (below the cluster). Flip up if there isn't room below and there's
      // more room above.
      const roomBelow = vh - r.bottom - EDGE_MARGIN;
      const roomAbove = r.top - EDGE_MARGIN;
      const v: "down" | "up" = roomBelow < ph + EDGE_MARGIN && roomAbove > roomBelow ? "up" : "down";
      setPlacement({ v, h });
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [open]);

  // Esc dismisses (and is swallowed so it can't also clear the map selection); a pointer-down
  // anywhere outside the controls+panel dismisses without yanking focus back.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close(true);
      }
    };
    const onDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onDown, true);
    };
  }, [open, close]);

  const active = CONTROLS.find((c) => c.id === open) ?? null;

  return (
    <div className="map-controls" ref={rootRef}>
      <div className="map-controls__buttons">
        {/* Assistant: toggles the docked left Agent panel rather than opening a popover. */}
        <button
          type="button"
          className="map-control-btn"
          aria-label="Assistant"
          title="Assistant"
          aria-pressed={agentOpen}
          onClick={() => onToggleAgent?.()}
        >
          <Icon icon={Sparkles} size={18} />
        </button>
        {CONTROLS.map((c) => (
          <button
            key={c.id}
            type="button"
            className="map-control-btn"
            aria-label={c.label}
            title={c.label}
            aria-haspopup="dialog"
            aria-expanded={open === c.id}
            onClick={(e) => toggle(c.id, e)}
          >
            <Icon icon={c.icon} size={18} />
          </button>
        ))}
        <ThemeToggle />
      </div>

      {active && (
        <MapPanel
          key={active.id}
          title={active.label}
          icon={active.icon}
          placement={placement}
          onClose={() => close(true)}
        >
          {active.id === "scoring" && <ScoringControl />}
          {active.id === "layers" && <LayerControl />}
          {active.id === "share" && <ShareControl />}
          {active.id === "settings" && (
            <div className="map-panel__stack">
              <BasemapControl />
              {onStartTour && (
                <button
                  type="button"
                  className="panel-btn"
                  onClick={() => {
                    onStartTour();
                    close();
                  }}
                >
                  <Icon icon={Compass} size={16} />
                  Take a tour
                </button>
              )}
            </div>
          )}
        </MapPanel>
      )}
    </div>
  );
}

function MapPanel({
  title,
  icon,
  placement,
  onClose,
  children,
}: {
  title: string;
  icon: LucideIcon;
  placement: Placement;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div
      className={`map-panel overlay-panel map-panel--${placement.v} map-panel--${placement.h}`}
      role="dialog"
      aria-label={title}
      tabIndex={-1}
      ref={ref}
    >
      <div className="map-panel__head">
        <h2 className="map-panel__title">
          <Icon icon={icon} size={14} />
          {title}
        </h2>
        <button type="button" className="map-panel__close" aria-label="Close" onClick={onClose}>
          <Icon icon={X} size={16} />
        </button>
      </div>
      <div className="map-panel__body">{children}</div>
    </div>
  );
}
