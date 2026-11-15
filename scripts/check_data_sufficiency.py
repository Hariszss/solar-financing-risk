"""Data-sufficiency check: is a predictive model of capture-rate risk statistically defensible? (not a model)

Questions answered with data:
 1. Can NL day-ahead prices before 2021 be retrieved, and how comparable are the years (regimes)?
 2. Are day-ahead FORECASTS (solar, wind on/offshore, load) available for NL — i.e. information known
    before the auction, usable as non-leaking features — and since when?
 3. Can PVGIS 6 production be extended back (documented coverage 2014–2025)?
 4. Effective sample size: how autocorrelated are daily / monthly capture-rate series?
Writes reports/data_sufficiency/data_sufficiency.json and a figure.
"""

from __future__ import annotations

import json
import sys
import time
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
from solarrisk.ingestion import prices, pvgis  # noqa: E402

CFG = yaml.safe_load((ROOT / "configs/stage1.yaml").read_text(encoding="utf-8"))
RAW = ROOT / "data/raw/data_sufficiency"
OUT = ROOT / "reports/data_sufficiency"
YEARS = list(range(2015, 2025))
FC_TYPES = ["solar", "wind_onshore", "wind_offshore", "load"]


def get_prices():
    s = []
    for y in YEARS:
        f = RAW / f"prices_NL_{y}.json"
        r = io_utils.fetch_to_file(CFG["prices"]["energycharts_url"], f, params=prices.energycharts_params("NL", y))
        if r.ok:
            p, _ = prices.parse_energycharts(f)
            s.append(p[p.index.year == y])
        time.sleep(2)
    return pd.concat(s)


def get_forecasts():
    out = {}
    for t in FC_TYPES:
        parts = []
        for y in YEARS:
            f = RAW / f"fc_{t}_{y}.json"
            r = io_utils.fetch_to_file("https://api.energy-charts.info/public_power_forecast", f,
                                       params={"country": "nl", "production_type": t, "forecast_type": "day-ahead",
                                               "start": f"{y}-01-01T00:00Z", "end": f"{y}-12-31T23:59Z"})
            if r.ok:
                d = json.loads(f.read_text())
                if d.get("unix_seconds"):
                    idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
                    parts.append(pd.Series(d["forecast_values"], idx, dtype=float))
            time.sleep(2)
        out[t] = pd.concat(parts).sort_index() if parts else pd.Series(dtype=float)
    return out


def get_pvgis6():
    s = CFG["site"]
    pv = CFG["pv_system"]
    parts = []
    for y in YEARS:
        f = ROOT / "data/raw/pvgis6" / f"pvgis6_{y}.json"
        io_utils.fetch_to_file(CFG["pvgis6"]["base_url"], f, params=pvgis.pvgis6_params(
            s["latitude"], s["longitude"], pv["peak_power_kwp"], pv["system_loss_pct"], pv["tilt_deg"],
            pv["azimuth_south_based_deg"], y))
        df, _ = pvgis.parse_pvgis6(f, y)
        parts.append(df["pv_kwh"])
    return pd.concat(parts)


def ess_ar1(x: pd.Series) -> dict:
    x = x.dropna()
    r1 = float(x.autocorr(1))
    n = len(x)
    return {"n": n, "lag1_autocorr": round(r1, 3), "ess_ar1_approx": round(n * (1 - r1) / (1 + r1), 1) if r1 < 1 else None}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pr = get_prices()
    fc = get_forecasts()
    pv = get_pvgis6()
    res = {"years": YEARS}

    # 1. price regimes
    res["prices_by_year"] = {int(y): {"n_hours": int(g.notna().sum()), "mean": round(float(g.mean()), 1),
                                      "std": round(float(g.std()), 1), "p05": round(float(g.quantile(.05)), 1),
                                      "p95": round(float(g.quantile(.95)), 1), "negative_hours": int((g < 0).sum())}
                             for y, g in pr.groupby(pr.index.year)}

    # 2. forecast availability (hourly means of 15-min values)
    fh = {t: s.resample("h").mean() for t, s in fc.items() if len(s)}
    res["forecast_coverage"] = {t: {"first": str(s.index.min()), "last": str(s.index.max()),
                                    "non_missing_hours_by_year": {int(y): int(g.notna().sum()) for y, g in s.groupby(s.index.year)}}
                                for t, s in fh.items()}
    # solar penetration proxy: max and mean of solar forecast / load forecast at midday
    if "solar" in fh and "load" in fh:
        j = pd.concat({"solar": fh["solar"], "load": fh["load"]}, axis=1).dropna()
        j["share"] = j["solar"] / j["load"]
        res["solar_forecast_share_of_load"] = {int(y): {"mean_all_hours": round(float(g["share"].mean()), 3),
                                                        "p95": round(float(g["share"].quantile(.95)), 3),
                                                        "max": round(float(g["share"].max()), 3)}
                                               for y, g in j.groupby(j.index.year)}

    # 3 & 4. capture rates per year (PVGIS 6, raw nulls dropped) and daily/monthly series autocorrelation
    df = pd.concat({"e": pv / 1000, "p": pr}, axis=1).dropna()
    df["ep"] = df["e"] * df["p"]
    ann = df.groupby(df.index.year).agg(e=("e", "sum"), ep=("ep", "sum"), pbar=("p", "mean"))
    res["capture_rate_by_year"] = {int(y): round(float(r.ep / r.e / r.pbar), 3) for y, r in ann.iterrows()}
    day = df.resample("D").agg({"e": "sum", "ep": "sum", "p": "mean"})
    day = day[day["e"] > 0.05]
    day["cr"] = day["ep"] / day["e"] / day["p"]
    day = day[(day["p"].abs() > 5)]  # capture rate is ill-defined when the daily mean price ≈ 0
    mon = df.resample("MS").agg({"e": "sum", "ep": "sum", "p": "mean"})
    mon["cr"] = mon["ep"] / mon["e"] / mon["p"]
    res["daily_capture_rate"] = {"all": ess_ar1(day["cr"]),
                                 "by_year_mean": {int(y): round(float(g.mean()), 3) for y, g in day["cr"].groupby(day.index.year)},
                                 "days_excluded_mean_price_near_zero": int((df.resample("D")["p"].mean().abs() <= 5).sum())}
    res["monthly_capture_rate"] = ess_ar1(mon["cr"])
    res["annual_observations"] = len(ann)

    # figure
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.2))
    yrs = sorted(res["prices_by_year"])
    ax[0].bar([str(y) for y in yrs], [res["prices_by_year"][y]["mean"] for y in yrs])
    ax[0].set_title("NL day-ahead mean price (€/MWh)")
    ax[1].plot([str(y) for y in sorted(res["capture_rate_by_year"])], list(res["capture_rate_by_year"].values()), marker="o")
    ax[1].set_title("Annual solar capture rate (PVGIS 6 site profile)")
    if "solar_forecast_share_of_load" in res:
        s = res["solar_forecast_share_of_load"]
        ax[2].plot([str(y) for y in s], [v["p95"] for v in s.values()], marker="o", label="P95 hourly")
        ax[2].plot([str(y) for y in s], [v["mean_all_hours"] for v in s.values()], marker=".", label="mean")
        ax[2].set_title("Day-ahead solar forecast / load forecast")
        ax[2].legend()
    for a in ax:
        a.tick_params(axis="x", rotation=45)
    fig.suptitle("Data sufficiency: ten years, several market regimes (prices: Energy-Charts / SMARD)")
    fig.tight_layout()
    fig.savefig(ROOT / "reports/figures/data_sufficiency_regimes.png", dpi=130)
    (OUT / "data_sufficiency.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=1)[:6000])


if __name__ == "__main__":
    main()
