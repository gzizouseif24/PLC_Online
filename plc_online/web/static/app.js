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
  lastStatus = s;
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
  if (s.occupe) {
    setPill("off", `${s.marque} — ${s.occupe}...`);
  } else {
    setPill(faults ? "err" : "on", `${s.marque} — ${s.mode}${faults ? " — " + faults + " alerte(s)" : ""}`);
  }
}

let currentTab = localStorageGet("onglet") || "etat-plein";

let lastStatus = null;

function setCpuMsg(text, isError) {
  const m = $("cpu-msg");
  m.textContent = text || "";
  m.className = "msg" + (isError ? " err" : "");
}

async function cpuAction(path, question, done) {
  if (!confirm(question)) return;
  for (const id of ["btn-run", "btn-stop", "btn-horloge"]) $(id).disabled = true;
  try {
    const r = await api("POST", path);
    setCpuMsg(done(r));
    setTimeout(refresh, 300);
  } catch (e) {
    setCpuMsg(e.message, true);
  } finally {
    for (const id of ["btn-run", "btn-stop", "btn-horloge"]) $(id).disabled = false;
  }
}

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
  if (id === "vue-prog") loadBackups();
}

// ---- program: save, compare, restore ----

let jobTimer = null;

function setProgMsg(text, isError) {
  const m = $("prog-msg");
  m.textContent = text || "";
  m.className = "msg" + (isError ? " err" : "");
}

function showResult(cls, build) {
  const box = $("prog-resultat");
  box.className = "resultat" + (cls ? " " + cls : "");
  box.replaceChildren();
  build(box);
  box.hidden = false;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function chips(items, cls) {
  const d = el("div", "chips");
  d.append(...items.map((t) => el("span", "chip" + (cls ? " " + cls : ""), t)));
  return d;
}

function setProgButtons(disabled) {
  for (const b of document.querySelectorAll("#vue-prog button")) b.disabled = disabled;
}

function runJob(path, body, onDone) {
  setProgMsg("");
  setProgButtons(true);
  api("POST", path, body).then(() => {
    $("tache").hidden = false;
    pollJob(onDone);
  }).catch((e) => {
    setProgButtons(false);
    setProgMsg(e.message, true);
  });
}

function pollJob(onDone) {
  clearTimeout(jobTimer);
  jobTimer = setTimeout(async () => {
    let j;
    try { j = await api("GET", "/api/tache"); } catch (e) { setProgMsg(e.message, true); setProgButtons(false); return; }
    $("tache-nom").textContent = j.nom.charAt(0).toUpperCase() + j.nom.slice(1);
    $("tache-texte").textContent = j.texte || "...";
    const bar = $("tache-barre").parentElement;
    bar.classList.toggle("indet", !j.total);
    $("tache-barre").style.width = j.total ? Math.round(100 * j.fait / j.total) + "%" : "";
    if (!j.fini) return pollJob(onDone);
    $("tache").hidden = true;
    setProgButtons(false);
    if (j.erreur) setProgMsg(j.erreur, true);
    else onDone(j.resultat);
    loadBackups();
  }, 400);
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

async function importBackup(fileList) {
  const files = [...fileList].filter((f) => /\.(bin|zip)$/i.test(f.name));
  if (!files.length) {
    setProgMsg("Aucun fichier .zip ou .bin (OB1.bin, FC1.bin...) dans la selection.", true);
    return;
  }
  setProgMsg("Import de " + files.length + " fichier(s)...");
  try {
    const fichiers = await Promise.all(files.map(async (f) => ({ nom: f.name, contenu: toBase64(await f.arrayBuffer()) })));
    const r = await api("POST", "/api/programme/importer", { fichiers, note: $("sauver-note").value });
    $("sauver-note").value = "";
    setProgMsg("Sauvegarde importee : " + r.comptes + "." + (r.ignores.length ? " Ignores : " + r.ignores.join(", ") : ""));
    loadBackups();
  } catch (e) {
    setProgMsg(e.message, true);
  }
}

async function loadBackups() {
  try {
    const r = await api("GET", "/api/programme/sauvegardes");
    $("prog-dossier").textContent = "Dossier : " + r.dossier;
    rows($("table-sauvegardes").tBodies[0], r.sauvegardes, (b) => {
      const tr = el("tr");
      const actions = el("div", "row-actions");
      for (const [text, fn] of [["Comparer", () => compareBackup(b)], ["Restaurer", () => planRestore(b, false)],
                                ["Blocs", () => showBlocks(b)]]) {
        const btn = el("button", "btn small", text);
        btn.type = "button";
        btn.addEventListener("click", fn);
        actions.append(btn);
      }
      const dl = el("a", "btn small", "Telecharger");
      dl.href = "/api/programme/telecharger?id=" + encodeURIComponent(b.id);
      dl.title = "Copie au format .zip (cle USB, e-mail...)";
      actions.append(dl);
      const cpu = el("div");
      cpu.append(el("span", "", b.modele || "—"), el("span", "sub mono", b.cpu));
      tr.append(cell(b.date, "mono", "Date"), cell(b.note || "—", "", "Note"), cell(cpu, "", "CPU"),
                cell(String(b.blocs), "mono", "Blocs"), cell(actions, "", ""));
      return tr;
    }, "Aucune sauvegarde : cliquez sur Sauvegarder le programme de la CPU.", 5);
  } catch (e) {
    setProgMsg(e.message, true);
  }
}

function saveProgram(ev) {
  ev.preventDefault();
  runJob("/api/programme/sauvegarder", { note: $("sauver-note").value }, (r) => {
    $("sauver-note").value = "";
    showResult("ok", (box) => {
      box.append(el("h4", "", "Programme sauvegarde"), el("p", "", r.comptes + "."));
    });
  });
}

function compareBackup(b) {
  runJob("/api/programme/comparer", { id: b.id }, (r) => {
    showResult(r.identique ? "ok" : "bad", (box) => {
      box.append(el("h4", "", r.identique ? "Programme identique a la sauvegarde du " + b.date
                                           : r.differences.length + " bloc(s) different(s) de la sauvegarde du " + b.date));
      if (!r.identique) {
        const ul = el("ul");
        ul.append(...r.differences.map((d) => {
          const li = el("li");
          li.append(el("b", "mono", d.bloc + " " + d.verdict), document.createTextNode(d.detail ? "  " + d.detail : ""));
          return li;
        }));
        box.append(ul);
      }
      if (r.valeurs_db.length) {
        box.append(el("p", "hint", "Valeurs actuelles differentes (normal en marche, pas un changement de programme) :"),
                   chips(r.valeurs_db));
      }
    });
  });
}

async function showBlocks(b) {
  try {
    const r = await api("GET", "/api/programme/blocs?id=" + encodeURIComponent(b.id));
    showResult("", (box) => {
      box.append(el("h4", "", "Blocs de la sauvegarde du " + b.date + (b.note ? " — " + b.note : "")));
      const wrap = el("div", "table-wrap");
      const t = el("table", "table");
      const head = el("thead");
      const hr = el("tr");
      hr.append(...["Bloc", "Taille", "Date", "Somme"].map((h) => el("th", "", h)));
      head.append(hr);
      const body = el("tbody");
      body.append(...r.blocs.map((x) => {
        const tr = el("tr");
        tr.append(cell(x.bloc, "mono", "Bloc"), cell(x.taille + " octets", "mono", "Taille"),
                  cell(x.date || "—", "mono", "Date"), cell(x.somme || "—", "mono", "Somme"));
        return tr;
      }));
      t.append(head, body);
      wrap.append(t);
      box.append(wrap);
    });
  } catch (e) {
    setProgMsg(e.message, true);
  }
}

async function planRestore(b, withDb, deleteExtra = false) {
  let plan;
  try {
    plan = await api("POST", "/api/programme/plan", { id: b.id, avec_db: withDb });
  } catch (e) {
    setProgMsg(e.message, true);
    return;
  }
  showResult("", (box) => {
    box.append(el("h4", "", "Restaurer la sauvegarde du " + b.date + (b.note ? " — " + b.note : "")));
    box.append(el("p", "", `Blocs a charger dans la CPU (${plan.charger.length}) :`), chips(plan.charger));
    if (plan.en_trop.length) {
      box.append(el("p", "", "Dans la CPU mais pas dans la sauvegarde :"), chips(plan.en_trop, "warn"));
    }
    const db = el("label", "check");
    const dbBox = el("input");
    dbBox.type = "checkbox";
    dbBox.checked = withDb;
    db.append(dbBox, el("span", "", "Charger aussi les DB (remplace les valeurs actuelles de la machine : compteurs, recettes...)"));
    dbBox.addEventListener("change", () => planRestore(b, dbBox.checked, delBox.checked));
    const del = el("label", "check");
    const delBox = el("input");
    delBox.type = "checkbox";
    delBox.checked = deleteExtra;
    delBox.disabled = !plan.en_trop.length;
    del.append(delBox, el("span", "", "Supprimer de la CPU les blocs absents de la sauvegarde"));
    box.append(db, del);
    box.append(el("p", "hint", "La configuration materielle (SDB) n'est pas modifiee."));
    if (plan.run) box.append(el("p", "avert", "La CPU est en RUN : elle sera mise en STOP pendant le chargement."));
    const go = el("button", "btn btn-danger", "Lancer la restauration");
    go.type = "button";
    go.disabled = !plan.charger.length;
    go.addEventListener("click", () => {
      const lines = [`${plan.charger.length} bloc(s) vont etre charges dans la CPU.`];
      if (plan.run) lines.push("La CPU va passer en STOP : la machine s'arrete.");
      if (dbBox.checked) lines.push("Les DB seront remplacees par celles de la sauvegarde.");
      if (delBox.checked && plan.en_trop.length) lines.push("Blocs supprimes : " + plan.en_trop.join(" "));
      if (!confirm(lines.join("\n") + "\n\nMachine en securite ?")) return;
      runJob("/api/programme/restaurer", { id: b.id, avec_db: dbBox.checked, supprimer_en_trop: delBox.checked },
             (r) => restoreDone(b, r));
    });
    box.append(go);
  });
}

function restoreDone(b, r) {
  const ok = !r.erreur && !r.differences.length;
  showResult(ok ? "ok" : "bad", (box) => {
    if (r.erreur) {
      box.append(el("h4", "", "Restauration arretee : " + r.erreur));
      box.append(el("p", "", "Blocs charges avant l'erreur : " + (r.charges.join(" ") || "aucun")));
      box.append(el("p", "avert", "La CPU reste en STOP. Relancer la restauration complete avant de redemarrer."));
      return;
    }
    box.append(el("h4", "", ok ? "Restauration terminee : programme identique a la sauvegarde"
                               : "Restauration terminee, mais la relecture differe"));
    box.append(el("p", "", "Blocs charges : "), chips(r.charges));
    if (r.supprimes.length) box.append(el("p", "", "Blocs supprimes : "), chips(r.supprimes, "warn"));
    if (r.differences.length) box.append(el("p", "avert", "Differences : " + r.differences.join(", ")));
    box.append(el("p", "", "La CPU est en STOP."));
    const start = el("button", "btn btn-primary", "Redemarrer la CPU (RUN)");
    start.type = "button";
    start.addEventListener("click", async () => {
      if (!confirm("Redemarrer la CPU ? La machine repart.\n\nMachine en securite ?")) return;
      try {
        await api("POST", "/api/cpu/demarrer");
        start.disabled = true;
        start.textContent = "Demarrage demande — verifier le voyant RUN";
        refresh();
      } catch (e) { setProgMsg(e.message, true); }
    });
    box.append(start);
  });
}

// ---- watch table ----

const WATCH_MS = 1000;
let watch = loadWatch();          // [{adresse, libelle}]
let watchTimer = null;
let watchBusy = false;
let monitoring = localStorageGet("visualiser") === "1";      // off by default, like TIA Portal
const lastValues = {};

const FORMAT_NAMES = { dec: "DEC", ud: "DEC non signe", hex: "HEX", bin: "BIN", real: "REAL", char: "CHAR",
                       s5time: "S5TIME", bcd: "BCD" };

function formatsFor(adresse) {
  /** Formats that fit the size of the address ([] for a bit: always 0 / 1). */
  const a = adresse.toUpperCase();
  if (/^[TZC]\d+$/.test(a)) return a[0] === "T" ? ["s5time", "hex", "dec"] : ["bcd", "hex", "dec"];
  if (/\.\d$/.test(a) && !/DB[BWD]\d+$/.test(a)) return [];
  if (/^([EIAQM]D|DB\d+\.DBD)/.test(a)) return ["dec", "ud", "hex", "bin", "real", "char"];
  if (/^([EIAQM]B|DB\d+\.DBB)/.test(a)) return ["dec", "hex", "bin", "char"];      // a byte has no sign
  return ["dec", "ud", "hex", "bin", "char"];
}

function fullAddress(w) {
  return w.adresse + (w.format ? ":" + w.format : "");
}

function splitAddress(text) {
  const [adresse, format = ""] = text.trim().split(":");
  const formats = formatsFor(adresse);
  const f = format.toLowerCase();
  return { adresse, format: formats.includes(f) ? f : (formats[0] || "") };
}

function loadWatch() {
  try {
    const rows = JSON.parse(localStorageGet("table-surveillance")) || [];
    return rows.map((w) => ({ ...splitAddress(w.format ? w.adresse + ":" + w.format : w.adresse), libelle: w.libelle || "" }));
  } catch { return []; }
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
    td.colSpan = 6;
    tr.append(td);
    tbody.replaceChildren(tr);
    return;
  }
  tbody.replaceChildren(...watch.map((w, i) => {
    const tr = el("tr");
    tr.dataset.adresse = fullAddress(w);
    const label = el("input", "libelle");
    label.value = w.libelle || "";
    label.placeholder = "libelle";
    label.addEventListener("change", () => { w.libelle = label.value; saveWatch(); });
    const value = el("span", "valeur", (monitoring && lastValues[fullAddress(w)]) || "—");
    const formats = formatsFor(w.adresse);
    let fmt;
    if (formats.length) {
      fmt = el("select", "format");
      fmt.title = "Format d'affichage";
      for (const f of formats) {
        const o = el("option", "", FORMAT_NAMES[f]);
        o.value = f;
        fmt.append(o);
      }
      fmt.value = w.format;
      fmt.addEventListener("change", () => {
        w.format = fmt.value;
        saveWatch();
        renderWatchRows();
        readWatch();
      });
    } else {
      fmt = el("span", "sub", "BOOL");
    }
    const input = el("input", "nouvelle");
    input.placeholder = "—";
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") writeValues(); });
    const del = el("button", "icon-btn", "×");
    del.type = "button";
    del.title = "Retirer " + w.adresse;
    del.addEventListener("click", () => { watch.splice(i, 1); saveWatch(); renderWatchRows(); });
    tr.append(cell(w.adresse, "mono", "Adresse"), cell(label, "", "Libelle"), cell(fmt, "", "Format"), cell(value, "", "Valeur"),
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
  $("watch-maj").textContent = "Lu a " + new Date().toLocaleTimeString("fr-FR");
}

async function readWatch() {
  if (watchBusy || !watch.length) return;
  watchBusy = true;
  try {
    renderWatchValues(await api("POST", "/api/valeurs", { adresses: watch.map(fullAddress) }));
  } catch (e) {
    setWatchMsg(e.message, true);
    if (e.status === 409 || e.status === 502) { stopWatch(); stopPolling(); setConnected(false); }
  } finally {
    watchBusy = false;
  }
}

function setMonitoring(on) {
  monitoring = on;
  localStorageSet("visualiser", on ? "1" : "0");
  $("btn-visu").setAttribute("aria-pressed", String(on));
  $("visu-texte").textContent = on ? "Visualisation en cours" : "Visualiser";
  $("table-watch").classList.toggle("off", !on);
  if (!on) $("watch-maj").textContent = "Visualisation arretee : cliquer sur les lunettes pour voir les valeurs en direct.";
  if (!$("vue-watch").hidden) startWatch();
}

function startWatch() {
  stopWatch();
  renderWatchRows();
  if (!monitoring) return;
  readWatch();
  watchTimer = setInterval(readWatch, WATCH_MS);
}
function stopWatch() {
  if (watchTimer) clearInterval(watchTimer);
  watchTimer = null;
}

function addWatch(text, libelle) {
  if (!text.trim()) return false;
  const row = splitAddress(text);
  if (watch.some((w) => fullAddress(w).toUpperCase() === fullAddress(row).toUpperCase())) {
    setWatchMsg(row.adresse + " est deja dans la table.", true);
    return false;
  }
  watch.push({ ...row, libelle: (libelle || "").trim() });
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
    if (monitoring) readWatch();
  } catch (e) {
    setWatchMsg(e.message, true);
  }
}

function exportWatch() {
  const text = "# Table de surveillance PLC Online\n" + watch.map((w) => (fullAddress(w) + " " + (w.libelle || "")).trim()).join("\n") + "\n";
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
  $("btn-visu").addEventListener("click", () => setMonitoring(!monitoring));
  setMonitoring(monitoring);
  $("btn-ecrire").addEventListener("click", writeValues);
  $("form-sauver").addEventListener("submit", saveProgram);
  $("btn-run").addEventListener("click", () => cpuAction("/api/cpu/demarrer",
    "Passer la CPU en RUN ? La machine peut demarrer.\n\nMachine en securite ?",
    () => lastStatus && lastStatus.cle === "STOP"
      ? "Demarrage demande, mais la cle est sur STOP : la CPU restera en STOP (mettre la cle sur RUN ou RUN-P)."
      : "Demarrage demande (RUN)."));
  $("btn-stop").addEventListener("click", () => cpuAction("/api/cpu/arreter",
    "Passer la CPU en STOP ? La machine s'arrete, les sorties sont coupees.\n\nMachine en securite ?",
    () => "Arret demande (STOP)."));
  $("btn-horloge").addEventListener("click", () => cpuAction("/api/cpu/horloge",
    "Mettre l'horloge de la CPU a l'heure de ce PC (" + new Date().toLocaleString("fr-FR") + ") ?",
    (r) => "Horloge CPU reglee : " + r.horloge.slice(0, 19)));
  $("import-fichiers").addEventListener("change", (ev) => { importBackup(ev.target.files); ev.target.value = ""; });
  $("import-dossier").addEventListener("change", (ev) => { importBackup(ev.target.files); ev.target.value = ""; });
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
  if (s.connecte && s.connexion && brands[s.connexion.marque]) {
    select.value = s.connexion.marque;
    renderSettings();
  }
  if (s.connecte) {
    renderStatus(s);
    setConnected(true);
    startPolling();
  } else {
    setConnected(false);
  }
}

init().catch((e) => setMsg("Serveur injoignable : " + e.message, true));
