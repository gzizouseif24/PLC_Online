"""Allen-Bradley ControlLogix / GuardLogix in the web interface: status, chassis, Kinetix axes,
tags by name in the watch table. Read-only on the drives: no reset, no motion."""
import json
import time

from plc_online.protocols import logix as lx


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "ip", "nom": "Adresse IP de l'automate", "defaut": "", "type": "texte"},
            {"cle": "slot", "nom": "Emplacement CPU", "defaut": 0}]
MAX_SLOT = 16


def open_logix(settings: dict):
    ip = str(settings.get("ip") or "").strip()
    if not ip:
        raise _drv().DriverError("Adresse IP de l'automate a renseigner (ex. 192.168.1.10)")
    try:
        slot = int(settings.get("slot") or 0)
    except (TypeError, ValueError):
        raise _drv().DriverError("Emplacement CPU : un nombre") from None
    try:
        return lx.Logix(ip, slot)
    except lx.LogixError as e:
        raise _drv().DriverError(f"Pas de reponse de {ip} : {e}. Le PC est-il dans le meme reseau que l'automate "
                                 "(meme debut d'adresse) ? Emplacement de la CPU correct ?") from None


# ---- demo: GuardLogix 5580 with three Kinetix 5500 axes ----

class DemoLogix:
    nom = "Allen-Bradley Logix (demo)"

    def __init__(self):
        self.t0 = time.time()
        self.tags = {"Recipe_Speed": ("REAL", 1250.0), "Recipe_Length": ("DINT", 360), "Line_Run": ("BOOL", True),
                     "Batch_Count": ("DINT", 4211), "SafetyOK": ("BOOL", True)}
        self.safety = {"SafetyOK"}
        self.axes = {"Ax_Infeed": {"CIPAxisState": 4, "AxisFault": 0, "CIPAxisFaults": 0, "CIPStartInhibits": 0,
                                   "CIPStartInhibitsRA": 0, "DCBusVoltage": 562.0, "MotorCapacity": 38.0,
                                   "InverterCapacity": 22.0},
                     "Ax_Knife": {"CIPAxisState": 8, "AxisFault": 1, "CIPAxisFaults": 1 << 7, "CIPStartInhibits": 0,
                                  "CIPStartInhibitsRA": 0, "DCBusVoltage": 560.0, "MotorCapacity": 112.0,
                                  "InverterCapacity": 64.0, "ActualPosition": 181.5, "ActualVelocity": 0.0},
                     "Ax_Unwind": {"CIPAxisState": 9, "AxisFault": 0, "CIPAxisFaults": 0, "CIPStartInhibits": 0,
                                   "CIPStartInhibitsRA": 1 << 1, "DCBusVoltage": 561.0, "MotorCapacity": 0.0,
                                   "InverterCapacity": 0.0, "ActualPosition": 12.0, "ActualVelocity": 0.0}}

    def close(self):
        pass

    def info(self):
        return {"produit": "1756-L82ES/B LOGIX5582ES", "revision": "33.11", "serie": "60c1a2b3",
                "programme": "LINE_3", "statut": bytes([0x60, 0x31])}

    def module(self, slot):
        rack = {0: ("1756-L82ES/B LOGIX5582ES", "Programmable Logic Controller", b"\x60\x31"),
                1: ("1756-L8SP/B Safety Partner", "Safety Processor", b"\x60\x00"),
                2: ("1756-EN2T/D", "Communications Adapter", b"\x60\x00"),
                3: ("1756-EN2TR/C", "Communications Adapter", b"\x20\x01")}
        if slot not in rack:
            return None
        name, typ, st = rack[slot]
        return {"produit": name, "type": typ, "revision": "11.002", "serie": f"00{slot}a7c4e", "statut": st}

    def tag_types(self):
        out = {n: t for n, (t, _) in self.tags.items()}
        out.update({a: "AXIS_CIP_DRIVE" for a in self.axes})
        return out

    def axis_names(self):
        return sorted(self.axes)

    def read_axis(self, axis):
        m = dict(self.axes[axis])
        if axis == "Ax_Infeed":
            t = time.time() - self.t0
            m["ActualPosition"] = round((t * 90.0) % 360, 1)
            m["ActualVelocity"] = 90.0
        return {k: m.get(k) for k in lx.AXIS_MEMBERS}

    def read(self, names):
        out = {}
        for n in names:
            base, _, member = n.partition(".")
            if base in self.axes and member:
                v = self.read_axis(base).get(member)
                out[n] = (v, "DINT", None) if v is not None else (None, None, "Tag doesn't exist")
            elif n in self.tags:
                out[n] = (self.tags[n][1], self.tags[n][0], None)
            else:
                out[n] = (None, None, "Tag doesn't exist")
        return out

    def write(self, name, value):
        if name in self.safety:
            raise lx.TagRefused(f"{name} : Privilege violation")
        if name not in self.tags:
            raise lx.TagRefused(f"{name} : Tag doesn't exist")
        self.tags[name] = (self.tags[name][0], value)


# ---- status / diagnostic ----

def logix_status(plc) -> dict:
    i = plc.info()
    st = lx.decode_status(i["statut"])
    alerts = [f"Automate : {d}" for d in st["defauts"] if "MAJEUR" in d]
    out = {"marque": "Allen-Bradley", "modele": i["produit"] or plc.nom, "reference": f"firmware {i['revision']}",
           "mode": "DEFAUT" if st["majeur"] else st["mode"], "alertes": alerts, "cle": st["cle"]}
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": st["mode"] == "RUN", "clignote": False, "defaut": False},
        {"nom": "OK", "texte": "defaut majeur", "allume": True, "clignote": st["majeur"], "defaut": st["majeur"]},
        {"nom": "MIN", "texte": "defaut mineur", "allume": st["mineur"], "clignote": False, "defaut": st["mineur"]},
    ]
    out["details"] = [("Cle", st["cle"]), ("Programme", i["programme"] or "?"), ("N° de serie", i["serie"])]
    if st["defauts"]:
        out["details"].append(("Defauts", ", ".join(st["defauts"])))
    return out


def rack_rows(plc) -> list:
    rows = []
    empty = 0
    for slot in range(MAX_SLOT + 1):
        m = plc.module(slot)
        if m is None:
            empty += 1
            if empty >= 4 and slot >= 4:        # end of the chassis
                break
            continue
        empty = 0
        st = lx.module_status(m["statut"])
        problems = st["defauts"] + (["connexion E/S en defaut"] if st["connexion_en_defaut"] else [])
        rows.append({"emplacement": slot, "type": m["type"], "adresse": "",
                     "configure": {"code": "", "nom": m["produit"], "references": f"firmware {m['revision']}"},
                     "en_place": {"code": "", "nom": st["etat"] or "ok", "references": f"serie {m['serie']}"},
                     "etat": ", ".join(problems) or "ok", "ok": not problems, "variables": []})
    return rows


def logix_diagnostic(plc) -> dict:
    i = plc.info()
    st = lx.decode_status(i["statut"])
    out = {"alertes": [f"Automate : {d}" for d in st["defauts"]], "config_compilee": "",
           "modules": rack_rows(plc)}
    for row in out["modules"]:
        if not row["ok"]:
            out["alertes"].append(f"Emplacement {row['emplacement']} ({row['configure']['nom']}) : {row['etat']}")
    for a in [lx.axis_summary(n, plc.read_axis(n)) for n in plc.axis_names()]:
        if not a["ok"]:
            out["alertes"].append(f"Axe {a['axe']} : {a['etat']}" + (" - " + ", ".join(a["defauts"]) if a["defauts"] else ""))
    out["parametres_cpu"] = [("Produit", i["produit"]), ("Firmware", i["revision"]), ("N° de serie", i["serie"]),
                             ("Programme", i["programme"] or "?"), ("Mode", st["mode"]), ("Cle", st["cle"]),
                             ("Mot d'etat (identite)", i["statut"].hex(" "))]
    return out


# ---- watch table (tags by name) ----

def show(value) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= 200 else text[:197] + "..."
    return str(value)


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    names = [str(t).strip() for t in texts]
    got = plc.read([n for n in names if n])
    for t, n in zip(texts, names):
        value, _, err = got.get(n, (None, None, "adresse vide"))
        if err:
            out["erreurs"][str(t)] = f"{n} : {err}"
        else:
            out["valeurs"][str(t)] = show(value)
    return out


def parse_value(text: str, typ: str):
    t = text.strip()
    if typ == "BOOL":
        if t not in ("0", "1"):
            raise ValueError("0 ou 1")
        return t == "1"
    if typ in ("REAL", "LREAL"):
        try:
            return float(t.replace(",", "."))
        except ValueError:
            raise ValueError("nombre attendu (ex. 12.5)") from None
    if typ in ("SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT", "BYTE", "WORD", "DWORD", "LWORD"):
        try:
            return int(t[3:], 16) if t.lower().startswith("16#") else int(t)
        except ValueError:
            raise ValueError("nombre entier attendu") from None
    if typ == "STRING":
        return t
    raise ValueError(f"type {typ or '?'} : ecriture d'une structure entiere non geree, ecrire ses membres un par un")


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        name = str(it.get("adresse", "")).strip()
        old, typ, err = plc.read([name]).get(name, (None, None, "absent"))
        if err:
            raise _drv().DriverError(f"{name} : {err}")
        try:
            value = parse_value(str(it.get("valeur", "")), typ)
        except ValueError as e:
            raise _drv().DriverError(f"{name} : {e}") from None
        try:
            plc.write(name, value)
        except lx.TagRefused as e:
            raise _drv().DriverError(f"{e} : refuse par l'automate (tag de securite, en lecture seule ou "
                                     "inexistant). Les tags de securite ne s'ecrivent pas de l'exterieur.") from None
        back = plc.read([name])[name][0]
        results.append({"adresse": it.get("adresse"), "avant": show(old), "relu": show(back),
                        "ok": (abs(back - value) < 1e-6 * max(1, abs(value))) if isinstance(value, float) else back == value})
    return {"resultats": results, "run": lx.decode_status(plc.info()["statut"])["mode"] == "RUN"}


# ---- axes (Kinetix): live state and fault history ----

class LogixOps:
    def __init__(self, session):
        self.s = session
        self.history = []          # [{heure, axe, texte, actif}]
        self.last = {}             # axis -> set of active fault / inhibit texts

    def status(self, plc):
        return logix_status(plc)

    def diagnostic(self):
        return self.s._call(logix_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def axes(self):
        def run(plc):
            rows = [lx.axis_summary(n, plc.read_axis(n)) for n in plc.axis_names()]
            now = time.strftime("%H:%M:%S")
            for a in rows:
                active = set(a["defauts"] + a["inhibitions"] + a["alarmes"])
                before = self.last.get(a["axe"])
                if before is not None:
                    for t in sorted(active - before):
                        self.history.append({"heure": now, "axe": a["axe"], "texte": t, "actif": True})
                    for t in sorted(before - active):
                        self.history.append({"heure": now, "axe": a["axe"], "texte": t, "actif": False})
                elif active:
                    for t in sorted(active):
                        self.history.append({"heure": now, "axe": a["axe"], "texte": t + " (deja present)", "actif": True})
                self.last[a["axe"]] = active
            self.history = self.history[-200:]
            return {"axes": rows, "historique": list(reversed(self.history))}
        return self.s._call(run)

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction non disponible pour un automate Allen-Bradley (programme : Studio 5000)")
