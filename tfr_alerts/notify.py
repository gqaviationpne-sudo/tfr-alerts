"""Build and send the morning alert by email and (optionally) SMS."""
import base64
import html
import json
import smtplib
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

BRAND = "GQ Aviation"

DISCLAIMER = (
    "Situational awareness only - not an official briefing. TFRs can be issued at any time. "
    "Always get an official briefing (1800wxbrief.com / 1-800-WX-BRIEF) before flight."
)


def _when(tfr):
    # The FAA's dateEffective/dateExpire are always UTC; codeTimeZone only names the local zone.
    this_year = datetime.now(timezone.utc).year

    def fmt(dt):
        return dt.strftime("%b %d %H%MZ" if dt.year == this_year else "%b %d %Y %H%MZ")

    start = fmt(tfr.effective) if tfr.effective else "In effect"
    end = fmt(tfr.expires) if tfr.expires else "until further notice"
    return f"{start} - {end}"


def _where(match):
    if match.distance_nm == 0:
        return "OVER your location"
    return f"{match.distance_nm:.0f} NM {match.bearing}"


def subject(place, matches, radius):
    if not matches:
        return f"TFR brief {place}: none within {radius} NM"
    active = sum(m.active_now for m in matches)
    return f"TFR brief {place}: {len(matches)} within {radius} NM ({active} active now)"


def email_bodies(name, place, radius, matches, links):
    """Returns (plain_text, html) for the morning email."""
    greet = f"Good morning{', ' + name if name else ''}."
    lines = [greet, "", f"TFRs within {radius} NM of {place} (now through the next 48 hours):", ""]
    if not matches:
        lines.append("None found. Have a good flight - and still get your official briefing.")
    for m in matches:
        t = m.tfr
        status = "ACTIVE NOW" if m.active_now else "UPCOMING"
        lines += [
            f"* {t.notam_id} {t.type} [{status}] - {_where(m)}",
            f"  {t.description}",
            f"  When: {_when(t)}",
            f"  Altitude: {', '.join(dict.fromkeys(t.altitudes)) or 'see NOTAM'}",
            f"  Details: {t.url}",
            "",
        ]
    lines += [
        "",
        DISCLAIMER,
        "",
        f"Live TFR map around you: {links['app']}",
        f"Unsubscribe: {links['unsubscribe']}",
        "",
        f"A free service from {BRAND}.",
    ]
    text = "\n".join(lines)

    e = html.escape
    cards = []
    for m in matches:
        t = m.tfr
        color = "#b42318" if m.active_now else "#b54708"
        status = "ACTIVE NOW" if m.active_now else "UPCOMING"
        cards.append(f"""
<div style="border:1px solid #ddd;border-left:4px solid {color};border-radius:6px;padding:12px 14px;margin:0 0 12px">
  <div style="font-weight:600">{e(t.notam_id)} &middot; {e(t.type)}
    <span style="color:{color};font-size:12px;margin-left:6px">{status}</span></div>
  <div style="font-size:14px;margin:4px 0">{e(_where(m))} &middot; {e(t.description)}</div>
  <div style="font-size:13px;color:#555">When: {e(_when(t))}<br>
    Altitude: {e(', '.join(dict.fromkeys(t.altitudes)) or 'see NOTAM')}</div>
  <div style="font-size:13px;margin-top:6px"><a href="{e(t.url)}">Full NOTAM on tfr.faa.gov</a></div>
</div>""")
    if not cards:
        cards.append('<p style="font-size:15px">None found. Have a good flight &mdash; and still get your official briefing.</p>')

    body_html = f"""<!doctype html><html><body style="margin:0;background:#f6f7f9">
<div style="max-width:560px;margin:0 auto;padding:20px 16px;font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#1d2433">
  <p style="margin:0 0 4px">{e(greet)}</p>
  <h2 style="margin:0 0 16px;font-size:18px">TFRs within {radius} NM of {e(place)}</h2>
  {''.join(cards)}
  <p style="font-size:12px;color:#666;margin-top:20px">{e(DISCLAIMER)}</p>
  <p style="font-size:12px;color:#666">
    <a href="{e(links['app'])}">Open the live TFR map</a> &middot;
    <a href="{e(links['unsubscribe'])}">Unsubscribe</a></p>
  <p style="font-size:12px;color:#666">A free service from {e(BRAND)}.</p>
</div></body></html>"""
    return text, body_html


def sms_body(place, radius, matches, links):
    if not matches:
        return f"TFR brief {place}: no TFRs within {radius}NM. Get an official briefing before flight. Reply STOP to opt out."
    parts = []
    for m in matches[:4]:
        flag = "ACTIVE" if m.active_now else "upcoming"
        parts.append(f"{m.tfr.notam_id} {m.tfr.type} {_where(m)} ({flag})")
    if len(matches) > 4:
        parts.append(f"+{len(matches) - 4} more")
    return (f"TFR brief {place} ({radius}NM): " + "; ".join(parts) +
            ". Details: tfr.faa.gov. Not an official briefing. Reply STOP to opt out.")


class EmailSender:
    def __init__(self, host, port, user, password, from_addr):
        self.host, self.port = host, int(port)
        # Gmail shows app passwords as "abcd efgh ijkl mnop"; stray spaces or newlines from pasting break login.
        self.user = "".join((user or "").split())
        self.password = "".join((password or "").split())
        self.from_addr = from_addr or self.user
        self._smtp = None

    def _diagnose(self):
        print(f"SMTP check: user {'looks like an email' if '@' in self.user else 'is EMPTY or not an email'}, "
              f"password is {len(self.password)} characters (a Gmail app password is 16)")

    def __enter__(self):
        if not self.user or not self.password:
            self._diagnose()
            raise RuntimeError("SMTP_USER and SMTP_PASSWORD secrets must both be set")
        try:
            self._smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=30)
            self._smtp.login(self.user, self.password)
        except smtplib.SMTPServerDisconnected:
            # Some networks drop port 465; retry on 587 with STARTTLS before giving up.
            print("SMTP: port 465 closed the connection, retrying on 587")
            try:
                self._smtp = smtplib.SMTP(self.host, 587, timeout=30)
                self._smtp.starttls()
                self._smtp.login(self.user, self.password)
            except smtplib.SMTPException:
                self._diagnose()
                raise
        except smtplib.SMTPException:
            self._diagnose()
            raise
        return self

    def __exit__(self, *exc):
        if self._smtp:
            self._smtp.quit()

    def send(self, to, subj, text, body_html, unsubscribe_url):
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = subj
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg.set_content(text)
        msg.add_alternative(body_html, subtype="html")
        self._smtp.send_message(msg)


class TwilioSender:
    def __init__(self, account_sid, auth_token, from_number):
        self.sid, self.token, self.from_number = account_sid, auth_token, from_number

    def send(self, to, body):
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        data = urllib.parse.urlencode({"From": self.from_number, "To": to, "Body": body}).encode()
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        req = urllib.request.Request(url, data=data, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
