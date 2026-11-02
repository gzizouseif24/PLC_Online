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

let currentTab = localStorageGet("onglet") || "etat-plein";

function showTab(id) {
  if (!$(id)) id = "etat-plein";
  currentTab = id;
  localStorageSet("onglet", id);
  for (const t of document.querySelectorAll(".tab")) {
    const on = t.dataset.tab === id;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on);
  }
  for (const v of document.querySelectorAll(".vue")) v.hidden = v.id !== id;
  if (id === "vue-diag" && !diagLoaded) loadDiagnostic();
  if (id === "vue-watch") startWatch(); else stopWatch();
}

// ---- watch table ----

const WATCH_MS = 1000;
let watch = loadWatch();          // [{adresse, libelle}]
let watchTimer = null;
let watchBusy = false;
let watchPaused = false;
const lastValues = {};

function loadWatch() {
  try { return JSON.parse(localStorageGet("table-surveillance")) || []; } catch { return []; }
}
function saveWatch() {
  localStorageSet("table-surveillance", JSON.stringify(watch));
}

function setWatchMsg(text, isError) {
  const m = $("watch-msg");
  m.textContent = text || "";
  m.className = "msg" + (isError ? " err" : "");
}

function renderWatchRows() {
  const tbody = $("table-watch").tBodies[0];
  if (!watch.length) {
    const tr = el("tr");
    const td = cell("Ajoutez une adresse (ex. MW10) ou importez une table.", "empty");
    td.colSpan = 5;
    tr.append(td);
    tbody.replaceChildren(tr);
    return;
  }
  tbody.replaceChildren(...watch.map((w, i) => {
    const tr = el("tr");
    tr.dataset.adresse = w.adresse;
    const label = el("input", "libelle");
    label.value = w.libelle || "";
    label.placeholder = "libelle";
    label.addEventListener("change", () => { w.libelle = label.value; saveWatch(); });
    const value = el("span", "valeur", lastValues[w.adresse] ?? "…");
    const input = el("input", "nouvelle");
    input.placeholder = "—";
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") writeValues(); });
    const del = el("button", "icon-btn", "×");
    del.type = "button";
    del.title = "Retirer " + w.adresse;
    del.addEventListener("click", () => { watch.splice(i, 1); saveWatch(); renderWatchRows(); });
    tr.append(cell(w.adresse, "mono", "Adresse"), cell(label, "", "Libelle"), cell(value, "", "Valeur"),
              cell(input, "", "Nouvelle"), cell(del, "", ""));
    return tr;
  }));
}

function renderWatchValues(r) {
  for (const tr of $("table-watch").tBodies[0].rows) {
    const a = tr.dataset.adresse;
    if (!a) continue;
    const span = tr.querySelector(".valeur");
    if (a in r.erreurs) {
      span.textContent = r.erreurs[a];
      span.className = "valeur erreur";
    } else if (a in r.valeurs) {
      const v = r.valeurs[a];
      const changed = a in lastValues && lastValues[a] !== v;
      span.textContent = v;
      span.className = "valeur" + (v === "1" ? " bit1" : "");
      if (changed) { void span.offsetWidth; span.classList.add("changed"); }
      lastValues[a] = v;
    }
  }
  $("watch-maj").textContent = (watchPaused ? "En pause — " : "") + "Lu a " + new Date().toLocaleTimeString("fr-FR");
}

async function readWatch() {
  if (watchBusy || !watch.length) return;
  watchBusy = true;
  try {
    renderWatchValues(await api("POST", "/api/valeurs", { adresses: watch.map((w) => w.adresse) }));
  } catch (e) {
    setWatchMsg(e.message, true);
    if (e.status === 409 || e.status === 502) { stopWatch(); stopPolling(); setConnected(false); }
  } finally {
    watchBusy = false;
  }
}

function startWatch() {
  stopWatch();
  renderWatchRows();
  if (watchPaused) return;
  readWatch();
  watchTimer = setInterval(readWatch, WATCH_MS);
}
function stopWatch() {
  if (watchTimer) clearInterval(watchTimer);
  watchTimer = null;
}

function addWatch(adresse, libelle) {
  adresse = adresse.trim();
  if (!adresse) return false;
  if (watch.some((w) => w.adresse.toUpperCase() === adresse.toUpperCase())) {
    setWatchMsg(adresse + " est deja dans la table.", true);
    return false;
  }
  watch.push({ adresse, libelle: (libelle || "").trim() });
  return true;
}

async function writeValues() {
  const items = [];
  for (const tr of $("table-watch").tBodies[0].rows) {
    const input = tr.querySelector("input.nouvelle");
    if (input && input.value.trim() !== "") items.push({ adresse: tr.dataset.adresse, valeur: input.value.trim(), input });
  }
  if (!items.length) {
    setWatchMsg("Tapez une nouvelle valeur dans la colonne de droite, puis Ecrire.", true);
    return;
  }
  const lines = items.map((it) => `  ${it.adresse} : ${lastValues[it.adresse] ?? "?"}  ->  ${it.valeur}`).join("\n");
  if (!confirm(`Ecrire dans l'automate ?\n\n${lines}\n\nMachine en securite ?`)) {
    setWatchMsg("Annule, rien n'a ete ecrit.");
    return;
  }
  try {
    const r = await api("POST", "/api/ecrire", { ecritures: items.map(({ adresse, valeur }) => ({ adresse, valeur })) });
    const bad = r.resultats.filter((x) => !x.ok);
    for (const it of items) if (!bad.some((b) => b.adresse === it.adresse)) it.input.value = "";
    let text = bad.length
      ? "Relu different : " + bad.map((b) => `${b.adresse} = ${b.relu ?? "?"}`).join(", ") + " (reecrit par le programme ?)"
      : `${r.resultats.length} valeur(s) ecrite(s) et relue(s).`;
    const io = items.some((it) => /^[EIAQ]/i.test(it.adresse) && !/^DB/i.test(it.adresse));
    if (r.run && io) text += " CPU en RUN : les entrees / sorties sont reecrites a chaque cycle.";
    else if (r.run) text += " CPU en RUN : le programme peut reecrire ces valeurs.";
    setWatchMsg(text, bad.length > 0);
    readWatch();
  } catch (e) {
    setWatchMsg(e.message, true);
  }
}

function exportWatch() {
  const text = "# Table de surveillance PLC Online\n" + watch.map((w) => (w.adresse + " " + (w.libelle || "")).trim()).join("\n") + "\n";
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = "table_surveillance.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function importWatch(file) {
  const text = await file.text();
  let added = 0;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.split("#")[0].trim();
    if (!line) continue;
    const [adresse, ...rest] = line.split(/\s+/);
    if (addWatch(adresse, rest.join(" "))) added++;
  }
  saveWatch();
  setWatchMsg(`${added} adresse(s) importee(s) depuis ${file.name}.`);
  startWatch();
}

let diagLoaded = false;

function cell(text, cls, label) {
  const td = el("td", cls);
  if (label) td.dataset.label = label;
  if (text instanceof Node) td.append(text); else td.textContent = text ?? "";
  return td;
}

function moduleCell(m) {
  const box = el("div");
  if (!m) { box.append(el("span", "sub", "—")); return box; }
  box.append(el("span", "", m.nom), el("span", "sub mono", m.code + (m.references ? " · " + m.references : "")));
  return box;
}

function rows(tbody, items, render, emptyText, cols) {
  if (!items.length) {
    const tr = el("tr");
    const td = cell(emptyText, "empty");
    td.colSpan = cols;
    tr.append(td);
    tbody.replaceChildren(tr);
    return;
  }
  tbody.replaceChildren(...items.map(render));
}

function renderDiagnostic(d) {
  $("diag-alertes").replaceChildren(...d.alertes.map((a) => el("div", "alerte", a)));
  $("diag-info").textContent = d.config_compilee
    ? "Configuration materielle compilee le " + d.config_compilee + "."
    : "Configuration materielle non lue.";

  rows($("table-rack").tBodies[0], d.modules, (m) => {
    const tr = el("tr", m.ok ? "" : "bad");
    const state = el("span", "badge " + (m.ok ? "ok" : "bad"), m.ok ? "OK" : m.etat);
    tr.append(cell(String(m.emplacement), "mono", "Empl."), cell(m.type, "", "Type"), cell(m.adresse, "mono", "Adresses"),
              cell(moduleCell(m.configure), "", "Configure"), cell(moduleCell(m.en_place), "", "En place"),
              cell(state, "", "Etat"));
    return tr;
  }, "Aucun module lu", 6);

  rows($("table-events").tBodies[0], d.evenements, (e) => {
    const tr = el("tr");
    tr.title = "Info : " + e.info;
    const text = el("span", "", e.texte);
    const box = el("div");
    box.append(text);
    if (!e.entrant) box.append(el("span", "sub", "evenement sortant"));
    tr.append(cell(e.date, "mono", "Date"), cell(e.id, "mono", "ID"), cell(box, "", "Evenement"),
              cell(e.classe, "", "Classe"));
    return tr;
  }, "Tampon vide", 4);

  $("params").replaceChildren(...d.parametres_cpu.flatMap(([k, v]) => [el("dt", "", k), el("dd", "", v)]));
}

async function loadDiagnostic() {
  const btn = $("btn-diag");
  btn.disabled = true;
  $("diag-msg").textContent = "Lecture en cours...";
  $("diag-msg").className = "msg";
  try {
    renderDiagnostic(await api("GET", "/api/diagnostic"));
    diagLoaded = true;
    $("diag-msg").textContent = "Lu a " + new Date().toLocaleTimeString("fr-FR");
  } catch (e) {
    $("diag-msg").textContent = e.message;
    $("diag-msg").className = "msg err";
  } finally {
    btn.disabled = false;
  }
}

function setConnected(connected) {
  $("btn-deconnecter").hidden = !connected;
  $("btn-connecter").textContent = connected ? "Reconnecter" : "Connecter";
  $("tabs").hidden = !connected;
  if (connected) showTab(currentTab);
  if (!connected) {
    stopWatch();
    $("etat-vide").hidden = false;
    for (const v of document.querySelectorAll(".vue")) v.hidden = true;
    diagLoaded = false;
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
    diagLoaded = false;
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
  $("btn-diag").addEventListener("click", loadDiagnostic);
  for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => showTab(t.dataset.tab));
  $("form-ajout").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (addWatch($("ajout-adresse").value, $("ajout-libelle").value)) {
      saveWatch();
      $("ajout-adresse").value = "";
      $("ajout-libelle").value = "";
      setWatchMsg("");
      startWatch();
    }
    $("ajout-adresse").focus();
  });
  $("btn-pause").addEventListener("click", () => {
    watchPaused = !watchPaused;
    $("btn-pause").textContent = watchPaused ? "Reprendre" : "Pause";
    if (watchPaused) { stopWatch(); $("watch-maj").textContent = "En pause"; } else startWatch();
  });
  $("btn-ecrire").addEventListener("click", writeValues);
  $("btn-exporter").addEventListener("click", exportWatch);
  $("fichier-table").addEventListener("change", (ev) => {
    if (ev.target.files[0]) importWatch(ev.target.files[0]);
    ev.target.value = "";
  });
  $("btn-vider").addEventListener("click", () => {
    if (watch.length && confirm("Retirer toutes les adresses de la table ?")) { watch = []; saveWatch(); renderWatchRows(); }
  });

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
