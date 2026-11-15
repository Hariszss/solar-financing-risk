"""Project cash-flow waterfall for a solar SPV: production → revenue → CFADS → DSCR.

Waterfall (one operating year, all EUR):
    cash revenue            R      = merchant Σ E_t·p_t   or   PPA K·Σ E_t (subject to clause)
  − operating expenditure   OPEX   = fixed (EUR/kWp) + variable & imbalance (EUR/MWh × delivered MWh) + land
  = operating cash flow     EBITDA (cash from operations before financing and tax)
  − tax paid                TAX    = rate × max(0, EBITDA − depreciation − interest)   [no loss carry-forward]
  − maintenance reserve    (year-13 inverter replacement pre-funded evenly over years 1–13)
  = CFADS                  cash flow available for debt service
    debt service            DS     = interest + scheduled principal
    DSCR                    CFADS / DS

Interpretation guard-rail: DSCR < 1 means CFADS did not cover scheduled debt service in that year.
It is NOT a default event and NOT a probability of default (reserves, sponsor support, waivers exist,
and we have no loan-performance data).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .annuity import amortization_schedule


# ------------------------------------------------------------------------------------------ inputs
@dataclass(frozen=True)
class Assumptions:
    dc_kwp: float = 10_000
    export_cap_fraction: float = 0.50      # grid connection / DC peak
    capex_eur_per_kwp: float = 426.0
    fixed_opex_eur_per_kwp: float = 15.9
    variable_opex_eur_per_mwh: float = 1.9
    imbalance_eur_per_mwh: float = 7.0
    land_lease_eur_per_kwp: float = 0.0
    gearing: float = 0.80
    interest_rate: float = 0.040
    tenor: int = 15
    tax_rate: float = 0.258
    depreciation_years: int = 15
    production_factor: float = 1.0         # model-uncertainty / calibration multiplier on PVGIS output
    degradation: float = 0.0064
    opex_inflation: float = 0.02
    inverter_replacement_eur_per_kwp: float = 13.0   # PBL: 130,000 EUR for the 10 MWp reference
    inverter_replacement_year: int = 13
    dsra_months: float = 6

    @property
    def capex(self) -> float:
        return self.capex_eur_per_kwp * self.dc_kwp

    @property
    def inverter_replacement_eur(self) -> float:
        return self.inverter_replacement_eur_per_kwp * self.dc_kwp

    @property
    def debt(self) -> float:
        return self.gearing * self.capex


@dataclass(frozen=True)
class Contract:
    kind: str                              # "merchant" | "ppa"
    curtail_negative: bool = False         # merchant: switch off when p < 0 (idealised)
    ppa_price: float = 60.0
    ppa_clause: str = "none"               # "none" | "no_payment_and_curtail"
    label: str = field(default="")


MERCHANT = Contract("merchant", False, label="Merchant – generate always")
MERCHANT_CURTAIL = Contract("merchant", True, label="Merchant – idealised curtailment at p<0")
PPA_NONE = Contract("ppa", ppa_clause="none", label="PPA – no negative-price clause")
PPA_NOPAY = Contract("ppa", ppa_clause="no_payment_and_curtail", label="PPA – no payment & curtail at p<0")
CONTRACTS = [MERCHANT, MERCHANT_CURTAIL, PPA_NONE, PPA_NOPAY]


# ------------------------------------------------------------------------------------------ production
def production_mwh(pv_kw_per_kwp: pd.Series, a: Assumptions, year_index: int = 1) -> pd.Series:
    """Hourly delivered-able energy (MWh) for the park.
    PVGIS gives mean AC power per kWp in each hour (kW/kWp). Scale to DC size, apply the model-uncertainty
    factor and degradation, then cap at the grid export limit (hourly clipping):
        E_t = min(pv_t · factor · (1−d)^(y−1) · DC, cap · DC) / 1000   [kW·1h → MWh]"""
    deg = (1 - a.degradation) ** (year_index - 1)
    kw = pv_kw_per_kwp * a.production_factor * deg * a.dc_kwp
    return kw.clip(upper=a.export_cap_fraction * a.dc_kwp) / 1000.0


# ------------------------------------------------------------------------------------------ revenue
def revenue(e_mwh: pd.Series, price: pd.Series, c: Contract) -> dict:
    df = pd.concat({"e": e_mwh, "p": price}, axis=1, join="inner")
    if df.isna().any().any():
        raise ValueError("revenue() needs complete paired hourly data")
    neg = df["p"] < 0
    if c.kind == "merchant":
        sold = df["e"].where(~neg, 0.0) if c.curtail_negative else df["e"]
        rev = float((sold * df["p"]).sum())
    elif c.kind == "ppa":
        sold = df["e"].where(~neg, 0.0) if c.ppa_clause == "no_payment_and_curtail" else df["e"]
        rev = float(c.ppa_price * sold.sum())
    else:
        raise ValueError(c.kind)
    return {"revenue": rev, "delivered_mwh": float(sold.sum()), "available_mwh": float(df["e"].sum()),
            "curtailed_mwh": float(df["e"].sum() - sold.sum()),
            "realised_price": rev / float(sold.sum()) if sold.sum() > 0 else np.nan}


# ------------------------------------------------------------------------------------------ waterfall
def opex(delivered_mwh: float, a: Assumptions, year_index: int = 1) -> dict:
    infl = (1 + a.opex_inflation) ** (year_index - 1)
    fixed = (a.fixed_opex_eur_per_kwp + a.land_lease_eur_per_kwp) * a.dc_kwp * infl
    var = a.variable_opex_eur_per_mwh * delivered_mwh * infl
    imb = a.imbalance_eur_per_mwh * delivered_mwh * infl
    return {"fixed_opex": fixed, "variable_opex": var, "imbalance": imb, "opex_total": fixed + var + imb}


def waterfall(rev: dict, a: Assumptions, year_index: int = 1, schedule: pd.DataFrame | None = None) -> dict:
    sch = schedule if schedule is not None else amortization_schedule(a.debt, a.interest_rate, a.tenor)
    ox = opex(rev["delivered_mwh"], a, year_index)
    ebitda = rev["revenue"] - ox["opex_total"]
    interest = float(sch.loc[year_index, "interest"]) if year_index in sch.index else 0.0
    principal = float(sch.loc[year_index, "principal"]) if year_index in sch.index else 0.0
    dep = a.capex / a.depreciation_years if year_index <= a.depreciation_years else 0.0
    taxable = ebitda - dep - interest
    tax = a.tax_rate * max(0.0, taxable)
    # Maintenance reserve account (MRA): the year-13 inverter replacement is pre-funded in equal instalments
    # over years 1..13 (common project-finance practice), instead of hitting CFADS in one year.
    mcapex = (a.inverter_replacement_eur / a.inverter_replacement_year
              if year_index <= a.inverter_replacement_year else 0.0)
    cfads = ebitda - tax - mcapex
    ds = interest + principal
    return {**rev, **ox, "ebitda": ebitda, "depreciation": dep, "taxable_income": taxable, "tax": tax,
            "maintenance_capex": mcapex, "cfads": cfads, "interest": interest, "principal": principal,
            "debt_service": ds, "dscr": cfads / ds if ds > 0 else np.inf,
            "shortfall": max(0.0, ds - cfads)}


def year_result(pv_kw_per_kwp: pd.Series, price: pd.Series, a: Assumptions, c: Contract, year_index: int = 1) -> dict:
    e = production_mwh(pv_kw_per_kwp, a, year_index)
    return waterfall(revenue(e, price, c), a, year_index)


# ------------------------------------------------------------------------------------------ solvers
def bisect(f, lo: float, hi: float, target: float, tol: float = 1e-6, it: int = 200) -> float | None:
    """Solve f(x) = target for monotone f on [lo, hi]; None if no sign change."""
    flo, fhi = f(lo) - target, f(hi) - target
    if np.sign(flo) == np.sign(fhi):
        return None
    for _ in range(it):
        mid = 0.5 * (lo + hi)
        fm = f(mid) - target
        if abs(fm) < tol or (hi - lo) < tol:
            return mid
        if np.sign(fm) == np.sign(flo):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def breakeven_ppa_price(pv, price, a: Assumptions, clause: str, target_dscr: float) -> float | None:
    f = lambda k: year_result(pv, price, a, Contract("ppa", ppa_price=k, ppa_clause=clause))["dscr"]  # noqa: E731
    return bisect(f, 0.0, 500.0, target_dscr, tol=1e-4)


def breakeven_production_factor(pv, price, a: Assumptions, c: Contract, target_dscr: float) -> float | None:
    f = lambda x: year_result(pv, price, replace(a, production_factor=x), c)["dscr"]  # noqa: E731
    return bisect(f, 0.0, 3.0, target_dscr, tol=1e-5)


def breakeven_price_multiplier(pv, price, a: Assumptions, c: Contract, target_dscr: float) -> float | None:
    """Uniform multiplier m on all hourly prices (merchant) s.t. DSCR = target. Note: scaling prices
    scales negative prices too, so this is a level shift of the whole price curve, shape unchanged."""
    f = lambda m: year_result(pv, price * m, a, c)["dscr"]  # noqa: E731
    return bisect(f, 0.0, 10.0, target_dscr, tol=1e-5)


def debt_capacity_gearing(pv, price, a: Assumptions, c: Contract, target_dscr: float) -> float:
    """Largest gearing (debt/capex, capped at 1) such that year-1 DSCR ≥ target, holding rate & tenor."""
    f = lambda g: year_result(pv, price, replace(a, gearing=g), c)["dscr"]  # noqa: E731
    if f(1e-6) < target_dscr:
        return 0.0
    if f(1.0) >= target_dscr:
        return 1.0
    return bisect(f, 1e-6, 1.0, target_dscr, tol=1e-5)


# ------------------------------------------------------------------------------------------ multi-year
def tenor_projection(pv, price, a: Assumptions, c: Contract, price_index: float = 0.0) -> pd.DataFrame:
    """Deterministic replay of ONE historical weather-and-price year in every year of the loan,
    with degradation, opex inflation and the year-13 inverter replacement.
    price_index = annual escalation applied to market prices (merchant) or to the PPA price.
    price_index = 0 → flat NOMINAL prices while opex inflates, i.e. a real price decline (stress);
    price_index = opex_inflation → flat REAL prices. Neither is a forecast."""
    sch = amortization_schedule(a.debt, a.interest_rate, a.tenor)
    rows = []
    for y in range(1, a.tenor + 1):
        e = production_mwh(pv, a, y)
        g = (1 + price_index) ** (y - 1)
        cc = replace(c, ppa_price=c.ppa_price * g) if c.kind == "ppa" else c
        rows.append({"year": y, **waterfall(revenue(e, price * g if c.kind == "merchant" else price, cc), a, y, sch)})
    return pd.DataFrame(rows).set_index("year")


def annuity_vs_sculpted_capacity(proj: pd.DataFrame, a: Assumptions, target: float) -> dict:
    """Debt each repayment shape could support so that DSCR ≥ target in EVERY year, given a CFADS path.
    * Annuity: level payment A ≤ min_t CFADS_t / target  →  D = A · [1 − (1+r)^−n] / r
    * Sculpted: payment follows cash, DS_t = CFADS_t / target  →  D = Σ_t DS_t / (1+r)^t
    Approximation: CFADS path taken from the base projection (tax computed with base interest)."""
    r, n = a.interest_rate, a.tenor
    cf = proj["cfads"].clip(lower=0)
    a_max = float(cf.min()) / target
    d_ann = a_max * (1 - (1 + r) ** (-n)) / r
    d_sc = float(sum(cf.loc[t] / target / (1 + r) ** t for t in cf.index))
    return {"annuity_debt": round(d_ann, 0), "sculpted_debt": round(d_sc, 0),
            "annuity_gearing": round(d_ann / a.capex, 3), "sculpted_gearing": round(d_sc / a.capex, 3)}


def dsra_summary(proj: pd.DataFrame, a: Assumptions) -> dict:
    ds = float(proj["debt_service"].iloc[0])
    dsra = a.dsra_months / 12 * ds
    cum = proj["shortfall"].cumsum()
    exhausted = cum[cum > dsra]
    return {"dsra_eur": round(dsra, 0), "min_dscr": round(float(proj["dscr"].min()), 3),
            "mean_dscr": round(float(proj["dscr"].mean()), 3),
            "years_below_1": int((proj["dscr"] < 1).sum()), "years_below_1_2": int((proj["dscr"] < 1.2).sum()),
            "cumulative_shortfall_eur": round(float(cum.iloc[-1]), 0),
            "first_year_cum_shortfall_exceeds_dsra": int(exhausted.index[0]) if len(exhausted) else None}
