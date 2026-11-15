"""Stage 3 finance formulas. All inputs are SYNTHETIC, hand-checkable examples."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from solarrisk.finance.annuity import amortization_schedule, annuity_payment
from solarrisk.finance import project as pj

IDX = pd.date_range("2024-06-01", periods=4, freq="h", tz="UTC")


# ---------------------------------------------------------------- annuity
def test_annuity_textbook_value():
    # 1000 at 5% over 10 years: A = 1000*0.05/(1-1.05^-10) = 129.5046
    assert annuity_payment(1000, 0.05, 10) == pytest.approx(129.5046, abs=1e-4)
    assert annuity_payment(1000, 0.0, 10) == 100.0


def test_schedule_properties():
    s = amortization_schedule(3_408_000, 0.04, 15)
    assert s["principal"].sum() == pytest.approx(3_408_000, rel=1e-12)   # all principal repaid
    assert s["closing_balance"].iloc[-1] == pytest.approx(0, abs=1e-6)
    assert s["debt_service"].nunique() == 1                               # level payment
    assert (s["interest"].diff().dropna() < 0).all()                      # interest falls
    assert (s["principal"].diff().dropna() > 0).all()                     # principal rises
    assert s.loc[1, "interest"] == pytest.approx(0.04 * 3_408_000)


# ---------------------------------------------------------------- worked example (DSCR 1.25 / 0.92)
def test_worked_dscr_example():
    a = pj.Assumptions(dc_kwp=1, fixed_opex_eur_per_kwp=250_000, variable_opex_eur_per_mwh=0, imbalance_eur_per_mwh=0,
                       tax_rate=0.0, capex_eur_per_kwp=5_000_000, gearing=1.0,
                       inverter_replacement_eur_per_kwp=0.0)
    sched = pd.DataFrame({"interest": [300_000.0], "principal": [300_000.0]}, index=[1])
    base = pj.waterfall({"revenue": 1_000_000.0, "delivered_mwh": 0.0}, a, 1, sched)
    bad = pj.waterfall({"revenue": 800_000.0, "delivered_mwh": 0.0}, a, 1, sched)
    assert base["cfads"] == 750_000 and base["dscr"] == pytest.approx(1.25)
    assert bad["cfads"] == 550_000 and bad["dscr"] == pytest.approx(0.9167, abs=1e-4)
    assert bad["shortfall"] == 50_000


# ---------------------------------------------------------------- production
def test_production_scaling_clipping_and_degradation():
    pv = pd.Series([0.0, 0.3, 0.6, 0.9], IDX)          # kW per kWp
    a = pj.Assumptions(dc_kwp=1000, export_cap_fraction=0.5, degradation=0.01)
    e1 = pj.production_mwh(pv, a, 1)
    assert list(e1.round(6)) == [0.0, 0.3, 0.5, 0.5]    # MWh; capped at 500 kW
    e2 = pj.production_mwh(pv, a, 2)
    assert e2.iloc[1] == pytest.approx(0.3 * 0.99)


# ---------------------------------------------------------------- revenue & clauses
E = pd.Series([1.0, 2.0, 3.0, 1.0], IDX)
P = pd.Series([50.0, -10.0, 20.0, 0.0], IDX)


def test_revenue_contracts():
    assert pj.revenue(E, P, pj.MERCHANT)["revenue"] == 50 - 20 + 60 + 0
    m = pj.revenue(E, P, pj.MERCHANT_CURTAIL)
    assert m["revenue"] == 110 and m["curtailed_mwh"] == 2.0
    k = pj.Contract("ppa", ppa_price=40, ppa_clause="none")
    assert pj.revenue(E, P, k)["revenue"] == 40 * 7
    k2 = pj.Contract("ppa", ppa_price=40, ppa_clause="no_payment_and_curtail")
    r2 = pj.revenue(E, P, k2)
    assert r2["revenue"] == 40 * 5 and r2["delivered_mwh"] == 5   # zero-price hour still paid (p not < 0)


def test_imbalance_applies_to_delivered_energy_only():
    a = pj.Assumptions(dc_kwp=1, fixed_opex_eur_per_kwp=0, variable_opex_eur_per_mwh=2, imbalance_eur_per_mwh=7)
    assert pj.opex(10, a)["opex_total"] == 90


def test_tax_floor_and_positive_tax():
    a = pj.Assumptions(dc_kwp=1, capex_eur_per_kwp=1500, depreciation_years=15, fixed_opex_eur_per_kwp=0,
                       variable_opex_eur_per_mwh=0, imbalance_eur_per_mwh=0, tax_rate=0.25, gearing=0.0)
    sched = pd.DataFrame({"interest": [0.0], "principal": [0.0]}, index=[1])
    lo = pj.waterfall({"revenue": 50.0, "delivered_mwh": 0}, a, 1, sched)    # EBITDA 50 < depreciation 100
    hi = pj.waterfall({"revenue": 300.0, "delivered_mwh": 0}, a, 1, sched)   # taxable 200
    assert lo["tax"] == 0 and hi["tax"] == 50


def test_inverter_replacement_prefunded_by_maintenance_reserve():
    a = pj.Assumptions(dc_kwp=1, tax_rate=0, fixed_opex_eur_per_kwp=0, variable_opex_eur_per_mwh=0,
                       imbalance_eur_per_mwh=0, inverter_replacement_eur_per_kwp=130, opex_inflation=0)
    sched = amortization_schedule(a.debt, a.interest_rate, a.tenor)
    r = {"revenue": 1000.0, "delivered_mwh": 0}
    contrib = [pj.waterfall(r, a, y, sched)["maintenance_capex"] for y in range(1, 16)]
    assert contrib[:13] == [pytest.approx(10.0)] * 13 and contrib[13:] == [0.0, 0.0]
    assert sum(contrib) == pytest.approx(130)                 # fully funded by year 13


# ---------------------------------------------------------------- solvers & invariances
def _toy_year():
    idx = pd.date_range("2023-01-01", periods=24 * 365, freq="h", tz="UTC")
    pv = pd.Series(np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * 0.6, idx)
    p = pd.Series(80 - 60 * pv.to_numpy(), idx)  # synthetic negative correlation
    return pv, p


def test_breakevens_hit_their_targets():
    pv, p = _toy_year()
    a = pj.Assumptions()
    k = pj.breakeven_ppa_price(pv, p, a, "none", 1.2)
    assert pj.year_result(pv, p, a, pj.Contract("ppa", ppa_price=k))["dscr"] == pytest.approx(1.2, abs=1e-3)
    g = pj.debt_capacity_gearing(pv, p, a, pj.Contract("ppa", ppa_price=k), 1.5)
    assert pj.year_result(pv, p, replace(a, gearing=g), pj.Contract("ppa", ppa_price=k))["dscr"] == pytest.approx(1.5, abs=1e-3)
    f = pj.breakeven_production_factor(pv, p, a, pj.Contract("ppa", ppa_price=k), 1.0)
    assert f < 1.0
    assert pj.year_result(pv, p, replace(a, production_factor=f), pj.Contract("ppa", ppa_price=k))["dscr"] == pytest.approx(1.0, abs=1e-3)


def test_dscr_is_scale_invariant_without_clipping():
    """With per-kWp costs, proportional debt and no grid cap, DSCR does not depend on plant size."""
    pv, p = _toy_year()
    small = pj.Assumptions(dc_kwp=1_000, export_cap_fraction=10.0)
    big = replace(small, dc_kwp=10_000)
    for c in pj.CONTRACTS:
        assert pj.year_result(pv, p, small, c)["dscr"] == pytest.approx(pj.year_result(pv, p, big, c)["dscr"], rel=1e-9)
