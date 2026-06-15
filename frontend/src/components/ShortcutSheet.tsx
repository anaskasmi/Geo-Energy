import { useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";

import { SHORTCUTS } from "../hooks/useKeyboardShortcuts";

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Keyboard-shortcut help sheet (GEO-30), toggled with "?". A modal dialog: focus moves in on open,
 * Tab is trapped inside, the rest of the app is marked `inert` (so neither pointer nor keyboard
 * reaches it), Escape / backdrop / close button dismiss it, and focus returns to the opener.
 */
export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const openerRef = useRef<Element | null>(null);

  useEffect(() => {
    const shell = document.querySelector(".shell");
    if (open) {
      openerRef.current = document.activeElement;
      closeRef.current?.focus();
      shell?.setAttribute("inert", "");
      shell?.setAttribute("aria-hidden", "true");
    } else if (openerRef.current instanceof HTMLElement) {
      openerRef.current.focus();
      openerRef.current = null;
    }
    return () => {
      const s = document.querySelector(".shell");
      s?.removeAttribute("inert");
      s?.removeAttribute("aria-hidden");
    };
  }, [open]);

  if (!open) return null;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    // Escape is handled globally (useKeyboardShortcuts) while a dialog is open, so it can't also
    // leak to the selection-clear. Here we only trap Tab inside the dialog.
    if (e.key !== "Tab") return;
    const focusables = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!focusables || focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="modal__head">
          <h2 className="modal__title">Keyboard shortcuts</h2>
          <button ref={closeRef} type="button" className="modal__close" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        <dl className="shortcut-list">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="shortcut-list__row">
              <dt>
                <kbd>{s.keys}</kbd>
              </dt>
              <dd>{s.action}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}
