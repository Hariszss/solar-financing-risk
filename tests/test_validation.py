"""Validation utilities. Synthetic inputs (clearly constructed in-test) + optional real-data checks."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from solarrisk.validation import checks, solar

ROOT = Path(__file__).resolve().parents[1]


def test_expected_hours_2021_2024():
    assert len(checks.expected_hourly_index([2021, 2022, 2023, 2024])) == 35064  # 3*8760 + 8784


def test_missing_runs():
    m = pd.Series([0, 1, 1, 0, 1, 0, 1, 1, 1], dtype=bool)
    r = checks.missing_runs(m)
    assert r == {"n_runs": 3, "max_run": 3, "run_length_counts": {1: 1, 2: 1, 3: 1}}


def test_audit_detects_duplicates_gaps_and_bounds():
    idx = pd.date_range("2022-01-01", periods=8760, freq="h", tz="UTC")
    s = pd.Series(1.0, index=idx, name="x")
    s = s.drop(idx[100])                               # one gap
    s = pd.concat([s, s.iloc[[5]]]).sort_index()       # one duplicate
    s.iloc[10] = 999                                   # out of bounds
    a = checks.audit_hourly(s, [2022], {"x": (0, 10)})
    assert a["n_duplicate_timestamps"] == 1
    assert a["n_expected_missing_from_index"] == 1
    assert a["columns"]["x"]["n_out_of_bounds"] == 1


def test_solar_elevation_equinox_noon_and_midnight():
    # Around the March equinox, solar-noon elevation ≈ 90 - latitude.
    lat, lon = 52.45, 5.70
    noon_utc = pd.DatetimeIndex([pd.Timestamp("2022-03-20 11:38", tz="UTC")])  # ~12:00 - 4 min*5.7 - EoT
    assert solar.solar_elevation_deg(noon_utc, lat, lon)[0] == pytest.approx(90 - lat, abs=1.0)
    midnight = pd.DatetimeIndex([pd.Timestamp("2022-06-21 23:30", tz="UTC")])
    assert solar.solar_elevation_deg(midnight, lat, lon)[0] < 0


REAL = ROOT / "data" / "interim" / "pvgis53_hourly.csv"


@pytest.mark.skipif(not REAL.exists(), reason="real data not downloaded; run scripts/run_stage1_audit.py")
def test_real_solar_position_matches_pvgis53():
    a = pd.read_csv(REAL, index_col=0, parse_dates=True)
    m = a["sun_height_deg"] > 0.5
    el = solar.solar_elevation_deg(a.index[m] + pd.Timedelta(minutes=11), 52.45, 5.70)
    assert np.abs(el - a.loc[m, "sun_height_deg"].to_numpy()).max() < 1.0


@pytest.mark.skipif(not (ROOT / "data/processed/stage1_aligned_hourly.csv").exists(), reason="real data not downloaded")
def test_real_aligned_dataset_shape():
    al = pd.read_csv(ROOT / "data/processed/stage1_aligned_hourly.csv", index_col=0, parse_dates=True)
    assert len(al) == 35064
    assert not al.index.duplicated().any()
    assert al["price_eur_mwh"].notna().all()
