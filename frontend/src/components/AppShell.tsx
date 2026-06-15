import { useIsDesktop } from "../hooks/useBreakpoint";
import { MapView } from "../map/MapView";
import { BottomSheet } from "./BottomSheet";
import { DrawToolbar } from "./DrawToolbar";
import { ResultsPanel } from "./ResultsPanel";
import { Sidebar } from "./Sidebar";
import { ThemeToggle } from "./ThemeToggle";

/**
 * Responsive application shell.
 *
 * - Desktop (>= 768px): 3-pane CSS grid — left controls / center map / right results.
 * - Mobile (< 768px): full-screen map with a draggable bottom-sheet for controls/results
 *   and a floating theme toggle.
 *
 * The drawing toolbar (GEO-23) floats over the map pane in both layouts.
 */
export function AppShell() {
  const isDesktop = useIsDesktop();

  if (isDesktop) {
    return (
      <div className="shell shell--desktop">
        <aside className="pane pane--left">
          <Sidebar />
        </aside>
        <main className="pane pane--map">
          <MapView />
          <DrawToolbar />
        </main>
        <aside className="pane pane--right">
          <ResultsPanel />
        </aside>
      </div>
    );
  }

  return (
    <div className="shell shell--mobile">
      <main className="pane pane--map">
        <MapView />
        <DrawToolbar />
      </main>
      <div className="floating-toolbar">
        <ThemeToggle />
      </div>
      <BottomSheet>
        <Sidebar />
        <ResultsPanel />
      </BottomSheet>
    </div>
  );
}
