import unittest
from datetime import datetime, timedelta, timezone

from tfr_alerts import faa, geo, matching
from tfr_alerts.airports import Airports

# 1 NM radius circle-ish square around 40.0N 75.0W
SQUARE = [(40.0167, -75.0217), (40.0167, -74.9783), (39.9833, -74.9783), (39.9833, -75.0217)]

SAMPLE_XML = b"""<XNOTAM-Update><Group><Add><Not>
  <dateEffective>2026-10-22T15:00:00</dateEffective>
  <dateExpire>2026-10-23T02:00:00</dateExpire>
  <codeTimeZone>EDT</codeTimeZone>
  <TfrNot><TFRAreaGroup>
    <aseTFRArea>
      <codeDistVerUpper>ALT</codeDistVerUpper><valDistVerUpper>3000</valDistVerUpper><uomDistVerUpper>FT</uomDistVerUpper>
      <codeDistVerLower>ALT</codeDistVerLower><valDistVerLower>0</valDistVerLower><uomDistVerLower>FT</uomDistVerLower>
    </aseTFRArea>
    <abdMergedArea>
      <Avx><geoLat>40.01670000N</geoLat><geoLong>075.02170000W</geoLong></Avx>
      <Avx><geoLat>40.01670000N</geoLat><geoLong>074.97830000W</geoLong></Avx>
      <Avx><geoLat>39.98330000N</geoLat><geoLong>074.97830000W</geoLong></Avx>
    </abdMergedArea>
  </TFRAreaGroup></TfrNot>
  <txtDescrTraditional>!FDC 6/1234 TEST</txtDescrTraditional>
</Not></Add></Group></XNOTAM-Update>"""


class GeoTests(unittest.TestCase):
    def test_inside_is_zero(self):
        self.assertEqual(geo.distance_to_polygon_nm(40.0, -75.0, SQUARE), 0.0)

    def test_distance_north(self):
        # 1 degree of latitude is 60 NM; the square's north edge is ~1 NM north of centre.
        d = geo.distance_to_polygon_nm(41.0, -75.0, SQUARE)
        self.assertAlmostEqual(d, 59.0, delta=0.2)

    def test_haversine(self):
        self.assertAlmostEqual(geo.haversine_nm(40, -75, 41, -75), 60.0, delta=0.2)


class ParseTests(unittest.TestCase):
    def test_parse_detail(self):
        t = faa.parse_detail("6/1234", SAMPLE_XML, {"type": "HAZARDS"})
        self.assertEqual(len(t.areas), 1)
        self.assertEqual(t.areas[0][0], (40.0167, -75.0217))
        self.assertEqual(t.altitudes, ["SFC to 3,000FT MSL"])
        self.assertEqual(t.effective, datetime(2026, 10, 22, 15, tzinfo=timezone.utc))
        self.assertEqual(t.url, "https://tfr.faa.gov/tfr3/?page=detail_6_1234")


class MatchingTests(unittest.TestCase):
    def tfr(self, start, end):
        return faa.TFR("6/1", "HAZARDS", "PA", "test", areas=[SQUARE], effective=start, expires=end)

    def test_filters(self):
        now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        active = self.tfr(now - timedelta(hours=1), now + timedelta(hours=5))
        soon = self.tfr(now + timedelta(hours=20), now + timedelta(hours=30))
        later = self.tfr(now + timedelta(days=5), now + timedelta(days=6))
        expired = self.tfr(now - timedelta(days=2), now - timedelta(days=1))
        m = matching.nearby([later, soon, expired, active], 40.5, -75.0, 50, now=now)
        self.assertEqual([x.tfr for x in m], [active, soon])
        self.assertTrue(m[0].active_now)
        self.assertEqual(m[0].bearing, "S")

    def test_outside_radius(self):
        now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        t = self.tfr(None, None)
        self.assertEqual(matching.nearby([t], 42.0, -75.0, 50, now=now), [])


class AirportTests(unittest.TestCase):
    CSV = ("ident,type,name,latitude_deg,longitude_deg,gps_code,iata_code,local_code\n"
           "KPNE,small_airport,Northeast Philadelphia Airport,40.0819,-75.0106,KPNE,PNE,PNE\n"
           "N57,small_airport,New Garden Airport,39.83,-75.77,,,N57\n")

    def test_find(self):
        a = Airports.from_csv_text(self.CSV)
        self.assertEqual(a.find("pne")["code"], "KPNE")
        self.assertEqual(a.find("KPNE")["code"], "KPNE")
        self.assertEqual(a.find("N57")["name"], "New Garden Airport")
        self.assertIsNone(a.find("ZZZZ"))
        self.assertEqual(a.nearest(40.08, -75.01)["code"], "KPNE")


class ResolvePointTests(unittest.TestCase):
    def test_fresh_location_then_home_fallback(self):
        from tfr_alerts.__main__ import resolve_point
        apts = Airports.from_csv_text(AirportTests.CSV)
        now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        sub = {"airport": "KPNE", "lat": 39.84, "lon": -75.76, "location_updated": "2026-10-21T09:00:00Z"}
        self.assertEqual(resolve_point(sub, apts, now)[2], "your location (near N57)")
        sub["location_updated"] = "2026-10-15T09:00:00Z"  # a week old: back to home airport
        self.assertEqual(resolve_point(sub, apts, now)[2], "KPNE")


if __name__ == "__main__":
    unittest.main()
