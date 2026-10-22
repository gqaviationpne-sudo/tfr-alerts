/**
 * TFR Alerts backend - Google Apps Script bound to a Google Sheet.
 *
 * Accounts are passwordless: a pilot enters their email, gets a one-time sign-in link, and the app keeps
 * the account token on their phone. Requests (JSON POST bodies):
 *   {action:"signin", email}                      email a sign-in link (creates the account if new)
 *   {action:"verify", token, nonce}               finish sign-in from that link; returns the account
 *   {action:"get", token}                         read account settings
 *   {action:"save", token, airport, radius, ...}  save settings and turn morning alerts on/off
 *   {action:"location", token, lat, lon}          app reports where the pilot is ({clear:true} to reset)
 * GET links:
 *   ?action=unsubscribe&token=..                  one-tap unsubscribe from every alert
 *   ?action=list&key=API_KEY                      active subscribers, read by the daily GitHub Action
 *
 * Script Properties (Project Settings > Script Properties):
 *   API_KEY   long random string, also stored as the SCRIPT_API_KEY GitHub secret
 *   APP_URL   the app's address, e.g. https://yourname.github.io/tfr-alerts/
 */

const SHEET_NAME = 'Subscribers';
const HEADERS = ['email', 'name', 'phone', 'airport', 'lat', 'lon', 'location_updated', 'radius',
                 'sms', 'only_when_tfrs', 'follow_location', 'status', 'token', 'pending', 'created', 'updated'];
const SERVICE_NAME = 'GQ Aviation TFR Alerts';
const RESEND_WAIT_MS = 60 * 1000;
const LINK_VALID_MS = 60 * 60 * 1000;

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ok: false, error: 'Bad request'});
  }
  const handlers = {signin: signin_, verify: verify_, get: getAccount_, save: save_, location: updateLocation_};
  const handler = handlers[body.action];
  if (!handler) return json_({ok: false, error: 'Unknown action'});
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    return json_(handler(body));
  } catch (err) {
    console.error(err);
    return json_({ok: false, error: 'Something went wrong. Please try again.'});
  } finally {
    lock.releaseLock();
  }
}

function doGet(e) {
  const p = e.parameter || {};
  if (p.action === 'list') return json_(list_(p.key));
  if (p.action === 'unsubscribe') {
    const lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      return page_(unsubscribe_(p.token));
    } finally {
      lock.releaseLock();
    }
  }
  return page_('Nothing to see here.');
}

// ---- actions ---------------------------------------------------------------

function signin_(b) {
  if (b.website) return {ok: true, message: 'Check your email for a sign-in link.'};  // honeypot
  const email = String(b.email || '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254) {
    return {ok: false, error: 'Please enter a valid email address.'};
  }
  const appUrl = PropertiesService.getScriptProperties().getProperty('APP_URL');
  if (!appUrl) return {ok: false, error: 'The app is not set up yet (APP_URL missing).'};

  const sheet = sheet_();
  const now = new Date();
  let row = findRow_(sheet, 'email', email);
  if (!row) {
    const init = {email: email, status: 'pending', token: Utilities.getUuid(), radius: 50,
                  follow_location: true, created: now, updated: now};
    sheet.appendRow(HEADERS.map(h => (h in init ? init[h] : '')));
    row = findRow_(sheet, 'email', email);
  }
  const rec = read_(sheet, row);
  const old = rec.pending ? JSON.parse(rec.pending) : null;
  if (old && now - new Date(old.sent) < RESEND_WAIT_MS) {
    return {ok: true, message: 'We just sent you a link. Check your inbox (and spam).'};
  }
  const nonce = Utilities.getUuid();
  write_(sheet, row, {pending: JSON.stringify({nonce: nonce, sent: now.toISOString()}), updated: now});

  const link = appUrl.replace(/\/?$/, '/') + '#login=' + rec.token + '.' + nonce;
  MailApp.sendEmail({
    to: email,
    name: SERVICE_NAME,
    subject: 'Your ' + SERVICE_NAME + ' sign-in link',
    htmlBody:
      '<p>Tap below to sign in to ' + SERVICE_NAME + '. The link works once, for the next hour.</p>' +
      '<p><a href="' + link + '" style="background:#1d4ed8;color:#fff;padding:10px 16px;border-radius:6px;' +
      'text-decoration:none;display:inline-block">Sign in</a></p>' +
      '<p style="color:#666;font-size:12px">If you didn\'t ask for this, ignore this email.</p>',
  });
  return {ok: true, message: 'Check your email and tap the sign-in link.'};
}

function verify_(b) {
  const sheet = sheet_();
  const row = b.token && findRow_(sheet, 'token', String(b.token));
  if (!row) return {ok: false, error: 'That sign-in link is not valid.'};
  const rec = read_(sheet, row);
  const pending = rec.pending ? JSON.parse(rec.pending) : null;
  if (!pending || pending.nonce !== b.nonce || new Date() - new Date(pending.sent) > LINK_VALID_MS) {
    return {ok: false, error: 'That sign-in link has expired or was already used. Request a new one.'};
  }
  const fields = {pending: '', updated: new Date()};
  if (rec.status === 'pending' || rec.status === 'unsubscribed') fields.status = 'paused';
  write_(sheet, row, fields);
  return {ok: true, account: account_(read_(sheet, row))};
}

function getAccount_(b) {
  const found = byToken_(b.token);
  if (!found) return {ok: false, error: 'signed_out'};
  return {ok: true, account: account_(found.rec)};
}

function save_(b) {
  const found = byToken_(b.token);
  if (!found) return {ok: false, error: 'signed_out'};
  const airport = String(b.airport || '').trim().toUpperCase();
  if (!/^[A-Z0-9]{3,4}$/.test(airport)) {
    return {ok: false, error: 'Home airport should be an ICAO or FAA code like KPNE or PNE.'};
  }
  const radius = Math.round(Number(b.radius) || 50);
  if (radius < 10 || radius > 150) return {ok: false, error: 'Radius must be between 10 and 150 NM.'};
  const phone = normalizePhone_(b.phone);
  if (b.phone && !phone) return {ok: false, error: 'Please enter a US mobile number, e.g. 215-555-0123.'};
  if (phone && b.sms && !b.sms_consent) return {ok: false, error: 'Please tick the box to agree to texts.'};

  write_(found.sheet, found.row, {
    name: String(b.name || '').trim().slice(0, 60),
    phone: phone ? "'" + phone : '',  // leading ' keeps Sheets from turning it into a number
    airport: airport,
    radius: radius,
    sms: !!(phone && b.sms && b.sms_consent),
    only_when_tfrs: !!b.only_when_tfrs,
    follow_location: !!b.follow_location,
    status: b.alerts ? 'active' : 'paused',
    updated: new Date(),
  });
  return {ok: true, account: account_(read_(found.sheet, found.row)), message: 'Saved.'};
}

function updateLocation_(b) {
  const found = byToken_(b.token);
  if (!found) return {ok: false, error: 'signed_out'};
  if (b.clear) {
    write_(found.sheet, found.row, {lat: '', lon: '', location_updated: '', updated: new Date()});
    return {ok: true};
  }
  const lat = Number(b.lat), lon = Number(b.lon);
  if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    return {ok: false, error: 'Could not read your location.'};
  }
  write_(found.sheet, found.row, {lat: lat.toFixed(3), lon: lon.toFixed(3), location_updated: new Date()});
  return {ok: true};
}

function unsubscribe_(token) {
  const found = byToken_(token);
  if (!found) return 'That link is not valid.';
  write_(found.sheet, found.row, {status: 'unsubscribed', updated: new Date()});
  return 'You have been unsubscribed from the morning alerts. You can still use the app. Fly safe!';
}

function list_(key) {
  const expected = PropertiesService.getScriptProperties().getProperty('API_KEY');
  if (!expected || key !== expected) return {error: 'unauthorized'};
  const values = sheet_().getDataRange().getValues();
  const head = values[0];
  const subs = values.slice(1)
    .map(r => Object.fromEntries(head.map((h, i) => [h, r[i]])))
    .filter(s => s.status === 'active')
    .map(s => ({email: s.email, name: s.name, phone: String(s.phone || ''), airport: s.airport,
                lat: s.follow_location ? s.lat : '', lon: s.follow_location ? s.lon : '',
                location_updated: s.location_updated instanceof Date ? s.location_updated.toISOString() : '',
                radius: s.radius, sms: s.sms, only_when_tfrs: s.only_when_tfrs, token: s.token}));
  return {subscribers: subs};
}

// ---- helpers ---------------------------------------------------------------

function account_(rec) {
  return {
    email: rec.email, name: rec.name, phone: String(rec.phone || ''), airport: rec.airport,
    radius: Number(rec.radius) || 50, sms: rec.sms === true, only_when_tfrs: rec.only_when_tfrs === true,
    follow_location: rec.follow_location === true, alerts: rec.status === 'active',
  };
}

function byToken_(token) {
  if (!token) return null;
  const sheet = sheet_();
  const row = findRow_(sheet, 'token', String(token));
  if (!row) return null;
  const rec = read_(sheet, row);
  if (rec.status === 'pending') return null;  // email never verified
  return {sheet: sheet, row: row, rec: rec};
}

function normalizePhone_(raw) {
  const digits = String(raw || '').replace(/\D/g, '');
  if (digits.length === 10) return '+1' + digits;
  if (digits.length === 11 && digits[0] === '1') return '+' + digits;
  return '';
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function findRow_(sheet, column, value) {
  const col = HEADERS.indexOf(column) + 1;
  const last = sheet.getLastRow();
  if (last < 2) return null;
  const values = sheet.getRange(2, col, last - 1, 1).getValues();
  for (let i = 0; i < values.length; i++) {
    if (String(values[i][0]).toLowerCase() === String(value).toLowerCase()) return i + 2;
  }
  return null;
}

function read_(sheet, row) {
  const r = sheet.getRange(row, 1, 1, HEADERS.length).getValues()[0];
  return Object.fromEntries(HEADERS.map((h, i) => [h, r[i]]));
}

function write_(sheet, row, fields) {
  Object.keys(fields).forEach(k => {
    const col = HEADERS.indexOf(k) + 1;
    if (col > 0) sheet.getRange(row, col).setValue(fields[k]);
  });
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function page_(message) {
  return HtmlService.createHtmlOutput(
    '<div style="font-family:Arial,sans-serif;max-width:480px;margin:60px auto;padding:0 16px;text-align:center">' +
    '<h2>' + SERVICE_NAME + '</h2><p style="font-size:17px">' + message + '</p></div>'
  ).setTitle(SERVICE_NAME).addMetaTag('viewport', 'width=device-width, initial-scale=1');
}
