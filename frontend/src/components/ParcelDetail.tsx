import { useMapStore } from "../map/useMapStore";

/**
 * Selected-parcel detail (GEO-26 select → detail). Shows the parcel's quick attributes from
 * the vector tile; full scoring detail arrives with the scoring engine (GEO-16+).
 */
export function ParcelDetail() {
  const { selected, setSelected } = useMapStore();

  if (!selected) {
    return (
      <p className="placeholder-text">
        Select a parcel on the map to see its detail. Scoring detail arrives with GEO-16+.
      </p>
    );
  }

  return (
    <div className="parcel-detail">
      <dl className="parcel-detail__grid">
        <dt>Parcel ID</dt>
        <dd>{selected.id}</dd>
        <dt>APN</dt>
        <dd>{selected.apn ?? "—"}</dd>
        <dt>Acres</dt>
        <dd>{selected.acres != null ? selected.acres.toFixed(2) : "—"}</dd>
      </dl>
      <button type="button" className="parcel-detail__clear" onClick={() => setSelected(null)}>
        Clear selection
      </button>
    </div>
  );
}
