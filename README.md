# TFR Alerts

A free app for pilots, plus a morning TFR brief by email (and SMS later):

- **Live TFR map and list.** Open the app and it finds where you are and shows the TFRs within your radius:
  active ones in red, upcoming ones (next 48 h) in amber, with distance, direction, times and altitudes.
  You can switch to your home airport instead.
- **Account with no password.** Pilots sign in with a one-time email link.
- **Morning brief.** Every morning, signed-in pilots who turned it on get an email listing TFRs around their home
  airport. With **Follow me** on, the brief uses wherever they last opened the app instead, falling back to the
  home airport after 3 days.
- **Installable.** On iPhone: Share, then Add to Home Screen. On Android: the Install prompt. It opens like an app
  and works offline with the last data it loaded. No App Store fees.

Data comes from the FAA's public feed at tfr.faa.gov. Everything runs on free services:

| Piece | Runs on | Files |
|---|---|---|
| The app | GitHub Pages | `docs/` (index.html, app.js, app.css, sw.js, manifest) |
| Live TFR data for the app, refreshed every 30 min | GitHub Actions | `tfr_alerts/publish.py`, `.github/workflows/publish-app.yml` |
| Accounts, sign-in links, settings, last location | Google Sheet + Apps Script | `apps_script/Code.gs` |
| Morning brief: fetch, match, send | GitHub Actions (daily 10:00 UTC) | `tfr_alerts/`, `.github/workflows/daily-alerts.yml` |
| Email | Gmail SMTP (about 500/day on a free account) | `tfr_alerts/notify.py` |
| SMS (optional, about $0.01/text) | Twilio | `tfr_alerts/notify.py` |

## Try it locally

Print the TFRs near one airport:

```bash
python -m tfr_alerts --airport KPNE
```

Build every morning email for the sample pilots without sending anything (written to `previews/`):

```bash
python -m tfr_alerts --dry-run --subscribers sample_subscribers.json
```

Build the app with live data, then open http://localhost:8765:

```bash
python -m tfr_alerts.publish --out _site
```

```bash
python -m http.server 8765 --directory _site
```

Tests: `python -m unittest discover -s tests -t .`

## Setup (about 30 minutes, one time)

### 1. Make a Gmail account for sending
Create a dedicated Gmail account (for example `tfralerts.yourname@gmail.com`). Turn on 2-Step Verification,
then create an **App Password** at myaccount.google.com/apppasswords. You'll paste it into GitHub in step 4.

### 2. GitHub repo and app hosting
1. Create a free GitHub account if you don't have one, then a new **public** repository named `tfr-alerts`
   (scheduled Actions are free and unlimited for public repos). Upload this whole folder.
2. **Settings > Pages > Build and deployment > Source: GitHub Actions.**
3. Your app's address will be `https://<username>.github.io/tfr-alerts/`. You need it in the next step.

No secrets live in the code, so a public repo is safe. All keys go in GitHub Secrets.

### 3. Google Sheet + Apps Script (accounts)
1. While signed in to the sending Gmail account, create a new Google Sheet named "TFR Alerts".
2. **Extensions > Apps Script**. Delete the sample code and paste in everything from `apps_script/Code.gs`.
3. **Project Settings (gear) > Script Properties**, add:
   - `API_KEY`: a long random string (40 or more characters; a password generator works)
   - `APP_URL`: your app's address from step 2
4. **Deploy > New deployment > Web app**. Execute as: **Me**. Who has access: **Anyone**. Deploy and approve
   the permissions. Copy the **Web app URL** (it ends in `/exec`).

When you change `Code.gs` later, use **Deploy > Manage deployments > Edit > New version** so the URL stays the same.

### 4. GitHub secrets and variables
Go to **Settings > Secrets and variables > Actions**.

Secrets:
- `SCRIPT_URL`: the Apps Script web app URL
- `SCRIPT_API_KEY`: the API_KEY from step 3
- `SMTP_USER`: the sending Gmail address
- `SMTP_PASSWORD`: the Gmail App Password

Variables (Variables tab):
- `APP_URL`: your app's address
- `EMAIL_FROM`: the sender name pilots see, for example `GQ Aviation TFR Alerts <gqaviation.alerts@gmail.com>`

### 5. Publish and test
1. **Actions > Publish app > Run workflow.** After about a minute the app is live at your address. It then
   refreshes TFR data every 30 minutes on its own.
2. Open the app on your phone, allow location, and check the map. Tap the account icon, sign in with your
   email, set your home airport, and turn on the morning brief.
3. **Actions > Daily TFR alerts > Run workflow** with "Dry run" checked. The log lists who would get what.
4. Run it again with "Dry run" unchecked to send a real email. After that it runs every morning.

## Turning on SMS later (Twilio)
1. Create a Twilio account, buy a local number (about $1.15/month), and complete **A2P 10DLC** registration
   (one-time fee of about $20, takes a few days to a few weeks). US carriers block unregistered texts.
   Use your app's address and the consent wording on its Account screen in the registration.
2. Add secrets `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` (for example `+12155550123`).
3. That's all. Pilots who entered a mobile number and ticked "Also text me" plus the consent box get one text
   each morning. Twilio handles STOP replies automatically.

## How it works
- `faa.py` downloads the TFR list, then each TFR's XML detail file with its exact boundary, altitudes and times
  (always UTC).
- `matching.py` (and the same math in `docs/app.js`) measures the shortest distance from the pilot to each TFR
  boundary (0 if inside) and keeps TFRs that are active now or start within 48 hours.
- `publish.py` writes `data/tfrs.json` and `data/airports.json` for the app. The FAA feed blocks direct browser
  access, so the app reads these copies.
- The app reports the pilot's location to their account only when Follow me is on, it is signed in, and the pilot
  has moved more than 2 NM or 3 hours have passed. The location is rounded to about 100 m.

## Limits to know
- **Location only updates when the app is opened.** Web apps can't track location in the background. Pilots
  open the app at the airport and the next brief follows them. Background tracking needs a native App Store /
  Play Store app.
- **Data age.** The app's data is at most about 30 minutes old (GitHub can delay scheduled runs). The morning email
  only shows TFRs issued before it was sent. Both say clearly that this isn't an official briefing.
- **Sign-in tokens.** The app keeps the account token on the phone, and the unsubscribe link in each email carries
  it. Anyone holding an alert email could change that pilot's alert settings. That's acceptable for TFR
  preferences, but don't add sensitive data to accounts without moving to proper sessions.
- **Email caps.** Gmail allows about 500 recipients/day. Apps Script `MailApp` (sign-in emails) allows 100/day on a
  free account. Past that, switch SMTP to Brevo (300/day free) or Amazon SES (pennies) by changing the `SMTP_*`
  settings.
