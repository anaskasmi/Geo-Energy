import { LAYERS, SCORE_RAMP } from "../map/layers";

/**
 * Map legend (GEO-26): layer symbols + the suitability score color ramp. The ramp is the
 * scale the scoring result layer will use (GEO-16+/GEO-24+).
 */
export function Legend() {
  const symbols = LAYERS.filter((layer) => layer.symbol !== "ramp");
  return (
    <div className="legend">
      <ul className="legend__symbols">
        {symbols.map((layer) => (
          <li key={layer.id} className="legend__item">
            <span
              className="layer-swatch"
              data-symbol={layer.symbol}
              style={{ background: layer.swatch }}
              aria-hidden="true"
            />
            <span>{layer.label}</span>
          </li>
        ))}
      </ul>
      <div className="legend__ramp">
        <span className="legend__ramp-label">Low</span>
        <span
          className="legend__ramp-bar"
          style={{ background: `linear-gradient(to right, ${SCORE_RAMP.join(", ")})` }}
          role="img"
          aria-label="Suitability score scale from low to high"
        />
        <span className="legend__ramp-label">High</span>
      </div>
    </div>
  );
}
