"""Stage 2 — Solar capture price and merchant revenue analysis (2021–2024).

Prices: Energy-Charts (SMARD data). Structural checks passed; not independently cross-checked against ENTSO-E.
No debt model and no PPA comparison are performed here.

Inputs : data/interim/*.csv produced by scripts/run_stage1_audit.py
Outputs: reports/stage2/stage2_results.json, reports/stage2/STAGE2_REPORT.md, reports/figures/stage2_*.png
"""

from __future__ import annotations

import json
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
from solarrisk.analysis import capture as cp  # noqa: E402
from solarrisk.validation import solar  # noqa: E402

CFG = yaml.safe_load((ROOT / "configs/stage1.yaml").read_text(encoding="utf-8"))
KWP = CFG["pv_system"]["peak_power_kwp"]
LAT, LON = CFG["site"]["latitude"], CFG["site"]["longitude"]
OUT = ROOT / "reports/stage2"
FIG = ROOT / "reports/figures"
PRICE_STATUS = "Prices: Energy-Charts (Bundesnetzagentur | SMARD.de, CC BY 4.0); not independently cross-checked against ENTSO-E"


def load():
    pv6 = pd.read_csv(ROOT / "data/interim/pvgis6_hourly.csv", index_col=0, parse_dates=True)
    pv53 = pd.read_csv(ROOT / "data/interim/pvgis53_hourly.csv", index_col=0, parse_dates=True)
    pr = pd.read_csv(ROOT / "data/interim/prices_energycharts_nl_hourly.csv", index_col=0, parse_dates=True)["price_eur_mwh"]
    kn = pd.read_csv(ROOT / "data/interim/knmi_hourly.csv", parse_dates=["interval_start_utc"])
    ghi = kn[kn.station == 269].set_index("interval_start_utc")["ghi_wm2"]
    return pv6, pv53, pr, ghi


def production_treatments(pv6, pv53, ghi) -> dict[str, pd.Series]:
    """Energy series in MWh under different missing-data treatments."""
    t = {}
    filled, _ = cp.fill_with_ratio_proxy(pv6["pv_kwh"], ghi)
    t["pv6_proxy_filled"] = filled / 1e3                       # PRIMARY
    t["pv6_nulls_as_zero"] = pv6["pv_kwh"].fillna(0) / 1e3      # naive
    t["pv6_complete_case"] = pv6["pv_kwh"] / 1e3                # NaN hours dropped from capture sums
    t["pv53_raw"] = pv53["pv_kwh"] / 1e3                        # silent zeros taken at face value
    e11 = pd.Series(solar.solar_elevation_deg(pv53.index + pd.Timedelta(minutes=11), LAT, LON), pv53.index)
    zeroed = (pv53.pv_kw == 0) & (pv53.poa_wm2 == 0) & (pv53.sun_height_deg == 0) & (e11 > 2)
    fixed = pv53["pv_kwh"].mask(zeroed)
    fixed, _ = cp.fill_with_ratio_proxy(fixed, ghi)
    t["pv53_zeros_fixed"] = fixed / 1e3
    return t


def by_year(e: pd.Series, p: pd.Series, f) -> dict:
    e = e.dropna() if False else e
    out = {}
    for y in sorted(set(e.index.year)):
        ey, py = e[e.index.year == y], p[p.index.year == y]
        out[int(y)] = f(ey, py)
    return out


def monthly_table(e: pd.Series, p: pd.Series) -> pd.DataFrame:
    df = pd.concat({"e": e, "p": p}, axis=1).dropna()
    df["rev"] = df["e"] * df["p"]
    g = df.groupby(pd.Grouper(freq="MS"))
    m = pd.DataFrame({"energy_mwh": g["e"].sum(), "revenue_eur": g["rev"].sum(), "baseload": g["p"].mean()})
    m["capture_price"] = m["revenue_eur"] / m["energy_mwh"]
    m["capture_rate"] = m["capture_price"] / m["baseload"]
    return m


def price_by_decile(e: pd.Series, p: pd.Series) -> dict:
    df = pd.concat({"e": e, "p": p}, axis=1).dropna()
    df = df[df["e"] > 0]
    out = {}
    for y, g in df.groupby(df.index.year):
        q = pd.qcut(g["e"], 10, labels=False, duplicates="drop")
        out[int(y)] = [round(float(v), 2) for v in g.groupby(q)["p"].mean()]
    return out


# ------------------------------------------------------------------------------------------ figures
def figures(res, e, p, monthly) -> list[str]:
    made = []
    yrs = sorted(res["annual"])
    # 1 capture vs baseload
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(yrs))
    base = [res["annual"][y]["baseload_price"] for y in yrs]
    cap = [res["annual"][y]["capture_price"] for y in yrs]
    ax.bar(x - 0.2, base, 0.4, label="Baseload (arithmetic mean) price")
    ax.bar(x + 0.2, cap, 0.4, label="Solar capture price (energy-weighted)")
    for i, y in enumerate(yrs):
        ax.text(x[i] + 0.2, cap[i] + 4, f"{res['annual'][y]['capture_rate']:.0%}", ha="center", fontsize=9)
    ax.set_xticks(x, yrs)
    ax.set_ylabel("€/MWh")
    ax.set_title("Baseload vs solar capture price, NL (capture rate above bars)")
    ax.legend()
    fig.tight_layout()
    made.append(_save(fig, "stage2_capture_vs_baseload.png"))

    # 2 price by PV decile
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for y, v in res["price_by_pv_decile"].items():
        ax.plot(range(1, len(v) + 1), v, marker="o", label=str(y))
    ax.set_xlabel("Decile of hourly PV output (producing hours only; 1 = lowest)")
    ax.set_ylabel("Mean day-ahead price €/MWh")
    ax.set_title("Price falls as solar output rises")
    ax.legend()
    fig.tight_layout()
    made.append(_save(fig, "stage2_price_by_pv_decile.png"))

    # 3 heatmaps month x hour mean price
    fig, axes = plt.subplots(1, 4, figsize=(16, 4), sharey=True)
    for ax, y in zip(axes, yrs):
        s = p[p.index.year == y]
        h = s.groupby([s.index.month, s.index.hour]).mean().unstack()
        im = ax.imshow(h.values, aspect="auto", cmap="RdBu_r", vmin=-50, vmax=np.nanpercentile(h.values, 98),
                       extent=[-0.5, 23.5, 12.5, 0.5])
        ax.set_title(str(y))
        ax.set_xlabel("Hour (UTC)")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
    axes[0].set_ylabel("Month")
    fig.suptitle("Mean day-ahead price by month and hour (€/MWh)")
    fig.tight_layout()
    made.append(_save(fig, "stage2_price_heatmaps.png"))

    # 4 monthly revenue + capture/baseload
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.bar(monthly.index, monthly["revenue_eur"] / 1e3, width=25, color="tab:green", alpha=0.6, label="Merchant revenue (k€, 1 MWp)")
    ax.set_ylabel("k€ per month")
    ax2 = ax.twinx()
    ax2.plot(monthly.index, monthly["baseload"], "k-", lw=1, label="Baseload price")
    ax2.plot(monthly.index, monthly["capture_price"], "r-", lw=1.2, label="Capture price")
    ax2.set_ylabel("€/MWh")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8)
    ax.set_title("Monthly merchant revenue and prices")
    fig.tight_layout()
    made.append(_save(fig, "stage2_monthly_revenue.png"))

    # 5 negative price exposure
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ng = res["negative_prices"]
    ax[0].bar([str(y) for y in yrs], [ng[y]["negative_hours_with_production"] for y in yrs], color="tab:red")
    ax[0].set_title("Negative-price hours with production")
    ax[1].bar([str(y) for y in yrs], [ng[y]["pct_energy_in_negative_hours"] for y in yrs], color="tab:orange")
    ax[1].set_title("% of annual energy produced at negative prices")
    fig.suptitle("Negative-price exposure, 1 MWp test system")
    fig.tight_layout()
    made.append(_save(fig, "stage2_negative_price_exposure.png"))

    # 6 diurnal price by year + PV profile
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for y in yrs:
        s = p[p.index.year == y]
        ax.plot(s.groupby(s.index.hour).mean(), label=f"price {y}")
    ax.set_xlabel("Hour (UTC)")
    ax.set_ylabel("€/MWh")
    ax2 = ax.twinx()
    ax2.fill_between(range(24), e.groupby(e.index.hour).mean() * 1e3, color="gold", alpha=0.3, label="mean PV (kW)")
    ax2.set_ylabel("kW")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("Mean diurnal price by year vs PV profile")
    fig.tight_layout()
    made.append(_save(fig, "stage2_diurnal_price_by_year.png"))
    return made


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, dpi=130)
    plt.close(fig)
    return name


# ------------------------------------------------------------------------------------------ main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pv6, pv53, pr, ghi = load()
    tr = production_treatments(pv6, pv53, ghi)
    e = tr["pv6_proxy_filled"]
    res: dict = {"price_status": PRICE_STATUS, "system": f"{KWP} kWp hypothetical test system",
                 "primary_production_series": "PVGIS 6, nulls filled with KNMI month-hour ratio proxy"}

    res["annual"] = by_year(e, pr, cp.capture_metrics)
    res["negative_prices"] = by_year(e, pr, cp.negative_price_exposure)
    res["covariance_decomposition_month_hour"] = by_year(
        e, pr, lambda a, b: cp.covariance_decomposition(a, b.reindex(a.index), [a.index.month, a.index.hour]))
    res["price_by_pv_decile"] = price_by_decile(e, pr)

    # treatments
    res["treatments"] = {}
    for name, s in tr.items():
        res["treatments"][name] = {y: {k: v[k] for k in ["energy_mwh", "revenue_eur", "capture_price", "capture_rate",
                                                          "n_hours_dropped_missing"]}
                                   for y, v in by_year(s, pr, cp.capture_metrics).items()}
    # PVGIS 5.3 vs 6 (common years) with comparable missing-data treatment
    cmpv = {}
    for y in sorted(set(tr["pv53_zeros_fixed"].index.year)):
        a, b = res["treatments"]["pv53_zeros_fixed"][y], res["treatments"]["pv6_proxy_filled"][y]
        cmpv[y] = {"revenue_53": a["revenue_eur"], "revenue_6": b["revenue_eur"],
                   "revenue_pct_diff": round(100 * (b["revenue_eur"] / a["revenue_eur"] - 1), 2),
                   "energy_pct_diff": round(100 * (b["energy_mwh"] / a["energy_mwh"] - 1), 2),
                   "capture_price_53": a["capture_price"], "capture_price_6": b["capture_price"],
                   "capture_price_diff_eur": round(b["capture_price"] - a["capture_price"], 3)}
    res["pvgis53_vs_6"] = cmpv

    # monthly & seasonal
    monthly = monthly_table(e, pr)
    res["monthly"] = {str(k.date()): {c: round(float(v), 3) for c, v in r.items()} for k, r in monthly.iterrows()}
    seas = monthly.assign(season=[cp.season(m) for m in monthly.index.month], year=monthly.index.year)
    sh = {}
    for y, g in seas.groupby("year"):
        gs = g.groupby("season")[["energy_mwh", "revenue_eur"]].sum()
        sh[int(y)] = {s: {"pct_energy": round(float(100 * gs.loc[s, "energy_mwh"] / gs["energy_mwh"].sum()), 1),
                          "pct_revenue": round(float(100 * gs.loc[s, "revenue_eur"] / gs["revenue_eur"].sum()), 1)}
                      for s in ["DJF", "MAM", "JJA", "SON"]}
    res["seasonal_shares"] = sh
    mr = monthly["revenue_eur"]
    res["monthly_revenue_stats"] = {int(y): {"min_month": str(g.idxmin().date()), "min_eur": round(float(g.min()), 0),
                                             "max_month": str(g.idxmax().date()), "max_eur": round(float(g.max()), 0),
                                             "n_months_negative": int((g < 0).sum())}
                                    for y, g in mr.groupby(mr.index.year)}
    res["months_capture_rate_above_1"] = [str(k.date()) for k, v in monthly["capture_rate"].items() if v > 1]
    # year-on-year decomposition
    yrs = sorted(res["annual"])
    res["yoy_lmdi"] = {f"{a}->{b}": cp.volume_price_decomposition(
        res["annual"][a]["energy_mwh"], res["annual"][a]["capture_price"],
        res["annual"][b]["energy_mwh"], res["annual"][b]["capture_price"]) for a, b in zip(yrs[:-1], yrs[1:])}

    res["figures"] = figures(res, e, pr, monthly)
    (OUT / "stage2_results.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print("wrote reports/stage2/stage2_results.json")


if __name__ == "__main__":
    main()
