# GEO-28 — Accessibility (WCAG 2.2 AA for an interactive map)

The [RESEARCH] question: how do you make a **draw-on-a-map** scoring tool usable without a mouse
and without relying on colour, to WCAG 2.2 AA? The principle we follow: the map is an *enhancement*,
not the only path — every result is also available as **structured text + numbers**, fully keyboard
and screen-reader operable. Below is what each relevant success criterion required and how we meet it.

## How we meet the relevant 2.2 AA success criteria

| SC | Requirement | How this app meets it |
|----|-------------|------------------------|
| **1.4.1 Use of Color** | Don't convey info by colour alone | Suitability is shown by colour **and** the numeric 0–100 score chip **and** the rank number **and** the ranked list order. The detail panel restates each factor as text + value. |
| **1.4.3 Contrast (Min)** | 4.5:1 text / 3:1 large | Control text uses theme tokens on solid surfaces. Score chips pick near-black/white text by background luminance (`scoreTextColor`). Floating map controls now sit on a **near-solid backing** (was translucent) so text keeps contrast over any basemap. |
| **1.4.11 Non-text Contrast** | 3:1 for UI components/graphics | Borders (`--border-strong`), the selected-parcel outline (`#f97316`, 3px), and focus rings (2px accent) meet 3:1. |
| **2.1.1 Keyboard** | All functionality via keyboard | Toolbar, scoring radiogroup (arrow keys), layer toggles, sort/filter, results list (Tab + Enter/Space), compare, and detail are all keyboard operable. The MapLibre canvas is keyboard-pannable/zoomable. Drawing a polygon by pointer is enhanced by the keyboard **shortcuts** in GEO-30 and, crucially, by the **agent chat** (GEO-27): "score parcels near Mojave" reaches the same scored results with **no drawing at all**. |
| **2.3.3 Animation from Interactions** / reduced motion | Honour `prefers-reduced-motion` | Global CSS disables transitions/animations and instant-scrolls; JS paths (`flyTo`, `scrollIntoView`) check the same media query and jump instead of animating. |
| **2.4.7 Focus Visible** | Visible keyboard focus | Global `:focus-visible` 2px accent ring on every interactive element + the map canvas. |
| **2.4.11 Focus Not Obscured (Min)** *(new in 2.2)* | Focused element not fully hidden | Focusable content lives in scrollable panes/toolbars that are never overlaid by sticky chrome; the focus ring has `outline-offset` so it isn't clipped. |
| **2.5.7 Dragging Movements** *(new in 2.2)* | Drag actions need a non-drag alternative | Polygon vertices are placed by **clicking** (terra-draw), not dragging. The bottom sheet (mobile) snaps via the drag handle **and** taps. Map pan has button/keyboard equivalents (NavigationControl + arrow keys). |
| **2.5.8 Target Size (Min)** *(new in 2.2)* | ≥ 24×24 CSS px | Toolbar/segment buttons ≥ 28px; the banner-close and compare hit areas ≥ 24px. GEO-29 bumps mobile targets to 44px. |
| **4.1.3 Status Messages** | Announce status without focus | A visually-hidden `role="status" aria-live="polite"` region in the results panel announces scoring state and a **textual equivalent of the ranked results** ("12 of 30 parcels shown for utility solar; highest score 87 at rank 1, APN …"). The draw-area readout and explain breakdown are also live/`status`. |

## The screen-reader path to the same task

1. Pick a use case in the **Scoring** radiogroup (arrow keys).
2. Get a scored area without drawing — via the **agent chat** (GEO-27) "score near <place>", or by panning/zooming and using the (pointer) draw as an enhancement.
3. The **live region** announces the count + top result; the **ranked list** is a list of buttons, each labelled "Rank N, APN …, suitability S of 100, A acres".
4. Activating a row selects the parcel; the **Detail** panel reads the per-factor breakdown as text (label, raw value, weight, points), the plain-language "why this rank", and any Stage-A exclusions.

Net: colour is never load-bearing, every control is keyboard reachable with a visible ring, motion respects the OS setting, and the full result set is available as announced text — the map is the nice-to-have, not the requirement.

Sources: [WCAG 2.2 (W3C)](https://www.w3.org/TR/WCAG22/), [SC 2.5.8 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html), [SC 2.5.7 Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html), [SC 2.4.11 Focus Not Obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html).
