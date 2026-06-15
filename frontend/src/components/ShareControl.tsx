import { useMemo, useState } from "react";

import { copyText } from "../export/clipboard";
import { exportCSV, exportGeoJSON } from "../export/exporters";
import { useMapStore } from "../map/useMapStore";
import {
  deleteAnalysis,
  listSavedAnalyses,
  loadAnalysis,
  saveAnalysis,
  storageAvailable,
} from "../state/savedAnalyses";
import type { SavedAnalysis } from "../state/savedAnalyses";
import { applyShareState, buildShareUrl, currentShareState } from "../state/useUrlState";

/**
 * Share, save & export controls (GEO-31). Lives in the sidebar:
 *  - Copy a shareable link encoding the current area + use case + selection.
 *  - Export the scored results as GeoJSON / CSV.
 *  - Save the current analysis by name to localStorage; list / load / delete saved analyses.
 *
 * Save/load degrades gracefully when storage is unavailable (private mode): the save row is
 * disabled with an explanatory note. Copy/export use feedback that auto-clears.
 */
export function ShareControl() {
  const store = useMapStore();
  const { drawnPolygon, scoreResult } = store;
  const canStore = useMemo(storageAvailable, []);

  const [name, setName] = useState("");
  const [saved, setSaved] = useState<SavedAnalysis[]>(() => (canStore ? listSavedAnalyses() : []));
  const [copyMsg, setCopyMsg] = useState<string | null>(null);

  const hasArea = !!drawnPolygon;
  const hasResults = (scoreResult?.features.length ?? 0) > 0;

  const flash = (msg: string) => {
    setCopyMsg(msg);
    window.setTimeout(() => setCopyMsg(null), 2000);
  };

  const onCopyLink = async () => {
    const ok = await copyText(buildShareUrl(currentShareState(store)));
    flash(ok ? "Link copied to clipboard" : "Couldn't copy — copy the address bar instead");
  };

  const onSave = () => {
    const next = saveAnalysis(name, currentShareState(store));
    if (next) {
      setSaved(next);
      setName("");
    } else {
      flash("Couldn't save the analysis");
    }
  };

  const onLoad = (entry: SavedAnalysis) => {
    const state = loadAnalysis(entry);
    if (state) applyShareState(store, state);
  };

  const onDelete = (id: string) => setSaved(deleteAnalysis(id));

  return (
    <div className="share-control">
      <div className="share-control__row">
        <button type="button" className="panel-btn" onClick={onCopyLink}>
          Copy share link
        </button>
      </div>

      <div className="share-control__row">
        <button
          type="button"
          className="panel-btn"
          onClick={() => scoreResult && exportGeoJSON(scoreResult)}
          disabled={!hasResults}
          title={hasResults ? "Download scored parcels as GeoJSON" : "Score an area first"}
        >
          Export GeoJSON
        </button>
        <button
          type="button"
          className="panel-btn"
          onClick={() => scoreResult && exportCSV(scoreResult)}
          disabled={!hasResults}
          title={hasResults ? "Download scored parcels as CSV" : "Score an area first"}
        >
          Export CSV
        </button>
      </div>

      <div className="share-control__save">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name this analysis"
          aria-label="Name this analysis"
          disabled={!canStore}
          maxLength={80}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && hasArea) onSave();
          }}
        />
        <button
          type="button"
          className="panel-btn panel-btn--primary"
          onClick={onSave}
          disabled={!canStore || !name.trim() || !hasArea}
          title={hasArea ? "Save the current analysis" : "Draw an area to save it"}
        >
          Save
        </button>
      </div>
      {!canStore && (
        <p className="share-control__note">Saving is unavailable in this browser mode.</p>
      )}

      {copyMsg && (
        <p className="share-control__note" role="status" aria-live="polite">
          {copyMsg}
        </p>
      )}

      {saved.length > 0 && (
        <ul className="saved-list" aria-label="Saved analyses">
          {saved.map((a) => (
            <li key={a.id} className="saved-list__item">
              <button
                type="button"
                className="saved-list__load"
                onClick={() => onLoad(a)}
                title={`Load "${a.name}"`}
              >
                <span className="saved-list__name">{a.name}</span>
                <span className="saved-list__date">{new Date(a.savedAt).toLocaleDateString()}</span>
              </button>
              <button
                type="button"
                className="saved-list__delete"
                onClick={() => onDelete(a.id)}
                aria-label={`Delete "${a.name}"`}
                title="Delete"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
