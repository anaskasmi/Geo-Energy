import { AppShell } from "./components/AppShell";
import { MobileGate } from "./components/MobileGate";
import { MapProvider } from "./map/MapProvider";

export default function App() {
  return (
    <MobileGate>
      <MapProvider>
        <AppShell />
      </MapProvider>
    </MobileGate>
  );
}
