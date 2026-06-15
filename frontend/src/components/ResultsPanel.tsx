import { ParcelDetail } from "./ParcelDetail";

/**
 * Right results + detail pane (desktop) / bottom-sheet body (mobile). The results list is a
 * skeleton until scoring exists (GEO-16+); the detail section shows the selected parcel's
 * quick attributes (GEO-26 select → detail).
 */
export function ResultsPanel() {
  return (
    <div className="results-panel">
      <section className="panel-section">
        <h2 className="panel-section__title">Results</h2>
        <ul className="results-list">
          {[0, 1, 2].map((i) => (
            <li key={i} className="results-list__item">
              <span className="skeleton skeleton--line" />
              <span className="skeleton skeleton--line skeleton--short" />
            </li>
          ))}
        </ul>
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Detail</h2>
        <ParcelDetail />
      </section>
    </div>
  );
}
