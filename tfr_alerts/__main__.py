"""Send the morning TFR alert to every subscriber.

  python -m tfr_alerts                       # real run (uses environment variables, see README)
  python -m tfr_alerts --dry-run             # build every alert, send nothing, write previews
  python -m tfr_alerts --airport KPNE        # quick check for one airport, prints to screen
"""
import argparse
import html
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import faa, matching, notify
from .airports import Airports

PREVIEW_DIR = os.path.join(os.path.dirname(__file__), "..", "previews")
LOCATION_MAX_AGE = timedelta(days=3)


def env(name, default=""):
    return os.environ.get(name, default).strip()


def load_subscribers(path=None):
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    url = env("SCRIPT_URL")
    key = env("SCRIPT_API_KEY")
    if not url or not key:
        sys.exit("SCRIPT_URL and SCRIPT_API_KEY must be set (or pass --subscribers FILE).")
    query = urllib.parse.urlencode({"action": "list", "key": key})
    with urllib.request.urlopen(f"{url}?{query}", timeout=60) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        sys.exit(f"Subscriber list error: {data['error']}")
    return data["subscribers"]


def resolve_point(sub, airports, now=None):
    """Returns (lat, lon, label) for a subscriber.

    Uses the location the app last detected if it is under LOCATION_MAX_AGE old, else the home airport.
    """
    now = now or datetime.now(timezone.utc)
    lat, lon = sub.get("lat"), sub.get("lon")
    fresh = True
    if sub.get("location_updated"):
        try:
            seen = datetime.fromisoformat(str(sub["location_updated"]).replace("Z", "+00:00"))
            fresh = now - seen <= LOCATION_MAX_AGE
        except ValueError:
            fresh = False
    if lat not in (None, "") and lon not in (None, "") and fresh:
        lat, lon = float(lat), float(lon)
        near = airports.nearest(lat, lon)
        label = f"your location (near {near['code']})" if near else "your location"
        return lat, lon, label
    apt = airports.find(sub.get("airport"))
    if apt:
        return apt["lat"], apt["lon"], apt["code"]
    return None


def links_for(sub):
    token = sub.get("token", "")
    script = env("SCRIPT_URL", "https://example.invalid/script")
    return {
        "unsubscribe": f"{script}?" + urllib.parse.urlencode({"action": "unsubscribe", "token": token}),
        "app": env("APP_URL", "https://example.invalid/app"),
    }


def quick_check(code, radius, tfrs, airports):
    apt = airports.find(code)
    if not apt:
        sys.exit(f"Unknown airport: {code}")
    matches = matching.nearby(tfrs, apt["lat"], apt["lon"], radius)
    print(f"{apt['code']} {apt['name']}: {len(matches)} TFR(s) within {radius} NM\n")
    text, _ = notify.email_bodies("", apt["code"], radius, matches, links_for({}))
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="build alerts but send nothing")
    ap.add_argument("--subscribers", help="read subscribers from a JSON file instead of the Google Sheet")
    ap.add_argument("--airport", help="print TFRs near one airport and exit")
    ap.add_argument("--radius", type=int, default=50)
    args = ap.parse_args()

    airports = Airports.load()
    tfrs = faa.fetch_all()
    print(f"Loaded {len(tfrs)} TFRs from the FAA")

    if args.airport:
        quick_check(args.airport, args.radius, tfrs, airports)
        return

    subscribers = load_subscribers(args.subscribers)
    print(f"{len(subscribers)} active subscriber(s)")

    twilio = None
    if env("TWILIO_ACCOUNT_SID") and env("TWILIO_AUTH_TOKEN") and env("TWILIO_FROM"):
        twilio = notify.TwilioSender(env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"), env("TWILIO_FROM"))

    email = None
    if not args.dry_run:
        email = notify.EmailSender(
            env("SMTP_HOST", "smtp.gmail.com"), env("SMTP_PORT", "465"),
            env("SMTP_USER"), env("SMTP_PASSWORD"), env("EMAIL_FROM") or env("SMTP_USER"),
        )
        email.__enter__()
    else:
        os.makedirs(PREVIEW_DIR, exist_ok=True)

    sent = failed = skipped = 0
    try:
        for sub in subscribers:
            addr = sub.get("email", "")
            radius = int(sub.get("radius") or 50)
            links = links_for(sub)
            point = resolve_point(sub, airports)
            try:
                if point is None:
                    subj = "TFR alerts: we couldn't find your home airport"
                    text = (f"We couldn't find the airport \"{sub.get('airport')}\". "
                            f"Open the app and set a valid ICAO/FAA code under Account: {links['app']}\n\n"
                            f"Unsubscribe: {links['unsubscribe']}")
                    body_html = "<pre>" + html.escape(text) + "</pre>"
                    matches = []
                else:
                    lat, lon, place = point
                    matches = matching.nearby(tfrs, lat, lon, radius)
                    if not matches and str(sub.get("only_when_tfrs")).lower() == "true":
                        skipped += 1
                        continue
                    subj = notify.subject(place, matches, radius)
                    text, body_html = notify.email_bodies(sub.get("name", ""), place, radius, matches, links)

                if args.dry_run:
                    safe = "".join(c if c.isalnum() else "_" for c in addr)
                    with open(os.path.join(PREVIEW_DIR, f"{safe}.html"), "w", encoding="utf-8") as f:
                        f.write(body_html)
                    print(f"[dry run] {addr}: {subj}")
                else:
                    email.send(addr, subj, text, body_html, links["unsubscribe"])

                if twilio and sub.get("phone") and str(sub.get("sms")).lower() == "true" and point:
                    # Sheets may turn "+12155550123" into the number 12155550123.
                    phone = str(sub["phone"]).split(".")[0]
                    phone = phone if phone.startswith("+") else "+" + phone
                    body = notify.sms_body(point[2], radius, matches, links)
                    if args.dry_run:
                        print(f"[dry run] SMS {phone}: {body}")
                    else:
                        twilio.send(phone, body)
                sent += 1
            except Exception as exc:  # keep going so one bad address doesn't block everyone else
                failed += 1
                print(f"error sending to {addr}: {exc}")
    finally:
        if email:
            email.__exit__(None, None, None)

    print(f"Done: {sent} sent, {skipped} skipped (no TFRs), {failed} failed")
    if failed and not sent:
        sys.exit(1)


if __name__ == "__main__":
    main()
