"""Stage 1 — Data feasibility & PVGIS audit.

Run from the project root:
    python scripts/run_stage1_audit.py            # uses cached raw files when present
    python scripts/run_stage1_audit.py --refresh  # re-download everything

Outputs
    data/raw/...                         raw API responses + manifest.jsonl (provenance)
    data/interim/*.csv                   standardised per-source hourly tables (UTC, interval start)
    data/processed/stage1_aligned_hourly.csv
    reports/data_audit/data_quality.json machine-readable audit
    reports/data_audit/AUDIT_REPORT.md   human-readable audit (generated from computed numbers)
    reports/figures/stage1_*.png
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from solarrisk import io_utils  # noqa: E402
from solarrisk.ingestion import knmi, prices, pvgis  # noqa: E402
from solarrisk.validation import checks, solar  # noqa: E402

log = logging.getLogger("stage1")


# ============================================================================ retrieval
def retrieve(cfg: dict, refresh: bool) -> dict:
    raw = ROOT / cfg["paths"]["raw"]
    s, pv = cfg["site"], cfg["pv_system"]
    years = cfg["period"]["years"]
    args = (s["latitude"], s["longitude"], pv["peak_power_kwp"], pv["system_loss_pct"],
            pv["tilt_deg"], pv["azimuth_south_based_deg"])
    status: dict = {"pvgis53": {}, "pvgis6": {}, "knmi": {}, "entsoe": {}, "energycharts": {}}

    # PVGIS 6 OpenAPI snapshot (documentation version actually used)
    r = io_utils.fetch_to_file(cfg["pvgis6"]["openapi_url"], raw / "pvgis6" / "openapi.json",
                               use_cache=not refresh)
    status["pvgis6_openapi"] = _st(r)

    for y in years:
        r = io_utils.fetch_to_file(cfg["pvgis53"]["base_url"], raw / "pvgis53" / f"pvgis53_{y}.json",
                                   params=pvgis.pvgis53_params(*args, y), use_cache=not refresh)
        status["pvgis53"][y] = _st(r)
        r = io_utils.fetch_to_file(cfg["pvgis6"]["base_url"], raw / "pvgis6" / f"pvgis6_{y}.json",
                                   params=pvgis.pvgis6_params(*args, y), use_cache=not refresh)
        status["pvgis6"][y] = _st(r)
        r = io_utils.fetch_to_file(cfg["prices"]["energycharts_url"],
                                   raw / "prices" / f"energycharts_NL_{y}.json",
                                   params=prices.energycharts_params(cfg["prices"]["energycharts_bzn"], y),
                                   use_cache=not refresh)
        status["energycharts"][y] = _st(r)
        token = os.environ.get("ENTSOE_API_TOKEN")
        if token:
            r = io_utils.fetch_to_file(cfg["prices"]["entsoe_url"], raw / "prices" / f"entsoe_NL_{y}.xml",
                                       params=prices.entsoe_params(token, cfg["prices"]["entsoe_bidding_zone"], y),
                                       use_cache=not refresh)
            status["entsoe"][y] = _st(r)
        else:
            status["entsoe"][y] = {"ok": False, "status": None,
                                   "error": "ENTSOE_API_TOKEN not set; unauthenticated request returns HTTP 401"}

    stns = list(cfg["knmi"]["stations"].keys())
    r = io_utils.fetch_to_file(cfg["knmi"]["url"], raw / "knmi" / f"knmi_hourly_{min(years)}_{max(years)}.txt",
                               method="POST", data=knmi.knmi_form(stns, cfg["knmi"]["variables"], min(years), max(years)),
                               use_cache=not refresh)
    status["knmi"]["all"] = _st(r)
    return status


def _st(r: io_utils.FetchResult) -> dict:
    return {"ok": r.ok, "status": r.status, "file": str(r.path.relative_to(ROOT)) if r.path else None,
            "error": r.error}


# ============================================================================ parsing
def load_all(cfg: dict, status: dict) -> dict:
    raw = ROOT / cfg["paths"]["raw"]
    years = cfg["period"]["years"]
    out: dict = {"meta": {}}

    f53, m53 = [], {}
    for y in years:
        if status["pvgis53"][y]["ok"]:
            df, meta = pvgis.parse_pvgis53(raw / "pvgis53" / f"pvgis53_{y}.json")
            f53.append(df)
            m53[y] = meta
    out["pv53"] = pd.concat(f53) if f53 else None
    out["meta"]["pvgis53"] = m53

    f6, m6 = [], {}
    for y in years:
        if status["pvgis6"][y]["ok"]:
            df, meta = pvgis.parse_pvgis6(raw / "pvgis6" / f"pvgis6_{y}.json", y)
            f6.append(df)
            m6[y] = meta
    out["pv6"] = pd.concat(f6) if f6 else None
    out["meta"]["pvgis6"] = m6

    fp, mp = [], {}
    for y in years:
        if status["energycharts"][y]["ok"]:
            s, meta = prices.parse_energycharts(raw / "prices" / f"energycharts_NL_{y}.json")
            fp.append(s)
            mp[y] = meta
    out["price_ec"] = pd.concat(fp) if fp else None
    out["meta"]["energycharts"] = mp

    fe = []
    for y in years:
        if status["entsoe"][y].get("ok"):
            fe.append(prices.parse_entsoe_a44((raw / "prices" / f"entsoe_NL_{y}.xml").read_text(encoding="utf-8")))
    out["price_entsoe"] = pd.concat(fe) if fe else None

    if status["knmi"]["all"]["ok"]:
        out["knmi"] = knmi.parse_knmi_hourly(ROOT / status["knmi"]["all"]["file"])
    else:
        out["knmi"] = None

    # OpenAPI version info
    if status.get("pvgis6_openapi", {}).get("ok"):
        spec = json.loads((raw / "pvgis6" / "openapi.json").read_text(encoding="utf-8"))
        desc = spec["info"].get("description", "")
        out["meta"]["pvgis6_openapi"] = {
            "title": spec["info"].get("title"), "version": spec["info"].get("version"),
            "documented_time_period": _between(desc, "**Time period**:", "\n"),
            "under_development_notice": "actively under development" in desc.lower(),
        }
    return out


def _between(text, a, b):
    i = text.find(a)
    if i < 0:
        return None
    j = text.find(b, i + len(a))
    return text[i + len(a): j].strip()


# ============================================================================ analyses
def pvgis_comparison(pv53: pd.DataFrame, pv6: pd.DataFrame, kwp: float) -> dict:
    common = pv53.index.intersection(pv6.index)
    a, b = pv53.loc[common], pv6.loc[common]
    res = {"overlap_hours": int(len(common)), "overlap_years": sorted(set(common.year))}
    ann = []
    for y in res["overlap_years"]:
        ay, by = a[a.index.year == y], b[b.index.year == y]
        both = ay["pv_kwh"].notna() & by["pv_kwh"].notna()
        ann.append({
            "year": int(y),
            "pvgis53_mwh": round(ay["pv_kwh"].sum() / 1e3, 2),
            "pvgis6_mwh_nansum": round(by["pv_kwh"].sum() / 1e3, 2),
            "pvgis6_null_hours": int(by["pv_kwh"].isna().sum()),
            "pvgis53_mwh_on_pvgis6_null_hours": round(ay.loc[by["pv_kwh"].isna(), "pv_kwh"].sum() / 1e3, 3),
            "pvgis53_mwh_common_valid": round(ay.loc[both, "pv_kwh"].sum() / 1e3, 2),
            "pvgis6_mwh_common_valid": round(by.loc[both, "pv_kwh"].sum() / 1e3, 2),
            "pct_diff_6_vs_53_common_valid": round(100 * (by.loc[both, "pv_kwh"].sum() / ay.loc[both, "pv_kwh"].sum() - 1), 2),
            "pvgis53_capacity_factor": round(ay["pv_kwh"].sum() / (kwp * len(ay)), 4),
            "pvgis53_specific_yield_kwh_per_kwp": round(ay["pv_kwh"].sum() / kwp, 1),
            "pvgis6_specific_yield_kwh_per_kwp_nansum": round(by["pv_kwh"].sum() / kwp, 1),
            "pvgis53_reconstructed_hours": int((ay["reconstructed_flag"] > 0).sum()),
        })
    res["annual"] = ann
    # monthly
    both = a["pv_kwh"].notna() & b["pv_kwh"].notna()
    mon = pd.DataFrame({"pvgis53_mwh": a.loc[both, "pv_kwh"], "pvgis6_mwh": b.loc[both, "pv_kwh"]}) \
        .groupby(pd.Grouper(freq="MS")).sum() / 1e3
    mon["pct_diff"] = 100 * (mon["pvgis6_mwh"] / mon["pvgis53_mwh"] - 1)
    res["monthly"] = {str(k.date()): {kk: round(float(vv), 2) for kk, vv in r.items()} for k, r in mon.iterrows()}
    # hourly — daylight hours where either model produces
    day = both & ((a["pv_kw"] > 0) | (b["pv_kw"] > 0))
    d = (b.loc[day, "pv_kw"] - a.loc[day, "pv_kw"])
    res["hourly_daylight"] = {
        "n_hours": int(day.sum()),
        "mean_diff_kw_6_minus_53": round(float(d.mean()), 2),
        "mean_abs_diff_kw": round(float(d.abs().mean()), 2),
        "rms_diff_kw": round(float(np.sqrt((d ** 2).mean())), 2),
        "pearson_r": round(float(np.corrcoef(a.loc[day, "pv_kw"], b.loc[day, "pv_kw"])[0, 1]), 5),
        "p95_abs_diff_kw": round(float(d.abs().quantile(0.95)), 2),
    }
    lags = {}
    for L in (-2, -1, 0, 1, 2):
        x = b["pv_kw"].shift(L)
        ok = a["pv_kw"].notna() & x.notna()
        lags[L] = round(float(np.corrcoef(a.loc[ok, "pv_kw"], x[ok])[0, 1]), 5)
    res["lag_correlation_pvgis6_shift_vs_53"] = lags
    if "pv_kw_without_loss" in b:
        ratio = (b["pv_kw"] / b["pv_kw_without_loss"]).where(b["pv_kw_without_loss"] > 0)
        res["pvgis6_power_over_power_without_loss"] = {"median": round(float(ratio.median()), 5),
                                                         "min": round(float(ratio.min()), 5),
                                                         "max": round(float(ratio.max()), 5)}
    return res


def silent_zero_and_gap_check(pv53: pd.DataFrame | None, pv6: pd.DataFrame, kn: pd.DataFrame,
                              lat: float, lon: float) -> dict:
    """(1) Detect 'zero output while the sun is up' using an independent solar-position model.
    (2) Estimate the materiality of PVGIS 6 null hours with a KNMI-based proxy:
        PV_hat = GHI_knmi(hour) * [sum PV / sum GHI] over valid hours of the same month & UTC hour.
        This is an order-of-magnitude check for the gaps, NOT a production model."""
    out = {}
    for name, df in [("pvgis53", pv53), ("pvgis6", pv6)]:
        if df is None:
            continue
        el = solar.hourly_mean_elevation_deg(df.index, lat, lon)
        sz = (el > 5) & (df["pv_kw"] == 0)
        out[name] = {"n_zero_power_sun_above_5deg": int(sz.sum()),
                     "by_year": {int(k): int(v) for k, v in pd.Series(sz, df.index).groupby(df.index.year).sum().items()},
                     "n_null": int(df["pv_kw"].isna().sum())}
        if name == "pvgis53" and pv6 is not None:
            b = pv6.reindex(df.index)
            # Category A: whole record zeroed (POA = 0 and source sun height = 0) -> silently missing data
            silent = sz & (df["poa_wm2"] == 0) & (df["sun_height_deg"] == 0)
            # Category B: POA > 0 but tiny (sunrise, sun behind the south-facing plane) -> model output 0 (legitimate)
            lowlight = sz & (df["poa_wm2"] > 0)
            out[name]["A_silent_missing_hours"] = int(silent.sum())
            out[name]["A_of_which_pvgis6_null"] = int(b.loc[silent, "pv_kw"].isna().sum())
            out[name]["A_reconstructed_flag_set"] = int((df.loc[silent, "reconstructed_flag"] > 0).sum())
            out[name]["B_low_light_zero_hours"] = int(lowlight.sum())
            out[name]["B_max_poa_wm2"] = round(float(df.loc[lowlight, "poa_wm2"].max()), 2) if lowlight.any() else None
            out[name]["B_pvgis6_mwh_same_hours"] = round(float(b.loc[lowlight, "pv_kwh"].sum() / 1e3), 3)
    g = kn[kn.station == 269].set_index("interval_start_utc")["ghi_wm2"]
    j = pd.concat([pv6["pv_kwh"], g], axis=1, join="inner")
    j.columns = ["pv", "ghi"]
    valid = j.dropna()
    ratio = valid.groupby([valid.index.month, valid.index.hour]).apply(
        lambda x: x["pv"].sum() / x["ghi"].sum() if x["ghi"].sum() > 0 else 0.0)
    gaps = j[j["pv"].isna()].copy()
    gaps["ratio"] = [ratio.get((m, h), 0.0) for m, h in zip(gaps.index.month, gaps.index.hour)]
    gaps["pv_proxy_kwh"] = gaps["ghi"] * gaps["ratio"]
    annual = pv6["pv_kwh"].groupby(pv6.index.year).sum()
    by_y = gaps["pv_proxy_kwh"].groupby(gaps.index.year).sum()
    out["pvgis6_gap_materiality"] = {
        int(y): {"null_hours": int((gaps.index.year == y).sum()),
                 "proxy_mwh": round(float(by_y.get(y, 0) / 1e3), 2),
                 "pct_of_annual": round(float(100 * by_y.get(y, 0) / annual[y]), 3)}
        for y in annual.index}
    return out


def timing_vs_knmi(pv: pd.DataFrame, kn: pd.DataFrame, station: int, label: str) -> dict:
    """Timestamp-convention diagnostic ONLY. POA (tilted) vs GHI (horizontal) are different quantities;
    a correlation peak at lag 0 indicates consistent interval labelling, not model accuracy."""
    g = kn[kn.station == station].set_index("interval_start_utc")["ghi_wm2"]
    res = {}
    for L in (-2, -1, 0, 1, 2):
        x = pv["poa_wm2"].shift(L)
        j = pd.concat([x, g], axis=1, join="inner").dropna()
        res[L] = round(float(j.corr().iloc[0, 1]), 4)
    best = max(res, key=res.get)
    return {"label": label, "station": station, "lag_corr": res, "best_lag_hours": int(best)}


def weather_consistency(pv: pd.DataFrame, kn: pd.DataFrame, station: int) -> dict:
    """Annual/daily consistency of PVGIS (satellite-modelled POA) with KNMI (ground GHI).
    Ratios POA/GHI are expected to be >1 for a 35° south tilt at 52°N; we report them, we do not
    turn them into 'errors'."""
    g = kn[kn.station == station].set_index("interval_start_utc")["ghi_wm2"]
    j = pd.concat([pv["poa_wm2"], pv["pv_kwh"], g], axis=1, join="inner")
    j.columns = ["poa", "pv_kwh", "ghi"]
    j = j.dropna()
    daily = j.resample("D").sum(min_count=20)
    daily = daily.dropna()
    out = {"station": station, "n_hours_joint": int(len(j)),
           "spearman_daily_pv_kwh_vs_knmi_ghi": round(float(daily["pv_kwh"].corr(daily["ghi"], method="spearman")), 4),
           "annual": {}}
    for y, grp in j.groupby(j.index.year):
        out["annual"][int(y)] = {
            "knmi_ghi_kwh_m2": round(grp["ghi"].sum() / 1e3, 1),
            "pvgis_poa_kwh_m2": round(grp["poa"].sum() / 1e3, 1),
            "pv_mwh": round(grp["pv_kwh"].sum() / 1e3, 1),
        }
    return out


def price_summary(s: pd.Series) -> dict:
    out = {}
    for y, g in s.groupby(s.index.year):
        out[int(y)] = {
            "n": int(g.notna().sum()), "mean": round(float(g.mean()), 2), "median": round(float(g.median()), 2),
            "std": round(float(g.std()), 2), "min": round(float(g.min()), 2), "max": round(float(g.max()), 2),
            "p05": round(float(g.quantile(0.05)), 2), "p95": round(float(g.quantile(0.95)), 2),
            "negative_hours": int((g < 0).sum()), "zero_hours": int((g == 0).sum()),
            "pct_negative_hours_in_08_15_utc": round(float(100 * ((g < 0) & g.index.hour.isin(range(8, 16))).sum()
                                                         / max((g < 0).sum(), 1)), 1),
        }
    return out


def local_day_lengths(idx: pd.DatetimeIndex) -> dict:
    """Hours per Europe/Amsterdam calendar day — should be 23 on spring-DST days and 25 on autumn days.
    Confirms that UTC handling reproduces local clock changes correctly."""
    loc = pd.Series(1, index=idx.tz_convert("Europe/Amsterdam"))
    n = loc.groupby(loc.index.date).sum()
    n = n.iloc[1:-1]  # first/last local days are partial by construction (UTC year boundaries)
    return {"hours_per_local_day_counts": {int(k): int(v) for k, v in n.value_counts().items()},
            "days_with_23h": [str(d) for d in n[n == 23].index],
            "days_with_25h": [str(d) for d in n[n == 25].index]}


def compare_price_sources(a: pd.Series, b: pd.Series) -> dict:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    d = j.iloc[:, 0] - j.iloc[:, 1]
    return {"n_common": int(len(j)), "n_abs_diff_gt_0.01": int((d.abs() > 0.01).sum()),
            "max_abs_diff": round(float(d.abs().max()), 4)}


# ============================================================================ figures
def figures(data: dict, cmp: dict, timing: list[dict], fig_dir: Path) -> list[str]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    made = []
    pv53, pv6, pr, kn = data["pv53"], data["pv6"], data["price_ec"], data["knmi"]

    # 1. Monthly PVGIS comparison
    mon = pd.DataFrame(cmp["monthly"]).T
    mon.index = pd.to_datetime(mon.index)
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True, height_ratios=[2, 1])
    ax[0].plot(mon.index, mon["pvgis53_mwh"], marker="o", ms=3, label="PVGIS 5.3 (SARAH3/ERA5)")
    ax[0].plot(mon.index, mon["pvgis6_mwh"], marker="s", ms=3, label="PVGIS 6 (SARAH-3)")
    ax[0].set_ylabel("MWh / month (1 MWp)")
    ax[0].legend()
    ax[0].set_title("Modelled monthly PV energy — hours valid in both versions")
    ax[1].bar(mon.index, mon["pct_diff"], width=20, color="grey")
    ax[1].axhline(0, color="k", lw=0.8)
    ax[1].set_ylabel("PVGIS 6 vs 5.3 (%)")
    fig.tight_layout()
    p = fig_dir / "stage1_pvgis_monthly_comparison.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)

    # 2. Hourly scatter
    common = pv53.index.intersection(pv6.index)
    j = pd.concat([pv53.loc[common, "pv_kw"], pv6.loc[common, "pv_kw"]], axis=1).dropna()
    fig, ax = plt.subplots(figsize=(5.5, 5.5))
    ax.scatter(j.iloc[:, 0], j.iloc[:, 1], s=2, alpha=0.15)
    m = float(j.max().max())
    ax.plot([0, m], [0, m], "k--", lw=1)
    ax.set_xlabel("PVGIS 5.3 power (kW)")
    ax.set_ylabel("PVGIS 6 power (kW)")
    ax.set_title("Hourly PV power, 2021–2023 (model vs model)")
    fig.tight_layout()
    p = fig_dir / "stage1_pvgis_hourly_scatter.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)

    # 3. Timing diagnostic
    fig, ax = plt.subplots(figsize=(7, 4))
    for t in timing:
        ax.plot(list(t["lag_corr"].keys()), list(t["lag_corr"].values()), marker="o", label=t["label"])
    ax.set_xlabel("Shift applied to PVGIS series (hours)")
    ax.set_ylabel("Pearson r with KNMI GHI")
    ax.set_title("Timestamp alignment check (peak at 0 = consistent labelling)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = fig_dir / "stage1_timing_alignment.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)

    # 4. Mean diurnal profile (UTC) — PV vs KNMI GHI vs price
    g = kn[kn.station == 269].set_index("interval_start_utc")["ghi_wm2"]
    fig, ax1 = plt.subplots(figsize=(8, 4))
    ax1.plot(pv6.groupby(pv6.index.hour)["poa_wm2"].mean(), label="PVGIS 6 POA (W/m²)")
    ax1.plot(pv53.groupby(pv53.index.hour)["poa_wm2"].mean(), label="PVGIS 5.3 POA (W/m²)")
    ax1.plot(g.groupby(g.index.hour).mean(), label="KNMI Lelystad GHI (W/m²)")
    ax1.set_xlabel("Hour of day (UTC, interval start)")
    ax1.set_ylabel("W/m²")
    ax2 = ax1.twinx()
    ax2.plot(pr.groupby(pr.index.hour).mean(), "k:", label="NL day-ahead price (€/MWh)")
    ax2.set_ylabel("€/MWh")
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=8, loc="upper left")
    ax1.set_title("Mean diurnal profiles 2021–2024")
    fig.tight_layout()
    p = fig_dir / "stage1_diurnal_profiles.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)

    # 5. Prices: daily mean + monthly negative hours
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax[0].plot(pr.resample("D").mean(), lw=0.7)
    ax[0].set_ylabel("Daily mean €/MWh")
    ax[0].set_title("Netherlands day-ahead prices (Energy-Charts / SMARD)")
    neg = (pr < 0).resample("MS").sum()
    ax[1].bar(neg.index, neg.values, width=20, color="tab:red")
    ax[1].set_ylabel("Negative-price hours / month")
    fig.tight_layout()
    p = fig_dir / "stage1_prices_overview.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    made.append(p.name)
    return made


# ============================================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "configs" / "stage1.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    audit_dir, fig_dir = ROOT / cfg["paths"]["audit"], ROOT / cfg["paths"]["figures"]
    interim, processed = ROOT / cfg["paths"]["interim"], ROOT / cfg["paths"]["processed"]
    for d in (audit_dir, fig_dir, interim, processed):
        d.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(audit_dir / "stage1_run.log", mode="w")])

    years, kwp = cfg["period"]["years"], cfg["pv_system"]["peak_power_kwp"]
    status = retrieve(cfg, a.refresh)
    data = load_all(cfg, status)

    # ---- save standardised interim tables
    for key, name in [("pv53", "pvgis53_hourly.csv"), ("pv6", "pvgis6_hourly.csv")]:
        if data[key] is not None:
            data[key].to_csv(interim / name)
    if data["price_ec"] is not None:
        data["price_ec"].to_csv(interim / "prices_energycharts_nl_hourly.csv")
    if data["knmi"] is not None:
        data["knmi"].to_csv(interim / "knmi_hourly.csv", index=False)

    # ---- audits
    pv_bounds = {"pv_kw": (0, kwp), "poa_wm2": (0, 1400), "t2m_c": (-30, 45)}
    q = {"retrieval": status, "metadata": data["meta"], "audits": {}, "analyses": {}}
    if data["pv53"] is not None:
        yrs = sorted(set(data["pv53"].index.year))
        q["audits"]["pvgis53"] = checks.audit_hourly(data["pv53"], yrs, pv_bounds)
        night = data["pv53"]["sun_height_deg"] <= 0
        q["audits"]["pvgis53"]["n_power_when_sun_below_horizon"] = int((data["pv53"].loc[night, "pv_kw"] > 0).sum())
    if data["pv6"] is not None:
        yrs6 = sorted(set(data["pv6"].index.year))
        q["audits"]["pvgis6"] = checks.audit_hourly(data["pv6"].drop(columns=["pv_kw_without_loss"], errors="ignore"),
                                                    yrs6, pv_bounds)
        nul = data["pv6"]["pv_kw"].isna()
        q["audits"]["pvgis6"]["null_hours_by_year"] = {int(k): int(v) for k, v in nul.groupby(nul.index.year).sum().items()}
        q["audits"]["pvgis6"]["null_hours_by_utc_hour"] = {int(k): int(v) for k, v in
                                                          nul[nul].groupby(nul[nul].index.hour).size().items()}
        q["audits"]["pvgis6"]["values_vs_expected_per_year"] = {
            int(y): [m["n_values"], m["n_expected"]] for y, m in data["meta"]["pvgis6"].items()}
    if data["knmi"] is not None:
        q["audits"]["knmi"] = {}
        for stn, g in data["knmi"].groupby("station"):
            g = g.set_index("interval_start_utc").drop(columns="station")
            g = g[g.index.year.isin(years)]
            q["audits"]["knmi"][int(stn)] = checks.audit_hourly(
                g, years, {"ghi_wm2": (0, 1200), "t_c": (-30, 45), "sun_h": (0, 1)})
            q["audits"]["knmi"][int(stn)]["n_rows_outside_period_dropped"] = int(
                (~data["knmi"][data["knmi"].station == stn]["interval_start_utc"].dt.year.isin(years)).sum())
    if data["price_ec"] is not None:
        pr = data["price_ec"]
        q["audits"]["prices_energycharts"] = checks.audit_hourly(pr, years, {"price_eur_mwh": (-500, 5000)})
        q["audits"]["prices_energycharts"]["dst_local_day_check"] = local_day_lengths(pr.index)
        q["analyses"]["price_summary"] = price_summary(pr)
    if data["price_entsoe"] is not None and data["price_ec"] is not None:
        q["analyses"]["entsoe_vs_energycharts"] = compare_price_sources(data["price_entsoe"], data["price_ec"])

    # ---- analyses
    if data["pv53"] is not None and data["pv6"] is not None:
        q["analyses"]["pvgis_comparison"] = pvgis_comparison(data["pv53"], data["pv6"], kwp)
    timing = []
    if data["knmi"] is not None:
        for lab, key in [("PVGIS 5.3 POA vs KNMI 269", "pv53"), ("PVGIS 6 POA vs KNMI 269", "pv6")]:
            if data[key] is not None:
                timing.append(timing_vs_knmi(data[key], data["knmi"], 269, lab))
        q["analyses"]["timing_vs_knmi"] = timing
        q["analyses"]["silent_zeros_and_gaps"] = silent_zero_and_gap_check(
            data["pv53"], data["pv6"], data["knmi"], cfg["site"]["latitude"], cfg["site"]["longitude"])
        el = solar.solar_elevation_deg(data["pv53"].index + pd.Timedelta(minutes=11),
                                       cfg["site"]["latitude"], cfg["site"]["longitude"])
        m = data["pv53"]["sun_height_deg"] > 0.5
        dif = np.abs(el[m.to_numpy()] - data["pv53"].loc[m, "sun_height_deg"].to_numpy())
        q["analyses"]["solar_position_validation_vs_pvgis53"] = {
            "n": int(m.sum()), "max_abs_diff_deg": round(float(dif.max()), 3),
            "mean_abs_diff_deg": round(float(dif.mean()), 3)}
        q["analyses"]["weather_consistency"] = {
            "pvgis6_vs_269": weather_consistency(data["pv6"], data["knmi"], 269),
            "pvgis6_vs_273": weather_consistency(data["pv6"], data["knmi"], 273),
            "pvgis53_vs_269": weather_consistency(data["pv53"], data["knmi"], 269),
        }
        # inter-station agreement (spatial representativeness)
        k = data["knmi"].pivot(index="interval_start_utc", columns="station", values="ghi_wm2")
        k = k[k.index.year.isin(years)].dropna()
        dd = k.resample("D").sum(min_count=20).dropna()
        q["analyses"]["knmi_station_agreement"] = {
            "hourly_pearson_269_273": round(float(k[269].corr(k[273])), 4),
            "daily_pearson_269_273": round(float(dd[269].corr(dd[273])), 4),
            "annual_ghi_kwh_m2": {int(y): {int(c): round(float(g[c].sum() / 1e3), 1) for c in g.columns}
                                  for y, g in k.groupby(k.index.year)},
        }

    # ---- aligned dataset
    exp = checks.expected_hourly_index(years)
    al = pd.DataFrame(index=exp)
    if data["pv6"] is not None:
        al["pv6_kwh"] = data["pv6"]["pv_kwh"]
    if data["pv53"] is not None:
        al["pv53_kwh"] = data["pv53"]["pv_kwh"]
    if data["knmi"] is not None:
        al["knmi269_ghi_wm2"] = data["knmi"][data["knmi"].station == 269].set_index("interval_start_utc")["ghi_wm2"]
    if data["price_ec"] is not None:
        al["price_eur_mwh"] = data["price_ec"]
    al.to_csv(processed / "stage1_aligned_hourly.csv")
    q["analyses"]["aligned_dataset"] = {
        "n_rows": int(len(al)),
        "non_missing_by_column": {c: int(al[c].notna().sum()) for c in al.columns},
        "complete_rows_pv6_price": int(al[["pv6_kwh", "price_eur_mwh"]].dropna().shape[0]) if "pv6_kwh" in al else None,
    }

    q["figures"] = figures(data, q["analyses"]["pvgis_comparison"], timing, fig_dir) \
        if data["pv53"] is not None and data["pv6"] is not None and data["price_ec"] is not None else []

    (audit_dir / "data_quality.json").write_text(json.dumps(q, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s", audit_dir / "data_quality.json")


if __name__ == "__main__":
    main()
