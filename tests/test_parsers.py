"""Parser unit tests.

ALL inputs in this file are SYNTHETIC test fixtures written by hand to mimic the documented
structure of each source. They are NOT observations and must never be used as data.
"""

import numpy as np
import pandas as pd
import pytest

from solarrisk.ingestion import knmi, prices, pvgis


# ----------------------------------------------------------------------------- PVGIS
def test_azimuth_conversion_south_based_to_north_based():
    assert pvgis.south_to_north_azimuth(0) == 180      # south
    assert pvgis.south_to_north_azimuth(-90) == 90     # east
    assert pvgis.south_to_north_azimuth(90) == 270     # west
    assert pvgis.south_to_north_azimuth(180) == 0      # north


def test_pvgis6_params_convert_loss_and_azimuth():
    p = pvgis.pvgis6_params(52.45, 5.70, 1000, 14, 35, 0, 2022)
    assert p["system_efficiency"] == pytest.approx(0.86)
    assert p["surface_orientation"] == 180
    assert p["start_time"].startswith("2022-01-01")


SYNTH_PVGIS53 = {  # synthetic
    "inputs": {},
    "outputs": {"hourly": [
        {"time": "20220101:0011", "P": 0.0, "Gb(i)": 0, "Gd(i)": 0, "Gr(i)": 0, "H_sun": 0, "T2m": 5, "WS10m": 2, "Int": 0},
        {"time": "20220101:0111", "P": 250000.0, "Gb(i)": 200, "Gd(i)": 80, "Gr(i)": 5, "H_sun": 10, "T2m": 6, "WS10m": 2, "Int": 0},
    ]},
}


def test_parse_pvgis53_units_and_interval_start():
    df, meta = pvgis.parse_pvgis53(SYNTH_PVGIS53)
    assert str(df.index.tz) == "UTC"
    assert df.index[1] == pd.Timestamp("2022-01-01 01:00", tz="UTC")  # HH:11 -> interval start HH:00
    assert df["pv_kw"].iloc[1] == 250.0                                # W -> kW
    assert df["poa_wm2"].iloc[1] == 285
    assert meta["raw_timestamp_minutes"] == [11]


def test_parse_pvgis6_nulls_and_leap_year_length():
    vals = [0.0] * 8784
    vals[12] = None
    df, meta = pvgis.parse_pvgis6({"power": vals, "global_inclined_irradiance": vals}, 2024)  # synthetic
    assert len(df) == 8784 == meta["n_expected"]
    assert df["pv_kw"].isna().sum() == 1
    assert df.index[-1] == pd.Timestamp("2024-12-31 23:00", tz="UTC")


# ----------------------------------------------------------------------------- KNMI
SYNTH_KNMI = """# synthetic fixture
# STN,YYYYMMDD,HH,    Q,    T,   SQ
  269,20220101,    1,    0,   50,    0
  269,20220101,   13,   36,  100,   -1
  269,20220101,   24,     ,  -12,    5
"""


def test_parse_knmi_hour_ending_and_units():
    df = knmi.parse_knmi_hourly(SYNTH_KNMI)
    assert df["interval_start_utc"].iloc[0] == pd.Timestamp("2022-01-01 00:00", tz="UTC")  # HH=1 -> 00:00
    assert df["interval_start_utc"].iloc[2] == pd.Timestamp("2022-01-01 23:00", tz="UTC")  # HH=24 -> 23:00
    assert df["ghi_wm2"].iloc[1] == pytest.approx(100.0)  # 36 J/cm2/h = 100 W/m2
    assert np.isnan(df["ghi_wm2"].iloc[2])                # blank -> NaN
    assert df["t_c"].iloc[2] == pytest.approx(-1.2)
    assert df["sun_h"].iloc[1] == pytest.approx(0.025)    # -1 -> <0.05 h


# ----------------------------------------------------------------------------- prices
def test_parse_energycharts():
    s, meta = prices.parse_energycharts({"unix_seconds": [1640995200, 1640998800], "price": [10.0, -5.0],
                                          "unit": "EUR / MWh", "license_info": "x"})  # synthetic
    assert s.index[0] == pd.Timestamp("2022-01-01 00:00", tz="UTC")
    assert s.iloc[1] == -5.0


SYNTH_A44 = """<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
 <TimeSeries><curveType>A03</curveType>
  <Period>
   <timeInterval><start>2023-03-25T23:00Z</start><end>2023-03-26T03:00Z</end></timeInterval>
   <resolution>PT60M</resolution>
   <Point><position>1</position><price.amount>50.0</price.amount></Point>
   <Point><position>3</position><price.amount>-10.5</price.amount></Point>
  </Period>
 </TimeSeries>
</Publication_MarketDocument>"""  # synthetic


def test_parse_entsoe_a44_curve_a03_forward_fill():
    s = prices.parse_entsoe_a44(SYNTH_A44)
    assert len(s) == 4
    assert list(s.values) == [50.0, 50.0, -10.5, -10.5]  # omitted positions repeat the previous price
    assert s.index[0] == pd.Timestamp("2023-03-25 23:00", tz="UTC")


def test_entsoe_acknowledgement_raises():
    xml = """<Acknowledgement_MarketDocument xmlns="urn:x"><Reason><text>No matching data</text></Reason>
    </Acknowledgement_MarketDocument>"""
    with pytest.raises(ValueError, match="No matching data"):
        prices.parse_entsoe_a44(xml)
