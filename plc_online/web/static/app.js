"use strict";

const REFRESH_MS = 2000;
const $ = (id) => document.getElementById(id);
let brands = {};
let timer = null;
let busy = false;

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ erreur: "Reponse illisible du serveur" }));
  if (!res.ok) throw Object.assign(new Error(data.erreur || "Erreur"), { status: res.status });
  return data;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function setPill(state, text) {
  const p = $("link-pill");
  p.className = "pill " + { on: "pill-on", off: "pill-off", err: "pill-err" }[state];
  p.textContent = text;
}

function setMsg(text, isError) {
  const m = $("connexion-msg");
  m.textContent = text || "";
  m.className = "msg" + (isError ? " err" : "");
}

function renderSettings() {
  const brand = brands[$("marque").value];
  const box = $("reglages");
  box.replaceChildren();
  for (const r of brand.reglages) {
    const label = el("label", "field");
    label.append(el("span", "", r.nom));
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.name = r.cle;
    input.value = localStorageGet("reglage-" + r.cle) ?? r.defaut;
    label.append(input);
    box.append(label);
  }
  $("hint-s7").hidden = $("marque").value !== "s7-300";
}

function localStorageGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function localStorageSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private window: ignore */ }
}

function renderStatus(s) {
  $("etat-vide").hidden = true;
  $("etat-plein").hidden = false;
  $("modele").textContent = s.modele || s.marque;
  $("reference").textContent = s.reference || "";

  const mode = $("mode");
  mode.textContent = s.mode;
  mode.className = "mode" + (s.mode === "RUN" ? " run" : s.mode === "STOP" ? " stop" : "");

  const alerts = $("alertes");
  alerts.replaceChildren(...s.alertes.map((a) => el("div", "alerte", a)));

  const leds = $("voyants");
  leds.replaceChildren(...s.voyants.map((v) => {
    const cls = ["voyant", v.allume && "on", v.clignote && "clignote", v.defaut && "defaut", v.nom === "STOP" && "stop"];
    const d = el("div", cls.filter(Boolean).join(" "));
    d.title = v.texte + (v.allume ? (v.clignote ? " (clignote)" : " (allume)") : " (eteint)");
    d.append(el("span", "led"), el("span", "nom", v.nom));
    if (v.texte !== v.nom) d.append(el("span", "texte", v.texte));
    return d;
  }));

  const dl = $("details");
  dl.replaceChildren(...s.details.flatMap(([k, v]) => [el("dt", "", k), el("dd", "", v)]));

  $("maj").textContent = "Mis a jour a " + new Date().toLocaleTimeString("fr-FR");
  const faults = s.alertes.length;
  setPill(faults ? "err" : "on", `${s.marque} — ${s.mode}${faults ? " — " + faults + " alerte(s)" : ""}`);
}

function setConnected(connected) {
  $("btn-deconnecter").hidden = !connected;
  $("btn-connecter").textContent = connected ? "Reconnecter" : "Connecter";
  if (!connected) {
    $("etat-vide").hidden = false;
    $("etat-plein").hidden = true;
    setPill("off", "Non connecte");
  }
}

function startPolling() {
  stopPolling();
  timer = setInterval(refresh, REFRESH_MS);
}
function stopPolling() {
  if (timer) clearInterval(timer);
  timer = null;
}

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const s = await api("GET", "/api/etat");
    if (!s.connecte) throw Object.assign(new Error("Pas connecte"), { status: 409 });
    renderStatus(s);
  } catch (e) {
    stopPolling();
    setConnected(false);
    if (e.status !== 409) {
      setPill("err", "Liaison perdue");
      setMsg(e.message, true);
    }
  } finally {
    busy = false;
  }
}

async function connect(ev) {
  ev.preventDefault();
  const marque = $("marque").value;
  const reglages = {};
  for (const input of $("reglages").querySelectorAll("input")) {
    reglages[input.name] = Number(input.value);
    localStorageSet("reglage-" + input.name, input.value);
  }
  localStorageSet("marque", marque);
  const btn = $("btn-connecter");
  btn.disabled = true;
  setMsg("Connexion...");
  stopPolling();
  try {
    renderStatus(await api("POST", "/api/connexion", { marque, reglages }));
    setConnected(true);
    setMsg("");
    startPolling();
  } catch (e) {
    setConnected(false);
    setPill("err", "Echec de connexion");
    setMsg(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function disconnect() {
  stopPolling();
  try { await api("POST", "/api/deconnexion"); } catch { /* already closed */ }
  setConnected(false);
  setMsg("Deconnecte.");
}

async function init() {
  brands = await api("GET", "/api/marques");
  const select = $("marque");
  for (const [key, b] of Object.entries(brands)) {
    const opt = el("option", "", b.nom + (b.disponible ? "" : " (bientot)"));
    opt.value = key;
    opt.disabled = !b.disponible;
    select.append(opt);
  }
  const saved = localStorageGet("marque");
  if (saved && brands[saved]?.disponible) select.value = saved;
  select.addEventListener("change", renderSettings);
  renderSettings();
  $("form-connexion").addEventListener("submit", connect);
  $("btn-deconnecter").addEventListener("click", disconnect);

  // already connected (page reloaded): show the state straight away
  const s = await api("GET", "/api/etat").catch(() => ({ connecte: false }));
  if (s.connecte) {
    renderStatus(s);
    setConnected(true);
    startPolling();
  } else {
    setConnected(false);
  }
}

init().catch((e) => setMsg("Serveur injoignable : " + e.message, true));
