import { ThemeToggle } from "./ThemeToggle";

/**
 * Left control pane (desktop). Scaffolding only — real scoring controls/weights land in
 * later tickets (GEO-16+/GEO-24). For now it hosts the theme toggle and placeholder
 * sections so the layout and theming are exercised.
 */
export function Sidebar() {
  return (
    <div className="sidebar">
      <header className="sidebar__header">
        <h1 className="sidebar__title">Site Selection</h1>
        <p className="sidebar__subtitle">Kern County, CA</p>
      </header>

      <section className="panel-section">
        <h2 className="panel-section__title">Appearance</h2>
        <ThemeToggle />
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Filters</h2>
        <p className="placeholder-text">Scoring criteria and weights will appear here.</p>
      </section>

      <section className="panel-section">
        <h2 className="panel-section__title">Layers</h2>
        <p className="placeholder-text">Parcels are loaded from PMTiles. More layers soon.</p>
      </section>
    </div>
  );
}
