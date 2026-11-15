# Methods, assumptions and limitations

This document summarises how the analysis was done, which inputs are sourced and which are
illustrative, what was corrected during development, and what the results can and cannot support.
Detailed numbers are in the generated reports:

- [Data audit](../reports/data_audit/AUDIT_REPORT.md) and [data review](../reports/data_audit/STAGE1_REVIEW.md)
- [Capture prices and merchant revenue](../reports/stage2/STAGE2_REPORT.md)
- [Cash-flow and DSCR analysis](../reports/stage3/STAGE3_REPORT.md)

## 1. Data and validation

| Source | Used for | Period |
|---|---|---|
| PVGIS 6 (EU JRC) | Hourly PV output, main production series | 2021–2024 |
| PVGIS 5.3 (EU JRC) | Comparison (model uncertainty) | 2021–2023 (API covers 2005–2023 only) |
| KNMI stations 269 Lelystad, 273 Marknesse | Timing and consistency checks (measured irradiance) | 2021–2024 |
| Energy-Charts NL day-ahead prices (Bundesnetzagentur / SMARD data) | Market prices | 2021–2024 (2015–2020 used only in the data-sufficiency check) |

All series are converted to UTC, labelled by the start of each hour. Checks performed:

- record counts against the expected 35,064 hours, duplicates, step sizes, physical bounds;
- daylight-saving check: converting the UTC price index to Dutch local time reproduces exactly
  four 23-hour and four 25-hour days;
- time alignment: lagged correlations between independent series peak at a shift of 0 h;
- PVGIS 6 JSON output has no timestamps; the rebuilt index was compared row by row with the
  timestamped CSV output for every year (including 29 February 2024): identical;
- independent solar-position calculation (NOAA equations; max. 0.34° from PVGIS 5.3's own sun height)
  to detect zero output while the sun is up.

Main data findings:

- **PVGIS 5.3 records 26 missing satellite hours as zero output** (sun height also 0, no
  reconstruction flag). In those hours the computed sun elevation was 10–61° and the nearest KNMI
  station measured a median of 322 W/m². PVGIS 6 reports the same hours as missing.
- **PVGIS 6 gives 4.1–4.4 % more energy than 5.3**, although the irradiance on the panel is identical
  (within 0.01 %). Controlled PVGIS 6 runs show that the updated module coefficients explain about
  2.5 of the 4.1 percentage points; the rest is not attributed.
- PVGIS and KNMI were **not** compared with an error metric: tilted-plane satellite estimates and
  horizontal station measurements are different quantities. KNMI is used for timing and year ranking only.

## 2. Methods

**Capture price.** With hourly energy $E_t$ (MWh) and price $p_t$ (€/MWh):

$$p_{cap} = \frac{\sum_t E_t p_t}{\sum_t E_t} = \bar p + \frac{\mathrm{Cov}(E,p)}{\bar E}$$

The capture rate is $p_{cap}/\bar p$. Production and prices are always kept paired by hour. The
covariance is split into a predictable timing part and a weather part with the law of total
covariance over month × hour cells. Year-on-year revenue changes are split into volume and price
effects with an LMDI decomposition, which adds up exactly.

**Missing hours.** The 33 null hours in PVGIS 6 are filled with a KNMI-based month × hour ratio.
Zero-filling and complete-case analysis are reported alongside; revenue differs by at most 0.45 %.

**Cash flow and DSCR.** For one operating year:

```
cash revenue − operating costs = operating cash flow (EBITDA)
EBITDA − tax − maintenance reserve = CFADS (cash flow available for debt service)
DSCR = CFADS / (interest + principal due in the year)
```

Debt is a 15-year annuity, $A = D\,r/(1-(1+r)^{-n})$. Each historical year (2021–2024) is replayed as
operating year 1. Break-even contract prices and maximum debt at a target DSCR are solved
numerically and checked against closed-form expressions.

## 3. Assumptions: sourced and illustrative

Every parameter, with its source and page number, is listed in
[`configs/stage3.yaml`](../configs/stage3.yaml) and repeated in the Stage 3 report.

- **Sourced** (PBL, *Eindadvies basisbedragen SDE++ 2025* and erratum of June 2025): capex €426/kWp;
  fixed opex €15.9/kWp/yr; variable opex €1.9/MWh; imbalance cost €7.0/MWh long term (€18.1/MWh
  calculated for 2023, used as a stress case); 80 % debt; 4.0 % interest; 15-year tenor; 25.8 % tax;
  0.64 %/yr degradation; 10 MWp reference size with grid connection at 50 % of DC capacity.
- **Illustrative**: PPA price €60/MWh (tested €40–70); 6-month debt service reserve; DSCR reference
  levels 1.2 and 1.5; 35° south-facing tilt; contract clauses; imbalance cost borne by the project
  in the PPA case; idealised curtailment at negative prices.

**Important context.** PBL's financing parameters describe projects that receive the SDE++ subsidy.
The subsidy is excluded here. "DSCR at 80 % debt" therefore mixes a subsidised financing structure
with unsubsidised revenue. For that reason the main financing results are **break-even prices** and
**maximum debt at a target DSCR**, with 80 % used only as a reference.

No verified Dutch PPA price was found. The only verified reference is LevelTen's continental P25 index
of PPA *offers* (€63.64/MWh Q3 2024; €62.31/MWh Q4 2024, as reported by PV Tech).

## 4. Corrections made during development

| Issue | Correction | Effect |
|---|---|---|
| PBL's 855 full-load hours is a 15-year average that already includes degradation; year 1 had been calibrated to it and degradation then applied again | Year 1 calibrated to 855 ÷ 0.956 = 894 kWh/kWp | Conservative-case DSCR (2024, PPA) 0.81 → 0.88 |
| A first missing-data diagnostic used hourly-average sun elevation and over-counted near sunrise | Elevation evaluated at the sampling instant (HH:11) | Count stable at 26 hours for thresholds 2–5° |
| Inverter replacement entered as a fixed €130,000, which broke the scale-invariance test | Expressed per kWp (€13/kWp), pre-funded over years 1–13 | Model scales consistently |
| A revenue decomposition based on shares of log changes is unstable when effects have opposite signs | Replaced by LMDI | Exact decomposition |

## 5. Cost components: double-counting check

| Item | Where it appears | Assessment |
|---|---|---|
| Grid connection | One-off in capex (€43/kWp) and an annual fee (€2.0/kWp/yr) | Different items; not double counted |
| Profile cost (solar sells when prices are low) | Already in the hourly capture prices | Not added separately |
| Imbalance cost | €7/MWh per MWh delivered | PBL's erratum defines it net of production-related profile cost, i.e. imbalance only; consistent |
| Variable O&M €1.9/MWh | Per MWh delivered | Composition not itemised by PBL; a small overlap (≈ €17k/yr, ≈ 0.06 DSCR) cannot be excluded |
| Land lease | Excluded by PBL | Under-counted; tested as a stress (€5/kWp/yr) |

## 6. Price data validation status

Prices come from the Energy-Charts API (Fraunhofer ISE), which republishes day-ahead results from
SMARD (Bundesnetzagentur) under CC BY 4.0. The series is complete (35,064 hours), has no duplicates,
handles daylight saving correctly and contains known market events (e.g. −€500/MWh on 2 July 2023).

**The prices have not been independently cross-checked against the official ENTSO-E Transparency
Platform.** The ENTSO-E parser (`src/solarrisk/ingestion/prices.py`) is implemented and unit-tested
on a synthetic file, and the comparison runs automatically when an API token is available:

1. Register at <https://transparency.entsoe.eu> and verify the account.
2. Email `transparency@entsoe.eu` with subject `RESTful API access` and the registered email address
   in the body ([procedure](https://transparencyplatform.zendesk.com/hc/en-us/articles/12845911031188)).
3. Generate a token under *My Account Settings* and set it as `ENTSOE_API_TOKEN` (never commit it).
4. Rerun the scripts; the audit report then shows the ENTSO-E vs Energy-Charts comparison.

## 7. Why no predictive model was built

A data-sufficiency check (`scripts/check_data_sufficiency.py`,
[`reports/data_sufficiency/`](../reports/data_sufficiency/)) tested whether capture-rate risk could
be predicted:

- Prices for 2015–2024 are available, but they cover different market regimes: annual means between
  €32 and €242/MWh, and almost no negative prices before 2020. The capture rate for this site profile
  fell from 1.09 (2015) to 0.55 (2024). That is a trend driven by solar build-out, not variation around
  a stable mean.
- Effective sample size: annual n = 10 and trending; monthly n = 120 with lag-1 autocorrelation 0.80,
  giving an AR(1) effective sample size of about 13; daily n ≈ 3,640 (≈ 1,860 effective), still
  containing the regime trend.
- The public day-ahead solar forecast series for the Netherlands is inconsistent across years (missing
  2016–2017; resolution change; a lower peak in 2024 than in 2023). Without a consistently defined
  solar-penetration variable, a model would learn the calendar rather than the mechanism. Using
  observed weather instead of forecasts would leak information.
- There are no observed defaults, so there is no target for a default model.

The analysis therefore uses historical replays, stress tests and break-even calculations instead.

## 8. What the tests do and do not show

The 32 tests check arithmetic, units, time conventions, parsers, identities (e.g. the capture-price
identity, exact decompositions), the annuity schedule and invariances (DSCR does not depend on plant
size when everything scales proportionally). They do **not** show that input assumptions are
realistic, that 2021–2024 is representative of future years, that hourly data capture clipping within
the hour, or that the contract clauses match real contracts.

## 9. Limitations

- Production is modelled (PVGIS), not measured; one site, one orientation (35° south). Dutch parks
  often use lower tilts or east-west layouts.
- Hourly clipping understates clipping within the hour.
- Four historical years are four different regimes; they are replays, not a probability distribution.
- A DSCR below 1 means cash did not cover scheduled debt service in that year. It is not a default,
  and no default probabilities are estimated.
- Not modelled: subsidies, guarantees of origin, working capital, reserve replenishment, cash sweeps,
  tax-loss carry-forward, counterparty default, a merchant period after a shorter PPA.
- The project does not represent any bank's methods, data or lending terms.
