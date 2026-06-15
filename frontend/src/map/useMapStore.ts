import { useContext } from "react";

import { MapContext } from "./MapContext";
import type { MapStore } from "./MapContext";

/** Access the shared map state. Must be used within a <MapProvider>. */
export function useMapStore(): MapStore {
  const ctx = useContext(MapContext);
  if (!ctx) {
    throw new Error("useMapStore must be used within a MapProvider");
  }
  return ctx;
}
