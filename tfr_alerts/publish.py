"""Build the app's static site: copy docs/ and add live data files the app reads.

  python -m tfr_alerts.publish --out _site

Writes:
  data/tfrs.json      every current TFR with boundary, times (UTC) and altitudes
  data/airports.json  {code: [lat, lon, name, official code]} for US airports, to resolve home airports
  config.js           SCRIPT_URL for the app, from the SCRIPT_URL environment variable
"""
import argparse
import json
import os
import shutil
from datetime import datetime, timezone

from . import faa
from .airports import Airports

DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def tfrs_payload(tfrs):
    items = []
    for t in tfrs:
        if not t.areas:
            continue
        items.append({
            "id": t.notam_id,
            "type": t.type,
            "state": t.state,
            "description": t.description,
            "effective": _iso(t.effective),
            "expires": _iso(t.expires),
            "altitudes": list(dict.fromkeys(t.altitudes)),
            "url": t.url,
            # 4 decimals is about 11 m: plenty for a TFR boundary and keeps the file small.
            "areas": [[[round(lat, 4), round(lon, 4)] for lat, lon in poly] for poly in t.areas],
        })
    return {"updated": _iso(datetime.now(timezone.utc)), "tfrs": items}


def airports_payload(airports):
    out = {}
    for row in airports._airports:
        if row.get("iso_country") not in ("US", "PR", "VI", "GU", "AS", "MP"):
            continue
        info = Airports._info(row, row.get("ident", ""))
        entry = [round(info["lat"], 4), round(info["lon"], 4), info["name"], info["code"]]
        for key in ("gps_code", "ident", "local_code"):
            code = (row.get(key) or "").strip().upper()
            if code and code not in out:
                out[code] = entry
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()

    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(DOCS, args.out)
    os.makedirs(os.path.join(args.out, "data"), exist_ok=True)

    tfrs = faa.fetch_all()
    with open(os.path.join(args.out, "data", "tfrs.json"), "w", encoding="utf-8") as f:
        json.dump(tfrs_payload(tfrs), f, separators=(",", ":"))

    with open(os.path.join(args.out, "data", "airports.json"), "w", encoding="utf-8") as f:
        json.dump(airports_payload(Airports.load()), f, separators=(",", ":"))

    script_url = os.environ.get("SCRIPT_URL", "").strip()
    if script_url:
        with open(os.path.join(args.out, "config.js"), "w", encoding="utf-8") as f:
            f.write(f"window.TFR_CONFIG = {json.dumps({'scriptUrl': script_url})};\n")
    print(f"Published {len(tfrs)} TFRs to {args.out}")


if __name__ == "__main__":
    main()
