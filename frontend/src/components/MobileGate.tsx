import { useState } from "react";
import { Monitor, Smartphone } from "lucide-react";

import { useIsMobile } from "../hooks/useBreakpoint";
import { Icon } from "./Icon";

/**
 * Phone gate. This is a map-first site-selection tool that leans on three resizable panels and
 * fine-grained drawing — it needs real screen estate, so on small touch devices we ask the user
 * to switch to a desktop/laptop rather than ship a cramped experience. A "Continue anyway" escape
 * hatch keeps the underlying (responsive) mobile layout reachable for anyone who insists.
 *
 * Wraps the app: renders the gate when `useIsMobile()` matches and the user hasn't dismissed it,
 * otherwise renders its children unchanged.
 */
export function MobileGate({ children }: { children: React.ReactNode }) {
  const isMobile = useIsMobile();
  const [dismissed, setDismissed] = useState(false);

  if (!isMobile || dismissed) return <>{children}</>;

  return (
    <div className="mobile-gate" role="dialog" aria-modal="true" aria-labelledby="mobile-gate-title">
      <div className="mobile-gate__card">
        <div className="mobile-gate__icons" aria-hidden>
          <Icon icon={Smartphone} size={28} className="mobile-gate__icon-from" />
          <span className="mobile-gate__arrow">→</span>
          <Icon icon={Monitor} size={32} className="mobile-gate__icon-to" />
        </div>
        <h1 id="mobile-gate-title" className="mobile-gate__title">
          Best viewed on desktop
        </h1>
        <p className="mobile-gate__body">
          This is a map-first site-selection tool with side-by-side drawing, scoring, and assistant
          panels. For the full experience, please open it on a desktop or laptop.
        </p>
        <button type="button" className="mobile-gate__continue" onClick={() => setDismissed(true)}>
          Continue anyway
        </button>
      </div>
    </div>
  );
}
