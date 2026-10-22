/* TFR Alerts app: live TFR map + list around the pilot, and a passwordless account for morning alerts. */
(function () {
  "use strict";

  const SCRIPT_URL = (window.TFR_CONFIG && window.TFR_CONFIG.scriptUrl) || "";
  const LOOKAHEAD_MS = 48 * 3600 * 1000;
  const NM_TO_M = 1852;
  const $ = (id) => document.getElementById(id);

  const state = {
    tfrs: [], updated: null, airports: null,
    source: "here", here: null, home: null, radius: 50,
    token: null, account: null,
  };

  // ---- storage (may be unavailable in private mode) ---------------------------------------------
  const store = {
    get(k, fallback) { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  // ---- geometry (same method as the morning emails: tfr_alerts/geo.py) --------------------------
  function project(lat0, lon0, lat, lon) {
    return [(lon - lon0) * 60 * Math.cos(lat0 * Math.PI / 180), (lat - lat0) * 60];
  }
  function segDist(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    if (!dx && !dy) return Math.hypot(px - ax, py - ay);
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }
  function inside(pts) {
    let isIn = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > 0) !== (yj > 0) && 0 < (xj - xi) * (0 - yi) / (yj - yi) + xi) isIn = !isIn;
    }
    return isIn;
  }
  function distToPoly(lat, lon, poly) {
    const pts = poly.map(([a, b]) => project(lat, lon, a, b));
    if (pts.length >= 3 && inside(pts)) return 0;
    let best = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      best = Math.min(best, segDist(0, 0, a[0], a[1], b[0], b[1]));
    }
    return best;
  }
  function bearing(lat1, lon1, lat2, lon2) {
    const p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180, dl = (lon2 - lon1) * Math.PI / 180;
    const x = Math.sin(dl) * Math.cos(p2);
    const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    const deg = (Math.atan2(x, y) * 180 / Math.PI + 360) % 360;
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.floor((deg + 22.5) / 45) % 8];
  }
  function centroid(poly) {
    return [poly.reduce((s, p) => s + p[0], 0) / poly.length, poly.reduce((s, p) => s + p[1], 0) / poly.length];
  }
  function nmBetween(a, b) { return Math.hypot(...project(a.lat, a.lon, b.lat, b.lon)); }

  // ---- formatting -------------------------------------------------------------------------------
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function fmtTime(iso) {
    const d = new Date(iso);
    const year = d.getUTCFullYear() !== new Date().getUTCFullYear() ? ` ${d.getUTCFullYear()}` : "";
    const z = `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCDate()).padStart(2, "0")}${year} ` +
      `${String(d.getUTCHours()).padStart(2, "0")}${String(d.getUTCMinutes()).padStart(2, "0")}Z`;
    const local = d.toLocaleString([], { hour: "numeric", minute: "2-digit" });
    return `${z} (${local} local)`;
  }
  function when(t) {
    return `${t.effective ? fmtTime(t.effective) : "In effect"} to ${t.expires ? fmtTime(t.expires) : "until further notice"}`;
  }
  function ago(iso) {
    const mins = Math.round((Date.now() - new Date(iso)) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins} min ago`;
    return `${Math.round(mins / 60)} h ago`;
  }

  // ---- data -------------------------------------------------------------------------------------
  async function loadTfrs() {
    const res = await fetch("data/tfrs.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("tfrs.json " + res.status);
    const data = await res.json();
    state.tfrs = data.tfrs;
    state.updated = data.updated;
  }

  async function airports() {
    if (!state.airports) {
      const res = await fetch("data/airports.json");
      state.airports = await res.json();
    }
    return state.airports;
  }

  async function findAirport(code) {
    code = String(code || "").trim().toUpperCase();
    if (!code) return null;
    const db = await airports();
    const hit = db[code] || (code.length === 3 ? db["K" + code] : null);
    if (!hit) return null;
    return { code: hit[3] || code, lat: hit[0], lon: hit[1], name: hit[2] };
  }

  function nearby(center, radius) {
    const now = Date.now(), horizon = now + LOOKAHEAD_MS, out = [];
    for (const t of state.tfrs) {
      if (t.expires && new Date(t.expires) < now) continue;
      if (t.effective && new Date(t.effective) > horizon) continue;
      const dist = Math.min(...t.areas.map((p) => distToPoly(center.lat, center.lon, p)));
      if (dist > radius) continue;
      const big = t.areas.reduce((a, b) => (b.length > a.length ? b : a));
      const [clat, clon] = centroid(big);
      out.push({
        tfr: t, dist,
        dir: dist === 0 ? "OVERHEAD" : bearing(center.lat, center.lon, clat, clon),
        active: !t.effective || new Date(t.effective) <= now,
      });
    }
    return out.sort((a, b) => (b.active - a.active) || (a.dist - b.dist));
  }

  // ---- map --------------------------------------------------------------------------------------
  const map = L.map("map", { zoomControl: false, attributionControl: true }).setView([39.8, -98.6], 4);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  L.control.zoom({ position: "bottomright" }).addTo(map);
  const tfrLayer = L.layerGroup().addTo(map);
  const meLayer = L.layerGroup().addTo(map);
  const polysById = {};

  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function drawTfrs() {
    tfrLayer.clearLayers();
    const now = Date.now();
    for (const t of state.tfrs) {
      if (t.expires && new Date(t.expires) < now) continue;
      const active = !t.effective || new Date(t.effective) <= now;
      const color = active ? cssVar("--active") : cssVar("--upcoming");
      const group = L.featureGroup(t.areas.map((p) =>
        L.polygon(p, { color, weight: 2, fillOpacity: active ? 0.25 : 0.12, dashArray: active ? null : "6 4" })));
      group.bindPopup(`<b>${esc(t.id)} ${esc(t.type)}</b><br>${esc(t.description)}<br>` +
        `<a href="${esc(t.url)}" target="_blank" rel="noopener">Full NOTAM</a>`);
      group.addTo(tfrLayer);
      polysById[t.id] = group;
    }
  }

  function drawCenter(center) {
    meLayer.clearLayers();
    if (!center) return;
    const ring = L.circle([center.lat, center.lon], {
      radius: state.radius * NM_TO_M, color: cssVar("--accent"), weight: 1.5, fill: false, dashArray: "4 6",
    }).addTo(meLayer);
    if (state.source === "here") {
      L.marker([center.lat, center.lon], {
        icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [16, 16] }),
        title: "You are here",
      }).addTo(meLayer);
    } else {
      L.circleMarker([center.lat, center.lon], { radius: 6, color: cssVar("--accent"), fillOpacity: 1 })
        .bindTooltip(center.code).addTo(meLayer);
    }
    map.fitBounds(ring.getBounds(), { padding: [12, 12] });
  }

  // ---- feed -------------------------------------------------------------------------------------
  function currentCenter() { return state.source === "here" ? state.here : state.home; }

  function render() {
    const center = currentCenter();
    const status = $("status"), list = $("list");
    drawCenter(center);
    if (!center) {
      list.innerHTML = "";
      if (state.source === "home") {
        status.innerHTML = 'Set your home airport: <input id="home-input" type="text" maxlength="4" placeholder="KPNE" ' +
          'style="width:90px;padding:4px 8px"> <button class="text-btn" id="home-go">Show</button>';
        $("home-go").onclick = async () => setHome($("home-input").value, true);
      }
      return;
    }
    const matches = nearby(center, state.radius);
    const label = state.source === "here" ? "you" : center.code;
    const activeCount = matches.filter((m) => m.active).length;
    status.textContent = `${matches.length} TFR${matches.length === 1 ? "" : "s"} within ${state.radius} NM of ${label}` +
      (matches.length ? ` (${activeCount} active now)` : "") + (state.updated ? ` · updated ${ago(state.updated)}` : "");
    if (!matches.length) {
      list.innerHTML = '<li class="empty">No active or upcoming TFRs in the next 48 hours.<br>Still get your official briefing.</li>';
      return;
    }
    list.innerHTML = matches.map((m) => {
      const t = m.tfr;
      const where = m.dist === 0 ? "Overhead" : `${Math.round(m.dist)} NM ${m.dir}`;
      return `<li class="item${m.active ? " active" : ""}" data-id="${esc(t.id)}">
        <div class="item-top"><span class="item-title">${esc(t.id)} ${esc(t.type)}
          <span class="badge">${m.active ? "ACTIVE" : "UPCOMING"}</span></span><span class="dist">${esc(where)}</span></div>
        <p>${esc(t.description)}</p>
        <p class="meta">${esc(when(t))}<br>${esc(t.altitudes.join(", ") || "See NOTAM for altitudes")}</p>
        <p><a href="${esc(t.url)}" target="_blank" rel="noopener">Full NOTAM on tfr.faa.gov</a></p></li>`;
    }).join("");
  }

  $("list").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const item = e.target.closest(".item");
    const group = item && polysById[item.dataset.id];
    if (!group) return;
    map.fitBounds(group.getBounds(), { padding: [30, 30], maxZoom: 12 });
    group.openPopup();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  function setSource(src) {
    state.source = src;
    $("src-here").setAttribute("aria-selected", src === "here");
    $("src-home").setAttribute("aria-selected", src === "home");
    store.set("tfr.source", src);
    if (src === "here" && !state.here) locate(); else render();
  }
  $("src-here").onclick = () => setSource("here");
  $("src-home").onclick = () => setSource("home");
  $("radius").onchange = (e) => { state.radius = Number(e.target.value); store.set("tfr.radius", state.radius); render(); };

  async function setHome(code, persist) {
    const apt = await findAirport(code);
    if (!apt) { $("status").textContent = `Couldn't find airport "${code}".`; return null; }
    state.home = apt;
    if (persist) store.set("tfr.home", apt.code);
    render();
    return apt;
  }

  function locate() {
    if (!navigator.geolocation) { $("status").textContent = "This device can't share its location. Try Home airport."; return; }
    $("status").textContent = "Finding your location...";
    navigator.geolocation.getCurrentPosition((pos) => {
      state.here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      render();
      reportLocation(state.here);
    }, () => {
      $("status").textContent = "Location is off. Allow location for this app, or switch to Home airport.";
    }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 5 * 60 * 1000 });
  }

  // ---- account ----------------------------------------------------------------------------------
  async function api(payload) {
    if (!SCRIPT_URL) throw new Error("Accounts aren't set up on this site yet.");
    // text/plain keeps this a "simple" request, so Apps Script answers without a CORS preflight.
    const res = await fetch(SCRIPT_URL, { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (data.error === "signed_out") { signOut(); throw new Error("You were signed out. Please sign in again."); }
    return data;
  }

  function reportLocation(here) {
    if (!state.token || !state.account || !state.account.follow_location) return;
    const last = store.get("tfr.lastReport", null);
    // Only report when the pilot moved more than 2 NM or 3 hours have passed.
    if (last && nmBetween(last, here) < 2 && Date.now() - last.t < 3 * 3600 * 1000) return;
    api({ action: "location", token: state.token, lat: here.lat, lon: here.lon })
      .then((r) => { if (r.ok) store.set("tfr.lastReport", { lat: here.lat, lon: here.lon, t: Date.now() }); })
      .catch(() => { /* try again next time the app opens */ });
  }

  function msg(el, text, ok) { el.textContent = text; el.className = "msg " + (ok ? "ok" : "err"); }

  function showAccount(open) {
    $("account-view").hidden = !open;
    $("feed-view").hidden = open;
    $("signin-form").hidden = !!state.account;
    $("settings-form").hidden = !state.account;
    if (state.account) fillSettings();
    window.scrollTo(0, 0);
  }
  $("account-btn").onclick = () => showAccount(true);
  $("account-close").onclick = () => { showAccount(false); render(); };

  function fillSettings() {
    const a = state.account, f = $("settings-form");
    $("signed-in-as").textContent = `Signed in as ${a.email}`;
    f.alerts.checked = a.alerts;
    f.airport.value = a.airport || "";
    f.radius.value = String([25, 50, 75, 100].includes(a.radius) ? a.radius : 50);
    f.follow_location.checked = a.follow_location;
    f.only_when_tfrs.checked = a.only_when_tfrs;
    f.name.value = a.name || "";
    f.phone.value = (a.phone || "").replace(/^'?\+1/, "");
    f.sms.checked = a.sms;
    f.sms_consent.checked = a.sms;
    showAirportName();
  }

  async function showAirportName() {
    const code = $("st-airport").value;
    const apt = code.length >= 3 ? await findAirport(code) : null;
    $("airport-name").textContent = apt ? apt.name : code.length >= 3 ? "Airport not found" : "";
  }
  $("st-airport").addEventListener("input", showAirportName);

  function setAccount(account) {
    state.account = account;
    $("account-btn").classList.toggle("signed-in", !!account);
    if (account && account.airport) setHome(account.airport, false);
  }

  function signOut() {
    state.token = null;
    store.del("tfr.token");
    store.del("tfr.lastReport");
    setAccount(null);
    if (!$("account-view").hidden) showAccount(true);
  }
  $("signout").onclick = signOut;

  $("signin-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button"), out = $("signin-msg");
    btn.disabled = true;
    msg(out, "Sending...", true);
    try {
      const r = await api({ action: "signin", email: $("si-email").value, website: e.target.website.value });
      msg(out, r.ok ? r.message : r.error, r.ok);
    } catch (err) {
      msg(out, err.message || "Couldn't reach the server. Try again.", false);
    } finally {
      btn.disabled = false;
    }
  });

  $("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, btn = f.querySelector("button.primary"), out = $("settings-msg");
    const apt = await findAirport(f.airport.value);
    if (!apt) return msg(out, "Enter a valid home airport, like KPNE or PNE.", false);
    btn.disabled = true;
    msg(out, "Saving...", true);
    try {
      const r = await api({
        action: "save", token: state.token, airport: apt.code, radius: Number(f.radius.value),
        alerts: f.alerts.checked, follow_location: f.follow_location.checked, only_when_tfrs: f.only_when_tfrs.checked,
        name: f.name.value, phone: f.phone.value, sms: f.sms.checked, sms_consent: f.sms_consent.checked,
      });
      if (r.ok) {
        setAccount(r.account);
        store.del("tfr.lastReport");
        if (state.here) reportLocation(state.here);
        msg(out, r.account.alerts ? "Saved. Your brief arrives every morning." : "Saved. Morning brief is off.", true);
      } else {
        msg(out, r.error, false);
      }
    } catch (err) {
      msg(out, err.message || "Couldn't reach the server. Try again.", false);
    } finally {
      btn.disabled = false;
    }
  });

  async function initAccount() {
    const m = location.hash.match(/login=([\w-]+)\.([\w-]+)/);
    if (m) {
      history.replaceState(null, "", location.pathname + location.search);
      try {
        const r = await api({ action: "verify", token: m[1], nonce: m[2] });
        if (r.ok) {
          state.token = m[1];
          store.set("tfr.token", m[1]);
          setAccount(r.account);
          showAccount(true);
          msg($("settings-msg"), r.account.airport ? "Signed in." : "Signed in! Set your home airport and turn on the morning brief.", true);
          if (state.here) reportLocation(state.here);
        } else {
          showAccount(true);
          msg($("signin-msg"), r.error, false);
        }
      } catch (err) {
        showAccount(true);
        msg($("signin-msg"), err.message, false);
      }
      return;
    }
    state.token = store.get("tfr.token", null);
    if (state.token) {
      try {
        const r = await api({ action: "get", token: state.token });
        if (r.ok) { setAccount(r.account); if (state.here) reportLocation(state.here); }
      } catch (err) { /* offline: keep the token and try next time */ }
    }
  }

  // ---- install prompt ---------------------------------------------------------------------------
  function initInstall() {
    const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    if (standalone || store.get("tfr.installDismissed", false)) return;
    const box = $("install");
    const dismiss = '<button class="text-btn" id="install-x" style="background:none;color:var(--muted)">Not now</button>';
    const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    if (ios) {
      box.innerHTML = `<span>Install: tap <b>Share</b>, then <b>Add to Home Screen</b>.</span>${dismiss}`;
      box.hidden = false;
    }
    window.addEventListener("beforeinstallprompt", (e) => {
      e.preventDefault();
      box.innerHTML = `<span>Install TFR Alerts on your phone.</span><span><button id="install-go">Install</button> ${dismiss}</span>`;
      box.hidden = false;
      $("install-go").onclick = () => { e.prompt(); box.hidden = true; };
      $("install-x").onclick = hide;
    });
    function hide() { box.hidden = true; store.set("tfr.installDismissed", true); }
    if (!box.hidden) $("install-x").onclick = hide;
  }

  // ---- start ------------------------------------------------------------------------------------
  async function start() {
    state.radius = store.get("tfr.radius", 50);
    $("radius").value = String(state.radius);
    initInstall();
    try {
      await loadTfrs();
    } catch (err) {
      $("status").textContent = "Couldn't load TFR data. Check your connection and reopen the app.";
      return;
    }
    drawTfrs();
    const savedHome = store.get("tfr.home", null);
    if (savedHome) setHome(savedHome, false);
    await initAccount();
    setSource(store.get("tfr.source", "here"));
    // Refresh when the app comes back to the foreground.
    document.addEventListener("visibilitychange", async () => {
      if (document.visibilityState !== "visible") return;
      try { await loadTfrs(); drawTfrs(); } catch (e) { return; }
      if (state.source === "here") locate(); else render();
    });
  }

  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  start();
})();
