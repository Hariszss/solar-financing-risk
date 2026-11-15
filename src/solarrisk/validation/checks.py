"""Generic data-quality checks for hourly UTC time series.

Each function returns plain dicts/lists so results can be written to JSON directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def expected_hourly_index(years: list[int]) -> pd.DatetimeIndex:
    return pd.date_range(f"{min(years)}-01-01", f"{max(years) + 1}-01-01", freq="h",
                         tz="UTC", inclusive="left", name="interval_start_utc")


def missing_runs(mask: pd.Series) -> dict:
    """Summarise consecutive runs of True in a boolean Series (e.g. isna())."""
    m = mask.astype(bool).to_numpy()
    if not m.any():
        return {"n_runs": 0, "max_run": 0, "run_length_counts": {}}
    edges = np.diff(np.concatenate([[0], m.astype(int), [0]]))
    starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
    lengths = ends - starts
    vals, counts = np.unique(lengths, return_counts=True)
    return {"n_runs": int(len(lengths)), "max_run": int(lengths.max()),
            "run_length_counts": {int(v): int(c) for v, c in zip(vals, counts)}}


def audit_hourly(df: pd.DataFrame | pd.Series, years: list[int], bounds: dict | None = None) -> dict:
    """Completeness, duplicates, step regularity, missingness and physical-bounds checks."""
    if isinstance(df, pd.Series):
        df = df.to_frame()
    idx = df.index
    exp = expected_hourly_index(years)
    in_period = idx[(idx >= exp[0]) & (idx <= exp[-1])]
    steps = pd.Series(idx).diff().dropna().value_counts()
    res = {
        "n_records": int(len(df)),
        "n_expected_hours": int(len(exp)),
        "index_tz": str(idx.tz),
        "first": str(idx.min()), "last": str(idx.max()),
        "n_duplicate_timestamps": int(idx.duplicated().sum()),
        "n_expected_missing_from_index": int(len(exp.difference(in_period))),
        "n_outside_period": int(len(idx) - len(in_period)),
        "time_step_counts": {str(k): int(v) for k, v in steps.items()},
        "columns": {},
    }
    bounds = bounds or {}
    for c in df.columns:
        s = pd.to_numeric(df[c], errors="coerce")
        col = {
            "n_missing": int(s.isna().sum()),
            "pct_missing": round(100 * float(s.isna().mean()), 4),
            "missing_runs": missing_runs(s.isna()),
            "min": _f(s.min()), "max": _f(s.max()), "mean": _f(s.mean()),
        }
        if c in bounds:
            lo, hi = bounds[c]
            bad = ((s < lo) | (s > hi)) & s.notna()
            col["bounds"] = [lo, hi]
            col["n_out_of_bounds"] = int(bad.sum())
            col["out_of_bounds_examples"] = {str(k): _f(v) for k, v in s[bad].head(5).items()}
        res["columns"][c] = col
    return res


def _f(x):
    return None if pd.isna(x) else round(float(x), 4)
