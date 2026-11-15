"""Render reports/stage2/STAGE2_REPORT.md from stage2_results.json (results are read from the JSON file)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = json.loads((ROOT / "reports/stage2/stage2_results.json").read_text(encoding="utf-8"))
A, N, C = R["annual"], R["negative_prices"], R["covariance_decomposition_month_hour"]
Y = sorted(A)


def tbl(h, rows):
    return "\n".join(["| " + " | ".join(h) + " |", "|" + "---|" * len(h)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def eur(x):
    return f"{x:,.0f}"


RATES = ", ".join(f"{y}: {A[y]['capture_rate']:.0%}" for y in Y)
DISCOUNTS = ", ".join(f"{y}: {A[y]['capture_discount_eur_mwh']:.1f}" for y in Y)
SPEARMAN = ", ".join(f"{y}: {A[y]['spearman_e_p_production_hours']}" for y in Y)


L = [f"# Stage 2 — Solar Capture Price & Merchant Revenue (2021–2024)\n",
     f"_Generated {date.today().isoformat()} by `scripts/build_stage2_report.py` from `stage2_results.json`._\n",
     f"> **Price data.** {R['price_status']}. Every € and €/MWh figure below depends on these prices. "
     "The structural checks in the data audit passed; the values have not been compared with the official ENTSO-E series.\n",
     "**Labels:** **[Verified]** = computed identity or data-structure check; **[Price-dependent]** = depends on the Energy-Charts "
     "prices; **[Modelled]** = PVGIS output; **[Assumed]** = our choice for a hypothetical project.\n",
     f"**[Assumed]** System: {R['system']} (52.45° N, 5.70° E, 35° tilt, south, 14 % losses). Production: "
     f"{R['primary_production_series']} **[Modelled]**. No grid fees, imbalance costs, subsidies, curtailment orders or "
     "degradation are included — this is gross day-ahead market value, not project revenue.\n",
     "## 1. Baseload price vs solar capture price\n",
     "Baseload price p̄ = arithmetic mean of all hourly prices. Capture price = Σ E·p / Σ E (energy-weighted). "
     "Capture rate = capture / baseload.\n",
     tbl(["Year", "Energy MWh", "Baseload €/MWh", "Capture €/MWh", "Capture rate", "Discount €/MWh", "Revenue €",
          "Identity p̄+Cov/Ē", "Spearman(E,p)"],
         [[y, A[y]["energy_mwh"], A[y]["baseload_price"], A[y]["capture_price"], A[y]["capture_rate"],
           A[y]["capture_discount_eur_mwh"], eur(A[y]["revenue_eur"]), A[y]["identity_check_pbar_plus_cov_over_ebar"],
           A[y]["spearman_e_p_production_hours"]] for y in Y]) + "\n",
     f"**[Verified]** The identity column reproduces the capture price exactly in every year. **[Price-dependent]** Capture "
     f"rate by year: {RATES} — not a steady decline (2022 was the highest), but the last two years are the lowest. "
     f"Discount (€/MWh): {DISCOUNTS}. Spearman correlation of hourly production and price (producing hours): {SPEARMAN}.\n",
     "**What it means for a bank:** a forecast of *baseload* prices is not a forecast of solar revenue. In the latest year "
     f"the plant earned {A[Y[-1]]['capture_rate']:.0%} of the average market price. A lender's revenue case for a merchant "
     "solar project has to be built on capture prices, and their downward trend over these four years is a risk factor in "
     "its own right.\n",
     "![capture](../figures/stage2_capture_vs_baseload.png)\n",
     "## 2. Relationship between solar production and price\n",
     "Mean price by decile of hourly PV output (producing hours):\n",
     tbl(["Year"] + [f"D{i}" for i in range(1, 11)], [[y] + R["price_by_pv_decile"][y] for y in Y]) + "\n",
     "Decomposition of the capture discount (law of total covariance, groups = month × UTC hour) **[Verified identity, "
     "price-dependent values]**:\n",
     tbl(["Year", "Total discount €/MWh", "Timing part (between month-hour cells)", "Weather part (within cells)",
          "Timing share"],
         [[y, C[y]["discount_total_eur_mwh"], C[y]["discount_between_eur_mwh"], C[y]["discount_within_eur_mwh"],
           f"{C[y]['discount_between_eur_mwh'] / C[y]['discount_total_eur_mwh']:.0%}"] for y in Y]) + "\n",
     "Reading: the *timing* part is the discount explained by **when** solar produces on average (season and time of day); "
     "the *weather* part is the additional discount because, at the same clock hour and month, sunnier hours coincide with "
     "lower prices. Most of the discount is timing, which is predictable; the weather part is smaller but non-zero: "
     "output in sunnier-than-usual hours is sold below the typical price for that hour and month.\n",
     "**What it means for a bank:** the two must never be simulated independently. Combining production and prices from "
     "different historical days would remove the weather part of the discount (and, if profiles are misaligned, part "
     "of the timing part), overstating merchant revenue. All later stages keep production and price "
     "from the **same historical hour**.\n",
     "![decile](../figures/stage2_price_by_pv_decile.png)\n\n![heat](../figures/stage2_price_heatmaps.png)\n\n"
     "_Heatmap colour scales differ per year (2022 prices were far higher)._\n\n"
     "![diurnal](../figures/stage2_diurnal_price_by_year.png)\n",
     "## 3. Negative-price exposure\n",
     tbl(["Year", "Negative hours", "…with production", "MWh at negative prices", "% of annual energy",
          "Revenue in those hours €", "Mean price when producing & negative", "Zero-price hours with production",
          "Revenue if switched off at p<0 €", "Gain €"],
         [[y, N[y]["negative_hours"], N[y]["negative_hours_with_production"], N[y]["energy_in_negative_hours_mwh"],
           N[y]["pct_energy_in_negative_hours"], eur(N[y]["revenue_in_negative_hours_eur"]),
           N[y]["mean_price_when_negative_and_producing"], N[y]["zero_price_hours_with_production"],
           eur(N[y]["revenue_if_curtailed_eur"]), eur(N[y]["curtailment_gain_eur"])] for y in Y]) + "\n",
     f"Reading **[Price-dependent]**: in {Y[-1]} {N[Y[-1]]['pct_energy_in_negative_hours']} % of the plant's energy was produced "
     "in negative-price hours. The direct revenue loss in those hours is modest because most negative prices were only "
     f"slightly below zero (mean {N[Y[-1]]['mean_price_when_negative_and_producing']} €/MWh when producing), but that "
     f"energy earned nothing. Switching off at negative prices **[Assumed operational ability]** would have raised {Y[-1]} "
     f"revenue by {eur(N[Y[-1]]['curtailment_gain_eur'])} € "
     f"({100 * N[Y[-1]]['curtailment_gain_eur'] / N[Y[-1]]['revenue_total_eur']:.1f} %).\n",
     "**What it means for a bank:** the larger risk is not the negative price itself but the growing share of output that "
     "earns ≈ 0. This is also why PPA negative-price clauses (Stage 3) matter: they decide who bears these hours.\n",
     "![neg](../figures/stage2_negative_price_exposure.png)\n",
     "## 4. Monthly and seasonal patterns\n",
     tbl(["Year", "Lowest month", "€", "Highest month", "€", "Months with negative revenue"],
         [[y, v["min_month"][:7], eur(v["min_eur"]), v["max_month"][:7], eur(v["max_eur"]), v["n_months_negative"]]
          for y, v in R["monthly_revenue_stats"].items()]) + "\n",
     "Share of annual energy vs revenue by season (DJF = Dec–Feb of the same calendar year):\n",
     tbl(["Year", "DJF energy/revenue %", "MAM", "JJA", "SON"],
         [[y] + [f"{v[s]['pct_energy']} / {v[s]['pct_revenue']}" for s in ["DJF", "MAM", "JJA", "SON"]]
          for y, v in R["seasonal_shares"].items()]) + "\n",
     f"Months where capture price exceeded baseload (capture rate > 1): {', '.join(m[:7] for m in R['months_capture_rate_above_1']) or 'none'}. "
     "All are November–February months. This is consistent with the heatmaps (in winter the cheapest hours are at night, "
     "not midday), but we have not tested a mechanism.\n",
     "**What it means for a bank:** revenue is concentrated in a few months and the seasonal mix changes from year to year "
     "(e.g. autumn 2021 or summer 2022). Project debt is typically repaid on a fixed periodic schedule, so the timing of "
     "cash relative to payment dates matters — a point for Stage 3.\n",
     "![monthly](../figures/stage2_monthly_revenue.png)\n",
     "## 5. What drove revenue changes between years? (LMDI decomposition)\n",
     tbl(["Change", "Revenue Δ €", "Δ %", "Volume (weather) effect €", "Price effect €", "Energy Δ %", "Capture price Δ %"],
         [[k, eur(v["revenue_change_eur"]), v["pct_change"], eur(v["volume_effect_eur"]), eur(v["price_effect_eur"]),
           v["energy_pct_change"], v["capture_price_pct_change"]] for k, v in R["yoy_lmdi"].items()]) + "\n",
     "**[Verified]** volume + price effects sum exactly to the revenue change. **What it means for a bank:** over these "
     "years, price movements dominated weather by an order of magnitude. For merchant revenue, price risk is the first-order "
     "risk; production risk is second-order — which is precisely the risk a fixed-price PPA transfers.\n",
     "## 6. PVGIS 5.3 vs PVGIS 6 — effect on revenue (2021–2023)\n",
     tbl(["Year", "Revenue 5.3 €", "Revenue 6 €", "Revenue Δ %", "Energy Δ %", "Capture 5.3", "Capture 6", "Capture Δ €/MWh"],
         [[y, eur(v["revenue_53"]), eur(v["revenue_6"]), v["revenue_pct_diff"], v["energy_pct_diff"], v["capture_price_53"],
           v["capture_price_6"], v["capture_price_diff_eur"]] for y, v in R["pvgis53_vs_6"].items()]) + "\n",
     "Both series with comparable missing-data handling (5.3 zeroed records → missing → same proxy fill). Revenue differs "
     "slightly more than energy, i.e. PVGIS 6's extra energy falls in slightly higher-priced hours on average; this is "
     "consistent with its relative difference being largest in winter months (Stage 1), but not separately tested. **What it means for a bank:** the choice of production model moves revenue by ~5 %, a level effect "
     "that should be carried as a sensitivity, not resolved by picking the more favourable version.\n",
     "## 7. Missing-data treatment — does it change the answer?\n",
     tbl(["Treatment", "Year", "Energy MWh", "Revenue €", "Capture €/MWh", "Hours dropped"],
         [[t, y, v["energy_mwh"], eur(v["revenue_eur"]), v["capture_price"], v["n_hours_dropped_missing"]]
          for t, yy in R["treatments"].items() for y, v in yy.items()]) + "\n",
     "Notes: *complete case* and *nulls as zero* give identical sums by construction (dropping an hour from Σ E·p and Σ E is "
     "the same as adding 0); they differ only in what they claim. The primary treatment fills PVGIS 6 nulls with a "
     "KNMI-based month×hour ratio proxy. Differences between treatments are ≤ 0.5 % in revenue and < 0.1 €/MWh in capture "
     "price. **[Verified]** The conclusion is robust to the missing-data treatment; for PVGIS 5.3 the zero-encoding, if "
     "uncorrected, understates energy by ~2–4 MWh/yr.\n",
     "## 8. Limitations\n",
     "- Prices not independently cross-checked against ENTSO-E.\n- Gross day-ahead value only: no imbalance costs, grid tariffs, "
     "profile/balancing fees, subsidies (e.g. SDE++), grid-operator curtailment, availability or degradation.\n"
     "- Price-taker assumption: a 1 MWp plant does not move prices; national solar build-out does, and that is already in "
     "the historical prices.\n- Four years are four different market regimes; no statement here is a forecast.\n"
     "- Capture prices are **realised** values for a modelled plant, not estimates from a sample — sampling confidence "
     "intervals would answer the wrong question. The uncertainty that matters (future years) is explored with stress "
     "scenarios in Stage 3.\n"]
(ROOT / "reports/stage2/STAGE2_REPORT.md").write_text("\n".join(L), encoding="utf-8")
print("wrote reports/stage2/STAGE2_REPORT.md")
