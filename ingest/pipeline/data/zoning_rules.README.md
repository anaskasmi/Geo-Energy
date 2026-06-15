# zoning_rules.csv — provenance & [CONFIRM] status

Curated lookup (FR-A2): for each Kern County base zoning district code, whether each
site-selection land use is **by_right**, **conditional** (Conditional Use Permit / CUP), or
**prohibited**. Long format, one row per `(zone_code, use_case)`.

- **Columns:** `zone_code, zone_name, use_case, permission, basis`
- **Use cases:** `solar` (utility-scale PV), `wind`, `storage` (stand-alone BESS), `data_center`.
  Tokens reflect the **utility-scale / primary** use — accessory rooftop solar and small wind
  systems are separately by-right in most districts.
- **Permissions:** `by_right | conditional | prohibited`.

## Source

Derived from the **official Kern County Zoning Ordinance, Title 19 (effective Feb 16, 2026)**,
read in full from the Kern County Planning & Natural Resources Department:
- https://psbweb.co.kern.ca.us/planning/pdfs/KCZOFeb2026.pdf
- Planning Dept. canonical page: https://kernplanning.com/planning/planning-documents/zoning-ordinance/

The `basis` column cites the controlling chapter/section per district (e.g. 19.12.030.G for
the Exclusive Agriculture CUP hook; 19.64.020.B for by-right production wind in the WE
overlay).

## Confidence & [CONFIRM]

- **HIGH** — chapter→district mapping; accessory-vs-primary solar; districts where primary
  solar / commercial wind require a CUP; by-right production wind in the WE overlay.
- **MEDIUM** — `data_center`: the term does not appear in Title 19; industrial by-right rests
  on a Planning-Director "similar-use" determination (19.08.030–080) treating it like the
  by-right office/R&D/computer/warehouse uses in M-1/2/3.
- **LOW** — `storage`: "battery"/"energy storage"/"BESS" appear nowhere in the ordinance;
  every cell is by analogy to "electric power generating plant" / "utility substation".
  Kern processes stand-alone BESS as a CUP in practice (e.g. A-district approvals under
  19.12.030), which is reflected here.

**Codes flagged for planning-department confirmation:** all `storage` cells; `data_center`
for A/A-1/NR/RF/PL/CO/CH; `SP` and `KRC` (depend on the adopted specific plan / underlying
base district); `FPP` (depends on base district + floodplain permit); and `GI`/`OTHER`
(no ordinance basis — GIS artifacts / unmapped, defaulted to `conditional`).

District-code corrections confirmed against Ch. 19.10 (these differ from common assumptions):
`MP` = Mobilehome Park (residential, **not** Industrial Park — Kern has no M-P district);
`MS` = Mobilehome Subdivision (interim, follows R-1, **not** Mineral/Petroleum);
`P` = Automobile Parking (interim, **not** Public Facilities); `GI` is **not** a Title 19
district. Mineral/oil resources are handled by `NR`, `DI`, and the PE combining overlay.

At build time the zoning fetcher fills any district present in the data but absent here with a
conservative `conditional` default (flagged in `basis`) so the scoring engine never treats an
unmapped district as by-right.
