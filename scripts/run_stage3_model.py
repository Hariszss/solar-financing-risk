"""Stage 3 — Project-finance model: CFADS, annuity debt service, DSCR; merchant vs PPA on the SAME project.

Prices: Energy-Charts (SMARD data), not cross-checked against ENTSO-E. All financing terms are public
benchmarks (PBL SDE++ 2025) or illustrative — never real lending terms.

Outputs: reports/stage3/stage3_results.json, reports/figures/stage3_*.png
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
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
from solarrisk.finance import project as pj  # noqa: E402
from solarrisk.finance.annuity import amortization_schedule  # noqa: E402

CFG = yaml.safe_load((ROOT / "configs/stage3.yaml").read_text(encoding="utf-8"))
OUT, FIG = ROOT / "reports/stage3", ROOT / "reports/figures"
V = lambda sec, k: CFG[sec][k]["value"]  # noqa: E731
PRICE_STATUS = "Prices: Energy-Charts (Bundesnetzagentur | SMARD.de, CC BY 4.0); not independently cross-checked against ENTSO-E"


def base_assumptions() -> pj.Assumptions:
    return pj.Assumptions(
        dc_kwp=V("project", "dc_capacity_kwp"), export_cap_fraction=V("project", "grid_export_fraction_of_dc"),
        capex_eur_per_kwp=V("costs", "capex_eur_per_kwp"), fixed_opex_eur_per_kwp=V("costs", "fixed_opex_eur_per_kwp_yr"),
        variable_opex_eur_per_mwh=V("costs", "variable_opex_eur_per_mwh"), imbalance_eur_per_mwh=V("costs", "imbalance_cost_eur_per_mwh"),
        land_lease_eur_per_kwp=V("costs", "land_lease_eur_per_kwp_yr"), gearing=V("financing", "gearing_debt_share"),
        interest_rate=V("financing", "interest_rate"), tenor=V("financing", "tenor_years"), tax_rate=V("financing", "tax_rate"),
        depreciation_years=V("financing", "depreciation_years"), degradation=V("project", "degradation_per_year"),
        opex_inflation=V("costs", "opex_inflation"), inverter_replacement_eur_per_kwp=V("costs", "inverter_replacement_eur") / V("project", "dc_capacity_kwp"),
        dsra_months=V("financing", "dsra_months"))


def load():
    pv6 = pd.read_csv(ROOT / "data/interim/pvgis6_hourly.csv", index_col=0, parse_dates=True)
    pv53 = pd.read_csv(ROOT / "data/interim/pvgis53_hourly.csv", index_col=0, parse_dates=True)
    pr = pd.read_csv(ROOT / "data/interim/prices_energycharts_nl_hourly.csv", index_col=0, parse_dates=True)["price_eur_mwh"]
    kn = pd.read_csv(ROOT / "data/interim/knmi_hourly.csv", parse_dates=["interval_start_utc"])
    ghi = kn[kn.station == 269].set_index("interval_start_utc")["ghi_wm2"]
    filled, _ = cp.fill_with_ratio_proxy(pv6["pv_kwh"], ghi)          # Stage 2 primary treatment
    pv = filled / 1000.0                                               # kWh per kWp per hour == mean kW/kWp
    ratio_53 = float(pv53["pv_kwh"].sum() / pv6["pv_kwh"].reindex(pv53.index).fillna(0).sum())
    return pv, pr, ratio_53


def years_of(s):
    return sorted(set(s.index.year))


def sl(s, y):
    return s[s.index.year == y]


def pbl_calibration_factor(pv, a, target=855.0) -> float:
    """Factor f so that the 2021–2024 mean clipped specific yield equals PBL's 855 full-load hours."""
    def mean_yield(f):
        aa = replace(a, production_factor=f)
        return np.mean([pj.production_mwh(sl(pv, y), aa).sum() * 1000 / a.dc_kwp for y in years_of(pv)])
    return pj.bisect(mean_yield, 0.5, 1.5, target, tol=1e-6)


def double_negative_hours(p: pd.Series) -> pd.Series:
    """Stress: push the lowest-priced non-negative hours below zero until the count of negative hours doubles;
    they receive the year's mean negative price. Transparent, deterministic, not a forecast."""
    neg = p < 0
    n = int(neg.sum())
    if n == 0:
        return p.copy()
    cand = p[~neg].sort_values().index[:n]
    q = p.copy()
    q.loc[cand] = float(p[neg].mean())
    return q


def r3(d):
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items()}


# ------------------------------------------------------------------------------------------ main
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pv, pr, ratio_53 = load()
    a = base_assumptions()
    k0 = V("ppa", "price_eur_per_mwh")
    contracts = [pj.MERCHANT, pj.MERCHANT_CURTAIL, replace(pj.PPA_NONE, ppa_price=k0), replace(pj.PPA_NOPAY, ppa_price=k0)]
    Y = years_of(pv)
    res: dict = {"price_status": PRICE_STATUS, "assumptions": asdict(a), "ppa_price_base": k0}
    sched = amortization_schedule(a.debt, a.interest_rate, a.tenor)
    res["debt"] = {"capex": a.capex, "debt": a.debt, "equity": a.capex - a.debt,
                   "annual_debt_service": round(float(sched["debt_service"].iloc[0]), 2),
                   "schedule": {int(k): {c: round(float(v), 2) for c, v in r.items()} for k, r in sched.iterrows()}}

    # production cross-checks
    # PBL's 855 h is the AVERAGE over years 1–15 and already includes 0.64 %/yr degradation (PBL p.76).
    # Calibrate YEAR 1 to the equivalent value, otherwise degradation is counted twice.
    deg_avg = float(np.mean([(1 - a.degradation) ** (t - 1) for t in range(1, a.tenor + 1)]))
    pbl_year1_target = 855.0 / deg_avg
    f_pbl = pbl_calibration_factor(pv, a, target=pbl_year1_target)
    res["production"] = {
        "pvgis53_over_pvgis6_energy_ratio": round(ratio_53, 4), "pbl_calibration_factor": round(f_pbl, 4),
        "specific_yield_kwh_per_kwp": {int(y): {
            "unclipped": round(float(sl(pv, y).sum()), 1),
            "clipped_50pct": round(float(pj.production_mwh(sl(pv, y), a).sum() * 1000 / a.dc_kwp), 1),
            "clipped_70pct": round(float(pj.production_mwh(sl(pv, y), replace(a, export_cap_fraction=0.7)).sum() * 1000 / a.dc_kwp), 1)}
            for y in Y},
        "pbl_full_load_hours_reference": 855, "pbl_year1_equivalent_target": round(pbl_year1_target, 1),
        "mean_degradation_factor_years_1_15": round(deg_avg, 4)}

    # cross-stage validation: unclipped 10 MWp merchant revenue = 10 × Stage-2 1 MWp revenue
    s2 = json.loads((ROOT / "reports/stage2/stage2_results.json").read_text())["annual"]
    a_nocap = replace(a, export_cap_fraction=10.0)
    res["validation_vs_stage2"] = {int(y): {
        "stage3_unclipped_revenue": round(pj.revenue(pj.production_mwh(sl(pv, y), a_nocap), sl(pr, y), pj.MERCHANT)["revenue"], 2),
        "10x_stage2_revenue": round(10 * s2[str(y)]["revenue_eur"], 2)} for y in Y}

    # ---------- year-1 results: each historical year replayed as year 1
    yr1 = {}
    for y in Y:
        yr1[int(y)] = {c.label: r3(pj.year_result(sl(pv, y), sl(pr, y), a, c)) for c in contracts}
    res["year1"] = yr1

    # ---------- scaling benchmark
    bench = {}
    # "no grid cap" rows keep PBL's per-kWp capex (which includes a 50 % connection) → pure scaling benchmark,
    # they understate the cost of a full-size connection.
    for name, aa in [("1 MWp, no grid cap (benchmark)", replace(a, dc_kwp=1000, export_cap_fraction=10.0)),
                     ("10 MWp, no grid cap (benchmark)", replace(a, export_cap_fraction=10.0)),
                     ("10 MWp, 70% grid cap (+17 €/kWp)", replace(a, export_cap_fraction=0.7, capex_eur_per_kwp=a.capex_eur_per_kwp + 17)),
                     ("10 MWp, 50% grid cap (base)", a)]:
        bench[name] = {int(y): {c.label: round(pj.year_result(sl(pv, y), sl(pr, y), aa, c)["dscr"], 3) for c in contracts} for y in Y}
    res["scaling_benchmark_dscr"] = bench

    # ---------- production model uncertainty
    pu = {}
    for name, f in [("PVGIS 6 (base)", 1.0), ("PVGIS 5.3 level", ratio_53), ("PBL 855 h calibrated", f_pbl)]:
        aa = replace(a, production_factor=f)
        pu[name] = {int(y): {c.label: r3({k: pj.year_result(sl(pv, y), sl(pr, y), aa, c)[k] for k in ["delivered_mwh", "revenue", "cfads", "dscr"]})
                             for c in contracts} for y in Y}
    res["production_uncertainty"] = pu

    # ---------- stresses (year 1)
    def stresses(c):
        out = [("Base", a, None, c)]
        out += [("Production: PVGIS 5.3 level", replace(a, production_factor=ratio_53), None, c),
                ("Production: PBL-calibrated", replace(a, production_factor=f_pbl), None, c),
                ("Production −10 %", replace(a, production_factor=0.9), None, c),
                ("Opex +20 %", replace(a, fixed_opex_eur_per_kwp=a.fixed_opex_eur_per_kwp * 1.2,
                                       variable_opex_eur_per_mwh=a.variable_opex_eur_per_mwh * 1.2,
                                       imbalance_eur_per_mwh=a.imbalance_eur_per_mwh * 1.2), None, c),
                ("Imbalance 18.1 €/MWh (2023 level)", replace(a, imbalance_eur_per_mwh=V("costs", "imbalance_cost_stress_eur_per_mwh")), None, c),
                ("Land lease 5 €/kWp/yr", replace(a, land_lease_eur_per_kwp=5.0), None, c),
                ("Interest +200 bp", replace(a, interest_rate=a.interest_rate + 0.02), None, c),
                ("Gearing 60 %", replace(a, gearing=0.60), None, c),
                # PBL Tabel 5.9 p.72: connection at 70% costs 30 (+30 cable) = 60 €/kWp vs 43 €/kWp at 50% → +17 €/kWp capex
                ("Grid cap 70 % (+17 €/kWp capex)", replace(a, export_cap_fraction=0.70, capex_eur_per_kwp=a.capex_eur_per_kwp + 17), None, c)]
        if c.kind == "merchant":
            out += [("Prices −20 %", a, ("mult", 0.8), c), ("Prices +20 %", a, ("mult", 1.2), c),
                    ("Negative-price hours doubled", a, ("dbl", None), c)]
        else:
            out += [("PPA price 50 €/MWh", a, None, replace(c, ppa_price=50.0)),
                    ("PPA price 70 €/MWh", a, None, replace(c, ppa_price=70.0)),
                    ("Negative-price hours doubled", a, ("dbl", None), c)]
        return out

    st = {}
    for y in Y:
        st[int(y)] = {}
        for c in contracts:
            st[int(y)][c.label] = {}
            for name, aa, pmod, cc in stresses(c):
                p = sl(pr, y)
                if pmod and pmod[0] == "mult":
                    p = p * pmod[1]
                elif pmod and pmod[0] == "dbl":
                    p = double_negative_hours(p)
                st[int(y)][c.label][name] = round(pj.year_result(sl(pv, y), p, aa, cc)["dscr"], 3)
    res["stress_dscr"] = st

    # ---------- break-even analysis
    be = {}
    for y in Y:
        pvy, py = sl(pv, y), sl(pr, y)
        cap_merch = pj.revenue(pj.production_mwh(pvy, a), py, pj.MERCHANT)["realised_price"]
        d = {"merchant_capture_price_clipped": round(cap_merch, 2)}
        for t in (1.0, 1.2):
            for clause in ("none", "no_payment_and_curtail"):
                v = pj.breakeven_ppa_price(pvy, py, a, clause, t)
                d[f"ppa_price_for_dscr_{t}_{clause}"] = round(v, 2) if v else None
            for c in (pj.MERCHANT, pj.MERCHANT_CURTAIL):
                m = pj.breakeven_price_multiplier(pvy, py, a, c, t)
                d[f"price_multiplier_for_dscr_{t}_{c.label}"] = round(m, 3) if m else None
                d[f"required_capture_price_for_dscr_{t}_{c.label}"] = round(m * cap_merch, 2) if m else None
        for c in contracts:
            f = pj.breakeven_production_factor(pvy, py, a, c, 1.0)
            d[f"production_factor_for_dscr_1_{c.label}"] = round(f, 3) if f else None
            for t in (1.2, 1.5):
                d[f"max_gearing_at_dscr_{t}_{c.label}"] = round(pj.debt_capacity_gearing(pvy, py, a, c, t), 3)
        be[int(y)] = d
    res["breakeven"] = be
    # PPA break-even price vs gearing (production-weighted, using all four weather years' mean → per year)
    grid = [0.5, 0.6, 0.7, 0.8]
    res["breakeven_ppa_vs_gearing"] = {int(y): {g: {t: round(pj.breakeven_ppa_price(sl(pv, y), sl(pr, y), replace(a, gearing=g), "none", t), 2)
                                                    for t in (1.0, 1.2)} for g in grid} for y in Y}

    # break-even PPA price under each production case (model uncertainty moves the break-even)
    res["breakeven_ppa_by_production_case"] = {
        name: {int(y): {t: round(pj.breakeven_ppa_price(sl(pv, y), sl(pr, y), replace(a, production_factor=f), "none", t), 2)
                        for t in (1.0, 1.2)} for y in Y}
        for name, f in [("PVGIS 6 (base)", 1.0), ("PVGIS 5.3 level", ratio_53), ("PBL 855 h calibrated", f_pbl)]}

    # ---------- deterministic tenor stress: one historical year repeated over the loan life
    lt, paths, sculpt = {}, {}, {}
    for idx_name, gidx in [("flat_nominal", 0.0), ("indexed_2pct", a.opex_inflation)]:
        lt[idx_name], paths[idx_name] = {}, {}
        for y in Y:
            lt[idx_name][int(y)], paths[idx_name][int(y)] = {}, {}
            for c in contracts:
                proj = pj.tenor_projection(sl(pv, y), sl(pr, y), a, c, price_index=gidx)
                lt[idx_name][int(y)][c.label] = pj.dsra_summary(proj, a)
                paths[idx_name][int(y)][c.label] = [round(float(v), 3) for v in proj["dscr"]]
                if idx_name == "flat_nominal" and c.label == contracts[2].label:
                    sculpt[int(y)] = {t: pj.annuity_vs_sculpted_capacity(proj, a, t) for t in (1.2,)}
    res["tenor_stress"] = lt
    res["tenor_dscr_paths"] = paths
    res["annuity_vs_sculpted_ppa_none"] = sculpt

    res["figures"] = figures(res, contracts, Y)
    (OUT / "stage3_results.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    print("wrote reports/stage3/stage3_results.json")


# ------------------------------------------------------------------------------------------ figures
def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, dpi=130)
    plt.close(fig)
    return name


def figures(res, contracts, Y):
    made = []
    labels = [c.label for c in contracts]
    # 1 year-1 DSCR grouped bars
    fig, ax = plt.subplots(figsize=(10, 5))
    w = 0.2
    x = np.arange(len(Y))
    for i, lab in enumerate(labels):
        vals = [min(res["year1"][int(y)][lab]["dscr"], 6) for y in Y]
        ax.bar(x + (i - 1.5) * w, vals, w, label=lab)
    for lv, ls in [(1.0, "-"), (1.2, "--"), (1.5, ":")]:
        ax.axhline(lv, color="k", lw=0.8, ls=ls)
    ax.set_xticks(x, [f"{y} weather & prices" for y in Y])
    ax.set_ylabel("Year-1 DSCR (capped at 6 for display)")
    ax.set_title("Year-1 DSCR, same 10 MWp project & debt\n(lines: 1.0 cash break-even; 1.2 / 1.5 illustrative reference levels)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    made.append(_save(fig, "stage3_dscr_year1.png"))

    # 2 waterfall 2024: merchant vs PPA
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, lab in zip(axes, [labels[0], labels[2]]):
        r = res["year1"][2024][lab] if 2024 in res["year1"] else res["year1"][Y[-1]][lab]
        steps = [("Revenue", r["revenue"]), ("− Opex", -r["opex_total"]), ("− Tax", -r["tax"]),
                 ("− Maint. reserve", -r["maintenance_capex"]), ("= CFADS", r["cfads"])]
        cum = 0
        for i, (n, v) in enumerate(steps):
            if n.startswith("="):
                ax.bar(i, v, color="tab:blue")
            else:
                ax.bar(i, v, bottom=cum if v < 0 else 0, color="tab:green" if v > 0 else "tab:red")
                cum += v
        ax.axhline(r["debt_service"], color="k", ls="--", label=f"Debt service {r['debt_service']/1e3:.0f} k€")
        ax.set_xticks(range(len(steps)), [s[0] for s in steps], fontsize=8)
        ax.set_ylim(0, max(r["revenue"], r["debt_service"]) * 1.12)
        ax.set_title(f"{lab}\n2024 weather & prices: DSCR {r['dscr']:.2f}", fontsize=9)
        ax.legend(fontsize=8)
    axes[0].set_ylabel("EUR")
    fig.suptitle("Cash waterfall, year 1 (10 MWp)")
    fig.tight_layout()
    made.append(_save(fig, "stage3_waterfall_2024.png"))

    # 3 tornado for 2024 & 2023, merchant and PPA none
    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    for ax, (y, lab) in zip(axes.flat, [(2023, labels[0]), (2023, labels[2]), (2024, labels[0]), (2024, labels[2])]):
        s = res["stress_dscr"][y][lab]
        base = s["Base"]
        items = sorted([(k, v - base) for k, v in s.items() if k != "Base"], key=lambda kv: abs(kv[1]))
        ax.barh([k for k, _ in items], [d for _, d in items], color=["tab:red" if d < 0 else "tab:green" for _, d in items])
        ax.axvline(0, color="k", lw=0.8)
        ax.set_title(f"{lab} — {y} (base DSCR {base:.2f})", fontsize=9)
        ax.tick_params(axis="y", labelsize=7)
        ax.set_xlabel("Δ DSCR vs base")
    fig.suptitle("Stress tests, year 1")
    fig.tight_layout()
    made.append(_save(fig, "stage3_stress_tornado.png"))

    # 4 break-even PPA price vs gearing
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for y in Y:
        d = res["breakeven_ppa_vs_gearing"][int(y)]
        g = sorted(d)
        ax.plot([100 * x for x in g], [d[x][1.2] for x in g], marker="o", label=f"DSCR 1.2, {y} production")
        ax.plot([100 * x for x in g], [d[x][1.0] for x in g], marker=".", ls=":", label=f"DSCR 1.0, {y} production")
    ax.axhspan(40, 70, color="grey", alpha=0.12, label="tested PPA range 40–70")
    ax.set_xlabel("Gearing (debt / capex, %)")
    ax.set_ylabel("Break-even PPA price €/MWh")
    ax.set_title("PPA price needed for a given year-1 DSCR (no negative-price clause)")
    ax.legend(fontsize=7, ncol=2)
    fig.tight_layout()
    made.append(_save(fig, "stage3_breakeven_ppa_vs_gearing.png"))

    # 5 tenor DSCR paths
    fig, axes = plt.subplots(2, len(Y), figsize=(16, 7.5), sharey=True)
    for row, idx_name in enumerate(["flat_nominal", "indexed_2pct"]):
        for ax, y in zip(axes[row], Y):
            for lab in labels:
                ax.plot(range(1, 16), [min(v, 5) for v in res["tenor_dscr_paths"][idx_name][int(y)][lab]], label=lab)
            ax.axhline(1, color="k", lw=0.8)
            ax.set_title(f"Every year like {y} — prices {'flat nominal' if row == 0 else 'indexed 2 %/yr'}", fontsize=8)
            ax.set_xlabel("Loan year")
        axes[row][0].set_ylabel("DSCR (capped at 5)")
    axes[0][0].legend(fontsize=7)
    fig.suptitle("Deterministic tenor stress: one historical year repeated, with degradation, 2 %/yr opex inflation and maintenance reserve — NOT a forecast")
    fig.tight_layout()
    made.append(_save(fig, "stage3_tenor_paths.png"))
    return made


if __name__ == "__main__":
    main()
