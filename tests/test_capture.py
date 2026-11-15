"""Stage 2 analysis functions. Inputs are tiny SYNTHETIC examples small enough to verify by hand."""

import numpy as np
import pandas as pd
import pytest

from solarrisk.analysis import capture

IDX = pd.date_range("2024-06-01", periods=4, freq="h", tz="UTC")


def test_capture_price_hand_example():
    # Energy (MWh) 0, 1, 3, 0 ; prices 100, 50, 10, 120
    e = pd.Series([0.0, 1.0, 3.0, 0.0], IDX)
    p = pd.Series([100.0, 50.0, 10.0, 120.0], IDX)
    m = capture.capture_metrics(e, p)
    assert m["energy_mwh"] == 4.0
    assert m["revenue_eur"] == 80.0                 # 1*50 + 3*10
    assert m["baseload_price"] == 70.0              # (100+50+10+120)/4
    assert m["capture_price"] == 20.0               # 80/4
    assert m["capture_rate"] == pytest.approx(20 / 70, abs=1e-4)
    # identity p_cap = p̄ + Cov(E,p)/Ē holds exactly
    assert m["identity_check_pbar_plus_cov_over_ebar"] == pytest.approx(m["capture_price"], abs=1e-6)


def test_independent_flat_production_gives_capture_equal_baseload():
    e = pd.Series([2.0] * 4, IDX)
    p = pd.Series([10.0, -5.0, 30.0, 45.0], IDX)
    m = capture.capture_metrics(e, p)
    assert m["capture_price"] == pytest.approx(m["baseload_price"])


def test_missing_hours_excluded_from_both_sums():
    e = pd.Series([1.0, np.nan, 1.0, 0.0], IDX)
    p = pd.Series([10.0, 1000.0, 30.0, 0.0], IDX)
    m = capture.capture_metrics(e, p)
    assert m["n_hours_dropped_missing"] == 1
    assert m["capture_price"] == 20.0               # the 1000 €/MWh hour is not used for capture...
    assert m["baseload_price"] == 260.0             # ...but the market benchmark uses every priced hour


def test_covariance_decomposition_is_exact():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2023-01-01", periods=24 * 60, freq="h", tz="UTC")
    e = pd.Series(np.clip(np.sin((idx.hour - 6) / 12 * np.pi), 0, None) * rng.uniform(0.2, 1, len(idx)), idx)
    p = pd.Series(100 - 50 * e + rng.normal(0, 10, len(idx)), idx)  # synthetic
    d = capture.covariance_decomposition(e, p, [idx.month, idx.hour])
    assert d["cov_within"] + d["cov_between"] == pytest.approx(d["cov_total"], abs=1e-4)
    m = capture.capture_metrics(e, p)
    assert d["discount_total_eur_mwh"] == pytest.approx(m["capture_discount_eur_mwh"], abs=1e-2)


def test_negative_price_exposure():
    e = pd.Series([1.0, 2.0, 0.0, 1.0], IDX)
    p = pd.Series([50.0, -10.0, -20.0, 0.0], IDX)
    x = capture.negative_price_exposure(e, p)
    assert x["negative_hours"] == 2
    assert x["negative_hours_with_production"] == 1
    assert x["energy_in_negative_hours_mwh"] == 2.0
    assert x["revenue_in_negative_hours_eur"] == -20.0
    assert x["revenue_total_eur"] == 30.0
    assert x["revenue_if_curtailed_eur"] == 50.0
    assert x["curtailment_gain_eur"] == 20.0
    assert x["zero_price_hours_with_production"] == 1


def test_ratio_proxy_fill_uses_same_month_hour():
    idx = pd.date_range("2022-07-01 12:00", periods=3, freq="D", tz="UTC")  # same UTC hour, same month
    pv = pd.Series([200.0, np.nan, 300.0], idx)
    ghi = pd.Series([400.0, 500.0, 600.0], idx)
    filled, mask = capture.fill_with_ratio_proxy(pv, ghi)
    assert mask.sum() == 1
    assert filled.iloc[1] == pytest.approx(500 * (500 / 1000))  # ratio = (200+300)/(400+600)


def test_lmdi_decomposition_is_exact_with_opposite_signs():
    d = capture.volume_price_decomposition(e_a=1000, cap_a=200, e_b=1100, cap_b=80)
    assert d["volume_effect_eur"] > 0 > d["price_effect_eur"]
    assert d["volume_effect_eur"] + d["price_effect_eur"] == pytest.approx(d["revenue_change_eur"], abs=0.05)


def test_unpaired_duplicates_rejected():
    e = pd.Series([1.0, 1.0], [IDX[0], IDX[0]])
    with pytest.raises(ValueError):
        capture.capture_metrics(e, pd.Series([1.0, 1.0], [IDX[0], IDX[0]]))
