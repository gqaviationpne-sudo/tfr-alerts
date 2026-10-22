"""Pick the TFRs that matter to one pilot: near their point and active soon."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import geo

LOOKAHEAD_HOURS = 48


@dataclass
class Match:
    tfr: object
    distance_nm: float
    bearing: str  # compass direction from the pilot to the TFR
    active_now: bool


def nearby(tfrs, lat, lon, radius_nm, now=None, lookahead_hours=LOOKAHEAD_HOURS):
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(hours=lookahead_hours)
    out = []
    for tfr in tfrs:
        if not tfr.areas:
            continue
        if tfr.expires and tfr.expires < now:
            continue
        if tfr.effective and tfr.effective > horizon:
            continue
        dist = min(geo.distance_to_polygon_nm(lat, lon, poly) for poly in tfr.areas)
        if dist is None or dist > radius_nm:
            continue
        clat, clon = geo.centroid(max(tfr.areas, key=len))
        direction = "OVERHEAD" if dist == 0 else geo.compass(geo.bearing_deg(lat, lon, clat, clon))
        active = tfr.effective is None or tfr.effective <= now
        out.append(Match(tfr, dist, direction, active))
    out.sort(key=lambda m: (not m.active_now, m.distance_nm))
    return out
