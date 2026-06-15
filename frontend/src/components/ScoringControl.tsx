import { useRef } from "react";
import type { KeyboardEvent } from "react";

import type { UseCase } from "../api/client";
import { useMapStore } from "../map/useMapStore";

const OPTIONS: { value: UseCase; label: string; hint: string }[] = [
  { value: "utility_solar", label: "Utility solar", hint: "Prioritises solar resource, low slope, and grid proximity." },
  { value: "data_center", label: "Data center", hint: "Prioritises substation capacity and grid proximity." },
];

const STATUS_TEXT: Record<string, string> = {
  idle: "Draw an area on the map to score parcels.",
  scoring: "Scoring…",
  done: "Scored.",
  error: "Scoring failed — see the results panel.",
};

/**
 * Scoring profile selector (GEO-24/25): pick the use case, which re-scores the drawn area with
 * that preset's weights + prohibited zoning. Shown in the left controls.
 */
export function ScoringControl() {
  const { useCase, setUseCase, scoreStatus } = useMapStore();
  const active = OPTIONS.find((o) => o.value === useCase);
  const btnRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Arrow-key navigation per the radiogroup pattern: move + select + focus the sibling.
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number | null = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (index + 1) % OPTIONS.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (index - 1 + OPTIONS.length) % OPTIONS.length;
    if (next === null) return;
    e.preventDefault();
    setUseCase(OPTIONS[next].value);
    btnRefs.current[next]?.focus();
  };

  return (
    <div className="scoring-control">
      <div className="segmented" role="radiogroup" aria-label="Scoring use case">
        {OPTIONS.map((o, i) => (
          <button
            key={o.value}
            ref={(el) => {
              btnRefs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={useCase === o.value}
            // Roving tabindex: only the checked radio is in the tab order; arrows move within.
            tabIndex={useCase === o.value ? 0 : -1}
            className={useCase === o.value ? "segmented__btn segmented__btn--active" : "segmented__btn"}
            onClick={() => setUseCase(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {o.label}
          </button>
        ))}
      </div>
      <p className="scoring-control__hint">{active?.hint}</p>
      <p className="scoring-control__status" aria-live="polite" data-status={scoreStatus}>
        {STATUS_TEXT[scoreStatus]}
      </p>
    </div>
  );
}
