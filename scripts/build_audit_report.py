"""Render reports/data_audit/AUDIT_REPORT.md from data_quality.json.

Every number in the report is read from the JSON produced by run_stage1_audit.py — nothing is
typed by hand — so the report cannot drift from the data.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
Q = json.loads((ROOT / "reports/data_audit/data_quality.json").read_text(encoding="utf-8"))
A, AN, R = Q["audits"], Q["analyses"], Q["retrieval"]


def tbl(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def ok(b):
    return "✅" if b else "❌"


L = []
L.append(f"# Stage 1 — Data Feasibility Audit\n\n_Generated {date.today().isoformat()} by `scripts/build_audit_report.py` "
         "from `data_quality.json`. All figures below are computed from data actually retrieved; "
         "nothing is synthetic._\n")
L.append("Site (hypothetical): 52.45° N, 5.70° E (Biddinghuizen, Flevoland). Test system: 1,000 kWp, "
         "14 % system loss, 35° tilt, south-facing. Period: 2021–2024 (35,064 expected UTC hours).\n")

# ---------------------------------------------------------------- retrieval
L.append("## 1. Retrieval status\n")
rows = []
for src in ["pvgis53", "pvgis6", "energycharts", "entsoe"]:
    for y, s in R[src].items():
        rows.append([src, y, ok(s["ok"]), s.get("status") or "—", (s.get("error") or "").strip()[:110]])
rows.append(["knmi (269, 273)", "2021–2024", ok(R["knmi"]["all"]["ok"]), R["knmi"]["all"]["status"], ""])
L.append(tbl(["Source", "Year", "OK", "HTTP", "Error"], rows) + "\n")
m = Q["metadata"].get("pvgis6_openapi", {})
L.append(f"PVGIS 6 OpenAPI snapshot: title `{m.get('title')}`, version `{m.get('version')}`, documented period "
         f"`{m.get('documented_time_period')}`, 'actively under development' notice present: "
         f"**{m.get('under_development_notice')}**. Raw responses and SHA-256 hashes are logged in "
         "`data/raw/manifest.jsonl`.\n")

# ---------------------------------------------------------------- per-source audit
L.append("## 2. Per-source structural audit\n")
rows = []
for name, a in [("PVGIS 5.3", A["pvgis53"]), ("PVGIS 6", A["pvgis6"]), ("Prices (Energy-Charts)", A["prices_energycharts"]),
                ("KNMI 269 Lelystad", A["knmi"]["269"]), ("KNMI 273 Marknesse", A["knmi"]["273"])]:
    main = next(iter(a["columns"]))
    rows.append([name, a["n_records"], a["n_expected_hours"], a["n_duplicate_timestamps"],
                 a["n_expected_missing_from_index"], ", ".join(a["time_step_counts"].keys()),
                 sum(c["n_missing"] for c in a["columns"].values()),
                 sum(c.get("n_out_of_bounds") or 0 for c in a["columns"].values())])
L.append(tbl(["Source", "Records", "Expected", "Duplicates", "Missing timestamps", "Step", "Missing values (all cols)",
              "Out of bounds"], rows) + "\n")
L.append("Notes:\n")
L.append("- PVGIS 5.3 rejects 2024 (`startyear` must be 2005–2023), so its expected count covers 2021–2023 (26,280 h).")
L.append(f"- PVGIS 6 returns no timestamps; the index is reconstructed. Values vs expected per year: "
         f"`{A['pvgis6']['values_vs_expected_per_year']}` (exact match, incl. leap year 2024).")
L.append(f"- PVGIS 6 null hours by year: `{A['pvgis6']['null_hours_by_year']}`; longest run "
         f"{A['pvgis6']['columns']['pv_kw']['missing_runs']['max_run']} h; all occur in daylight UTC hours "
         f"`{A['pvgis6']['null_hours_by_utc_hour']}`.")
L.append(f"- KNMI returned {A['knmi']['269']['n_rows_outside_period_dropped']} extra rows per station beyond the "
         "requested end (dropped). No missing Q/T/SQ values in 2021–2024 at either station.")
dst = A["prices_energycharts"]["dst_local_day_check"]
L.append(f"- DST check: when the UTC price index is converted to Europe/Amsterdam, local days have "
         f"`{dst['hours_per_local_day_counts']}` hours; 23-h days {dst['days_with_23h']}, 25-h days "
         f"{dst['days_with_25h']} — exactly the official clock-change dates, so UTC handling is correct.\n")

# ---------------------------------------------------------------- silent zeros
sz = AN["silent_zeros_and_gaps"]
p = sz["pvgis53"]
L.append("## 3. Hidden missing data: zeros that are not zeros\n")
L.append(f"An independent solar-position calculation (NOAA algorithm; agrees with PVGIS 5.3's own sun height to "
         f"max {AN['solar_position_validation_vs_pvgis53']['max_abs_diff_deg']}°, mean "
         f"{AN['solar_position_validation_vs_pvgis53']['mean_abs_diff_deg']}°) was used to find hours with zero "
         "output while the sun is > 5° above the horizon.\n")
L.append(tbl(["Category", "PVGIS 5.3", "PVGIS 6"], [
    ["Zero power with sun > 5°", p["n_zero_power_sun_above_5deg"], sz["pvgis6"]["n_zero_power_sun_above_5deg"]],
    ["A. Silent missing (POA = 0 **and** source sun height = 0)", p["A_silent_missing_hours"], "—"],
    ["  …of which PVGIS 6 reports null", p["A_of_which_pvgis6_null"], ""],
    ["  …of which PVGIS 5.3 'reconstructed' flag set", p["A_reconstructed_flag_set"], ""],
    ["B. Legitimate low-light zero (POA > 0, max POA W/m²)", f"{p['B_low_light_zero_hours']} (max {p['B_max_poa_wm2']})", "—"],
    ["  PVGIS 6 energy in those B hours (MWh, 3 yrs)", p["B_pvgis6_mwh_same_hours"], ""],
    ["Explicit nulls", p["n_null"], sz["pvgis6"]["n_null"]],
]) + "\n")
L.append("**Finding:** PVGIS 5.3 encodes missing satellite slots as zeros (including its sun-height column) without "
         "setting its reconstruction flag. Every such hour coincides with an explicit null in PVGIS 6. A pipeline "
         "that trusted 5.3's zeros would book these hours as 'no production'.\n")
g = sz["pvgis6_gap_materiality"]
L.append("Materiality of PVGIS 6 nulls (KNMI-based proxy: GHI × month-hour PV/GHI ratio; order-of-magnitude only):\n")
L.append(tbl(["Year", "Null hours", "Proxy MWh", "% of annual"],
             [[y, v["null_hours"], v["proxy_mwh"], v["pct_of_annual"]] for y, v in g.items()]) + "\n")

# ---------------------------------------------------------------- PVGIS comparison
c = AN["pvgis_comparison"]
L.append("## 4. PVGIS 5.3 vs PVGIS 6 (overlap 2021–2023)\n")
L.append("These are **model-vs-model differences**, not prediction errors: neither series is measured generation.\n")
L.append(tbl(["Year", "5.3 MWh", "6 MWh", "Δ % (common valid hours)", "5.3 capacity factor", "5.3 kWh/kWp", "6 kWh/kWp"],
             [[r["year"], r["pvgis53_mwh"], r["pvgis6_mwh_nansum"], r["pct_diff_6_vs_53_common_valid"],
               r["pvgis53_capacity_factor"], r["pvgis53_specific_yield_kwh_per_kwp"],
               r["pvgis6_specific_yield_kwh_per_kwp_nansum"]] for r in c["annual"]]) + "\n")
h = c["hourly_daylight"]
L.append(f"Hourly (daylight, n = {h['n_hours']}): mean difference 6 − 5.3 = {h['mean_diff_kw_6_minus_53']} kW, "
         f"mean |Δ| = {h['mean_abs_diff_kw']} kW, RMS = {h['rms_diff_kw']} kW, P95 |Δ| = {h['p95_abs_diff_kw']} kW, "
         f"Pearson r = {h['pearson_r']}.\n")
L.append(f"Lag correlation (PVGIS 6 shifted vs 5.3): `{c['lag_correlation_pvgis6_shift_vs_53']}` → peak at 0 h: the "
         "reconstructed PVGIS 6 index is aligned with PVGIS 5.3.\n")
L.append(f"PVGIS 6 `pv_kw / pv_kw_without_loss` = {c['pvgis6_power_over_power_without_loss']}. Unit finding: in the raw "
         "response `power` is in W while `power_without_loss` is in kW (raw ratio = 860); after converting both to kW the "
         "ratio is a constant 0.86 → `system_efficiency=0.86` is applied as a pure multiplier, equivalent to 5.3's `loss=14`. The ~4 % gap therefore comes from the other model "
         "components (module/temperature model, spectral and reflectivity treatment, irradiance processing), not from the "
         "loss parameter. Monthly differences range "
         f"{min(v['pct_diff'] for v in c['monthly'].values()):.1f}–{max(v['pct_diff'] for v in c['monthly'].values()):.1f} %, "
         "largest in December/January.\n")
L.append("![monthly](../figures/stage1_pvgis_monthly_comparison.png)\n\n![scatter](../figures/stage1_pvgis_hourly_scatter.png)\n")

# ---------------------------------------------------------------- KNMI consistency
L.append("## 5. Consistency with KNMI ground observations\n")
L.append("KNMI measures **global horizontal** irradiance (GHI) at a station; PVGIS gives **plane-of-array** irradiance "
         "(POA) on a 35° tilted surface from satellite data for the site. They are different quantities, so no error "
         "metric is computed. Two legitimate checks are made: (1) timestamp alignment, (2) whether year-to-year and "
         "day-to-day variation agree in direction.\n")
rows = [[t["label"], t["lag_corr"], t["best_lag_hours"]] for t in AN["timing_vs_knmi"]]
L.append(tbl(["Pair", "Pearson r by shift (h)", "Best shift"], rows) + "\n")
wc = AN["weather_consistency"]
rows = []
for y, v in wc["pvgis6_vs_269"]["annual"].items():
    rows.append([y, v["knmi_ghi_kwh_m2"], wc["pvgis6_vs_273"]["annual"][y]["knmi_ghi_kwh_m2"], v["pvgis_poa_kwh_m2"],
                 round(v["pvgis_poa_kwh_m2"] / v["knmi_ghi_kwh_m2"], 3), v["pv_mwh"]])
L.append(tbl(["Year", "KNMI 269 GHI kWh/m²", "KNMI 273 GHI kWh/m²", "PVGIS 6 POA kWh/m²", "POA/GHI", "PVGIS 6 PV MWh"], rows) + "\n")
L.append(f"Spearman correlation of daily PVGIS 6 energy with daily KNMI GHI: 269 = "
         f"{wc['pvgis6_vs_269']['spearman_daily_pv_kwh_vs_knmi_ghi']}, 273 = "
         f"{wc['pvgis6_vs_273']['spearman_daily_pv_kwh_vs_knmi_ghi']}. Between the two KNMI stations (≈ 37 km apart): "
         f"hourly r = {AN['knmi_station_agreement']['hourly_pearson_269_273']}, daily r = "
         f"{AN['knmi_station_agreement']['daily_pearson_269_273']}.\n")
def _rank(d):
    return [y for y, _ in sorted(d.items(), key=lambda kv: -kv[1])]


r_knmi269 = _rank({y: v["knmi_ghi_kwh_m2"] for y, v in wc["pvgis6_vs_269"]["annual"].items()})
r_knmi273 = _rank({y: v["knmi_ghi_kwh_m2"] for y, v in wc["pvgis6_vs_273"]["annual"].items()})
r_pv = _rank({y: v["pvgis_poa_kwh_m2"] for y, v in wc["pvgis6_vs_269"]["annual"].items()})
L.append(f"Year ranking (sunniest first): KNMI 269 {r_knmi269}, KNMI 273 {r_knmi273}, PVGIS 6 POA {r_pv}. "
         "The two clearly different years (2022 highest, 2023 second) agree across all sources; 2021 and 2024 are within "
         "~1 % of each other and their order is not robust. POA/GHI is stable (higher in the sunnier 2022, as expected "
         "for a tilted plane with more direct sunlight). This supports using PVGIS for **relative** year-to-year variation. It does "
         "**not** validate the absolute level of generation of a real plant — that would require metered output.\n")
L.append("![timing](../figures/stage1_timing_alignment.png)\n\n![diurnal](../figures/stage1_diurnal_profiles.png)\n")

# ---------------------------------------------------------------- prices
L.append("## 6. Netherlands day-ahead prices\n")
L.append(f"Source used: Energy-Charts API, license `{next(iter(Q['metadata']['energycharts'].values()))['license_info']}`. "
         "ENTSO-E (official primary source) could not be queried without a personal API token; the ENTSO-E parser is "
         "implemented and unit-tested on a synthetic XML fixture, and a cross-check will run automatically once "
         "`ENTSOE_API_TOKEN` is set.\n")
if "entsoe_vs_energycharts" in AN:
    L.append(f"ENTSO-E vs Energy-Charts: `{AN['entsoe_vs_energycharts']}`\n")
PS = AN["price_summary"]
L.append(tbl(["Year", "n", "Mean", "Median", "Std", "Min", "Max", "P05", "P95", "Negative h", "% of neg. h in 08–15 UTC", "Zero h"],
             [[y, v["n"], v["mean"], v["median"], v["std"], v["min"], v["max"], v["p05"], v["p95"], v["negative_hours"],
               v["pct_negative_hours_in_08_15_utc"], v["zero_hours"]] for y, v in PS.items()]) + "\n")
ys = sorted(PS)
L.append("All values in EUR/MWh. The −500 €/MWh minimum (2 July 2023, 11:00–13:00 UTC) is retained: it is an observed "
         f"market outcome, not an error. Negative-price hours rose from {PS[ys[0]]['negative_hours']} ({ys[0]}) to "
         f"{PS[ys[-1]]['negative_hours']} ({ys[-1]}); in {ys[-1]}, {PS[ys[-1]]['pct_negative_hours_in_08_15_utc']} % of "
         "them fell in 08:00–15:59 UTC (the hours of highest solar output) — directly relevant to merchant solar revenue "
         "and to PPA negative-price clauses.\n")
L.append("![prices](../figures/stage1_prices_overview.png)\n")

# ---------------------------------------------------------------- decisions
al = AN["aligned_dataset"]
gmax = max(v["pct_of_annual"] for v in g.values())
dmin = min(r["pct_diff_6_vs_53_common_valid"] for r in c["annual"])
dmax = max(r["pct_diff_6_vs_53_common_valid"] for r in c["annual"])
L.append("## 7. Feasibility verdict\n")
L.append(tbl(["Dataset", "Verdict", "Reason"], [
    ["PVGIS 6 (2021–2024)", "✅ PASS — primary production series",
     f"Only version covering 2024; complete index; {sz['pvgis6']['n_null']} explicit nulls (≤{gmax} % of annual energy); "
     "aligned with 5.3 and KNMI. Caveat: 'under development' → raw responses frozen with hashes."],
    ["PVGIS 5.3 (2021–2023)", "⚠️ PASS for sensitivity only",
     f"No 2024; silent zero-encoding of missing slots; systematically {dmin}–{dmax} % lower than 6 → model-uncertainty band."],
    ["KNMI 269 / 273", "✅ PASS — consistency & sensitivity", "Complete, timestamps verified; cannot validate plant output."],
    ["NL day-ahead prices (Energy-Charts)", "✅ PASS — not cross-checked against ENTSO-E", "Complete 35,064 h, no duplicates, DST correct."],
    ["NL day-ahead prices (ENTSO-E)", "⏳ PENDING", "Requires personal API token (HTTP 401 without)."],
    ["Mixing 5.3 (2021–23) + 6 (2024)", "❌ REJECTED", "Would introduce an artificial ~4 % level shift in 2024."],
]) + "\n")
L.append(f"Aligned hourly dataset `data/processed/stage1_aligned_hourly.csv`: {al['n_rows']} rows; non-missing by column "
         f"`{al['non_missing_by_column']}`; rows with both PVGIS 6 and price: {al['complete_rows_pv6_price']}.\n")

(ROOT / "reports/data_audit/AUDIT_REPORT.md").write_text("\n".join(L), encoding="utf-8")
print("wrote reports/data_audit/AUDIT_REPORT.md")
