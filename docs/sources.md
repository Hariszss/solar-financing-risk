# Sources and attribution

Accessed November 2026. Raw downloads are **not** redistributed in this repository; the scripts
retrieve them from the original providers.

## Data

| Source | Use | Attribution / terms |
|---|---|---|
| **PVGIS 6** (European Commission, Joint Research Centre) — API `https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6` | Hourly PV output 2015–2024 | Source: European Commission, JRC, PVGIS. The API's `/reference/license` endpoint lists the European Union Public Licence v1.2; see `/reference/citation` for the recommended citation. PVGIS 6 is marked "actively under development". |
| **PVGIS 5.3** (EC JRC) — `https://re.jrc.ec.europa.eu/api/v5_3/seriescalc` | Comparison 2021–2023 | Source: European Commission, JRC, PVGIS. |
| **KNMI** hourly station data — `https://www.daggegevens.knmi.nl/klimatologie/uurgegevens` | Measured irradiance, Lelystad (269) and Marknesse (273) | "SOURCE: ROYAL NETHERLANDS METEOROLOGICAL INSTITUTE (KNMI)" (as stated in the data files). KNMI notes that hourly series are inhomogeneous and not suitable for trend analysis. |
| **Energy-Charts** price and forecast API (Fraunhofer ISE) — `https://api.energy-charts.info` | NL day-ahead prices; NL forecasts in the data-sufficiency check | Licence stated by the API: "CC BY 4.0 (creativecommons.org/licenses/by/4.0) from Bundesnetzagentur \| SMARD.de". |
| **ENTSO-E Transparency Platform** — `https://web-api.tp.entsoe.eu/api` | Optional cross-check (not yet run) | Requires a personal API token. |

## Cost and financing assumptions

- PBL Netherlands Environmental Assessment Agency (2025), *Eindadvies basisbedragen SDE++ 2025*
  (S. Lensink & E. Eggink, eds.), publication 5472.
  <https://www.pbl.nl/system/files/document/2025-02/pbl-2025-eindadvies-sde-plus-plus-2025-5472.pdf>
  Pages used: 16, 29–33 (financing: interest, debt share, tax, inflation, tenor), 60, 70, 72–74
  (grid connection and fixed operating costs), 76–78 (full-load hours, degradation, clipping),
  81 (Tabel 5.23, ground-mounted 1–20 MWp reference installation).
- PBL (6 June 2025), *Aanpassingen aan advies SDE++ 2025* (erratum; imbalance costs for solar).
  <https://www.pbl.nl/system/files/document/2025-06/pbl-2025-erratum-eindadvies-sde-2025-en-addendum-5472.pdf>
- PV Tech (17 Oct 2024), LevelTen European PPA report, Q3 2024 (P25 solar €63.64/MWh).
  <https://www.pv-tech.org/solar-stabilisation-in-conversation-levelten-latest-european-ppa-report/>
- PV Tech (30 Jan 2025), LevelTen Continental Solar Index Q4 2024 (€62.31/MWh; based on offers).
  <https://www.pv-tech.org/eu-ppa-market-defined-by-falling-prices-corporate-demand-and-political-shifts/>
- Jordan, D.C. & Kurtz, S.R. (2013), Photovoltaic degradation rates — an analytical review,
  *Progress in Photovoltaics* 21(1), 12–29 (cross-reference for degradation).
  <https://www.osti.gov/biblio/1045052>
- Solar position: NOAA Global Monitoring Laboratory, *General Solar Position Calculations*
  (implementation validated against PVGIS sun height).

Typical DSCR levels (1.2 / 1.5) and a 6-month reserve are illustrative reference values, not taken
from a verified lending source.

## Public context that motivated the project

The project was inspired by publicly reported bank financing of Dutch solar projects. It does not
use, represent or reproduce any bank's internal information.

- Mercom India (1 Aug 2022), *ENCAVIS refinances 74 MW of Dutch solar projects*.
  <https://mercomindia.com/encavis-refinances-74-mw-solar-projects>
