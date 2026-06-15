import { useEffect, useMemo, useRef, useState } from "react";

import type { ScoredFeature } from "../api/client";
import { scoreColorCss, scoreTextColor } from "../map/layers";
import { useMapStore } from "../map/useMapStore";
import { SORTS, dominantReasons, fmtAcres, fmtKv, fmtMeters } from "../results/format";
import { useContextSummary } from "../results/hooks";
import { ParcelDetail } from "./ParcelDetail";

interface Filters {
  minAcres: number;
  maxSlope: number;
  maxTxKm: number;
}
// Max fields default to no cap (Infinity); an empty input means "no cap" rather than 0.
const NO_FILTERS: Filters = { minAcres: 0, maxSlope: Infinity, maxTxKm: Infinity };
const MAX_COMPARE = 3;
const numOrInfinity = (v: string) => (v === "" ? Infinity : Number(v));
const displayMax = (n: number) => (Number.isFinite(n) ? String(n) : "");

/**
 * Right results pane (GEO-25): a ranked, map-synced parcel list with client-side sort + filter,
 * compare (2–3 parcels), a CAISO queue context banner, and the per-factor detail breakdown.
 * Selecting a row highlights + flies to the parcel on the map (and vice-versa).
 */
export function ResultsPanel() {
  const { scoreResult, scoreStatus, scoreError, selected, setSelected, flyTo } = useMapStore();
  const context = useContextSummary();
  const [sortKey, setSortKey] = useState("score");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [compareIds, setCompareIds] = useState<(number | string)[]>([]);
  const [bannerOpen, setBannerOpen] = useState(true);
  const listRef = useRef<HTMLUListElement | null>(null);

  const features = scoreResult?.features ?? [];

  const filtered = useMemo(() => {
    const sort = SORTS.find((s) => s.key === sortKey) ?? SORTS[0];
    return features
      .filter((f) => {
        const p = f.properties;
        if ((p.acres ?? 0) < filters.minAcres) return false;
        if (p.factors.mean_slope_pct != null && p.factors.mean_slope_pct > filters.maxSlope) return false;
        if (p.factors.dist_tx_m != null && p.factors.dist_tx_m / 1000 > filters.maxTxKm) return false;
        return true;
      })
      .sort((a, b) => (sort.get(a) - sort.get(b)) * sort.dir);
  }, [features, sortKey, filters]);

  // Keep the active row visible when selection comes from the map (bidirectional sync).
  useEffect(() => {
    if (selected == null || !listRef.current) return;
    const row = listRef.current.querySelector<HTMLElement>(`[data-parcel="${CSS.escape(String(selected.id))}"]`);
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    row?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [selected]);

  const select = (f: ScoredFeature) => {
    const p = f.properties;
    setSelected({ id: p.id, apn: p.apn, acres: p.acres });
    if (p.centroid) flyTo(p.centroid);
  };
  const toggleCompare = (id: number | string) =>
    setCompareIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= MAX_COMPARE ? prev : [...prev, id],
    );

  const compared = features.filter((f) => compareIds.includes(f.properties.id));

  return (
    <div className="results-panel">
      <section className="panel-section">
        <div className="results-head">
          <h2 className="panel-section__title">Results</h2>
          {features.length > 0 && (
            <label className="results-sort">
              <span className="visually-hidden">Sort by</span>
              <select value={sortKey} onChange={(e) => setSortKey(e.target.value)}>
                {SORTS.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        {context && bannerOpen && (
          <div className="context-banner" role="note">
            <span>
              CAISO Kern queue: <strong>{context.total.n_projects ?? 0}</strong> projects ·{" "}
              <strong>{Math.round(context.total.total_mw ?? 0).toLocaleString()}</strong> MW (
              {Math.round(context.total.active_total_mw ?? 0).toLocaleString()} MW active)
            </span>
            <button type="button" className="context-banner__close" aria-label="Dismiss" onClick={() => setBannerOpen(false)}>
              ✕
            </button>
          </div>
        )}

        {scoreStatus === "idle" && (
          <p className="placeholder-text">Draw an area on the map to score the parcels inside it.</p>
        )}
        {scoreStatus === "scoring" && <p className="results-status">Scoring…</p>}
        {scoreStatus === "error" && <p className="error-text">{scoreError ?? "Scoring failed."}</p>}
        {scoreStatus === "done" && features.length === 0 && (
          <p className="placeholder-text">No parcels passed the screen in the drawn area.</p>
        )}

        {features.length > 0 && (
          <>
            <div className="results-summary">
              Showing <strong>{filtered.length}</strong> of {features.length} parcels
              {scoreResult ? ` · ${scoreResult.meta.use_case === "data_center" ? "Data center" : "Utility solar"}` : ""}
            </div>

            <fieldset className="results-filters">
              <legend className="visually-hidden">Filter results</legend>
              <label>
                Min acres
                <input
                  type="number"
                  min={0}
                  step={10}
                  value={filters.minAcres}
                  onChange={(e) => setFilters((f) => ({ ...f, minAcres: Number(e.target.value) || 0 }))}
                />
              </label>
              <label>
                Max slope %
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  placeholder="any"
                  value={displayMax(filters.maxSlope)}
                  onChange={(e) => setFilters((f) => ({ ...f, maxSlope: numOrInfinity(e.target.value) }))}
                />
              </label>
              <label>
                Max grid km
                <input
                  type="number"
                  min={0}
                  step={1}
                  placeholder="any"
                  value={displayMax(filters.maxTxKm)}
                  onChange={(e) => setFilters((f) => ({ ...f, maxTxKm: numOrInfinity(e.target.value) }))}
                />
              </label>
            </fieldset>

            {compared.length >= 2 && (
              <CompareTable features={compared} onClear={() => setCompareIds([])} />
            )}

            <ul className="results-list" ref={listRef}>
              {filtered.map((f) => {
                const p = f.properties;
                const active = selected?.id === p.id;
                return (
                  <li
                    key={String(p.id)}
                    data-parcel={String(p.id)}
                    className={active ? "results-row results-row--active" : "results-row"}
                  >
                    <button type="button" className="results-row__main" onClick={() => select(f)} aria-pressed={active}>
                      <span className="results-row__rank">#{p.rank}</span>
                      <span
                        className="score-chip"
                        style={{ background: scoreColorCss(p.score), color: scoreTextColor(p.score) }}
                      >
                        {p.score.toFixed(0)}
                      </span>
                      <span className="results-row__body">
                        <strong>{p.apn ?? `Parcel ${p.id}`}</strong>
                        <small>
                          {fmtAcres(p.acres)} · {p.zoning_class ?? "zoning n/a"}
                        </small>
                        {dominantReasons(f).length > 0 && (
                          <span className="results-row__tags">
                            {dominantReasons(f).map((t) => (
                              <span key={t} className="tag">
                                {t}
                              </span>
                            ))}
                          </span>
                        )}
                      </span>
                    </button>
                    <label className="results-row__compare" title="Add to compare (up to 3)">
                      <input
                        type="checkbox"
                        checked={compareIds.includes(p.id)}
                        disabled={!compareIds.includes(p.id) && compareIds.length >= MAX_COMPARE}
                        onChange={() => toggleCompare(p.id)}
                      />
                      <span className="visually-hidden">Compare parcel {p.id}</span>
                    </label>
                  </li>
                );
              })}
              {filtered.length === 0 && <li className="placeholder-text">No parcels match the filters.</li>}
            </ul>
          </>
        )}
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Detail</h2>
        <ParcelDetail />
      </section>
    </div>
  );
}

/** Side-by-side comparison of 2–3 parcels on the key factors (GEO-25 compare). */
function CompareTable({ features, onClear }: { features: ScoredFeature[]; onClear: () => void }) {
  const rows: { label: string; get: (f: ScoredFeature) => string }[] = [
    { label: "Score", get: (f) => f.properties.score.toFixed(0) },
    { label: "Acres", get: (f) => fmtAcres(f.properties.acres) },
    { label: "GHI", get: (f) => (f.properties.factors.ghi != null ? `${f.properties.factors.ghi.toFixed(1)}` : "—") },
    { label: "Slope %", get: (f) => (f.properties.factors.mean_slope_pct != null ? f.properties.factors.mean_slope_pct.toFixed(1) : "—") },
    { label: "To line", get: (f) => fmtMeters(f.properties.factors.dist_tx_m) },
    { label: "To sub", get: (f) => fmtMeters(f.properties.factors.dist_sub_m) },
    { label: "Sub kV", get: (f) => fmtKv(f.properties.factors.nearest_sub_kv) },
  ];
  return (
    <div className="compare">
      <div className="compare__head">
        <span>Compare</span>
        <button type="button" className="compare__clear" onClick={onClear}>
          Clear
        </button>
      </div>
      <table className="compare__table">
        <thead>
          <tr>
            <th scope="col">Factor</th>
            {features.map((f) => (
              <th key={String(f.properties.id)} scope="col">
                #{f.properties.rank}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <th scope="row">{r.label}</th>
              {features.map((f) => (
                <td key={String(f.properties.id)}>{r.get(f)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
