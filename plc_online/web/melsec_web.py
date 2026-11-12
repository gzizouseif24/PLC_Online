"""Mitsubishi FX (FX1N first) in the web interface: status, special M / D, I/O by unit (base + expansions
given at connection), watch table, D0..D511 data copies, RUN / STOP by the forced RUN / STOP flags."""
import datetime
import re
import time
from pathlib import Path

from plc_online.protocols import delta as dl
from plc_online.protocols import melsec_fx as fx
from plc_online.protocols.modbus import signed16
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM (cable de programmation)", "defaut": "COM5", "type": "port"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 9600},
            {"cle": "modele", "nom": "Unite de base (ex. FX1N-40MR)", "defaut": "FX1N-40MR", "type": "texte"},
            {"cle": "ext1", "nom": "Extension 1 (ex. FX2N-16EX, vide = aucune)", "defaut": "", "type": "texte"},
            {"cle": "ext2", "nom": "Extension 2", "defaut": "", "type": "texte"},
            {"cle": "ext3", "nom": "Extension 3", "defaut": "", "type": "texte"}]

# FX1N hardware manual JY992D89301, table 1.1: built-in inputs / outputs of the base units
BASE_UNITS = {("FX1N", 14): (8, 6), ("FX1N", 24): (14, 10), ("FX1N", 40): (24, 16), ("FX1N", 60): (36, 24)}
# tables 1.2, 1.3, 1.10: extension blocks / powered extension units (FX0N- and FX2N-), inputs / outputs used.
# Each unit occupies its count rounded up to 8 (FX?N-8ER: 4 / 4 used, 8 / 8 occupied).
EXPANSIONS = {"8EX": (8, 0), "8ER": (4, 4), "8EYR": (0, 8), "8EYT": (0, 8), "16EX": (16, 0), "16EYR": (0, 16),
              "16EYT": (0, 16), "32ER": (16, 16), "32ET": (16, 16), "40ER": (24, 16), "40ET": (24, 16),
              "48ER": (24, 24), "48ET": (24, 24)}
DATA_WORDS = 512                      # D0..D511: the documented map of the programming port


def base_model(text):
    """'FX1N-40MR-ES/UL' -> ('FX1N-40MR', (24, 16)); unknown -> (text, None)."""
    t = str(text or "").strip().upper().replace(" ", "")
    m = re.match(r"^(FX\d[A-Z]*)-?(\d+)M([RTS])?", t)
    if not m:
        return t or "?", None
    io = BASE_UNITS.get((m.group(1), int(m.group(2))))
    return f"{m.group(1)}-{m.group(2)}M{m.group(3) or ''}", io


def expansion_model(text):
    """'FX2N-16EX-ES/UL' -> '16EX', '' -> '' (none), not recognized -> None; other models (special
    blocks such as FX2N-4AD) are kept as given: they use FROM / TO, not X / Y."""
    t = str(text or "").strip().upper().replace(" ", "")
    if t in ("", "AUCUNE", "AUCUN", "NONE", "-"):
        return ""
    m = re.match(r"^(?:FX[02]N-?)?(\d+E[A-Z]+?)(?:-[A-Z0-9/]+)?$", t)
    if m and m.group(1) in EXPANSIONS:
        return m.group(1)
    return t if t.startswith("FX") else None


def round8(n: int) -> int:
    return (n + 7) // 8 * 8


def layout(model, expansions) -> list:
    """[(label, model text, (first X, n inputs), (first Y, n outputs))] in octal numbering order."""
    name, io = base_model(model)
    n_in, n_out = io or (16, 16)
    units = [("Unite de base", name if io else f"{name} (type non reconnu : 16 / 16 lues)", (0, n_in), (0, n_out))]
    x, y = round8(n_in), round8(n_out)
    for i, ext in enumerate(expansions, 1):
        if not ext or ext not in EXPANSIONS:
            continue
        e_in, e_out = EXPANSIONS[ext]
        units.append((f"Extension {i}", ext, (x, e_in), (y, e_out)))
        x, y = x + round8(e_in), y + round8(e_out)
    return units


def open_fx(settings: dict):
    port = str(settings.get("port") or "COM5").strip()
    try:
        baud = int(settings.get("baud") or 9600)
    except (TypeError, ValueError):
        raise _drv().DriverError("Vitesse : un nombre") from None
    try:
        ser = fx.open_port(port, baud)
    except Exception as e:                     # serial errors: port missing or busy
        raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    plc = fx.FxPlc(ser)
    try:
        plc.read_words("D", 8001, 1)
    except fx.FxRefused:
        pass
    except fx.FxError as e:
        ser.close()
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({baud} 7E1) : {e}. Cable SC-09 / USB-SC09 "
                                 "sur le port de programmation (mini-DIN 8 sous le capot) ? GX Developer / GX Works "
                                 "ferme ? Bon port COM ?") from None
    _setup(plc, settings)
    return plc


def _setup(plc, settings):
    plc.modele = base_model(settings.get("modele") or "FX1N-40MR")[0]
    plc.nom = f"Mitsubishi {plc.modele}"
    plc.expansions = [expansion_model(settings.get(f"ext{i}")) for i in (1, 2, 3)]
    plc.extensions_texte = [str(settings.get(f"ext{i}") or "").strip() for i in (1, 2, 3)]


# ---- demo: FX1N-40MR with an FX2N-16EX ----

class DemoPort:
    """Serial port answering the programming-port protocol from a 64 KB memory image."""

    def __init__(self):
        self.mem = bytearray(0x10000)
        self.buf = b""
        put = lambda area, n, v: self.mem.__setitem__(slice(*self._span(area, n)), v.to_bytes(2, "little"))
        self._put = put
        for n, v in {8000: 200, 8001: 26100, 8002: 8, 8003: 0x10, 8010: 32, 8011: 20, 8012: 61}.items():
            put("D", n, v)
        now = datetime.datetime.now()
        for i, v in enumerate([now.second, now.minute, now.hour, now.day, now.month, now.year % 100,
                               (now.weekday() + 1) % 7]):
            put("D", 8013 + i, v)
        put("D", 0, 1250)
        put("D", 1, 42)
        put("D", 100, 0xFFFB)
        self._set("M", 8000, True)
        self._set("X", 0, True)
        self._set("X", 3, True)
        self._set("X", 0o31, True)             # expansion input X31
        self._set("Y", 0, True)
        self.mem[0x8000:0x8004] = bytes([0x10, 0x00, 0xAB, 0xCD])

    @staticmethod
    def _span(area, n):
        a, size = fx.data_address(area, n)
        return a, a + size

    def _set(self, area, n, on):
        byte, bit = fx.bit_image(area, n)
        if on:
            self.mem[byte] |= 1 << bit
        else:
            self.mem[byte] &= ~(1 << bit)

    def _force(self, address, on):
        if address >= 0x0F00:
            area, n = "M", 8000 + address - 0x0F00
        else:
            area, n = next((a, address - f) for a, (_, f, count) in sorted(fx.BIT_AREAS.items(), key=lambda x: -x[1][1])
                           if address >= f)
        self._set(area, n, on)
        if (area, n) == ("M", 8037) and on:          # forced STOP: the PLC stops and clears the flags
            for k in (8000, 8035, 8036, 8037):
                self._set("M", k, False)
        elif area == "M" and n == 8036 and on:
            self._set("M", 8000, True)

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, request):
        if request == fx.ENQ:
            self.buf = fx.ACK
            return
        body = request[1:-2]
        if request[:1] != fx.STX or fx.checksum(body) != request[-2:]:
            self.buf = fx.NAK
            return
        text = body[:-1].decode()
        cmd, rest = text[0], text[1:]
        if cmd == "0":
            address, count = int(rest[:4], 16), int(rest[4:6], 16)
            data = self.mem[address:address + count].hex().upper().encode() + fx.ETX
            self.buf = fx.STX + data + fx.checksum(data)
        elif cmd == "1":
            address, count = int(rest[:4], 16), int(rest[4:6], 16)
            self.mem[address:address + count] = bytes.fromhex(rest[6:6 + 2 * count])
            self.buf = fx.ACK
        elif cmd in "78":
            self._force(int(rest[2:4] + rest[:2], 16), cmd == "7")
            self.buf = fx.ACK
        else:
            self.buf = fx.NAK

    def read(self, n=1):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_until(self, end=b"\n", size=None):
        i = self.buf.find(end)
        cut = len(self.buf) if i < 0 else i + 1
        out, self.buf = self.buf[:cut], self.buf[cut:]
        return out

    def close(self):
        pass


def demo_fx():
    plc = fx.FxPlc(DemoPort())
    _setup(plc, {"modele": "FX1N-40MR-ES/UL", "ext1": "FX2N-16EX-ES/UL"})
    plc.nom += " (demo)"
    return plc


# ---- status / diagnostic ----

ERROR_FLAGS = [(8061, "Erreur materiel"), (8064, "Erreur de parametre"), (8065, "Erreur de syntaxe"),
               (8066, "Erreur de structure du programme"), (8067, "Erreur d'execution")]
RELAYS = {8000: "RUN (contact ouvert en RUN)", 8002: "impulsion initiale", 8004: "erreur (une des M8060..M8067)",
          8034: "toutes les sorties bloquees", 8035: "mode RUN / STOP force", 8036: "RUN force", 8037: "STOP force",
          8039: "cycle constant", 8061: "erreur materiel", 8064: "erreur de parametre", 8065: "erreur de syntaxe",
          8066: "erreur de structure du programme", 8067: "erreur d'execution", 8068: "erreur d'execution memorisee"}
BAD_RELAYS = {8004, 8034, 8061, 8064, 8065, 8066, 8067, 8068}
REGISTERS = {8000: "chien de garde (ms)", 8001: "type et version de la CPU", 8002: "capacite memoire (k pas)",
             8003: "type de memoire", 8004: "numero de l'indicateur d'erreur (M8060..M8067)",
             8010: "cycle actuel (x 0.1 ms)", 8011: "cycle mini (x 0.1 ms)", 8012: "cycle maxi (x 0.1 ms)",
             8039: "duree du cycle constant (ms)", 8061: "code erreur materiel", 8064: "code erreur de parametre",
             8065: "code erreur de syntaxe", 8066: "code erreur de structure", 8067: "code erreur d'execution",
             8068: "pas de l'erreur d'execution memorisee", 8069: "pas de l'erreur (M8065..M8067)"}


def faults(st) -> list:
    m, d = st["m"], st["d"]
    out = []
    for n, text in ERROR_FLAGS:
        if m[n] or d[n]:
            line = f"{text} (M{n}) : D{n} = {d[n]} : {fx.error_text(n, d[n])}"
            if n in (8065, 8066, 8067) and d[8069]:
                line += f", au pas {d[8069]} (D8069)"
            out.append(line)
    if m[8034]:
        out.append("M8034 a 1 : toutes les sorties sont bloquees (le programme tourne, les sorties restent a 0)")
    return out


def clock_text(st) -> str:
    d = st["d"]
    s, mi, h, day, mo, y = (d[8013 + i] for i in range(6))
    if not (1 <= mo <= 12 and 1 <= day <= 31 and h < 24 and mi < 60 and s < 60):
        return "non reglee"
    year = y if y >= 1980 else (2000 + y if y < 80 else 1900 + y)
    return f"{day:02d}/{mo:02d}/{year} {h:02d}:{mi:02d}:{s:02d}"


def fx_status(plc) -> dict:
    st = plc.status()
    m, d = st["m"], st["d"]
    errs = faults(st)
    run = bool(m[8000])
    program_error = any(m[n] for n in (8064, 8065, 8066))
    out = {"marque": "Mitsubishi", "modele": plc.nom, "reference": fx.cpu_text(d[8001]),
           "mode": "RUN" if run else "STOP", "alertes": errs}
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": run, "clignote": False, "defaut": False},
        {"nom": "ERROR", "texte": "erreur (clignote : programme, fixe : CPU)", "allume": bool(errs),
         "clignote": program_error, "defaut": bool(errs)},
    ]
    out["details"] = [("Cycle", f"{d[8010] / 10:.1f} ms (mini {d[8011] / 10:.1f}, maxi {d[8012] / 10:.1f})"),
                      ("Chien de garde", f"{d[8000]} ms"),
                      ("Memoire", f"{d[8002]}k pas, {fx.MEMORY_TYPES.get(d[8003], f'type 16#{d[8003]:02X}')}"),
                      ("Horloge", clock_text(st))]
    return out


def io_bits(plc, area, first, count):
    if not count:
        return [], ""
    bits = plc.read_bits(area, first, count)
    return bits, f"{fx.name(area, first)}..{fx.name(area, first + count - 1)}"


def unit_row(plc, position, label, model, inputs, outputs) -> dict:
    try:
        xs, x_addr = io_bits(plc, "X", *inputs)
        ys, y_addr = io_bits(plc, "Y", *outputs)
    except fx.FxError as e:
        return {"emplacement": position, "type": label, "adresse": "", "en_place": {"code": "", "nom": f"non lu ({e})",
                "references": ""}, "configure": {"code": "", "nom": model, "references": ""}, "etat": "non lu",
                "ok": False, "variables": []}
    show = lambda bits: " ".join(str(int(b)) for b in bits)
    parts = ([f"{x_addr} : {show(xs)}"] if xs else []) + ([f"{y_addr} : {show(ys)}"] if ys else [])
    variables = ([{"adresse": fx.name("X", inputs[0] + i), "libelle": f"{label} entree"} for i in range(inputs[1])]
                 + [{"adresse": fx.name("Y", outputs[0] + i), "libelle": f"{label} sortie"} for i in range(outputs[1])])
    full = model if model.startswith("FX") else f"FX2N-{model} / FX0N-{model}"
    return {"emplacement": position, "type": label, "adresse": " / ".join(a for a in (x_addr, y_addr) if a),
            "configure": {"code": "", "nom": full, "references": "manuel materiel FX1N (tableaux 1.1 a 1.10)"},
            "en_place": {"code": "", "nom": "  /  ".join(parts), "references": ""}, "etat": "ok", "ok": True,
            "variables": variables}


def special_rows(plc) -> list:
    rows, k = [], 0
    for i, (ext, text) in enumerate(zip(plc.expansions, plc.extensions_texte), 1):
        if ext is None and text:
            rows.append({"emplacement": i, "type": f"Extension {i}", "adresse": "",
                         "configure": {"code": "", "nom": text, "references": "modele non reconnu"},
                         "en_place": None, "etat": "modele non reconnu (ex. FX2N-16EX, FX0N-8ER)", "ok": False,
                         "variables": []})
        elif ext and ext not in EXPANSIONS:
            rows.append({"emplacement": i, "type": f"Bloc special n° {k}", "adresse": f"FROM / TO K{k}",
                         "configure": {"code": "", "nom": ext, "references": "pas d'entrees / sorties X / Y"},
                         "en_place": None, "etat": "ok", "ok": True, "variables": []})
            k += 1
    return rows


def fx_diagnostic(plc) -> dict:
    st = plc.status()
    m, d = st["m"], st["d"]
    out = {"alertes": faults(st), "config_compilee": "", "systeme": [], "modules": []}
    for n, text in RELAYS.items():
        out["systeme"].append({"adresse": f"M{n}", "valeur": str(int(m[n])), "texte": text,
                               "ok": not (n in BAD_RELAYS and m[n])})
    for n, text in REGISTERS.items():
        extra = ""
        if n == 8001:
            extra = f" : {fx.cpu_text(d[n])}"
        elif n in (8061, 8064, 8065, 8066, 8067) and d[n]:
            extra = f" : {fx.error_text(n, d[n])}"
        out["systeme"].append({"adresse": f"D{n}", "valeur": str(d[n]), "texte": text + extra,
                               "ok": not (n in (8061, 8064, 8065, 8066, 8067) and d[n])})
    for pos, (label, model, inputs, outputs) in enumerate(layout(plc.modele, plc.expansions)):
        out["modules"].append(unit_row(plc, pos, label, model, inputs, outputs))
    out["modules"] += special_rows(plc)
    out["parametres_cpu"] = [("CPU", fx.cpu_text(d[8001])), ("Memoire", f"{d[8002]}k pas"),
                             ("Type de memoire", fx.MEMORY_TYPES.get(d[8003], f"16#{d[8003]:02X}")),
                             ("Chien de garde", f"{d[8000]} ms"), ("Horloge", clock_text(st)),
                             ("Extensions", "le FX ne dit pas quelles unites sont montees : celles renseignees "
                                            "a la connexion, numerotees en octal a la suite de la base")]
    return out


# ---- watch table ----

WORD_FORMATS = ["dec", "ud", "hex", "bin"]


class FxVar:
    """'D100', 'D100:hex', 'T5', 'C2' (current values), 'C200' (32 bits), 'X27' (octal), 'Y0', 'M8000', 'S0'."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        try:
            self.area, self.n = fx.parse_address(address)
        except fx.FxError as e:
            raise ValueError(str(e)) from None
        self.word = self.area in fx.WORD_AREAS
        self.size = fx.data_address(self.area, self.n)[1] if self.word else 0
        self.fmt = (fmt.strip().lower() or "dec") if self.word else ""
        if self.word and self.fmt not in WORD_FORMATS:
            raise ValueError(f"format {self.fmt} : {', '.join(WORD_FORMATS)}")

    def read(self, plc):
        if self.word:
            return plc.read_words(self.area, self.n, 1)[0]
        return int(plc.read_bits(self.area, self.n, 1)[0])

    def decode(self, v) -> str:
        if not self.word:
            return str(int(v))
        bits = 8 * self.size
        if self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:0{bits // 4}X}"
        if self.fmt == "bin":
            t = f"{v:0{bits}b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, bits, 4))
        return str(v - (1 << bits) if v >> (bits - 1) else v)

    def encode(self, text: str) -> int:
        t = text.strip().lower().replace("_", "")
        if not self.word:
            if t not in ("0", "1"):
                raise ValueError("0 ou 1")
            return int(t)
        try:
            if t.startswith("16#") or t.startswith("0x"):
                v = int(t[3:] if t.startswith("16#") else t[2:], 16)
            elif t.startswith("2#"):
                v = int(t[2:], 2)
            elif self.fmt == "hex":
                v = int(t, 16)
            elif self.fmt == "bin":
                v = int(t, 2)
            else:
                v = int(t)
        except ValueError:
            raise ValueError("valeur illisible") from None
        bits = 8 * self.size
        if not -(1 << (bits - 1)) <= v < (1 << bits):
            raise ValueError(f"hors {-(1 << (bits - 1))}..{(1 << bits) - 1}")
        return v & ((1 << bits) - 1)


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        try:
            v = FxVar(str(t))
            out["valeurs"][str(t)] = v.decode(v.read(plc))
        except ValueError as e:
            out["erreurs"][str(t)] = str(e)
        except fx.FxRefused as e:
            out["erreurs"][str(t)] = str(e)
    return out


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = FxVar(text)
            if v.area == "X":
                raise ValueError("les entrees X ne s'ecrivent pas (l'automate les relit a chaque cycle)")
            if v.area in ("T", "C") and not v.word:
                raise ValueError("contact de tempo / compteur : ecrire la valeur en cours")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = v.read(plc)
        if v.word:
            plc.write_words(v.area, v.n, [value])
        else:
            plc.force(v.area, v.n, bool(value))
        back = v.read(plc)
        results.append({"adresse": text, "avant": v.decode(old), "relu": v.decode(back), "ok": back == value})
    return {"resultats": results, "run": bool(plc.read_bits("M", 8000, 1)[0])}


# ---- data copies (D0..D511) ----

def read_all_d(plc, progress=None) -> list:
    out = []
    for start in range(0, DATA_WORDS, 32):
        out += plc.read_words("D", start, 32)
        if progress:
            progress(f"lecture D : {len(out)} mots", len(out), DATA_WORDS)
    return out


def changed_runs(addrs) -> list:
    runs = []
    for a in sorted(addrs):
        if runs and runs[-1][0] + runs[-1][1] == a and runs[-1][1] < 32:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return runs


def _running(plc) -> bool:
    return bool(plc.read_bits("M", 8000, 1)[0])


class FxOps:
    """Session calls for a Mitsubishi FX."""

    def __init__(self, session):
        self.s = session

    @property
    def data(self) -> Path:
        return self.s.tsx_backups.parent / "mitsubishi" / "donnees"

    def status(self, plc):
        return fx_status(plc)

    def diagnostic(self):
        return self.s._call(fx_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def _mode(self, run):
        def go(plc):
            state = plc.set_mode(run)
            if state != run:
                raise _drv().DriverError(
                    f"L'automate est reste en {'RUN' if state else 'STOP'} (commande par M8035 / M8036 / M8037). "
                    "Le selecteur RUN / STOP de l'automate peut l'emporter : essayer avec le selecteur sur STOP. "
                    "Voyant ERROR allume : erreur programme ou parametre, voir Diagnostic.")
            return {"ok": True}
        return self.s._call(go)

    def start_cpu(self):
        return self._mode(True)

    def stop_cpu(self):
        return self._mode(False)

    def set_clock(self):
        def run(plc):
            plc.set_clock(datetime.datetime.now())
            time.sleep(1.1)
            return {"horloge": clock_text(plc.status())}
        return self.s._call(run)

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": tsx_web.list_copies(self.data, tsx_web.DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            values = read_all_d(plc, progress)
            text = dl.data_csv(values, plc.nom, time.strftime("%Y-%m-%d %H:%M:%S"))
            return tsx_web.save_data(self.data, text, plc.nom, note, f"{len(values)} D")
        return self.s.start_job("sauvegarde des donnees", work)

    def _saved(self, backup_id):
        path = _drv().backup_path(self.data, backup_id)
        try:
            return dl.parse_data_csv((path / tsx_web.DATA_FILE).read_text(encoding="utf-8"))
        except (OSError, dl.DeltaError) as e:
            raise _drv().DriverError(f"copie illisible : {e}") from None

    def data_plan(self, backup_id):
        saved, plc_in_file = self._saved(backup_id)

        def run(plc):
            current = read_all_d(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            return {"zone": "D", "mots": len(saved), "differents": len(diffs), "zone_automate": len(current),
                    "hors_zone": sum(1 for a in saved if a >= len(current)), "kw_ignores": 0,
                    "automate_fichier": plc_in_file, "automate": plc.nom, "run": _running(plc),
                    "exemples": [{"adresse": f"D{a}", "automate": signed16(current[a]), "fichier": signed16(diffs[a])}
                                 for a in sorted(diffs)[:12]]}
        return self.s._call(run)

    def data_restore(self, backup_id):
        saved, _ = self._saved(backup_id)

        def work(plc, progress):
            progress("lecture des D de l'automate", 0, 0)
            current = read_all_d(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            runs = changed_runs(diffs)
            for i, (start, length) in enumerate(runs, 1):
                progress(f"ecriture D{start} ({length} mot(s))", i, len(runs))
                plc.write_words("D", start, [saved[a] for a in range(start, start + length)])
            progress("verification", len(runs), len(runs))
            after = read_all_d(plc)
            wrong = [f"D{a}" for a in sorted(saved) if a < len(after) and after[a] != saved[a]]
            return {"ecrits": len(diffs), "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_mitsubishi")

    def data_import(self, files, note):
        from plc_online.web import delta_web
        self.data.mkdir(parents=True, exist_ok=True)
        return delta_web.import_data_files(self.data, files, note)

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction pas encore disponible pour Mitsubishi FX "
                                 "(programme : python tools/mitsubishi.py programme, lecture seule)")
