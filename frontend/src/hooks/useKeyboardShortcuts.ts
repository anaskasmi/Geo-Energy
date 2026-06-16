import { useEffect, useRef } from "react";

import { useMapStore } from "../map/useMapStore";

/** A shortcut row for the help sheet (GEO-30). */
export interface ShortcutHelp {
  keys: string;
  action: string;
}

export const SHORTCUTS: ShortcutHelp[] = [
  { keys: "D", action: "Draw a search area" },
  { keys: "E", action: "Edit the drawn area" },
  { keys: "Esc", action: "Cancel drawing / clear selection" },
  { keys: "Enter / double-click", action: "Finish the polygon" },
  { keys: "↑ / ↓", action: "Move selection through results" },
  { keys: "1 / 2", action: "Utility solar / Data center" },
  { keys: "Ctrl+Z / Ctrl+Y", action: "Undo / redo a vertex" },
  { keys: "Right-click map", action: "Context menu (draw / center / copy)" },
  { keys: "?", action: "Show / hide this help" },
];

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable;
}

/**
 * Global desktop keyboard shortcuts (GEO-30). Mounted once (desktop shell). Reads the latest
 * store via a ref so the window listener is bound only once. Ignores keystrokes while the user is
 * typing in a field (except Escape), and never hijacks browser/OS chords (Ctrl/Cmd/Alt) so
 * Ctrl+Z/Y still reach terra-draw's own undo/redo.
 */
export function useKeyboardShortcuts(onToggleHelp: () => void): void {
  const store = useMapStore();
  const storeRef = useRef(store);
  storeRef.current = store;
  const helpRef = useRef(onToggleHelp);
  helpRef.current = onToggleHelp;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const s = storeRef.current;

      // An open modal dialog (the shortcut sheet) owns the keyboard — don't let Escape/keys leak
      // to the global handler (which would clear the selection while the sheet just closes). Only
      // the "?" toggle still passes through, so it can also dismiss the sheet.
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) {
        // The dialog owns the keyboard. "?" or Escape toggle it closed; every other key is
        // swallowed so it can't leak to the global shortcuts (e.g. Escape clearing the selection).
        // Escape is handled HERE (not in the dialog) because React flushes a dialog-close
        // synchronously before this window listener runs, which would otherwise defeat this guard.
        if (event.key === "?" || event.key === "Escape") {
          event.preventDefault();
          helpRef.current();
        }
        return;
      }

      // Escape always works (even from a focused control): cancel drawing, else clear selection.
      if (event.key === "Escape") {
        if (s.drawMode !== "idle") {
          event.preventDefault();
          s.setDrawMode("idle");
        } else if (s.selected) {
          s.setSelected(null);
        }
        return;
      }
      // Leave typing + modifier chords (undo/redo, browser shortcuts) alone.
      if (isTypingTarget(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;

      switch (event.key) {
        case "d":
        case "D":
          event.preventDefault();
          s.setDrawMode(s.drawMode === "draw" ? "idle" : "draw");
          break;
        case "e":
        case "E":
          event.preventDefault();
          s.setDrawMode(s.drawMode === "edit" ? "idle" : "edit");
          break;
        case "1":
          event.preventDefault();
          s.setUseCase("utility_solar");
          break;
        case "2":
          event.preventDefault();
          s.setUseCase("data_center");
          break;
        case "?":
          event.preventDefault();
          helpRef.current();
          break;
        case "ArrowDown":
        case "ArrowUp": {
          const feats = s.scoreResult?.features ?? [];
          if (feats.length === 0) return;
          // Navigate the ORDER SHOWN (the sorted + filtered list) using the order published by
          // ResultsPanel — robust even when the list is replaced by the single-parcel detail view.
          // Fall back to the raw scored order if nothing has been published yet.
          const shown = s.getResultOrder().map(String);
          const order = shown.length ? shown : feats.map((f) => String(f.properties.id));
          if (order.length === 0) return;
          event.preventDefault();
          const cur = order.indexOf(String(s.selected?.id));
          const nextId =
            cur === -1
              ? order[0]
              : event.key === "ArrowDown"
                ? order[Math.min(order.length - 1, cur + 1)]
                : order[Math.max(0, cur - 1)];
          const f = feats.find((x) => String(x.properties.id) === nextId);
          if (!f) return;
          s.setSelected({ id: f.properties.id, apn: f.properties.apn, acres: f.properties.acres });
          if (f.properties.centroid) s.flyTo(f.properties.centroid);
          break;
        }
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
