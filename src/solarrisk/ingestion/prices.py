"""Netherlands day-ahead electricity prices.

Primary (official) source : ENTSO-E Transparency Platform, document type A44 (price document),
                            bidding zone 10YNL----------L. Needs a personal security token.
Fallback source           : Energy-Charts API (Fraunhofer ISE); license "CC BY 4.0 from
                            Bundesnetzagentur | SMARD.de". No key needed.

Output convention: index `interval_start_utc` (UTC, interval start), column `price_eur_mwh`.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


# ----------------------------------------------------------------------------- Energy-Charts
def energycharts_params(bzn: str, year: int) -> dict:
    return {"bzn": bzn, "start": f"{year}-01-01T00:00Z", "end": f"{year}-12-31T23:59Z"}


def parse_energycharts(raw: dict | str | Path) -> tuple[pd.Series, dict]:
    d = raw if isinstance(raw, dict) else json.loads(Path(raw).read_text(encoding="utf-8"))
    idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True).rename("interval_start_utc")
    s = pd.Series(d["price"], index=idx, name="price_eur_mwh", dtype=float)
    meta = {"source": "Energy-Charts", "unit": d.get("unit"), "license_info": d.get("license_info"),
            "deprecated": d.get("deprecated")}
    return s, meta


# ----------------------------------------------------------------------------- ENTSO-E
def entsoe_params(token: str, zone: str, year: int) -> dict:
    return {"securityToken": token, "documentType": "A44", "in_Domain": zone, "out_Domain": zone,
            "periodStart": f"{year}01010000", "periodEnd": f"{year + 1}01010000"}


_RES = {"PT60M": "60min", "PT30M": "30min", "PT15M": "15min"}


def parse_entsoe_a44(xml_text: str) -> pd.Series:
    """Parse an ENTSO-E A44 Publication_MarketDocument into a UTC price series.

    Handles curveType A03 ('variable sized block'): positions whose price equals the previous
    position may be omitted, so missing positions are forward-filled within each Period.
    """
    root = ET.fromstring(xml_text)
    ns = {"ns": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {"ns": ""}
    p = "ns:" if ns["ns"] else ""
    if root.tag.endswith("Acknowledgement_MarketDocument"):
        reason = root.find(f".//{p}Reason/{p}text", ns)
        raise ValueError(f"ENTSO-E acknowledgement (no data): {reason.text if reason is not None else ''}")
    parts = []
    for ts in root.findall(f"{p}TimeSeries", ns):
        curve = ts.findtext(f"{p}curveType", default="A01", namespaces=ns)
        for period in ts.findall(f"{p}Period", ns):
            start = pd.Timestamp(period.findtext(f"{p}timeInterval/{p}start", namespaces=ns))
            end = pd.Timestamp(period.findtext(f"{p}timeInterval/{p}end", namespaces=ns))
            freq = _RES[period.findtext(f"{p}resolution", namespaces=ns)]
            grid = pd.date_range(start, end, freq=freq, inclusive="left", name="interval_start_utc")
            pts = {int(pt.findtext(f"{p}position", namespaces=ns)):
                   float(pt.findtext(f"{p}price.amount", namespaces=ns))
                   for pt in period.findall(f"{p}Point", ns)}
            s = pd.Series(pts).reindex(range(1, len(grid) + 1))
            if curve == "A03":
                s = s.ffill()
            s.index = grid
            parts.append(s)
    out = pd.concat(parts).sort_index()
    out.name = "price_eur_mwh"
    return out
