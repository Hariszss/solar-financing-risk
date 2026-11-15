"""Capture prices, merchant revenue and negative-price exposure.

Notation (hourly, UTC, interval start):
    E_t : energy produced in hour t, MWh       (PVGIS kWh / 1000)
    p_t : day-ahead price in hour t, EUR/MWh
Definitions:
    baseload (arithmetic) price  p̄      = (1/T) Σ p_t
    capture price                 p_cap  = Σ E_t p_t / Σ E_t       (energy-weighted mean)
    capture rate                  p_cap / p̄
    merchant revenue              R      = Σ E_t p_t               (EUR)
Identity (exact, population moments over the same T hours):
    p_cap = p̄ + Cov(E, p) / Ē
so the capture discount is entirely the covariance between production and price, scaled by
mean production. If E and p were independent, capture price = baseload price.

All functions require E and p on the SAME index (paired hours). They never resample or shuffle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _paired(e: pd.Series, p: pd.Series) -> pd.DataFrame:
    df = pd.concat({"e": e, "p": p}, axis=1, join="inner")
    if df.index.has_duplicates:
        raise ValueError("duplicate timestamps")
    return df


def capture_metrics(e_mwh: pd.Series, price: pd.Series) -> dict:
    """Metrics for one period.

    * baseload price  : mean over ALL hours with a price (the market benchmark must not depend on
                        whether our production series has gaps);
    * capture price / revenue / covariance : over complete pairs only (hours with E and p).
    The identity p_cap = p̄_cc + Cov_cc(E,p)/Ē_cc is checked on the complete-pair sample."""
    df = _paired(e_mwh, price)
    base = float(df["p"].dropna().mean())
    n_total = len(df)
    df = df.dropna()
    e, p = df["e"].to_numpy(float), df["p"].to_numpy(float)
    energy = e.sum()
    revenue = float((e * p).sum())
    cap = revenue / energy if energy > 0 else np.nan
    cov = float(np.mean((e - e.mean()) * (p - p.mean())))  # population covariance
    day = e > 0
    return {
        "n_hours": int(len(df)),
        "n_hours_dropped_missing": int(n_total - len(df)),
        "energy_mwh": round(float(energy), 3),
        "revenue_eur": round(revenue, 2),
        "baseload_price": round(base, 3),
        "capture_price": round(float(cap), 3),
        "capture_rate": round(float(cap / base), 4) if base != 0 else None,
        "capture_discount_eur_mwh": round(float(base - cap), 3),
        "identity_check_pbar_plus_cov_over_ebar": round(float(p.mean()) + cov / e.mean(), 3),
        "cov_e_p": round(cov, 5),
        "spearman_e_p_production_hours": round(float(pd.Series(e[day]).corr(pd.Series(p[day]), method="spearman")), 4)
        if day.sum() > 2 else None,
        "mean_price_production_hours": round(float(p[day].mean()), 3) if day.any() else None,
    }


def covariance_decomposition(e_mwh: pd.Series, price: pd.Series, groups: list) -> dict:
    """Law of total covariance with population moments (exact):
        Cov(E,p) = Σ_g w_g Cov_g(E,p)  +  Σ_g w_g (Ē_g − Ē)(p̄_g − p̄),    w_g = n_g / N
                   └── within groups ─┘    └────── between groups ──────┘
    With groups = (month, hour-of-day): 'between' = predictable timing (solar produces at midday
    in summer, when prices are typically low/high); 'within' = co-movement on the SAME clock hour
    in the same month across days (e.g. sunny vs cloudy days). Returns both parts, and their
    contribution to the capture discount in EUR/MWh (= −part / Ē)."""
    df = _paired(e_mwh, price).dropna()
    df["g"] = list(zip(*groups)) if len(groups) > 1 else groups[0]
    N = len(df)
    em, pm = df["e"].mean(), df["p"].mean()
    total = float(((df["e"] - em) * (df["p"] - pm)).mean())
    within = between = 0.0
    for _, sub in df.groupby("g"):
        w = len(sub) / N
        eg, pg = sub["e"].mean(), sub["p"].mean()
        within += w * float(((sub["e"] - eg) * (sub["p"] - pg)).mean())
        between += w * (eg - em) * (pg - pm)
    return {"cov_total": round(total, 5), "cov_within": round(within, 5), "cov_between": round(between, 5),
            "discount_total_eur_mwh": round(-total / em, 3), "discount_within_eur_mwh": round(-within / em, 3),
            "discount_between_eur_mwh": round(-between / em, 3)}


def negative_price_exposure(e_mwh: pd.Series, price: pd.Series) -> dict:
    """What happens in hours with p < 0 (and p == 0).

    'revenue_if_curtailed' assumes the plant could switch off whenever the day-ahead price is
    negative (an operational assumption, not a contract model)."""
    df = _paired(e_mwh, price).dropna()
    neg, zero = df["p"] < 0, df["p"] == 0
    prod = df["e"] > 0
    rev = df["e"] * df["p"]
    total_e = df["e"].sum()
    return {
        "negative_hours": int(neg.sum()),
        "negative_hours_with_production": int((neg & prod).sum()),
        "energy_in_negative_hours_mwh": round(float(df.loc[neg, "e"].sum()), 3),
        "pct_energy_in_negative_hours": round(float(100 * df.loc[neg, "e"].sum() / total_e), 3) if total_e else None,
        "revenue_in_negative_hours_eur": round(float(rev[neg].sum()), 2),
        "mean_price_when_negative_and_producing": round(float(df.loc[neg & prod, "p"].mean()), 2)
        if (neg & prod).any() else None,
        "zero_price_hours_with_production": int((zero & prod).sum()),
        "energy_in_zero_price_hours_mwh": round(float(df.loc[zero, "e"].sum()), 3),
        "revenue_total_eur": round(float(rev.sum()), 2),
        "revenue_if_curtailed_eur": round(float(rev[~neg].sum()), 2),
        "curtailment_gain_eur": round(float(-rev[neg].sum()), 2),
    }


def fill_with_ratio_proxy(pv_kwh: pd.Series, ghi_wm2: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Fill missing PV hours with GHI × (ΣPV/ΣGHI) of the same calendar month and UTC hour,
    estimated on valid hours of the SAME year. Returns (filled series, boolean mask of filled hours).

    Rationale: a ratio estimator conditional on month×hour captures the sun-geometry-dependent
    relation between horizontal ground irradiance and tilted-plane PV output. It is used only for
    a handful of hours (≈0.1 %), so its imperfections are immaterial — but it is better than 0."""
    df = pd.concat({"pv": pv_kwh, "ghi": ghi_wm2}, axis=1, join="inner")
    miss = df["pv"].isna()
    valid = df.dropna()
    key = [valid.index.year, valid.index.month, valid.index.hour]
    sums = valid.groupby(key)[["pv", "ghi"]].sum()
    ratio = (sums["pv"] / sums["ghi"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    out = df["pv"].copy()
    for t in df.index[miss]:
        r = ratio.get((t.year, t.month, t.hour), 0.0)
        g = df.at[t, "ghi"]
        out.at[t] = 0.0 if pd.isna(g) else float(g * r)
    return out, miss


def volume_price_decomposition(e_a: float, cap_a: float, e_b: float, cap_b: float) -> dict:
    """Split the revenue change from year a to year b (R = E × p_cap) into a volume (weather) effect
    and a price effect with the LMDI method (logarithmic mean Divisia index):
        L = (Rb - Ra) / (ln Rb - ln Ra)          (logarithmic mean of Ra and Rb)
        volume effect = L · ln(Eb/Ea),  price effect = L · ln(cap_b/cap_a)
    The two effects add up EXACTLY to Rb - Ra, with no residual, even when they have opposite signs.
    Requires positive revenues."""
    ra, rb = e_a * cap_a, e_b * cap_b
    if ra <= 0 or rb <= 0:
        raise ValueError("LMDI requires positive revenues")
    lmean = (rb - ra) / (np.log(rb) - np.log(ra)) if not np.isclose(ra, rb) else ra
    lv, lp = np.log(e_b / e_a), np.log(cap_b / cap_a)
    return {"revenue_change_eur": round(float(rb - ra), 2), "pct_change": round(float(100 * (rb / ra - 1)), 2),
            "volume_effect_eur": round(float(lmean * lv), 2), "price_effect_eur": round(float(lmean * lp), 2),
            "energy_pct_change": round(float(100 * (e_b / e_a - 1)), 2),
            "capture_price_pct_change": round(float(100 * (cap_b / cap_a - 1)), 2)}


def season(month: int) -> str:
    return {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
            6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}[month]
