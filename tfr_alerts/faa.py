"""Fetch and parse TFRs from the FAA's public feed at tfr.faa.gov."""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

LIST_URL = "https://tfr.faa.gov/tfrapi/exportTfrList"
DETAIL_URL = "https://tfr.faa.gov/download/detail_{id}.xml"
PUBLIC_URL = "https://tfr.faa.gov/tfr3/?page=detail_{id}"
USER_AGENT = "tfr-alerts/1.0 (free pilot TFR notification service)"


@dataclass
class TFR:
    notam_id: str
    type: str
    state: str
    description: str
    areas: list = field(default_factory=list)  # list of polygons, each a list of (lat, lon)
    altitudes: list = field(default_factory=list)  # e.g. ["SFC-7500FT MSL"]
    effective: datetime = None
    expires: datetime = None
    time_zone: str = "UTC"  # local zone label only; effective/expires are always UTC
    text: str = ""

    @property
    def file_id(self):
        return self.notam_id.replace("/", "_")

    @property
    def url(self):
        return PUBLIC_URL.format(id=self.file_id)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _coord(value):
    """'43.81666839N' -> 43.81666839, '115.74166667W' -> -115.74166667"""
    m = re.fullmatch(r"\s*([\d.]+)\s*([NSEW])\s*", value or "")
    if not m:
        return None
    num = float(m.group(1))
    return -num if m.group(2) in "SW" else num


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _text(el, path):
    found = el.find(path)
    return found.text.strip() if found is not None and found.text else ""


def _altitude(area):
    def side(prefix):
        val = _text(area, f"valDistVer{prefix}")
        code = _text(area, f"codeDistVer{prefix}")
        uom = _text(area, f"uomDistVer{prefix}")
        if val in ("", "0") and prefix == "Lower":
            return "SFC"
        if not val:
            return "?"
        ref = {"ALT": "MSL", "HEI": "AGL"}.get(code, code)
        if uom == "FL":
            return f"FL{val}"
        return f"{int(float(val)):,}FT {ref}".strip()

    return f"{side('Lower')} to {side('Upper')}"


def parse_detail(notam_id, xml_bytes, summary=None):
    summary = summary or {}
    root = ET.fromstring(xml_bytes)
    notam = root.find(".//Not")
    tfr = TFR(
        notam_id=notam_id,
        type=summary.get("type", ""),
        state=summary.get("state", ""),
        description=summary.get("description", ""),
    )
    if notam is None:
        return tfr
    tfr.effective = _date(_text(notam, "dateEffective"))
    tfr.expires = _date(_text(notam, "dateExpire"))
    tfr.time_zone = _text(notam, "codeTimeZone") or "UTC"
    tfr.text = _text(notam, ".//txtDescrTraditional")

    for area in notam.iter("aseTFRArea"):
        tfr.altitudes.append(_altitude(area))

    for merged in notam.iter("abdMergedArea"):
        poly = []
        for avx in merged.iter("Avx"):
            lat, lon = _coord(_text(avx, "geoLat")), _coord(_text(avx, "geoLong"))
            if lat is not None and lon is not None:
                poly.append((lat, lon))
        if poly:
            tfr.areas.append(poly)

    if not tfr.areas:
        # Fall back to any vertices in the document (e.g. shapes without a merged area).
        poly = []
        for avx in root.iter("Avx"):
            lat, lon = _coord(_text(avx, "geoLat")), _coord(_text(avx, "geoLong"))
            if lat is not None and lon is not None:
                poly.append((lat, lon))
        if poly:
            tfr.areas.append(poly)
    return tfr


def fetch_all(max_workers=8):
    """Return every current TFR with its boundary. TFRs whose detail fails to load are skipped."""
    summaries = json.loads(_get(LIST_URL))

    def load(s):
        notam_id = s["notam_id"]
        try:
            return parse_detail(notam_id, _get(DETAIL_URL.format(id=notam_id.replace("/", "_"))), s)
        except Exception as exc:  # one bad TFR must not stop everyone's alert
            print(f"warning: could not load TFR {notam_id}: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return [t for t in pool.map(load, summaries) if t is not None]
