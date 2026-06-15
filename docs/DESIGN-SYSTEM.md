# Design System Spec — Site Selection (Kern County, CA)
**Single source of truth.** Map-first geospatial siting dashboard. React 18 + Vite + TS, MapLibre GL + deck.gl, terra-draw. Theming via `data-theme` on `<html>` + CSS custom properties. This spec is grounded in the actual codebase (`src/styles/global.css`, `src/styles/components.css`, `src/theme/basemap.ts`, `src/map/layers.ts`, `src/map/constants.ts`) and follows every adversarial **verdict** where it adjusted or overrode a finding.

---

## 1. Design principles

1. **The map is the hero; the chrome recedes.** The basemap is data-muted (CARTO Positron / Dark Matter at full opacity); overlays carry the data-ink. Panels float *over* a busy raster with a baked-in 1px ring so they separate on white, black, and imagery alike.
2. **Numbers are the product.** Scores 0–100, acreage, slope %, grid-km, MW must be scannable. Every aligned number uses Inter + `tabular-nums`; precision lives in the ranked list / chip / factor bars, never in hue alone.
3. **Color is never the only channel.** Viridis score, CVD-safe categorical hues, and status colors are always paired with geometry, an icon, the numeric value, or the legend.
4. **Keep the accent OUT of the viridis gamut.** Viridis runs purple → blue → **teal → green** → yellow. A teal/green "clean-energy" accent would read as a *mid/high score* on the map. **Decision (overrides the color-tokens finding, per the Layout and Component verdicts which both flagged the collision): the interactive accent stays an intentional azure-blue; green is reserved strictly for `success` / "recommended best-site," never chrome.** Clean-energy brand character comes from typography, iconography, and spacing — not a green button.
5. **Tokens before bespoke px.** Spacing/radius/elevation/motion/focus read from one scale. Migration is **additive** — keep the existing role names (`--bg/--surface/--border/--text/--accent`), add the missing channels, don't mass-rename 192 call sites.
6. **WCAG 2.2 AA is a baseline we already advertise — don't regress it.** Theme-aware elevation, a clip-resilient `outline` focus ring (the densest rows use `overflow:hidden`), single-pointer/keyboard alternatives to every drag (2.5.7), and luminance-correct text on score chips.
7. **Motion is functional and reduced-motion-safe.** Animate only `transform`/`opacity`; drag follows the finger 1:1 and snaps on release; reduced-motion strips movement but never strips a state change (focus, selection, aria-live).

---

## 2. Foundations — token tables

### 2.1 COLOR (additive to `global.css`; keep existing role names)

**Existing tokens to KEEP (names unchanged):** `--bg --bg-elevated --bg-sunken --surface --border --border-strong --text --text-muted --text-faint --accent --accent-contrast --shadow --skeleton`.

#### Core surfaces & text — revised values

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--bg` | `#f4f5f7` *(keep)* | `#0f1115` *(keep)* | app/map canvas |
| `--bg-sunken` | `#ebedf0` *(keep)* | `#14171c` *(keep)* | inset wells, segmented track |
| `--surface` | `#ffffff` *(keep)* | `#1a1d23` *(keep)* | panels/cards |
| `--surface-elevated` *(ADD)* | `#ffffff` | `#222831` | popover/menu/modal (dark gets *lighter* with elevation — M3 tonal) |
| `--border` | `#d9dce1` *(keep)* | `#2a2f37` *(keep)* | decorative dividers (3:1 NOT required) |
| `--border-strong` | `#c2c7cf` *(keep)* | `#3a414b` *(keep)* | stronger divider |
| `--border-interactive` *(ADD)* | `#868d99` | `#646e78` | input/control edges (≥3:1, WCAG 1.4.11) |
| `--text` | `#1b1f24` *(keep)* | `#e8eaed` *(keep)* | body, 14:1+ |
| `--text-muted` | `#5b6370` *(keep)* | `#a4adba` *(keep)* | secondary, ≥4.5:1 |
| `--text-faint` | `#8a929e` *(keep)* | `#6b7480` *(keep)* | placeholder/meta, ~3:1 |

#### Accent (intentional azure — replaces generic `#2563eb`)

| Token | Light | Dark |
|---|---|---|
| `--accent` | `#1e5fd9` | `#4f8cff` |
| `--accent-hover` | `#1b4fc0` | `#6c9dff` |
| `--accent-contrast` | `#ffffff` *(keep)* | `#0f1115` *(keep)* |
| `--accent-text` *(ADD)* — links/inline | `#1d56cf` | `#7aa9ff` |
| `--accent-vivid` *(ADD)* — focus ring, active toggle, 3:1 contexts | `#2563eb` | `#4f8cff` |
| `--accent-subtle` *(ADD)* — tinted chip bg | `#e7effc` | `#10213f` |
| `--accent-border` *(ADD)* | `#b9d2f7` | `#274a78` |

White label on `#1e5fd9` ≈ 5.0:1 (AA text + UI). Accent rarely touches the map — map selection uses the hue-free casing in §4.

#### Status — solid (fill) + text (on tint) + subtle (tint bg) + border

`info` deliberately **shares the accent blue family** (avoids two competing blues). Replace the 7 hardcoded `#dc2626` in `components.css` with `--danger`/`--danger-text`.

**Light**
| Role | `-solid` (white label) | `-vivid` | `-text` (on subtle) | `-subtle` (bg) | `-border` |
|---|---|---|---|---|---|
| success | `#127c39` | `#16a34a` | `#15803d` | `#eaf7ee` | `#b6e3c2` |
| warning | `#b45309` | `#d97706` | `#a8550a` | `#fbeccf` | `#f0cd8f` |
| danger | `#dc2626` | `#ef4444` | `#c01a1a` | `#fdebeb` | `#f4b9b9` |
| info | `#1e5fd9` | `#2563eb` | `#1d56cf` | `#e7effc` | `#b9d2f7` |

**Dark**
| Role | `-solid` | `-text` | `-subtle` | `-border` |
|---|---|---|---|---|
| success | `#22c55e` | `#4ade80` | `#10271a` | `#1f3a28` |
| warning | `#f59e0b` | `#fbbf24` | `#2a1e08` | `#4a3613` |
| danger | `#ef4444` | `#f87171` | `#2a1416` | `#4a2125` |
| info | `#3b82f6` | `#60a5fa` | `#11203a` | `#1d3a66` |

**Recommended / "best site"** badge token (clean-energy green, **chrome-forbidden, badge-only**): `--positive` = light `#127c39` / dark `#22c55e` (reuse `success`). Best-in-row markers + "recommended" pills use this; never a button/selection.

> **Solid vs vivid rule:** white text on a *vivid* teal/green/amber fails 4.5:1 (`#0d9488`=3.7, `#16a34a`=3.3, `#d97706`=3.2). Always put white labels on `-solid`; use `-vivid` only for icons/focus/chart strokes (3:1 contexts).

### 2.2 TYPOGRAPHY

**Font:** **Inter Variable** (UI + data), self-hosted. One monospace, **JetBrains Mono Variable**, scoped *only* to lat/long + APN codes.

```
npm i @fontsource-variable/inter @fontsource-variable/jetbrains-mono
# src/main.tsx (once):
import '@fontsource-variable/inter/wght.css';            // ~47KB latin, weight axis only
import '@fontsource-variable/jetbrains-mono/wght.css';
```
- Family string **must** be `'Inter Variable'` (not `'Inter'` — silent system-ui fallback otherwise). OFL 1.1, no license/bundle trap.
- **Do NOT** add `font-optical-sizing` (the `wght.css` subset carries no `opsz` axis — it would no-op).
- **Add Fontaine** (Vite plugin, already in stack) for `size-adjust`/`ascent-override` fallback metrics → near-zero CLS when Inter's tall x-height swaps into the side panels.
- **Justification (correct the finding):** switch is for inconsistent cross-OS glyph *shapes* + slashed-zero/disambiguation control + brand neutrality — **not** "system fonts lack tabular figures" (SF/Segoe/Roboto all support `tabular-nums`).

```css
--font-sans: 'Inter Variable', system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
--font-mono: 'JetBrains Mono Variable', ui-monospace, 'SFMono-Regular', Menlo, monospace;
body { font-family: var(--font-sans); font-size: .875rem; line-height: 1.5; }
.num  { font-variant-numeric: tabular-nums; }          /* every aligned number/stat */
.code { font-family: var(--font-mono); font-variant-numeric: slashed-zero; } /* lat/long + APN only */
```
Inter defaults to **proportional** figures — columns misalign without `.num`. Prefer `font-variant-numeric` over raw `font-feature-settings` (the latter resets tnum). Don't blanket `tabular-nums` onto prose (AgentChat replies).

**Type scale (root 16px):**
| Role | px / rem | line-height | weight | tracking | numeric |
|---|---|---|---|---|---|
| Display / H1 (page title) | 24 / 1.5 | 30 (1.25) | 700 | -0.01em | — |
| H2 (panel section) | 18 / 1.125 | 24 (1.33) | 600 | -0.005em | — |
| H3 (card/subsection) | 15 / 0.9375 | 21 (1.4) | 600 | 0 | — |
| Body (UI, chat) | 14 / 0.875 | 21 (1.5) | 400 | 0 | proportional |
| Small (meta, labels) | 13 / 0.8125 | 19 (1.45) | 400 | 0 | — |
| Micro / eyebrow | 11 / 0.6875 | 16 (1.45) | 600 | 0.04em, UPPERCASE | — |
| Data / table cell | 13 / 0.8125 | 18 (1.4) | 500 | 0 | **tabular-nums** |
| KPI / big score | 20 / 1.25 | 24 (1.2) | 700 | -0.01em | **tabular-nums** |

### 2.3 SPACING (4px base + 10/14 half-steps — codebase uses 10px ~35×, 14px ~10×)
```css
--space-0:0; --space-0_5:2px; --space-1:4px; --space-1_5:6px; --space-2:8px;
--space-2_5:10px; --space-3:12px; --space-3_5:14px; --space-4:16px; --space-5:20px;
--space-6:24px; --space-8:32px; --space-10:40px; --space-12:48px;
```
Usage: 2/4/6 inside chips & result rows · 8/12 between controls & list rows · 16 panel/card padding · 24 modal/sheet padding & section dividers · 32–48 EmptyState only. **Defer the comfortable/compact density toggle (YAGNI);** if ever added, gate to `pointer:fine` and never reduce the existing 44px coarse-pointer targets.

### 2.4 RADIUS (6-stop; map existing 5→6, 10→12, 26→pill)
```css
--radius-xs:4px;   /* chips, score badge, tag, segmented inner, factor track */
--radius-sm:6px;   /* buttons, inputs, dropdown items, ThemeToggle */
--radius-md:8px;   /* cards, panels, popovers, LayerControl, Legend, ParcelDetail */
--radius-lg:12px;  /* BottomSheet body, large surfaces */
--radius-xl:16px;  /* BottomSheet top corners, modal, AgentChat container */
--radius-pill:999px; /* FAB, segmented active pill, toggle thumb, avatar */
```
Nested rule: inner = outer − padding (chip r4 inside card r8 p4).

### 2.5 ELEVATION (theme-split — never one shared value; dark needs near-black + luminous hairline)
```css
:root{
  --shadow-xs: 0 1px 2px 0 rgb(16 24 40 /.05);
  --shadow-sm: 0 1px 3px 0 rgb(16 24 40 /.10), 0 1px 2px -1px rgb(16 24 40 /.10);
  --shadow-md: 0 4px 8px -2px rgb(16 24 40 /.10), 0 2px 4px -2px rgb(16 24 40 /.06);
  --shadow-lg: 0 12px 16px -4px rgb(16 24 40 /.10), 0 4px 6px -2px rgb(16 24 40 /.05);
  --shadow-xl: 0 20px 24px -4px rgb(16 24 40 /.12), 0 8px 8px -4px rgb(16 24 40 /.06);
  --ring-hairline: inset 0 0 0 1px rgb(16 24 40 /.08);
  --edge-highlight: inset 0 1px 0 0 rgb(255 255 255 /.40);
}
:root[data-theme="dark"]{
  --shadow-xs: 0 1px 2px 0 rgb(0 0 0 /.40);
  --shadow-sm: 0 1px 3px 0 rgb(0 0 0 /.50), 0 1px 2px -1px rgb(0 0 0 /.40);
  --shadow-md: 0 4px 8px -2px rgb(0 0 0 /.55), 0 2px 4px -2px rgb(0 0 0 /.45);
  --shadow-lg: 0 12px 20px -4px rgb(0 0 0 /.60), 0 4px 8px -4px rgb(0 0 0 /.50);
  --shadow-xl: 0 24px 40px -8px rgb(0 0 0 /.70), 0 8px 16px -8px rgb(0 0 0 /.55);
  --ring-hairline: inset 0 0 0 1px rgb(255 255 255 /.08);
  --edge-highlight: inset 0 1px 0 0 rgb(255 255 255 /.06);
}
/* every panel floating over the map composes ring + shadow so it separates on any tile */
.overlay-panel { box-shadow: var(--ring-hairline), var(--shadow-lg); }
.overlay-modal { box-shadow: var(--ring-hairline), var(--edge-highlight), var(--shadow-xl); }
```
(`--shadow` / `--skeleton` stay for back-compat during migration.)

### 2.6 MOTION (Material 3 tokens; transform/opacity only)
```css
--dur-fast:120ms;  /* hover, focus, toggle, chip, slider thumb */
--dur-base:180ms;  /* popover/tooltip/dropdown, segmented slide, legend toggle */
--dur-slow:240ms;  /* panel collapse/expand, modal */
--dur-slower:320ms;/* full bottom-sheet open */
--ease-standard:   cubic-bezier(0.2,0,0,1);     /* in-place default */
--ease-decelerate: cubic-bezier(0.05,0.7,0.1,1);/* ENTERING */
--ease-accelerate: cubic-bezier(0.3,0,0.8,0.15);/* EXITING */
--ease-in-out:     cubic-bezier(0.4,0,0.2,1);   /* sliders */
```
Bottom sheet / resize handle: `transition:none` while dragging (follow 1:1), animate snap on release at `--dur-slower var(--ease-decelerate)`. Keep the existing `prefers-reduced-motion` block — but it must not remove focus ring, selection, or aria-live updates.

### 2.7 FOCUS RING (keep `outline` — it's clip-resilient; the densest rows use `overflow:hidden`)
```css
--focus-ring: var(--accent-vivid);          /* light #2563eb / dark #4f8cff, ≥3:1 */
:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }
.maplibregl-canvas:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: -2px; }
/* on-map floating controls get a halo so the ring keeps 3:1 over any tile */
.on-map:focus-visible { outline: none;
  box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--focus-ring); }
@media (forced-colors: active){ :focus-visible{ outline: 2px solid Highlight; } }
```
**Do NOT** globally set `outline:none` with an *outset* box-shadow double-ring — it gets clipped by `overflow:hidden` on `.results-row` (components.css:589), `.saved-list__item` (1171), `.factor-bar__track` (744) and would regress 2.4.7/2.4.11.

---

## 3. Iconography

**Library:** `lucide-react` (ISC), pinned exact version; **named barrel imports only** (deep `lucide-react/icons/X` paths don't resolve). Add `optimizeDeps: { include: ['lucide-react'] }` in `vite.config.ts` so dev mode pre-bundles instead of loading 1000+ modules. Escalation path if 16px stroke fails legibility QA on a standard-DPI monitor: `@phosphor-icons/react` (weighted, 16px-native) — **not** Tabler.

**Central `Icon` wrapper:** default `size={18}`, dense `size={16}`, `strokeWidth={2}` (hold at 2 in dense rows — do not thin to 1.75), `color="currentColor"`, `aria-hidden`, `focusable={false}`. Icon-only buttons keep their `aria-label`.

**Glyph → Lucide mapping** (delete all unicode glyphs):
| Current | Meaning | Lucide |
|---|---|---|
| `▱` | Draw AOI polygon | `Hexagon` |
| `✎` | Edit drawn polygon | `SquarePen` |
| `↶` | Undo | `Undo2` |
| `↷` | Redo | `Redo2` |
| `✕` (draw) | Delete selected feature | `Trash2` |
| `🗑` | Clear all | `Eraser` |
| `✏️` | Draw FAB | `PencilLine` |
| `☀` | Theme: Light | `Sun` |
| `◐` | Theme: System/Auto | `SunMoon` |
| `☾` | Theme: Dark | `Moon` |
| `✕` (dialogs) | Dismiss / close | `X` |

**Icons to ADD:** share `Share2` · copy `Copy` · download `Download` · save `Save` · locate `LocateFixed` · layers `Layers` · opacity `SlidersHorizontal` · info `Info` · help `CircleHelp` · compare `GitCompareArrows` · sort/ranked `ArrowUpDown` / `ListOrdered` · chevrons `ChevronDown/Up/Left/Right`, expand/collapse `ChevronsUpDown` · basemap `Map` · satellite `Satellite` · scoring `Gauge` · agent `Sparkles` · parcel `LandPlot` · search `Search` · pin `MapPin` · zoom `Plus`/`Minus` · north `Compass`. (Pin lucide v1 names — many 0.x names were renamed, e.g. `HelpCircle`→`CircleHelp`; grep after install.)

---

## 4. Map design

### 4.1 Basemap background + opacity (`src/theme/basemap.ts`)
| | Light | Dark | Satellite gap |
|---|---|---|---|
| `BACKGROUND_COLOR` | `#f2f2f0` *(was `#e9e6e1` — drop the warm tan/sepia)* | `#0e0e0e` *(was `#16181d` — match Dark Matter)* | `#0b0d10` *(keep)* |
| `raster-opacity` | **1.0** *(was 0.9 — stop double-fading CARTO)* | **1.0** | `0.96` (keep) |

Streets stays `0.9`. CARTO is already data-muted; render it crisp over a cool-neutral bg.

### 4.2 Suitability ramp — KEEP viridis (do NOT switch to cividis/ColorBrewer/turbo)
Upgrade `SCORE_RAMP` in `map/layers.ts` to **10 stops** (smoother sRGB lerp; preserves perceptual uniformity):
```
#440154 #482878 #3e4a89 #31688e #26828e #1f9e89 #35b779 #6dcd59 #b4de2c #fde725
```
deck.gl fill alpha **205 (~0.80)**, **normal** alpha blending (no additive/multiply — it drifts the fill off the legend swatch). Keep `scoreColor()/scoreColorCss()/scoreTextColor()` — `scoreTextColor` already flips black/white by WCAG contrast, so score chips are already correct; **do not** hardcode chip text color.

**Score-overlay casing (correct polarity — the finding's spec was backwards):**
- Light / satellite basemap → **dark** hairline `rgba(20,20,30,0.55)`, 0.75–1px → rescues the bright-yellow **high** end (measures ~1.1:1 on Positron land — the *best* parcels were invisible).
- Dark basemap → **light** hairline `rgba(255,255,255,0.5)` → rescues the dark-purple **low** end on `#0e0e0e`.

**Salience fix (map-first requirement):** make high scores the *most* salient — scale the parcel outline width/opacity by score (top parcels get a thicker/opaque casing), or render the overlay as a **5-bin quantile** with only the top bins fully opaque. Color stays a *secondary* channel; ranked list + chip + factor bars remain the precise readout.

### 4.3 Categorical layers (replace swatches in `map/layers.ts`) — hue-spread, CVD-safe, dual-basemap, every layer cased
| Layer | Core | Geometry | Casing/halo |
|---|---|---|---|
| **Parcels** | line `#3b5bdb` (indigo); fill `rgba(59,91,219,.08)` light / `.12` dark | fill+line, line 0.75–1px @ op 0.7–0.8 | `rgba(255,255,255,.6)` light / `rgba(10,10,10,.8)` dark |
| **Transmission** | `#f08c00` (amber), 1.5–2px | line | `rgba(0,0,0,.5)` light/satellite / `rgba(255,255,255,.75)` dark |
| **Substations** | `#e8368f` (magenta — was conflicting red `#ef4444`) | circle r4–6 | stroke `#ffffff` 1.5px (reads on white/black/imagery) |
| **Flood (SFHA)** | fill `#22b8cf` @ alpha **0.22 + 45° hatch** (not solid) | hatch+outline | outline `#1098ad` 1px |

Amber + magenta + indigo + cyan stay separable under deuteranopia/protanopia and in greyscale; circle-vs-fill geometry disambiguates the one protan-risky pair. Legend swatch for `result` should use a mid-viridis (`#22a884`), not `#1a9850`.

### 4.4 Selected-parcel highlight (replace `HIGHLIGHT_COLOR = "#f97316"` in `map/constants.ts`)
Hue-free **double casing** (collides with no categorical hue, reads on every basemap): **3px outer `#ffffff` + 1.5px inner `#111827`.** Implement as two stacked highlight line layers. Never use the azure UI accent for map selection.

Labels (when added): Positron → text `#1b1f24`, halo `rgba(255,255,255,.9)` 1.25px · Dark Matter → text `#e8eaed`, halo `rgba(0,0,0,.75)` 1.25px · Satellite → text `#ffffff`, halo `rgba(0,0,0,.8)` 1.5–2px.

---

## 5. Component specs (all reference the tokens above)

**Buttons** — 4 variants on heights `--ctl-h-sm:28 / -md:32 / -lg:40` (44 on `pointer:coarse`, already enforced):
- Primary (filled): `height:var(--ctl-h-md); padding:0 var(--space-3_5); border-radius:var(--radius-sm); font:600 13px; background:var(--accent); color:var(--accent-contrast);` hover→`--accent-hover`.
- Secondary (outlined): `background:var(--bg-sunken); border:1px solid var(--border-interactive);`
- Ghost: transparent; hover `background:var(--bg-sunken)`.
- Icon: 32×32 (28 dense / 44 coarse), `border-radius:var(--radius-sm)`, icon 18–20.
- State layers (`::before`, `currentColor`): hover 8% · focus 10% · pressed 10–12%; press `transform:translateY(1px)`; disabled `opacity:.5`; `transition:var(--dur-fast) var(--ease-standard)`.

**Segmented control (ScoringControl/ThemeToggle/BasemapControl)** — keep the radiogroup + roving-tabindex (`aria-checked`, arrow keys, `tabindex -1/0`) **exactly as shipped**. Container `border-radius:var(--radius-md); padding:2px; background:var(--bg-sunken)`. Segment `height:var(--ctl-h-sm); border-radius:var(--radius-sm); font:500 12px; flex:1` (equal width ≤4). Add a single sliding **thumb** via `transform:translateX(...)`, `transition:var(--dur-base) var(--ease-standard)`. **Cap at 5 segments** (basemap auto/light/dark/streets/satellite is the ceiling — a 6th → switch to a Select).

**Slider (opacity)** — track 4px `border-radius:2px`; thumb 16px desktop / 20px coarse; `accent-color:var(--accent)`; thumb focus→`--focus-ring`; `step:5`; `aria-valuetext:"60%"`; value label in `.num`; 44px hit row on coarse. Native arrow-keys satisfy 2.5.7.

**Tag / Badge / Chip / Score-chip:**
- `tag` (neutral): pill `--radius-pill`, 11px, `background:var(--bg-elevated)`, 1px border, no icon.
- `badge` (status): `-subtle` bg + `-text` text + `-border`; **must carry a Lucide icon** (`Check`/`AlertTriangle`/`X`) — color is the secondary channel (1.4.1).
- `chip` (interactive/removable): `height:24px`; hover/focus `border:var(--accent)`; trailing `X` icon-button `aria-label="Remove"`.
- `score-chip` (data): square `--radius-xs`, `min-width:30px/height:24px` (lg 44/40), background `scoreColorCss(score)`, **text `scoreTextColor(score)`**, `.num`, always prints the number.

**Factor bar** — track 6px `--radius-xs`; `role="meter" aria-valuemin/max/now`, `aria-label="<factor> contribution"`; label + value carry meaning (color-not-alone OK); value in `.num`.

**List row (ResultsPanel)** — selection = **3px left accent bar** (`border-left:3px solid var(--accent)`), NOT a 1px ring (avoids reflow inside `overflow:hidden`); hover `background:var(--bg-elevated)`; rank numeral `.num` `--text-faint`; row height ~56px standard; `transition:background var(--dur-fast)`. Bidirectional hover: row↔parcel highlight. Compare table: right-align + `.num`, `thead` `position:sticky;top:0`, best-in-row = `font-weight:700` + leading `▲`/`ArrowUp` marker (not color), best value uses `--positive`.

**Panel section header** — keep existing `.panel-section__title` (11px / 600 / uppercase / `letter-spacing:.06em` / `--text-faint`). Make Layers a collapsible accordion so opacity sliders don't push scoring off-screen.

**Bottom sheet** (`BottomSheet.tsx`, nonmodal/persistent — no scrim, no focus trap): top corners `--radius-xl`, `box-shadow:var(--ring-hairline), var(--shadow-lg)`. See §6 for snap/handle/scroll fixes.

**FAB (DrawFab)** — 56×56, `border-radius:var(--radius-pill)`, icon 24 (`PencilLine`), `box-shadow:var(--ring-hairline), var(--shadow-lg)`, bottom-RIGHT (clear of center grabber).

**Modal** — `--radius-xl`, `--surface-elevated`, `box-shadow:var(--ring-hairline), var(--edge-highlight), var(--shadow-xl)`, focus-trapped, Esc to close, `--dur-slow var(--ease-decelerate)` in.

**Tooltip vs popover** — tooltip (`role="tooltip"`, non-interactive): hover-in 150ms / instant on focus, `max-width:280px`, Dismissible(Esc)+Hoverable+Persistent (1.4.13), `--shadow-md`. Anything with buttons (parcel quick-actions) is a **popover** `role="dialog"`, focus-trapped, Esc, `--shadow-md`.

**Loading ladder** — <300ms: nothing · 0.3–10s: skeleton (keep `skeleton-row` + pulse) · >10s: determinate `<progress>`. Toasts: `aria-live=polite`+`role=status` success (auto 4–6s, pause-on-hover); `role=alert` error (persistent + recovery action); max 2 stacked, `--shadow-md`, on mobile offset above the sheet peek.

---

## 6. Layout

### Desktop (`AppShell.tsx` — verdict OVERRODE the finding; do NOT raise rail mins or right default)
- Keep the resizable 3-pane CSS grid: `left 320px | 6px handle | minmax(map) 1fr | 6px handle | right 360px`.
- **`MIN_MAP_PX`:** raise from 320 to a viewport-relative floor `clamp(360px, 38vw, 520px)` (never a flat 480 — it silently inverts near the 1024–1280 laptop band).
- **Keep `LEFT_MIN≈240`, `RIGHT_MIN≈280`, right default 360.** Do NOT bump to 280/340/384 — raising rail mins *starves* the map on common laptops, and the "compare-table overflow" rationale is false (table is `width:100%`, ≤4 short columns).
- **Update `aria-valuemin/valuemax` on the resize separators in lockstep** with whatever bounds you set (currently wired to LEFT_MIN/RIGHT_MIN).
- Both rails collapsible to 0 with persisted toggle + `[` / `]` shortcuts; persist in `geo.panels.v1`. Float non-input chrome over the map: zoom/north/scale bottom-right, Legend bottom-left, Basemap popover top-right, DrawToolbar top-center. Dock only ScoringControl + LayerControl + AgentChat. Optional `Cmd/Ctrl+K` command palette scoped to app verbs (score for…, toggle layer…, basemap…, go to parcel…, compare top 3, export, ask agent…).

### Mobile (`BottomSheet.tsx`, `DrawFab.tsx`, `MapView.tsx`, `index.html`)
- Sheet stays **nonmodal/persistent**. Snap points: **peek `104px` fixed**, half `52dvh`, full `92dvh` (use **`dvh`**, not `vh` — `vh` overflows mobile Safari). Tokens: `--sheet-peek:104px; --sheet-half:52dvh; --sheet-full:92dvh; --sheet-radius:16px; --fab:56px; --map-ctrl:44px; --gutter:16px;`.
- **Fix order (bugs first):** ① move `touch-action:none` OFF `.bottom-sheet` (it silently kills list scroll) → keep it only on `.bottom-sheet__handle`; `.bottom-sheet__content { touch-action:pan-y; overscroll-behavior:contain; }`; only resize-drag when `scrollTop===0`. ② `index.html`: drop `maximum-scale=1` (WCAG 1.4.4/1.4.10), keep `viewport-fit=cover`; add light/dark `theme-color` metas.
- **Then handle accessibility:** the grabber is a `role=button tabIndex=0` with no `onKeyDown`/`onClick` → fails **2.1.1 (A)** + **2.5.7 (AA)**. Make it a real `<button>` that BOTH taps (`onClick`) and keys (Enter/Space) to cycle peek→half→full (reuse the 48px threshold to split tap vs drag); add `aria-expanded`, action label ("Expand/Collapse results", not "Drag to resize"), and an `aria-live` detent announcement. Add an explicit chevron collapse in a sticky sheet header as the **primary** affordance; tap-cycle secondary. (Hand-roll; only fall back to `vaul` `modal={false}` if scroll-vs-resize can't be done cleanly.)
- FAB: `bottom:calc(var(--sheet-peek)+12px+env(safe-area-inset-bottom)); right:calc(var(--gutter)+env(safe-area-inset-right));`. Add a 48px **Locate** control in the thumb zone stacked above the FAB; zoom stays top-right. Bump `.maplibregl-ctrl button` to 44px on `(pointer:coarse)`; offset bottom-left scale/attrib up by a live `--sheet-h`.

---

## 7. Implementation checklist (mapped to files)

**P0 — outright bugs / compliance (ship first)**
1. `BottomSheet.tsx` + `components.css:171/183`: scope `touch-action:none` to the handle only; add `pan-y`+`overscroll-behavior:contain` to content; resize-drag only at `scrollTop===0`.
2. `index.html`: remove `maximum-scale=1`; keep `viewport-fit=cover`; add `theme-color` metas.
3. `BottomSheet.tsx`: real `<button>` grabber with `onClick`+`onKeyDown` cycling peek→half→full; `aria-expanded`+`aria-live`+action label; sticky-header chevron collapse.
4. `components.css`: replace the 7 `#dc2626` (lines 448,476,758,759,1214,1313,1314) with `--danger`/`--danger-text`/`--danger-subtle`/`--danger-border`.

**P1 — foundations + the "amateurish" fixes (highest leverage, parallelizable)**
5. `global.css`: add COLOR (status, accent sub-tokens, `--surface-elevated`, `--border-interactive`), SPACING, RADIUS, theme-split ELEVATION, MOTION, `--focus-ring`; revise `--accent` to azure (`#1e5fd9`/`#4f8cff`). Keep existing role names.
6. `main.tsx`: import Inter + JetBrains Mono `wght.css`. `global.css`: swap `body` font to `var(--font-sans)`; add `.num`/`.code`; apply the type scale. `vite.config.ts`: add **Fontaine** plugin + `optimizeDeps.include:['lucide-react']`.
7. Install pinned `lucide-react`; build the central `Icon` wrapper; **delete all unicode glyphs** across `DrawToolbar`, `ThemeToggle`, `DrawFab`, `ResultsPanel`, `BasemapControl`, `LayerControl`, `Legend`, `ParcelDetail`, `AgentChat`, `ShareControl`, `Coachmarks`, `EmptyState` per §3.
8. `theme/basemap.ts`: `BACKGROUND_COLOR` → `#f2f2f0`/`#0e0e0e`; `raster-opacity` → 1.0 for light/dark `lightDarkSource`.
9. `map/layers.ts`: `SCORE_RAMP` → 10-stop; swatches → parcels `#3b5bdb`, transmission `#f08c00`, substations `#e8368f`, sfha `#22b8cf`, result legend `#22a884`; deck.gl alpha 205; add per-basemap score casing (dark hairline on light/satellite, light on dark) + score-scaled outline salience.
10. `map/constants.ts`: replace `HIGHLIGHT_COLOR` with the white+`#111827` double-casing highlight layers.

**P2 — component + layout polish**
11. `components.css`: migrate ~300 hardcoded px/radius to tokens (5→6, 10→12, 26→pill); selection 3px left bar; segmented sliding thumb; button state layers; ensure score chips use `scoreTextColor`; `overlay-panel` ring+shadow on all floating panels; map-control 44px on coarse; bottom-left controls offset by `--sheet-h`.
12. `AppShell.tsx`: `MIN_MAP_PX` → `clamp(360px,38vw,520px)`; keep rail mins/right default; sync resize `aria-valuemin/valuemax`; rail collapse + `[`/`]`; float Legend/Basemap/zoom, dock only Scoring/Layers/Agent.
13. `DrawFab.tsx`/`components.css`: FAB 56px + token shadow + `--sheet-peek`-anchored bottom; add Locate thumb-zone control in `MapView.tsx`.
14. Optional: `Cmd/Ctrl+K` command palette; preload a default scored Kern AOI so first paint is never blank (`EmptyState` single primary action "Draw area" + "Load sample AOI").

**Guardrails:** Inter family string is `'Inter Variable'`; grep lucide v1 icon names post-install; never use teal/green for chrome (viridis collision); keep `outline`-based focus (don't outset-double-ring into `overflow:hidden` rows); elevation tokens must have a `[data-theme="dark"]` override.
---

## Addendum — layout pivot (implemented): map-first floating controls

§6's docked 3-pane sidebar plan was superseded during implementation by a user-directed
**map-first** layout (philosophy: *"separate things, keep each minimal"*). What shipped:

- **No left sidebar.** A full-width floating **TopBar** over the map holds the **DrawToolbar**
  (left) and **MapControls** (right): single-purpose icon buttons — Assistant, Scoring, Layers,
  Share & save, Settings, Theme. Each opens **one minimal popover** (`.map-panel`, non-blocking
  `role="dialog"`, Esc / click-outside to close, focus moves in on open and returns on close).
- **Settings** popover = Basemap selector + "Take a tour" (Help). **Default basemap = satellite.**
- **Theme** is a compact light↔dark icon button (replaces the 3-segment row that collided with the
  toolbar).
- The only docked surface is **Results** (right pane on desktop, bottom sheet on mobile) — **results
  only**. The **Detail** section renders only when a parcel is selected; the draw **Area** readout
  shows only while drawing.
- `Sidebar.tsx` and `DrawFab.tsx` were removed; new files: `TopBar.tsx`, `MapControls.tsx`,
  `Icon.tsx`. All foundations in §§1–5 (tokens, type, icons, map palette, components) are unchanged
  and still authoritative.
