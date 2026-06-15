import { useEffect, useRef } from "react";

import { scoreColorCss, scoreTextColor } from "../map/layers";
import { useMapStore } from "../map/useMapStore";
import { useExplain } from "../results/hooks";
import { fmtAcres, whyThisRank } from "../results/format";

const EXCLUSION_LABELS: Record<string, string> = {
  min_acres: "Below minimum acreage",
  sfha: "In a flood hazard area (SFHA)",
  slope: "Slope too steep",
  zoning: "Zoning prohibits this use",
  optional: "In an exclusion overlay",
};

/**
 * Selected-parcel detail (GEO-25): the authoritative per-factor breakdown from /api/explain —
 * score, Stage-A exclusions, a weighted-contribution bar per factor with raw values, and a
 * plain-language "why this rank". Falls back to the quick tile attributes if the API is
 * unavailable.
 */
export function ParcelDetail() {
  const { selected, setSelected, useCase } = useMapStore();
  const { data, loading, error } = useExplain(selected?.id ?? null, useCase);
  const placeholderRef = useRef<HTMLParagraphElement | null>(null);
  const clearedRef = useRef(false);

  // After "Clear selection", move focus to the placeholder so keyboard users aren't dropped.
  useEffect(() => {
    if (!selected && clearedRef.current) {
      placeholderRef.current?.focus();
      clearedRef.current = false;
    }
  }, [selected]);

  if (!selected) {
    return (
      <p className="placeholder-text" tabIndex={-1} ref={placeholderRef}>
        Select a parcel — on the map or in the results list — to see its scoring breakdown.
      </p>
    );
  }

  const maxContribution = data ? Math.max(0.01, ...data.factors.map((f) => f.contribution)) : 1;

  return (
    <div className="parcel-detail">
      <div className="parcel-detail__head">
        <div>
          <div className="parcel-detail__apn">{selected.apn ?? `Parcel ${selected.id}`}</div>
          <div className="parcel-detail__sub">
            ID {selected.id} · {fmtAcres(data?.acres ?? selected.acres)}
            {data?.zoning_class ? ` · ${data.zoning_class}` : ""}
          </div>
        </div>
        {data && (
          <div
            className="score-chip score-chip--lg"
            style={{ background: scoreColorCss(data.score), color: scoreTextColor(data.score) }}
          >
            {data.score.toFixed(0)}
          </div>
        )}
      </div>

      {loading && <p className="placeholder-text">Loading breakdown…</p>}
      {error && <p className="error-text">{error}</p>}

      {data && (
        <>
          {data.excluded && (
            <div className="exclusion-banner" role="status">
              <strong>Excluded by Stage A.</strong>
              <ul className="exclusion-list">
                {Object.entries(data.exclusions)
                  .filter(([, on]) => on)
                  .map(([key]) => (
                    <li key={key}>{EXCLUSION_LABELS[key] ?? key}</li>
                  ))}
              </ul>
            </div>
          )}

          <p className="parcel-detail__why">{whyThisRank(data.factors)}</p>

          <ul className="factor-bars">
            {data.factors.map((f) => (
              <li key={f.key} className="factor-bar">
                <div className="factor-bar__head">
                  <span className="factor-bar__label">{f.label}</span>
                  <span className="factor-bar__raw">
                    {f.known ? `${fmtFactorRaw(f.raw)} ${f.unit}` : "unknown"}
                  </span>
                </div>
                <div className="factor-bar__track" aria-hidden="true">
                  <div
                    className="factor-bar__fill"
                    style={{
                      width: `${Math.round(f.normalized * 100)}%`,
                      background: scoreColorCss(f.normalized * 100),
                    }}
                  />
                </div>
                <div className="factor-bar__meta">
                  weight {(f.weight * 100).toFixed(0)}% · +{f.contribution.toFixed(1)} pts
                  {!f.known && " (neutral)"}
                </div>
                <meter
                  className="visually-hidden"
                  min={0}
                  max={maxContribution}
                  value={f.contribution}
                  aria-label={`${f.label} contributes ${f.contribution.toFixed(1)} points`}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      <button
        type="button"
        className="parcel-detail__clear"
        onClick={() => {
          clearedRef.current = true;
          setSelected(null);
        }}
      >
        Clear selection
      </button>
    </div>
  );
}

function fmtFactorRaw(raw: number | null): string {
  if (raw == null) return "—";
  return Math.abs(raw) >= 100 ? raw.toFixed(0) : raw.toFixed(2);
}
