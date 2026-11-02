"""What the web interface needs from a PLC, the same for every brand.

A driver is opened with `connect(settings)` and gives `status()`: a plain dict the page can
show without knowing the brand. S7-300 first; Omron, TSX... plug in with the same dict.
"""
import random
import threading

from pathlib import Path

from plc_online.protocols.s7 import S7300, S7Error, parse_block, parse_sdb1_modules
from plc_online.protocols.s7_catalogue import catalog_name
from plc_online.protocols.s7_names import address_range, cpu_parameter_lines, module_text
from plc_online.protocols.s7_vars import AddressError, Var

FAULT_LEDS = {"SF": "Defaut groupe", "BAF": "Defaut pile", "BUS1F": "Defaut bus MPI/DP",
              "BUS2F": "Defaut bus DP", "FRCE": "Forcage actif"}
OTHER_LEDS = {"RUN": "RUN", "STOP": "STOP"}


class DriverError(Exception):
    """Message for the technician (French), shown as is on the page."""


def s7_status(plc) -> dict:
    """Brand-neutral status of an S7-300 (same reads as `tools/siemens.py etat`)."""
    out = {"marque": "Siemens S7-300", "alertes": [], "voyants": [], "details": []}
    try:
        info = plc.identity()
        out["reference"] = info.get("reference", "")
        out["modele"] = info.get("nom_module") or catalog_name(info.get("reference")) or "CPU S7-300"
        if info.get("firmware"):
            out["details"].append(("Firmware", info["firmware"]))
        if info.get("materiel"):
            out["details"].append(("Version materiel", info["materiel"]))
        if info.get("numero_serie"):
            out["details"].append(("N° de serie", info["numero_serie"]))
        if info.get("nom_automate"):
            out["details"].append(("Station", info["nom_automate"]))
    except S7Error as e:
        out["modele"] = "CPU S7-300"
        out["alertes"].append(f"Identification non lue : {e}")

    leds = plc.leds()
    on = {name for name, (state, blink) in leds.items() if state or blink}
    out["mode"] = "RUN" if "RUN" in on and "STOP" not in on else "STOP" if "STOP" in on else "?"
    for name, (state, blink) in leds.items():
        fault = name in FAULT_LEDS
        out["voyants"].append({"nom": name, "texte": FAULT_LEDS.get(name) or OTHER_LEDS.get(name, name),
                               "allume": bool(state or blink), "clignote": bool(blink), "defaut": fault})
    for name in sorted(on & set(FAULT_LEDS)):
        out["alertes"].append(f"{name} allume : {FAULT_LEDS[name]}")

    try:
        p = plc.protection()
        out["details"].append(("Cle", p["cle"]))
        out["details"].append(("Protection", f"niveau {p['protection_effective']}"
                               + (" (sans mot de passe)" if p["protection_parametree"] == 0 else "")))
        hw, prog = p["somme_config_materielle"], p["somme_programme"]
        if any(hw + prog):
            out["details"].append(("Somme programme", f"{prog[0]:04X}-{prog[1]:04X}"))
            out["details"].append(("Somme config materielle", f"{hw[0]:04X}-{hw[1]:04X}"))
    except S7Error as e:
        out["alertes"].append(f"Cle et protection non lues : {e}")
    return out


def module_info(code: int, kind: str) -> dict:
    """{'code': 'AFD0', 'nom': 'SM 322 DO16x...', 'references': '6ES7 322-1BH01-0AA0, ...'}"""
    if kind == "CPU":
        return {"code": f"{code:04X}", "nom": "CPU", "references": ""}
    name, _, refs = module_text(code).partition(" : ")
    return {"code": f"{code:04X}", "nom": name or "Module inconnu", "references": refs}


def s7_diagnostic(plc, rack: int = 0) -> dict:
    """Diagnostic buffer, rack (configured vs plugged in) and CPU parameters of an S7-300.
    Each part is read on its own: one refused read does not hide the others."""
    out = {"evenements": [], "modules": [], "parametres_cpu": [], "config_compilee": "", "alertes": []}
    try:
        for e in plc.diagnostic_buffer():
            out["evenements"].append({"date": e["date"], "id": f"{e['id']:04X}", "classe": e["classe"],
                                      "texte": e["texte"] or "Evenement non repertorie", "entrant": e["entrant"],
                                      "info": e["info"]})
    except S7Error as e:
        out["alertes"].append(f"Tampon de diagnostic non lu : {e}")

    actual = {}
    try:
        actual = {m["emplacement"]: m for m in plc.modules(rack)}
    except S7Error as e:
        out["alertes"].append(f"Modules en place non lus : {e}")
    configured = {}
    try:
        sdb1 = plc.upload_block("SDB", 1)
        out["config_compilee"] = parse_block(sdb1)["date"]
        configured = {m["emplacement"]: m for m in parse_sdb1_modules(sdb1)}
    except S7Error as e:
        out["alertes"].append(f"Configuration materielle non lue : {e}")
    try:
        out["parametres_cpu"] = cpu_parameter_lines(plc.upload_block("SDB", 0))
    except S7Error as e:
        out["alertes"].append(f"Parametres CPU non lus : {e}")

    for slot in sorted(set(actual) | set(configured)):
        c, a = configured.get(slot), actual.get(slot)
        kind = (c or a)["type"]
        row = {"emplacement": slot, "type": kind, "adresse": "", "configure": None, "en_place": None,
               "etat": a["etat"] if a else "absent", "ok": True}
        if c and kind != "CPU":
            row["adresse"] = address_range(c["type"], c["adresse"], c["octets"])
        elif a and kind != "CPU":
            row["adresse"] = f"{a['adresse']}"
        conf_code = c["code"] if c else (a["type_config"] if a else 0)
        real_code = a["type_reel"] if a else 0
        if conf_code:
            row["configure"] = module_info(conf_code, kind)
        if real_code:
            row["en_place"] = module_info(real_code, kind)
        if kind != "CPU" and conf_code and real_code and conf_code != real_code:
            row["ok"] = False
            row["etat"] = "MODULE DIFFERENT DE LA CONFIGURATION"
        elif kind != "CPU" and conf_code and not real_code:
            row["ok"] = False
            row["etat"] = "AUCUN MODULE EN PLACE"
        elif a and a["etat"] != "ok":
            row["ok"] = False
        out["modules"].append(row)
    bad = [r for r in out["modules"] if not r["ok"]]
    if bad:
        out["alertes"].append(f"{len(bad)} emplacement(s) en defaut dans le rack {rack}")
    return out


MAX_WATCH = 100


def parse_vars(texts) -> tuple:
    """([Var], {text: error}) for the addresses typed in the watch table."""
    good, bad = [], {}
    for t in texts:
        try:
            good.append(Var(str(t)))
        except AddressError as e:
            bad[str(t)] = str(e)
    return good, bad


def read_values(plc, texts) -> dict:
    """{'valeurs': {address text: value text}, 'erreurs': {address text: message}}."""
    texts = [str(t) for t in texts][:MAX_WATCH]
    variables, errors = parse_vars(texts)
    raw = plc.read_vars(variables) if variables else {}
    values = {}
    for v in variables:
        if raw.get(v) is None:
            errors[v.text] = "lecture refusee par la CPU (adresse hors de la memoire ?)"
        else:
            values[v.text] = v.decode(raw[v])
    return {"valeurs": values, "erreurs": errors}


def write_values(plc, items) -> dict:
    """Modify: items = [{'adresse': 'MW10', 'valeur': '123'}]. Everything is checked before the
    first write; then old value, write, read back for each address."""
    pairs = []
    for it in items:
        try:
            var = Var(str(it.get("adresse", "")))
            pairs.append((var, var.encode(str(it.get("valeur", "")))))
        except (AddressError, ValueError) as e:
            raise DriverError(f"{it.get('adresse', '')} : {e}") from None
    if not pairs:
        raise DriverError("Rien a ecrire")
    variables = [v for v, _ in pairs]
    old = plc.read_vars(variables)
    for v, data in pairs:
        plc.write_var(v, data)
    back = plc.read_vars(variables)
    out = []
    for v, data in pairs:
        wanted = str(data[0]) if v.bit is not None else v.decode(data)
        got = v.decode(back[v]) if back.get(v) is not None else None
        out.append({"adresse": v.text, "ancien": v.decode(old[v]) if old.get(v) is not None else None,
                    "demande": wanted, "relu": got, "ok": got == wanted})
    return {"resultats": out}


SDB_DEMO = Path(__file__).resolve().parents[2] / "research" / "siemens" / "sdb" / "1_config_fausse"


class DemoS7:
    """Simulated S7-300 (bench values of 31/10/2026) to show and test the page without a PLC.
    The mode changes now and then, SF lights up in STOP."""

    def __init__(self):
        self.running = True
        self.mem = {}
        self.log = lambda m: None
        self.tick = 0
        z = self._zone("M", 0)
        z[10:12] = (1234).to_bytes(2, "big")
        self._zone("DB", 10)[4:8] = bytes.fromhex("41AC0000")          # 21.5 as REAL

    def _zone(self, area, db):
        return self.mem.setdefault((area, db), bytearray(2048))

    def _simulate(self):
        """A little machine: inputs move, a counter counts, timer T1 runs down."""
        self.tick += 1
        i = self._zone("I", 0)
        i[0] = (0b00000101 if self.tick % 6 < 3 else 0b00000011) | (0x80 if random.random() < 0.2 else 0)
        q = self._zone("Q", 0)
        q[4] = i[0] & 0x0F
        m = self._zone("M", 0)
        m[20:22] = ((int.from_bytes(m[20:22], "big") + 1) & 0xFFFF).to_bytes(2, "big")
        m[100] = 0xFF if self.tick % 2 else 0x00                            # clock memory
        t = self._zone("T", 0)
        left = (50 - self.tick % 50)                                        # 5.0 s .. 0.1 s, base 100 ms
        t[2:4] = (0x1000 | int(f"{left:03d}", 16)).to_bytes(2, "big")

    def read_bytes(self, area, start, count, db=0):
        if start + count > 1024:
            raise S7Error("adresse hors de la memoire (demo)")
        z = self._zone(area, db if area == "DB" else 0)
        if area in ("T", "C"):
            return bytes(z[2 * start:2 * (start + count)])
        return bytes(z[start:start + count])

    def read_vars(self, variables):
        self._simulate()
        return S7300.read_vars(self, variables)

    def write_bytes(self, area, start, data, db=0):
        if area in ("T", "C") or start + len(data) > 1024:
            raise S7Error("ecriture refusee (demo)")
        self._zone(area, db if area == "DB" else 0)[start:start + len(data)] = data

    def write_bit(self, area, byte, bit, value, db=0):
        z = self._zone(area, db if area == "DB" else 0)
        z[byte] = (z[byte] & ~(1 << bit)) | ((1 if value else 0) << bit)

    def write_var(self, var, data):
        S7300.write_var(self, var, data)

    def identity(self):
        return {"reference": "6ES7 315-2AF03-0AB0", "firmware": "V1.2.0"}

    def leds(self):
        if random.random() < 0.05:
            self.running = not self.running
        r = self.running
        return {"SF": (0 if r else 1, 0), "RUN": (1 if r else 0, 0), "STOP": (0 if r else 1, 0),
                "FRCE": (0, 0), "BAF": (0, 0)}

    def protection(self):
        return {"cle": "RUN-P", "protection_effective": 1, "protection_parametree": 0,
                "somme_config_materielle": (0x5656, 0x2A00), "somme_programme": (0x6647, 0x0600),
                "sommes_valides": False}

    def diagnostic_buffer(self):
        def ev(i, date, info="FF06 C17B 0001 0004 8B07"):
            from plc_online.protocols.s7_events import EVENT_CLASSES, event_text
            return {"id": i, "classe": EVENT_CLASSES.get(i >> 12, ""), "texte": event_text(i),
                    "entrant": bool(i >> 8 & 1), "info": info, "date": date}
        return [ev(0x4302, "02/11/2026 07:41:03.120", "FF68 C010 0008 0000 0000"),
                ev(0x5961, "02/11/2026 07:41:02.880"),
                ev(0x5961, "02/11/2026 07:41:02.870", "FF06 C17B 0001 0000 8B07"),
                ev(0x4301, "02/11/2026 07:40:55.010", "FF84 4304 0000 0000 0000")]

    def modules(self, rack=0):
        def m(slot, addr, conf, real, kind, etat):
            return {"rack": rack, "emplacement": slot, "adresse": addr, "type": kind, "etat": etat,
                    "type_config": conf, "type_reel": real, "different": conf != real}
        return [m(2, 0x7FFF, 0x00C0, 0x00C0, "CPU", "ok"),
                m(4, 0, 0xAFD8, 0x9FC3, "sorties", "TYPE DIFFERENT DE LA CONFIG, non disponible"),
                m(5, 4, 0xAFC8, 0xAFD0, "sorties", "TYPE DIFFERENT DE LA CONFIG, non disponible")]

    def upload_block(self, kind, number):
        try:
            return (SDB_DEMO / f"{kind}{number}.bin").read_bytes()
        except OSError:
            raise S7Error(f"{kind}{number} absent de la demo") from None

    def close(self):
        pass


BRANDS = {
    "s7-300": {"nom": "Siemens S7-300", "disponible": True,
               "reglages": [{"cle": "mpi", "nom": "Adresse MPI", "defaut": 2},
                            {"cle": "rack", "nom": "Rack", "defaut": 0},
                            {"cle": "slot", "nom": "Emplacement CPU", "defaut": 2}]},
    "demo": {"nom": "Demo (S7-300 simule)", "disponible": True, "reglages": []},
    "omron": {"nom": "Omron CJ1M", "disponible": False, "reglages": []},
    "tsx": {"nom": "Schneider TSX", "disponible": False, "reglages": []},
    "panasonic": {"nom": "Panasonic FP0R", "disponible": False, "reglages": []},
}


class Session:
    """The one PLC the laptop is connected to. Calls are serialized: the link is not shared."""

    def __init__(self, opener=None):
        self.lock = threading.Lock()
        self.plc, self.brand, self.settings = None, None, {}
        self.opener = opener or open_plc

    def connect(self, brand: str, settings: dict) -> dict:
        if brand not in BRANDS or not BRANDS[brand]["disponible"]:
            raise DriverError(f"Marque non disponible : {brand}")
        with self.lock:
            self._close()
            self.plc = self.opener(brand, settings)
            self.brand, self.settings = brand, settings
        return self.status()

    @property
    def connected(self) -> bool:
        return self.plc is not None

    def status(self) -> dict:
        with self.lock:
            if self.plc is None:
                raise DriverError("Pas connecte")
            try:
                out = s7_status(self.plc)
            except S7Error as e:
                self._close()
                raise DriverError(f"Liaison perdue : {e}") from None
        out["connecte"] = True
        out["connexion"] = {"marque": self.brand, **self.settings}
        return out

    def diagnostic(self) -> dict:
        with self.lock:
            if self.plc is None:
                raise DriverError("Pas connecte")
            try:
                return s7_diagnostic(self.plc, int(self.settings.get("rack", 0) or 0))
            except S7Error as e:
                self._close()
                raise DriverError(f"Liaison perdue : {e}") from None

    def _call(self, action):
        with self.lock:
            if self.plc is None:
                raise DriverError("Pas connecte")
            try:
                return action(self.plc)
            except S7Error as e:
                self._close()
                raise DriverError(f"Liaison perdue : {e}") from None

    def values(self, texts) -> dict:
        return self._call(lambda plc: read_values(plc, texts))

    def write(self, items) -> dict:
        def run(plc):
            out = write_values(plc, items)
            out["run"] = bool(plc.leds().get("RUN", (0, 0))[0])
            return out
        return self._call(run)

    def disconnect(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.plc is not None:
            try:
                self.plc.close()
            except Exception:
                pass
        self.plc, self.brand = None, None


def open_plc(brand: str, settings: dict):
    if brand == "demo":
        return DemoS7()
    try:
        mpi, rack, slot = (int(settings.get(k, d)) for k, d in (("mpi", 2), ("rack", 0), ("slot", 2)))
    except (TypeError, ValueError):
        raise DriverError("Adresse MPI, rack et emplacement : des nombres") from None
    try:
        return S7300(mpi, rack, slot).open()
    except S7Error as e:
        raise DriverError(str(e)) from None
