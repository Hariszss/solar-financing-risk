"""Independent solar elevation (NOAA general solar position approximation, ~0.1–0.5° accuracy).

Why: data sources can encode missing values as zeros (we found PVGIS 5.3 does this, including
zeroing its own sun-height column). An independent astronomical calculation lets us detect
"zero output while the sun is clearly up" without trusting the source's own metadata.

Reference: NOAA Global Monitoring Laboratory, "General Solar Position Calculations".
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def solar_elevation_deg(times_utc: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    t = pd.DatetimeIndex(times_utc).tz_convert("UTC")
    doy = t.dayofyear.to_numpy()
    hour = t.hour.to_numpy() + t.minute.to_numpy() / 60 + t.second.to_numpy() / 3600
    days_in_year = np.where(t.is_leap_year, 366, 365)
    g = 2 * np.pi / days_in_year * (doy - 1 + (hour - 12) / 24)  # fractional year (rad)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))  # minutes
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    tst = hour * 60 + eqtime + 4 * lon  # true solar time, minutes (UTC offset 0)
    ha = np.deg2rad(tst / 4 - 180)
    phi = np.deg2rad(lat)
    cosz = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(ha)
    return 90 - np.rad2deg(np.arccos(np.clip(cosz, -1, 1)))


def hourly_mean_elevation_deg(interval_start_utc: pd.DatetimeIndex, lat: float, lon: float) -> np.ndarray:
    """Mean of elevations at :10, :30, :50 — a robust 'is the sun up during this hour' measure."""
    parts = [solar_elevation_deg(interval_start_utc + pd.Timedelta(minutes=m), lat, lon) for m in (10, 30, 50)]
    return np.mean(parts, axis=0)
