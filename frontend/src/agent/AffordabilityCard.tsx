import { Landmark, Minus, TrendingDown, TrendingUp } from "lucide-react";

import { Icon } from "../components/Icon";
import type { Affordability } from "./types";

/**
 * Compact land-affordability card (GEO-41) rendered under an assistant message when the agent ran
 * `check_affordability`. Surfaces the area-level signal — median home value, year-over-year price
 * trend, and a 0..1 affordability score (with a meter) — instead of leaving it only in the prose.
 * The data is COUNTY-level for Kern (free public sources), so it is framed as an estimate.
 */
const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const BAND_LABEL: Record<string, string> = {
  affordable: "Affordable",
  moderate: "Moderate",
  expensive: "Expensive",
  unknown: "Unknown",
};

export function AffordabilityCard({ data }: { data: Affordability }) {
  const {
    geography,
    median_home_value_usd,
    price_trend_yoy_pct,
    affordability_score,
    acs_vintage,
    affordability_band,
    sources,
    note,
  } = data;

  const band = affordability_band in BAND_LABEL ? affordability_band : "unknown";
  const score = affordability_score;
  const pct = score != null ? Math.round(score * 100) : null;

  const trend = price_trend_yoy_pct;
  const TrendIcon = trend == null ? Minus : trend > 0 ? TrendingUp : trend < 0 ? TrendingDown : Minus;

  return (
    <section className="afford-card" aria-label="Land affordability">
      <header className="afford-card__head">
        <span className="afford-card__title">
          <Icon icon={Landmark} size={14} />
          Land affordability
        </span>
        <span className={`afford-badge afford-badge--${band}`}>{BAND_LABEL[band]}</span>
      </header>

      <dl className="afford-stats">
        <div className="afford-stat">
          <dt>Median value</dt>
          <dd>{median_home_value_usd != null ? USD.format(median_home_value_usd) : "—"}</dd>
        </div>
        <div className="afford-stat">
          <dt>Price trend (YoY)</dt>
          <dd className="afford-stat__trend">
            <Icon icon={TrendIcon} size={13} />
            {trend != null ? `${trend > 0 ? "+" : ""}${trend.toFixed(1)}%` : "—"}
          </dd>
        </div>
        <div className="afford-stat">
          <dt>Affordability</dt>
          <dd>{score != null ? score.toFixed(2) : "—"}</dd>
        </div>
      </dl>

      {pct != null && (
        <div
          className="afford-meter"
          role="meter"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Affordability score"
        >
          <span className="afford-meter__fill" style={{ width: `${pct}%` }} />
        </div>
      )}

      <p className="afford-card__note">
        {geography}
        {acs_vintage ? ` · ${acs_vintage}` : ""}. {note}
        {sources.length > 0 && <span className="afford-card__src"> Sources: {sources.join(", ")}.</span>}
      </p>
    </section>
  );
}
