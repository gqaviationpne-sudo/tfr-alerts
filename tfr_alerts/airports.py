"""Look up airport coordinates from the free OurAirports database."""
import csv
import io
import os
import urllib.request

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", ".cache", "airports.csv")


class Airports:
    def __init__(self, rows):
        self._airports = [
            r for r in rows if r.get("type") in ("small_airport", "medium_airport", "large_airport")
        ]
        self._by_code = {}
        # Later keys never overwrite earlier ones, so the official ident wins over IATA/local codes.
        for key in ("ident", "gps_code", "local_code", "iata_code"):
            for row in rows:
                code = (row.get(key) or "").strip().upper()
                if code and code not in self._by_code:
                    self._by_code[code] = row

    @classmethod
    def load(cls, path=CACHE_PATH):
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with urllib.request.urlopen(AIRPORTS_URL, timeout=60) as resp:
                data = resp.read()
            with open(path, "wb") as f:
                f.write(data)
        with open(path, encoding="utf-8") as f:
            return cls(list(csv.DictReader(f)))

    @classmethod
    def from_csv_text(cls, text):
        return cls(list(csv.DictReader(io.StringIO(text))))

    def find(self, code):
        """Accepts ICAO (KPNE), FAA (PNE, N57) or IATA codes. Returns dict or None."""
        code = (code or "").strip().upper()
        if not code:
            return None
        row = self._by_code.get(code)
        if row is None and len(code) == 3:
            row = self._by_code.get("K" + code)
        if row is None:
            return None
        return self._info(row, code)

    def nearest(self, lat, lon):
        """Closest public-use-style airport to a point, used to label a pilot's current location."""
        best, best_d = None, float("inf")
        for row in self._airports:
            try:
                d = (float(row["latitude_deg"]) - lat) ** 2 + (float(row["longitude_deg"]) - lon) ** 2
            except ValueError:
                continue
            if d < best_d:
                best, best_d = row, d
        return self._info(best, best.get("ident", "")) if best else None

    @staticmethod
    def _info(row, code):
        return {
            "code": row.get("gps_code") or row.get("ident") or code,
            "name": row.get("name", ""),
            "lat": float(row["latitude_deg"]),
            "lon": float(row["longitude_deg"]),
        }
