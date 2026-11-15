"""PVGIS 5.3 and PVGIS 6 hourly PV output: request building and parsing.

Standard output convention used throughout the project
------------------------------------------------------
Index  : `interval_start_utc` — tz-aware UTC timestamp marking the START of a 1-hour interval
         [t, t+1h). Prices from ENTSO-E/Energy-Charts use the same convention.
Columns: `pv_kw`  — PV AC power in kW (PVGIS reports W; we divide by 1000)
         `pv_kwh` — energy in the interval in kWh (= pv_kw * 1 h)
         `poa_wm2` — plane-of-array (in-plane) global irradiance, W/m2

Verified API differences (Nov 2026, see reports/data_audit):
* 5.3 azimuth is SOUTH-based (0 = south); 6 is NORTH-based (180 = south).
* 5.3 takes `loss` in %; 6 takes `system_efficiency` as a fraction.
* 5.3 returns a timestamp per row ("YYYYMMDD:HH11", the SARAH satellite slot);
  6 returns bare value arrays — the time index must be reconstructed from start_time/frequency.
* 5.3 accepts years 2005–2023 only; 6 documents 2014–2025.
* 6 can contain nulls (missing satellite slots); 5.3 reconstructs them (flag column `Int`).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------- requests
def pvgis53_params(lat, lon, kwp, loss_pct, tilt, az_south, year) -> dict:
    return {
        "lat": lat, "lon": lon, "peakpower": kwp, "loss": loss_pct,
        "angle": tilt, "aspect": az_south,
        "startyear": year, "endyear": year,
        "pvcalculation": 1, "components": 1, "outputformat": "json",
    }


def south_to_north_azimuth(az_south: float) -> float:
    """Convert PVGIS 5.3 azimuth (S=0, W=+90, E=-90) to north-based (N=0, E=90, S=180, W=270)."""
    return (az_south + 180.0) % 360.0


def pvgis6_params(lat, lon, kwp, loss_pct, tilt, az_south, year) -> dict:
    return {
        "latitude": lat, "longitude": lon, "peak-power": kwp,
        "system_efficiency": round(1 - loss_pct / 100, 6),
        "surface_tilt": tilt, "surface_orientation": south_to_north_azimuth(az_south),
        "start_time": f"{year}-01-01T00:00:00", "end_time": f"{year}-12-31T23:59:59",
        "frequency": "Hourly", "timezone": "UTC", "verbose": 1,
    }


# ----------------------------------------------------------------------------- parsing
def parse_pvgis53(raw: dict | str | Path) -> tuple[pd.DataFrame, dict]:
    d = _load(raw)
    rows = d["outputs"]["hourly"]
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["time"], format="%Y%m%d:%H%M", utc=True)
    sample_minute = sorted(ts.dt.minute.unique().tolist())
    # Value is the satellite-slot estimate (minute ~11) taken to represent the hour → label interval start.
    df.index = ts.dt.floor("h").rename("interval_start_utc")
    out = pd.DataFrame(index=df.index)
    out["pv_kw"] = df["P"].astype(float) / 1000.0
    out["pv_kwh"] = out["pv_kw"] * 1.0
    out["poa_wm2"] = df[["Gb(i)", "Gd(i)", "Gr(i)"]].sum(axis=1, min_count=3)
    out["sun_height_deg"] = df["H_sun"].astype(float)
    out["t2m_c"] = df["T2m"].astype(float)
    out["ws10m_ms"] = df["WS10m"].astype(float)
    out["reconstructed_flag"] = df["Int"].astype(float)
    meta = {
        "source": "PVGIS 5.3",
        "inputs": d.get("inputs"),
        "raw_timestamp_minutes": sample_minute,
        "power_unit_raw": d.get("meta", {}).get("outputs", {}).get("hourly", {})
                            .get("variables", {}).get("P", {}).get("units"),
    }
    return out, meta


def parse_pvgis6(raw: dict | str | Path, year: int) -> tuple[pd.DataFrame, dict]:
    d = _load(raw)
    power = _nan_array(d["power"])
    n = len(power)
    idx = pd.date_range(f"{year}-01-01", periods=n, freq="h", tz="UTC", name="interval_start_utc")
    expected = len(pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h", tz="UTC", inclusive="left"))
    out = pd.DataFrame(index=idx)
    out["pv_kw"] = power / 1000.0
    out["pv_kwh"] = out["pv_kw"] * 1.0
    out["poa_wm2"] = _nan_array(d.get("global_inclined_irradiance", [np.nan] * n))
    if "power_without_loss" in d:
        # Verified empirically (Stage 1 audit): within the SAME response `power` is in W but
        # `power_without_loss` is in kW (power / power_without_loss == 860 == 0.86 * 1000).
        out["pv_kw_without_loss"] = _nan_array(d["power_without_loss"])
    meta = {
        "source": "PVGIS 6",
        "n_values": n,
        "n_expected": expected,
        "time_index": "reconstructed from start_time + hourly frequency (JSON has no timestamps)",
        **{k: d.get(k) for k in [
            "title", "technology", "module_type", "peak_power", "peak_power_unit", "power_model",
            "surface_orientation", "surface_tilt", "unit", "azimuth_origin", "system_efficiency",
            "incidence_algorithm", "position_algorithm", "shading"]},
    }
    return out, meta


def parse_pvgis6_csv(path: str | Path) -> pd.DataFrame:
    """PVGIS 6 CSV output (request with `csv=<name>`). Unlike JSON it carries explicit timestamps
    (`Time`, the satellite source time, e.g. 'YYYY-MM-DDTHH:11:02.587'). Used to VERIFY the
    index reconstructed for the JSON series."""
    c = pd.read_csv(path, usecols=["Time", "Power"])
    t = pd.to_datetime(c["Time"], utc=True)
    return pd.DataFrame({"source_time_utc": t, "power_w": c["Power"].astype(float)})


def _load(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, Path)) and Path(raw).exists():
        return json.loads(Path(raw).read_text(encoding="utf-8"))
    return json.loads(raw)


def _nan_array(values) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)
