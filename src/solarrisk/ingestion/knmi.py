"""KNMI hourly station observations (daggegevens.knmi.nl 'uurgegevens' service, no key needed).

Raw conventions (from the file header, verified):
* HH = 1..24, hour-ENDING in UT: "Uurvak 05 loopt van 04.00 UT tot 5.00 UT".
  -> interval_start_utc = date + (HH - 1) hours.
* Q  = global horizontal radiation, J/cm2 summed over the hour. Mean W/m2 = Q * 1e4 / 3600.
* T  = air temperature at 1.5 m in 0.1 degC, at the time of observation (end of hour).
* SQ = sunshine duration in 0.1 h; -1 means < 0.05 h.
* Blank fields = missing.
KNMI states these hourly series are inhomogeneous (station moves / method changes) and not
suitable for trend analysis — we use them only for consistency/sensitivity checks.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd


def knmi_form(stations: list[int], variables: list[str], start_year: int, end_year: int) -> dict:
    return {
        "stns": ":".join(str(s) for s in stations),
        "vars": ":".join(variables),
        "start": f"{start_year}010101",
        "end": f"{end_year}123124",
    }


def parse_knmi_hourly(text: str | Path) -> pd.DataFrame:
    if isinstance(text, Path) or (isinstance(text, str) and len(text) < 300 and Path(text).exists()):
        text = Path(text).read_text(encoding="latin-1")
    header = None
    for line in text.splitlines():
        if line.startswith("# STN,YYYYMMDD,HH"):
            header = [c.strip() for c in line.lstrip("# ").split(",")]
    if header is None:
        raise ValueError("KNMI header line '# STN,YYYYMMDD,HH,...' not found")
    body = "\n".join(l for l in text.splitlines() if l and not l.startswith("#"))
    df = pd.read_csv(io.StringIO(body), header=None, names=header, skipinitialspace=True,
                     na_values=["", " "])
    df = df.rename(columns={"STN": "station", "YYYYMMDD": "date", "HH": "hh"})
    day = pd.to_datetime(df["date"].astype(str), format="%Y%m%d", utc=True)
    df["interval_start_utc"] = day + pd.to_timedelta(df["hh"] - 1, unit="h")
    out = pd.DataFrame({"station": df["station"].astype(int),
                        "interval_start_utc": df["interval_start_utc"]})
    if "Q" in df:
        out["ghi_wm2"] = df["Q"].astype(float) * 1e4 / 3600.0
    if "T" in df:
        out["t_c"] = df["T"].astype(float) / 10.0
    if "SQ" in df:
        sq = df["SQ"].astype(float)
        out["sun_h"] = np.where(sq == -1, 0.025, sq / 10.0)  # -1 => <0.05 h; midpoint used
    return out
