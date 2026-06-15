import { LAYERS } from "../map/layers";
import { useMapStore } from "../map/useMapStore";

/**
 * Layer toggles + opacity sliders (GEO-26). Driven by the LAYERS registry: `parcels` is
 * live; the other layers render as disabled rows (clearly marked) until their tiles/scores
 * exist (GEO-16+/GEO-24+).
 */
export function LayerControl() {
  const { layers, setLayerVisible, setLayerOpacity } = useMapStore();
  return (
    <ul className="layer-list">
      {LAYERS.map((def) => {
        const toggle = layers[def.id];
        return (
          <li key={def.id} className="layer-row" data-available={def.available || undefined}>
            <label className="layer-row__main">
              <input
                type="checkbox"
                checked={toggle.visible}
                disabled={!def.available}
                onChange={(e) => setLayerVisible(def.id, e.target.checked)}
              />
              <span
                className="layer-swatch"
                data-symbol={def.symbol}
                style={def.symbol === "ramp" ? undefined : { background: def.swatch }}
                aria-hidden="true"
              />
              <span className="layer-row__label">{def.label}</span>
              {!def.available && <span className="layer-row__badge">soon</span>}
            </label>
            {def.available ? (
              <input
                className="layer-opacity"
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={toggle.opacity}
                aria-label={`${def.label} opacity`}
                onChange={(e) => setLayerOpacity(def.id, Number(e.target.value))}
              />
            ) : (
              def.note && <p className="layer-row__note">{def.note}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
