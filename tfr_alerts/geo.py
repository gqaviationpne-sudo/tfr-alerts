"""Geometry helpers: distance from a point to a TFR boundary, in nautical miles."""
import math

EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass(deg):
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return points[int((deg + 22.5) // 45) % 8]


def _project(lat0, lon0, lat, lon):
    """Flat projection in NM centred on (lat0, lon0). Accurate to well under 1% inside ~200 NM."""
    x = (lon - lon0) * 60.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 60.0
    return x, y


def _segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _inside(px, py, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def distance_to_polygon_nm(lat, lon, polygon):
    """Shortest distance from (lat, lon) to the polygon edge; 0 if the point is inside.

    polygon is a list of (lat, lon) vertices.
    """
    if not polygon:
        return None
    pts = [_project(lat, lon, plat, plon) for plat, plon in polygon]
    if len(pts) >= 3 and _inside(0.0, 0.0, pts):
        return 0.0
    if len(pts) == 1:
        return math.hypot(*pts[0])
    best = float("inf")
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        best = min(best, _segment_distance(0.0, 0.0, a[0], a[1], b[0], b[1]))
    return best


def centroid(polygon):
    lat = sum(p[0] for p in polygon) / len(polygon)
    lon = sum(p[1] for p in polygon) / len(polygon)
    return lat, lon
