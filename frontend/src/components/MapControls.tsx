import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { Compass, Gauge, Layers, Settings, Share2, Sparkles, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { AgentChat } from "./AgentChat";
import { BasemapControl } from "./BasemapControl";
import { Icon } from "./Icon";
import { LayerControl } from "./LayerControl";
import { Legend } from "./Legend";
import { ScoringControl } from "./ScoringControl";
import { ShareControl } from "./ShareControl";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Floating map controls (design philosophy: "separate things, keep each minimal"). A row of
 * single-purpose icon buttons sits on the right of the full-width top bar; each opens ONE minimal
 * popover for its concern — Assistant, Scoring, Layers, Share & save, Settings (basemap + help) —
 * plus a compact light/dark theme toggle. Only one panel is open at a time.
 *
 * Each popover is a non-blocking `role="dialog"` (the map stays interactive behind it): Esc and the
 * close button dismiss it AND return focus to the trigger; a click outside dismisses it without
 * stealing focus. Opening focuses the panel for keyboard / screen-reader users.
 */
type PanelId = "assistant" | "scoring" | "layers" | "share" | "settings";

interface ControlDef {
  id: PanelId;
  label: string;
  icon: LucideIcon;
}

const CONTROLS: ControlDef[] = [
  { id: "assistant", label: "Assistant", icon: Sparkles },
  { id: "scoring", label: "Scoring", icon: Gauge },
  { id: "layers", label: "Layers", icon: Layers },
  { id: "share", label: "Share & save", icon: Share2 },
  { id: "settings", label: "Settings", icon: Settings },
];

export function MapControls({ onStartTour }: { onStartTour?: () => void }) {
  const [open, setOpen] = useState<PanelId | null>(null);
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
        <MapPanel key={active.id} title={active.label} icon={active.icon} onClose={() => close(true)}>
          {active.id === "assistant" && <AgentChat />}
          {active.id === "scoring" && <ScoringControl />}
          {active.id === "layers" && (
            <div className="map-panel__stack">
              <LayerControl />
              <Legend />
            </div>
          )}
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
  onClose,
  children,
}: {
  title: string;
  icon: LucideIcon;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div className="map-panel overlay-panel" role="dialog" aria-label={title} tabIndex={-1} ref={ref}>
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
