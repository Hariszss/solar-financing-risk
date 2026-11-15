"""Render reports/stage3/STAGE3_REPORT.md from stage3_results.json and configs/stage3.yaml."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
R = json.loads((ROOT / "reports/stage3/stage3_results.json").read_text(encoding="utf-8"))
CFG = yaml.safe_load((ROOT / "configs/stage3.yaml").read_text(encoding="utf-8"))
Y = sorted(R["year1"])
C = list(R["year1"][Y[0]].keys())   # contract labels
SHORT = {C[0]: "Merchant", C[1]: "Merchant + curtail", C[2]: "PPA (no clause)", C[3]: "PPA (no pay & curtail)"}


def tbl(h, rows):
    return "\n".join(["| " + " | ".join(h) + " |", "|" + "---|" * len(h)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def k(x):
    return f"{x / 1e3:,.0f}"


def g(x):
    return "≥100 % (cap)" if x >= 0.999 else f"{100 * x:.0f} %"


L = ["# Stage 3 — Project-Finance Model: CFADS, Debt Service, DSCR\n",
     f"_Generated {date.today().isoformat()} by `scripts/build_stage3_report.py`; numbers read from `stage3_results.json`._\n",
     f"> **{R['price_status']}.** All merchant results and every comparison depend on these prices. Financing terms are public "
     "benchmarks (PBL SDE++ 2025, designed for **subsidised** projects) or illustrative values. **Nothing here is a bank's lending "
     "term, a real project's cost, a financing recommendation, or a probability of default.**\n",
     "## 1. The hypothetical project\n",
     tbl(["Item", "Value", "Status", "Source"],
         [[k_, f"{v['value']} {v.get('unit', '')}".strip(), v["status"], v["source"]] for k_, v in CFG["project"].items()
          if isinstance(v, dict)]) + "\n",
     "**Capacity units.** 10,000 kWp = 10 MWp **DC** (module nameplate). Inverters 7 MWac (70 %). Grid export capped at 5 MW (50 %), "
     "applied as hourly clipping: E_t = min(PV_t × 10,000 kWp, 5,000 kW) × 1 h.\n",
     "## 2. Assumption register\n",
     tbl(["Section", "Parameter", "Value", "Status", "Source"],
         [[sec, k_, f"{v['value']} {v.get('unit', '')}".strip(), v["status"], v["source"]]
          for sec in ["costs", "financing", "ppa"] for k_, v in CFG[sec].items() if isinstance(v, dict)]) + "\n",
     "## 3. Debt\n",
     f"Capex {k(R['debt']['capex'])} k€ = debt {k(R['debt']['debt'])} k€ (80 %) + equity {k(R['debt']['equity'])} k€. Annuity at 4.0 % "
     f"over 15 years: **{R['debt']['annual_debt_service']:,.0f} € per year**.\n",
     tbl(["Year", "Opening balance", "Interest", "Principal", "Debt service", "Closing"],
         [[y, k(s["opening_balance"]), k(s["interest"]), k(s["principal"]), k(s["debt_service"]), k(s["closing_balance"])]
          for y, s in R["debt"]["schedule"].items() if y in ("1", "2", "8", "14", "15")]) + "\n_(k€)_\n",
     "## 4. Production cross-checks\n",
     tbl(["Year", "Unclipped kWh/kWp", "Clipped at 50 % grid", "Clipped at 70 %", "PBL reference (50 %)"],
         [[y, v["unclipped"], v["clipped_50pct"], v["clipped_70pct"], R["production"]["pbl_full_load_hours_reference"]]
          for y, v in R["production"]["specific_yield_kwh_per_kwp"].items()]) + "\n",
     f"Our hourly-clipped PVGIS 6 yield is above PBL's 855 h. Two alternative production levels are carried as **model uncertainty**: "
     f"PVGIS 5.3 level (× {R['production']['pvgis53_over_pvgis6_energy_ratio']}) and PBL-calibrated (× "
     f"{R['production']['pbl_calibration_factor']} on hourly output before clipping, so that the 2021–24 mean clipped year-1 yield = "
     f"{R['production']['pbl_year1_equivalent_target']} kWh/kWp, the year-1 equivalent of PBL's 15-year average of 855 h "
     f"(855 ÷ mean degradation factor {R['production']['mean_degradation_factor_years_1_15']})).\n",
     "**Validation:** with the grid cap removed, Stage 3 merchant revenue equals exactly 10 × the Stage 2 1 MWp revenue in every year:\n",
     tbl(["Year", "Stage 3 (no cap) €", "10 × Stage 2 €"], [[y, f"{v['stage3_unclipped_revenue']:,.0f}", f"{v['10x_stage2_revenue']:,.0f}"]
                                                         for y, v in R["validation_vs_stage2"].items()]) + "\n",
     "## 5. Year-1 cash waterfall — each historical year replayed as operating year 1\n",
     "Cash revenue → − opex → **operating cash flow (EBITDA)** → − tax → − maintenance reserve → **CFADS** ÷ **debt service** = DSCR.\n"]
for y in Y:
    rows = []
    for c in C:
        v = R["year1"][y][c]
        rows.append([SHORT[c], f"{v['delivered_mwh']:,.0f}", f"{v['realised_price']:.1f}", k(v["revenue"]), k(v["opex_total"]),
                     k(v["ebitda"]), k(v["tax"]), k(v["maintenance_capex"]), k(v["cfads"]), k(v["debt_service"]), f"**{v['dscr']:.2f}**"])
    L.append(f"**{y} weather & prices**\n")
    L.append(tbl(["Contract", "MWh sold", "€/MWh realised", "Revenue k€", "Opex k€", "EBITDA k€", "Tax k€", "MRA k€", "CFADS k€",
                  "DS k€", "DSCR"], rows) + "\n")
L.append("![dscr](../figures/stage3_dscr_year1.png)\n\n![waterfall](../figures/stage3_waterfall_2024.png)\n")

L.append("## 6. Production model uncertainty → DSCR\n")
pu = R["production_uncertainty"]
L.append(tbl(["Production case", "Year"] + [SHORT[c] for c in C],
             [[n, y] + [f"{pu[n][y][c]['dscr']:.2f}" for c in C] for n in pu for y in Y]) + "\n")
L.append("Break-even PPA price (no clause) by production case, €/MWh, for DSCR 1.0 / 1.2:\n")
bp = R["breakeven_ppa_by_production_case"]
L.append(tbl(["Production case"] + Y, [[n] + [f"{bp[n][y]['1.0']:.1f} / {bp[n][y]['1.2']:.1f}" for y in Y] for n in bp]) + "\n")

L.append("## 7. Stress tests (year-1 DSCR)\n")
for y in Y:
    names = list(R["stress_dscr"][y][C[0]].keys())
    allnames = []
    for c in C:
        for n in R["stress_dscr"][y][c]:
            if n not in allnames:
                allnames.append(n)
    L.append(f"**{y}**\n")
    L.append(tbl(["Stress"] + [SHORT[c] for c in C],
                 [[n] + [f"{R['stress_dscr'][y][c][n]:.2f}" if n in R["stress_dscr"][y][c] else "—" for c in C] for n in allnames]) + "\n")
L.append("![tornado](../figures/stage3_stress_tornado.png)\n")

L.append("## 8. Break-even analysis\n")
B = R["breakeven"]
L.append(tbl(["Year", "Merchant capture €/MWh (clipped)", "Capture needed DSCR 1.0", "Capture needed DSCR 1.2",
              "PPA price DSCR 1.0 (no clause / no-pay)", "PPA price DSCR 1.2 (no clause / no-pay)"],
             [[y, B[y]["merchant_capture_price_clipped"], B[y][f"required_capture_price_for_dscr_1.0_{C[0]}"],
               B[y][f"required_capture_price_for_dscr_1.2_{C[0]}"],
               f"{B[y]['ppa_price_for_dscr_1.0_none']} / {B[y]['ppa_price_for_dscr_1.0_no_payment_and_curtail']}",
               f"{B[y]['ppa_price_for_dscr_1.2_none']} / {B[y]['ppa_price_for_dscr_1.2_no_payment_and_curtail']}"] for y in Y]) + "\n")
L.append("Production level (× base) at which year-1 DSCR = 1.0:\n")
L.append(tbl(["Year"] + [SHORT[c] for c in C], [[y] + [B[y][f"production_factor_for_dscr_1_{c}"] for c in C] for y in Y]) + "\n")
L.append("Debt capacity — largest gearing with year-1 DSCR ≥ 1.2 / ≥ 1.5 (rate and tenor held fixed):\n")
L.append(tbl(["Year"] + [SHORT[c] for c in C],
             [[y] + [f"{g(B[y][f'max_gearing_at_dscr_1.2_{c}'])} / {g(B[y][f'max_gearing_at_dscr_1.5_{c}'])}" for c in C] for y in Y]) + "\n")
L.append("Break-even PPA price (no clause) vs gearing, DSCR 1.0 / 1.2:\n")
bg = R["breakeven_ppa_vs_gearing"]
gs = list(bg[Y[0]].keys())
L.append(tbl(["Year"] + [f"{float(x):.0%} debt" for x in gs], [[y] + [f"{bg[y][x]['1.0']:.1f} / {bg[y][x]['1.2']:.1f}" for x in gs] for y in Y]) + "\n")
L.append("![be](../figures/stage3_breakeven_ppa_vs_gearing.png)\n")

L.append("## 9. Annuity vs sculpted repayment (illustration, PPA no clause, flat nominal price)\n")
L.append(tbl(["Weather year", "Annuity debt (DSCR ≥ 1.2 every year)", "Sculpted debt (DS_t = CFADS_t / 1.2)", "Annuity gearing", "Sculpted gearing"],
             [[y, k(v["1.2"]["annuity_debt"]) + " k€", k(v["1.2"]["sculpted_debt"]) + " k€", g(v["1.2"]["annuity_gearing"]),
               g(v["1.2"]["sculpted_gearing"])] for y, v in R["annuity_vs_sculpted_ppa_none"].items()]) + "\n")

L.append("## 10. Deterministic tenor stress (one historical year repeated for 15 years)\n")
for idx, title in [("flat_nominal", "Prices flat in nominal terms (real decline 2 %/yr vs opex)"), ("indexed_2pct", "Prices indexed 2 %/yr (flat real)")]:
    L.append(f"**{title}** — min DSCR / years < 1 / first year cumulative shortfall exceeds 6-month DSRA (no replenishment):\n")
    L.append(tbl(["Year repeated"] + [SHORT[c] for c in C],
                 [[y] + [f"{v['min_dscr']:.2f} / {v['years_below_1']} / {v['first_year_cum_shortfall_exceeds_dsra'] or '—'}"
                         for v in (R["tenor_stress"][idx][y][c] for c in C)] for y in Y]) + "\n")
L.append("![tenor](../figures/stage3_tenor_paths.png)\n")

L.append("## 11. Scaling benchmark (year-1 DSCR)\n")
sb = R["scaling_benchmark_dscr"]
L.append(tbl(["Configuration", "Year"] + [SHORT[c] for c in C], [[n, y] + [f"{sb[n][y][c]:.2f}" for c in C] for n in sb for y in Y]) + "\n")

L.append("## 12. What this model can and cannot conclude\n")
L.append("**Can (under stated assumptions; prices not independently cross-checked):** how year-1 CFADS and DSCR of one defined hypothetical project respond to "
         "the 2021–2024 weather-and-price years, to the contract type and negative-price clause, and to each stressed input; the "
         "break-even PPA prices, capture prices, production levels and debt capacities implied by those assumptions; which inputs "
         "matter most.\n")
L.append("**Cannot:** estimate a probability of default or a loss (no loan-performance data, no reserve/waiver dynamics); forecast "
         "prices or capture rates; represent any lender's actual terms or any real project's costs; establish a market PPA price "
         "for the Netherlands (no verified Dutch benchmark); value counterparty credit risk of a PPA buyer; replace an independent "
         "energy yield assessment. The four historical years are four regimes, not a probability distribution.\n")
(ROOT / "reports/stage3/STAGE3_REPORT.md").write_text("\n".join(L), encoding="utf-8")
print("wrote reports/stage3/STAGE3_REPORT.md")
