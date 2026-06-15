import { AppShell } from "./components/AppShell";
import { MapProvider } from "./map/MapProvider";

export default function App() {
  return (
    <MapProvider>
      <AppShell />
    </MapProvider>
  );
}
