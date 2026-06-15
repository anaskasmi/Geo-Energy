/**
 * Right results + detail pane (desktop) / bottom-sheet body (mobile). Scaffolding only —
 * no API endpoints exist yet (scoring is GEO-16+). Shows a results-list skeleton and a
 * detail placeholder so the layout is realistic.
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
        <p className="placeholder-text">
          Select a parcel to see scoring detail. Detail rendering arrives with scoring
          (GEO-16+).
        </p>
      </section>
    </div>
  );
}
