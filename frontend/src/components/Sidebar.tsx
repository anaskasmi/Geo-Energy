import { AgentChat } from "./AgentChat";
import { BasemapControl } from "./BasemapControl";
import { LayerControl } from "./LayerControl";
import { Legend } from "./Legend";
import { ScoringControl } from "./ScoringControl";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Left control pane (desktop) / bottom-sheet body (mobile). Hosts the appearance + basemap
 * controls, layer toggles (GEO-26), and the legend. Scoring criteria/weights land in
 * GEO-16+/GEO-24.
 */
export function Sidebar() {
  return (
    <div className="sidebar">
      <header className="sidebar__header">
        <h1 className="sidebar__title">Site Selection</h1>
        <p className="sidebar__subtitle">Kern County, CA</p>
      </header>

      <section className="panel-section">
        <h2 className="panel-section__title">Assistant</h2>
        <AgentChat />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Scoring</h2>
        <ScoringControl />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Appearance</h2>
        <ThemeToggle />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Basemap</h2>
        <BasemapControl />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Layers</h2>
        <LayerControl />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Legend</h2>
        <Legend />
      </section>
    </div>
  );
}
