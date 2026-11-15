"""Stage 1 critical review — three targeted verifications.

1. PVGIS 6 timestamps: compare the JSON-reconstructed index with the explicit timestamps of the
   PVGIS 6 CSV output for every year (year boundaries, leap day 2024).
2. Why PVGIS 6 ≈ 4 % above 5.3: (a) compare in-plane irradiance before reflection (radiation data
   + transposition); (b) controlled one-at-a-time PVGIS 6 runs switching model components.
3. Evidence that the 26 PVGIS 5.3 zero hours are missing data: computed sun elevation, KNMI
   ground irradiance, ERA5 temperature in the same records, threshold sensitivity.

Writes reports/data_audit/stage1_review.json and STAGE1_REVIEW.md. Run after run_stage1_audit.py.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from solarrisk import io_utils  # noqa: E402
from solarrisk.ingestion import pvgis  # noqa: E402
from solarrisk.validation import solar  # noqa: E402

CFG = yaml.safe_load((ROOT / "configs/stage1.yaml").read_text(encoding="utf-8"))
RAW = ROOT / "data/raw"
S, PV = CFG["site"], CFG["pv_system"]
ARGS = (S["latitude"], S["longitude"], PV["peak_power_kwp"], PV["system_loss_pct"], PV["tilt_deg"],
        PV["azimuth_south_based_deg"])


def _arr(j, k):
    return np.array([np.nan if v is None else v for v in j[k]], dtype=float)


# ------------------------------------------------------------------------------------------ 1
def verify_timestamps(years) -> dict:
    out = {}
    for y in years:
        f = RAW / "pvgis6_csv" / f"pvgis6_{y}.csv"
        r = io_utils.fetch_to_file(CFG["pvgis6"]["base_url"], f,
                                   params={**pvgis.pvgis6_params(*ARGS, y), "verbose": 0, "csv": f"pvgis6_{y}.csv"})
        if not r.ok:
            out[y] = {"ok": False, "error": r.error}
            continue
        c = pvgis.parse_pvgis6_csv(f)
        js, _ = pvgis.parse_pvgis6(RAW / "pvgis6" / f"pvgis6_{y}.json", y)
        floor = c["source_time_utc"].dt.floor("h")
        same_len = len(c) == len(js)
        idx_ok = same_len and bool((floor.to_numpy() == js.index.to_numpy()).all())
        vals_ok = same_len and bool(np.isclose(c["power_w"].to_numpy(), js["pv_kw"].to_numpy() * 1000,
                                               equal_nan=True, rtol=0, atol=1e-2).all())
        leap = js.index[(js.index.month == 2) & (js.index.day == 29)]
        out[y] = {
            "ok": True, "rows_csv": len(c), "rows_json": len(js), "index_matches": idx_ok, "values_match": vals_ok,
            "first_source_time": str(c["source_time_utc"].iloc[0]), "last_source_time": str(c["source_time_utc"].iloc[-1]),
            "source_minute_second": sorted({(t.minute, t.second) for t in c["source_time_utc"]}),
            "feb29_hours": int(len(leap)),
            "nulls_csv": int(c["power_w"].isna().sum()), "nulls_json": int(js["pv_kw"].isna().sum()),
        }
        time.sleep(1)
    return out


# ------------------------------------------------------------------------------------------ 2a
def poa_comparison(years) -> dict:
    out = {}
    for y in years:
        a = json.loads((RAW / "pvgis53" / f"pvgis53_{y}.json").read_text())["outputs"]["hourly"]
        j = json.loads((RAW / "pvgis6" / f"pvgis6_{y}.json").read_text(encoding="utf-8"))
        p6 = _arr(j, "power")
        ok = ~np.isnan(p6)
        c53 = {k: np.array([r[k] for r in a], float) for k in ["Gb(i)", "Gd(i)", "Gr(i)", "P"]}
        poa53 = c53["Gb(i)"] + c53["Gd(i)"] + c53["Gr(i)"]
        gb6 = _arr(j, "global_inclined_irradiance_before_reflectivity")
        g6 = _arr(j, "global_inclined_irradiance")
        geff6 = _arr(j, "effective_global_irradiance")
        kwh = lambda x: round(float(np.nansum(x[ok]) / 1e3), 2)  # noqa: E731  W/m2·h -> kWh/m2
        e53, e6 = c53["P"][ok].sum() / 1e6, np.nansum(p6[ok]) / 1e6
        out[y] = {
            "poa_before_reflection_kwh_m2": {"pvgis53": kwh(poa53), "pvgis6": kwh(gb6),
                                             "pct_diff": round(100 * (np.nansum(gb6[ok]) / poa53[ok].sum() - 1), 3)},
            "components_kwh_m2": {
                "beam": [kwh(c53["Gb(i)"]), kwh(_arr(j, "direct_inclined_irradiance_before_reflectivity"))],
                "diffuse": [kwh(c53["Gd(i)"]), kwh(_arr(j, "diffuse_inclined_irradiance_before_reflectivity"))],
                "ground_reflected": [kwh(c53["Gr(i)"]), kwh(_arr(j, "ground_reflected_inclined_irradiance_before_reflectivity"))]},
            "pvgis6_chain_kwh_m2": {"before_reflection": kwh(gb6), "after_reflection": kwh(g6),
                                    "effective (after efficiency & spectral)": kwh(geff6)},
            "pvgis6_energy_weighted_spectral_factor": round(float(np.nansum(_arr(j, "spectral_factor")[ok] * g6[ok]) / np.nansum(g6[ok])), 4),
            "energy_mwh": {"pvgis53": round(e53, 2), "pvgis6": round(e6, 2)},
            "yield_per_poa_kwh_per_kwp_per_kwh_m2": {"pvgis53": round(e53 * 1e3 / PV["peak_power_kwp"] / (poa53[ok].sum() / 1e3), 4),
                                                     "pvgis6": round(e6 * 1e3 / PV["peak_power_kwp"] / (np.nansum(gb6[ok]) / 1e3), 4)},
        }
    return out


# ------------------------------------------------------------------------------------------ 2b
ABLATIONS = {
    "default": {},
    "older_module_coefficients": {"photovoltaic_module": "cSi:Free standing"},
    "reflectivity_off": {"apply_reflectivity_factor": "false"},
    "older_module+reflectivity_off": {"photovoltaic_module": "cSi:Free standing", "apply_reflectivity_factor": "false"},
    "temperature_model_None": {"temperature_model": "None"},
}


def ablations(years) -> dict:
    out = {}
    for y in years:
        a = json.loads((RAW / "pvgis53" / f"pvgis53_{y}.json").read_text())["outputs"]["hourly"]
        p53 = np.array([r["P"] for r in a], float)
        out[y] = {}
        for name, extra in ABLATIONS.items():
            f = RAW / "pvgis6_ablation" / f"pvgis6_{y}_{name}.json"
            r = io_utils.fetch_to_file(CFG["pvgis6"]["base_url"], f, params={**pvgis.pvgis6_params(*ARGS, y), **extra})
            if not r.ok:
                out[y][name] = {"error": r.error}
                continue
            j = json.loads(f.read_text(encoding="utf-8"))
            p = _arr(j, "power")
            ok = ~np.isnan(p)
            out[y][name] = {"params": extra, "technology_reported": j.get("technology"),
                            "energy_mwh": round(float(np.nansum(p) / 1e6), 2),
                            "pct_vs_pvgis53_common_hours": round(float(100 * (p[ok].sum() / p53[ok].sum() - 1)), 2)}
            time.sleep(0.5)
    return out


# ------------------------------------------------------------------------------------------ 3
def silent_zero_evidence() -> dict:
    a = pd.read_csv(ROOT / "data/interim/pvgis53_hourly.csv", index_col=0, parse_dates=True)
    b = pd.read_csv(ROOT / "data/interim/pvgis6_hourly.csv", index_col=0, parse_dates=True).reindex(a.index)
    k = pd.read_csv(ROOT / "data/interim/knmi_hourly.csv", parse_dates=["interval_start_utc"])
    g269 = k[k.station == 269].set_index("interval_start_utc")["ghi_wm2"].reindex(a.index)
    el = pd.Series(solar.hourly_mean_elevation_deg(a.index, S["latitude"], S["longitude"]), a.index)
    silent = (el > 5) & (a.pv_kw == 0) & (a.poa_wm2 == 0) & (a.sun_height_deg == 0)
    # Sensitivity: elevation at the SAMPLING INSTANT (HH:11), the moment PVGIS 5.3 values refer to.
    e11 = pd.Series(solar.solar_elevation_deg(a.index + pd.Timedelta(minutes=11), S["latitude"], S["longitude"]), a.index)
    zeroed = (a.pv_kw == 0) & (a.poa_wm2 == 0) & (a.sun_height_deg == 0)
    sens = {}
    for thr in (0, 1, 2, 5, 10, 15):
        sens[thr] = {"zero_power_hours (hourly-mean elevation)": int(((el > thr) & (a.pv_kw == 0)).sum()),
                     "record_zeroed (elevation at HH:11)": int((zeroed & (e11 > thr)).sum())}
    # same diagnostic on all daylight hours where 5.3 and 6 both have output, for comparison
    norm = (el > 5) & (a.pv_kw > 0)
    rows = a[silent].assign(computed_elevation=el[silent], knmi269_ghi=g269[silent], pvgis6_kw=b.loc[silent, "pv_kw"])
    return {
        "n_silent": int(silent.sum()),
        "computed_elevation_deg": {"min": round(float(el[silent].min()), 1), "median": round(float(el[silent].median()), 1),
                                   "max": round(float(el[silent].max()), 1)},
        "knmi269_ghi_wm2_on_silent_hours": {"min": round(float(g269[silent].min()), 1), "median": round(float(g269[silent].median()), 1),
                                            "n_zero": int((g269[silent] == 0).sum())},
        "knmi269_ghi_wm2_on_normal_daylight_hours_median": round(float(g269[norm].median()), 1),
        "era5_t2m_present_on_silent_hours": bool(a.loc[silent, "t2m_c"].notna().all() and (a.loc[silent, "t2m_c"] != 0).all()),
        "pvgis6_null_on_silent_hours": int(b.loc[silent, "pv_kw"].isna().sum()),
        "threshold_sensitivity": sens,
        "hours": [{"t": str(t), "elev": round(r.computed_elevation, 1), "knmi_ghi": round(r.knmi269_ghi, 1),
                   "t2m": r.t2m_c, "pvgis6_kw": None if pd.isna(r.pvgis6_kw) else r.pvgis6_kw} for t, r in rows.iterrows()],
    }


# ------------------------------------------------------------------------------------------ report
def write_md(res: dict, path: Path):
    L = ["# Stage 1 — Critical Review\n",
         "_Generated by `scripts/run_stage1_review.py` from `stage1_review.json`; all numbers computed from retrieved data._\n",
         "## 1. Are the reconstructed PVGIS 6 timestamps correct?\n",
         "PVGIS 6 JSON has no timestamps; we built the index as `start_time` + hourly steps. PVGIS 6 CSV output *does* "
         "carry the source time of each value. We downloaded the CSV for every year and compared row by row.\n",
         "| Year | Rows CSV | Rows JSON | Index matches | Values match | First source time | Last source time | Feb-29 hours | Nulls CSV/JSON |",
         "|---|---|---|---|---|---|---|---|---|"]
    for y, v in res["timestamps"].items():
        if v.get("ok"):
            L.append(f"| {y} | {v['rows_csv']} | {v['rows_json']} | {v['index_matches']} | {v['values_match']} | "
                     f"{v['first_source_time']} | {v['last_source_time']} | {v['feb29_hours']} | {v['nulls_csv']}/{v['nulls_json']} |")
        else:
            L.append(f"| {y} | failed: {v.get('error')} |")
    mm = sorted({tuple(x) for v in res["timestamps"].values() if v.get("ok") for x in v["source_minute_second"]})
    L.append(f"\nSource (minute, second) values found: `{mm}` — every value is the satellite slot at HH:11, assigned to the "
             "interval HH:00–HH+1:00, the same convention as PVGIS 5.3.\n")

    L.append("## 2. Why is PVGIS 6 ≈ 4 % higher than PVGIS 5.3?\n")
    L.append("### 2a. Radiation input and transposition\n")
    L.append("| Year | 5.3 POA kWh/m² | 6 POA before reflection | Δ % | Beam 5.3 / 6 | Diffuse 5.3 / 6 | Reflected 5.3 / 6 |")
    L.append("|---|---|---|---|---|---|---|")
    for y, v in res["poa"].items():
        c = v["components_kwh_m2"]
        L.append(f"| {y} | {v['poa_before_reflection_kwh_m2']['pvgis53']} | {v['poa_before_reflection_kwh_m2']['pvgis6']} | "
                 f"{v['poa_before_reflection_kwh_m2']['pct_diff']} | {c['beam'][0]} / {c['beam'][1]} | "
                 f"{c['diffuse'][0]} / {c['diffuse'][1]} | {c['ground_reflected'][0]} / {c['ground_reflected'][1]} |")
    L.append("\n| Year | PVGIS 6 chain: before refl. → after refl. → effective (kWh/m²) | Spectral factor (energy-weighted) | "
             "Yield/POA 5.3 | Yield/POA 6 |")
    L.append("|---|---|---|---|---|")
    for y, v in res["poa"].items():
        ch = v["pvgis6_chain_kwh_m2"]
        L.append(f"| {y} | {' → '.join(str(x) for x in ch.values())} | {v['pvgis6_energy_weighted_spectral_factor']} | "
                 f"{v['yield_per_poa_kwh_per_kwp_per_kwh_m2']['pvgis53']} | {v['yield_per_poa_kwh_per_kwp_per_kwh_m2']['pvgis6']} |")
    L.append("\n**Reading:** plane-of-array irradiance before reflection agrees to within a few hundredths of a percent, component "
             "by component. Both versions use SARAH-3 and evidently the same transposition, so the radiation dataset is **not** "
             "the source of the gap. The gap arises in the conversion of irradiance to power (yield per unit POA).\n")
    L.append("### 2b. Controlled experiments (PVGIS 6, one change at a time)\n")
    names = list(ABLATIONS)
    L.append("| Year | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    for y, v in res["ablations"].items():
        L.append(f"| {y} | " + " | ".join(f"{v[n].get('pct_vs_pvgis53_common_hours', 'err'):+}" if 'pct_vs_pvgis53_common_hours'
                                          in v[n] else "err" for n in names) + " |")
    L.append("\nValues: % difference in annual energy vs PVGIS 5.3 on common hours.\n")
    L.append("**Reading:** switching PVGIS 6 to the older c-Si module coefficients removes most of the gap; the 2025 module "
             "performance coefficients are therefore the main identified contributor. Switching reflectivity off changes "
             "output only slightly, and `temperature_model=None` leaves the output **identical**, so we cannot tell from "
             "outside whether that parameter is applied. The remaining difference after the module switch is **not "
             "attributed**: candidates include spectral correction (PVGIS 6 reports an energy-weighted spectral factor "
             "> 1; PVGIS 5.3 does not report whether it applies one), temperature/wind inputs and low-irradiance handling. "
             "We state this as unexplained, not as a conclusion.\n")

    z = res["silent_zeros"]
    L.append("## 3. Are the 26 PVGIS 5.3 zero hours really missing data?\n")
    L.append(f"- Computed sun elevation in those hours: min {z['computed_elevation_deg']['min']}°, median "
             f"{z['computed_elevation_deg']['median']}°, max {z['computed_elevation_deg']['max']}° — far above the 5° "
             "threshold and the ~0.3° accuracy of the algorithm.")
    L.append(f"- KNMI Lelystad measured irradiance in those hours: min {z['knmi269_ghi_wm2_on_silent_hours']['min']}, median "
             f"{z['knmi269_ghi_wm2_on_silent_hours']['median']} W/m² ({z['knmi269_ghi_wm2_on_silent_hours']['n_zero']} hours with 0); "
             f"for comparison, median on ordinary producing daylight hours = {z['knmi269_ghi_wm2_on_normal_daylight_hours_median']} W/m².")
    L.append(f"- ERA5 temperature in the same PVGIS 5.3 records present and non-zero: {z['era5_t2m_present_on_silent_hours']} → "
             "only the satellite-derived fields are zeroed.")
    L.append(f"- PVGIS 6 reports null in {z['pvgis6_null_on_silent_hours']} of {z['n_silent']} hours.\n")
    L.append("Threshold sensitivity. Column 2 uses the hourly-mean elevation (Stage 1 screening). Column 3 counts records "
             "with power, POA and reported sun height all 0, using the computed elevation at the **sampling instant HH:11**, "
             "the moment the PVGIS 5.3 values refer to:\n")
    L.append("| Threshold ° | Zero-power hours (hourly mean) | Zeroed records (elevation at HH:11) |\n|---|---|---|")
    ts = z["threshold_sensitivity"]
    for t, v in ts.items():
        L.append(f"| {t} | {v['zero_power_hours (hourly-mean elevation)']} | {v['record_zeroed (elevation at HH:11)']} |")
    c = {int(k): v["record_zeroed (elevation at HH:11)"] for k, v in ts.items()}
    L.append(f"\n**Reading:** with the sampling-instant elevation, the zeroed-record count is {c[2]} at 2° and {c[5]} at 5° "
             f"(stable), {c[10]} at 10°. Below 2° it rises ({c[1]} at 1°, {c[0]} at 0°): those are near-horizon samples where "
             "PVGIS's terrain-horizon model and refraction conventions can legitimately report a sun height of 0, so they are "
             "**not** evidence of missing data. A first version of this review used the hourly-mean elevation at low thresholds "
             "and over-counted for that reason; the sampling-instant criterion is the correct one.\n")
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    years = CFG["period"]["years"]
    overlap = [y for y in years if (RAW / "pvgis53" / f"pvgis53_{y}.json").exists()]
    res = {"timestamps": verify_timestamps(years), "poa": poa_comparison(overlap),
           "ablations": ablations(overlap), "silent_zeros": silent_zero_evidence()}
    out = ROOT / "reports/data_audit"
    (out / "stage1_review.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    write_md(res, out / "STAGE1_REVIEW.md")
    print("wrote reports/data_audit/stage1_review.json and STAGE1_REVIEW.md")


if __name__ == "__main__":
    main()
