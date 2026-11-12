"""Delta DVP in the web interface: status, special registers, built-in I/O, watch table, D data copies."""
import datetime
import re
import struct
import time
from pathlib import Path

from plc_online.protocols import delta as dl
from plc_online.protocols.modbus import AsciiTransport, ModbusError, PlcRefused, lrc, signed16
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM", "defaut": "COM4", "type": "port"},
            {"cle": "mode", "nom": "Mode (ascii ou rtu)", "defaut": "ascii", "type": "texte"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 9600},
            {"cle": "parite", "nom": "Parite (E, O ou N)", "defaut": "E", "type": "texte"},
            {"cle": "station", "nom": "Station", "defaut": 1}]

# built-in I/O of the DVP-SV models (inputs, outputs)
BUILT_IN = {"DVP28SV": (16, 12), "DVP12SA": (8, 4), "DVP14SS": (8, 6), "DVP28SA": (16, 12)}
DEFAULT_IO = (16, 12)                # DVP28SV


def open_delta(settings: dict):
    port = str(settings.get("port") or "COM4").strip()
    mode = str(settings.get("mode") or "ascii").strip().lower()
    parity = str(settings.get("parite") or "E").strip().upper()[:1]
    if mode not in ("ascii", "rtu"):
        raise _drv().DriverError("Mode : ascii ou rtu")
    if parity not in ("E", "O", "N"):
        raise _drv().DriverError("Parite : E, O ou N")
    try:
        baud, station = int(settings.get("baud") or 9600), int(settings.get("station") or 1)
    except (TypeError, ValueError):
        raise _drv().DriverError("Vitesse et station : des nombres") from None
    try:
        plc = dl.open_delta(port, mode, baud, parity, 2 if (mode == "rtu" and parity == "N") else 1, station)
    except Exception as e:                     # serial errors: port missing or busy
        raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    try:
        plc.read_words("D", 1000, 1)
    except PlcRefused:
        pass
    except ModbusError as e:
        plc.close()
        bits = 7 if mode == "ascii" else 8
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({mode.upper()} {baud} {bits}{parity}, station "
                                 f"{station}) : {e}. Fils + / - inverses ? COM2 utilise par le programme ? Essayer "
                                 f"python tools/delta.py chercher --port {port}") from None
    plc.nom = "Delta DVP"
    return plc


# ---- demo ----

class DemoPort:
    """Serial port answering Modbus ASCII like a DVP28SV (coils + registers by Modbus address)."""

    def __init__(self):
        self.coils = {0x0800 + 1000: True, 0x0400: True, 0x0402: True, 0x0500 + 1: True}
        self.regs = {0x1000 + 1000: 200, 0x1000 + 1001: 0x0C24, 0x1000 + 1002: 7920, 0x1000 + 1010: 31,
                     0x1000 + 1011: 20, 0x1000 + 1012: 58, 0x1000 + 10: 1250, 0x1000 + 11: 42}
        now = datetime.datetime.now()
        for i, v in enumerate([now.second, now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7,
                               now.year % 100]):
            self.regs[0x1000 + 1313 + i] = v
        self.buf = b""

    def reset_input_buffer(self):
        self.buf = b""

    @staticmethod
    def _valid(addr):
        if 0x1000 <= addr < 0x2000:
            return addr - 0x1000 < 4096
        if addr >= 0x9000:
            return addr - 0x9000 + 4096 < 10000
        return True

    def write(self, frame):
        data = bytes.fromhex(frame[1:-2].decode())
        unit, func = data[0], data[1]
        addr, n = struct.unpack(">HH", data[2:6])
        if func == 3:
            if not all(self._valid(a) for a in range(addr, addr + n)):
                pdu = bytes([0x83, 2])
            else:
                pdu = bytes([3, 2 * n]) + struct.pack(f">{n}H", *[self.regs.get(a, 0) for a in range(addr, addr + n)])
        elif func == 1:
            raw = bytearray((n + 7) // 8)
            for i in range(n):
                if self.coils.get(addr + i):
                    raw[i // 8] |= 1 << (i % 8)
            pdu = bytes([1, len(raw)]) + raw
        elif func in (5, 6):
            if func == 6:
                self.regs[addr] = n
            else:
                self.coils[addr] = n == 0xFF00
            pdu = data[1:6]
        elif func == 16:
            for i, v in enumerate(struct.unpack(f">{n}H", data[7:7 + 2 * n])):
                self.regs[addr + i] = v
            pdu = data[1:6]
        else:
            pdu = bytes([func | 0x80, 1])
        body = bytes([unit]) + pdu
        self.buf = b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n"

    def read_until(self, end):
        out, self.buf = self.buf, b""
        return out

    def close(self):
        pass


def demo_delta():
    plc = dl.Delta(AsciiTransport(DemoPort()), 1)
    plc.nom = "Delta DVP (demo)"
    return plc


# ---- status / diagnostic ----

def _get(read):
    try:
        return read()
    except PlcRefused:
        return None


def faults(st) -> list:
    b, w = st["bits"], st["mots"]
    out = []
    if b[1004] or w[1004]:
        out.append(f"Erreur de syntaxe du programme (M1004) : D1004 = 16#{w[1004]:04X} : {dl.error_text(w[1004])}"
                   + (f", au pas D1137 = {w[1137]}" if w[1137] else ""))
    if b[1008]:
        out.append(f"Chien de garde depasse (M1008) : programme trop long ou boucle, au pas D1008 = {w[1008]}")
    if b[1009]:
        out.append("Tension 24 V insuffisante (M1009) : verifier l'alimentation")
    if b[1067] or b[1068] or w[1067]:
        out.append(f"Erreur d'execution (M1067 = {int(bool(b[1067]))}) : D1067 = 16#{w[1067]:04X}, au pas D1068 = {w[1068]}")
    return out


def clock_text(plc) -> str:
    try:
        s, mi, h, d, mo, wd, y = plc.clock()
    except (ModbusError, dl.DeltaError):
        return ""
    return f"{d:02d}/{mo:02d}/{2000 + y if y < 100 else y} {h:02d}:{mi:02d}:{s:02d}"


def com_lines(plc) -> list:
    out = []
    try:
        for port, text, kept in plc.com_settings():
            out.append((port, text + ("" if kept else "  (non conserve : defaut au redemarrage)")))
        station = _get(lambda: plc.read_words("D", dl.STATION, 1)[0])
        if station is not None:
            out.append(("Station (D1121)", str(station)))
    except (ModbusError, dl.DeltaError):
        pass
    return out


def delta_status(plc) -> dict:
    st = plc.status()
    b, w = st["bits"], st["mots"]
    errs = faults(st)
    run = bool(b[1000])
    out = {"marque": "Delta", "modele": plc.nom, "reference": f"firmware 16#{w[1001]:04X}",
           "mode": "RUN" if run else "STOP", "alertes": errs, "voyants": [], "details": []}
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": run, "clignote": False, "defaut": False},
        {"nom": "ERROR", "texte": "erreur programme / execution", "allume": bool(errs), "clignote": bool(errs),
         "defaut": bool(errs)},
        {"nom": "LV", "texte": "tension basse", "allume": bool(b[1009]), "clignote": False, "defaut": bool(b[1009])},
    ]
    out["details"] = [("Cycle", f"{w[1010] / 10:.1f} ms (mini {w[1011] / 10:.1f}, maxi {w[1012] / 10:.1f})"),
                      ("Chien de garde", f"{w[1000]} ms"), ("Capacite programme", f"{w[1002]} pas")]
    clock = clock_text(plc)
    if clock:
        out["details"].append(("Horloge", clock))
    out["details"] += com_lines(plc)
    return out


def io_row(plc, pos, area, count, label):
    try:
        bits = plc.read_bits(area, 0, count)
        values, ok, etat = " ".join(str(int(v)) for v in bits), True, "ok"
    except (ModbusError, dl.DeltaError) as e:
        values, ok, etat = f"non lu ({e})", False, "non lu"
    last = dl.name(area, count - 1)
    return {"emplacement": pos, "type": label, "adresse": f"{area}0..{last}",
            "configure": {"code": "", "nom": label, "references": f"{count} voies"},
            "en_place": {"code": "", "nom": values, "references": ""}, "etat": etat, "ok": ok,
            "variables": [{"adresse": dl.name(area, i), "libelle": f"{label} {i + 1}"} for i in range(count)]}


def delta_diagnostic(plc) -> dict:
    st = plc.status()
    b, w = st["bits"], st["mots"]
    out = {"alertes": faults(st), "parametres_cpu": [], "config_compilee": "", "systeme": [], "modules": []}
    bad_bits = {1004, 1008, 1009, 1067, 1068}
    for n, text in dl.STATUS_BITS.items():
        out["systeme"].append({"adresse": f"M{n}", "valeur": str(int(bool(b[n]))), "texte": text,
                               "ok": not (n in bad_bits and b[n])})
    for n, text in dl.STATUS_WORDS.items():
        ok = not (n in (1004, 1067) and w[n])
        extra = f" : {dl.error_text(w[n])}" if n in (1004, 1067) and w[n] else ""
        out["systeme"].append({"adresse": f"D{n}", "valeur": f"{w[n]}  (16#{w[n]:04X})", "texte": text + extra, "ok": ok})
    n_in, n_out = DEFAULT_IO
    out["modules"] = [io_row(plc, 0, "X", n_in, "Entrees integrees"), io_row(plc, 0, "Y", n_out, "Sorties integrees")]
    ext_x = _get(lambda: plc.read_words("D", 1142, 1)[0]) or 0
    ext_y = _get(lambda: plc.read_words("D", 1143, 1)[0]) or 0
    if 0 < ext_x <= 256 - n_in:
        row = io_row(plc, 1, "X", n_in + ext_x, "Entrees d'extension")
        row["variables"] = row["variables"][n_in:]
        row["adresse"] = f"{dl.name('X', n_in)}..{dl.name('X', n_in + ext_x - 1)}"
        out["modules"].append(row)
    if 0 < ext_y <= 256 - n_out:
        row = io_row(plc, 1, "Y", n_out + ext_y, "Sorties d'extension")
        row["variables"] = row["variables"][n_out:]
        row["adresse"] = f"{dl.name('Y', n_out)}..{dl.name('Y', n_out + ext_y - 1)}"
        out["modules"].append(row)
    specials = _get(lambda: plc.read_words("D", 1140, 1)[0]) or 0
    if 0 < specials <= 8:
        ids = _get(lambda: plc.read_words("D", dl.MODULE_IDS, specials)) or []
        for i, code in enumerate(ids, 1):
            out["modules"].append({"emplacement": 1 + i, "type": "module special", "adresse": f"CR (FROM/TO {i - 1})",
                                   "configure": {"code": f"{code:04X}", "nom": f"module {i}", "references": f"code 16#{code:04X}"},
                                   "en_place": None, "etat": "ok", "ok": True, "variables": []})
    out["parametres_cpu"] = [("Firmware", f"16#{w[1001]:04X}"), ("Capacite", f"{w[1002]} pas"),
                             ("Chien de garde", f"{w[1000]} ms"), ("Horloge", clock_text(plc) or "non lue")]
    out["parametres_cpu"] += com_lines(plc)
    return out


# ---- watch table ----

ADDRESS = re.compile(r"^([DMXYSTC])(\d+)$", re.IGNORECASE)
WORD_FORMATS = ["dec", "ud", "hex", "bin"]


class DeltaVar:
    """'D100', 'D100:hex', 'M1000', 'X17', 'Y0', 'T5', 'C2', 'S0' (T / C : current value)."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        m = ADDRESS.match(address.strip())
        if not m:
            raise ValueError("adresse Delta : D100, M10, X17 (octal), Y0, T5, C2, S0")
        try:
            self.area, self.n = dl.parse_address(address.strip())
        except dl.DeltaError as e:
            raise ValueError(str(e)) from None
        self.word = self.area in dl.WORD_AREAS
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
        if self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:04X}"
        if self.fmt == "bin":
            t = f"{v:016b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, 16, 4))
        return str(signed16(v))

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
        if not -32768 <= v <= 65535:
            raise ValueError("hors -32768..65535")
        return v & 0xFFFF


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        try:
            v = DeltaVar(str(t))
            out["valeurs"][str(t)] = v.decode(v.read(plc))
        except ValueError as e:
            out["erreurs"][str(t)] = str(e)
        except PlcRefused as e:
            out["erreurs"][str(t)] = str(e)
    return out


WRITABLE = ("D", "M", "Y", "T", "C", "S")


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = DeltaVar(text)
            if v.area not in WRITABLE:
                raise ValueError("les entrees X ne s'ecrivent pas")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = v.read(plc)
        if v.word:
            plc.write_word(v.area, v.n, value)
        else:
            plc.write_bit(v.area, v.n, bool(value))
        back = v.read(plc)
        results.append({"adresse": text, "avant": v.decode(old), "relu": v.decode(back), "ok": back == value})
    run = _get(lambda: plc.read_bits("M", 1000, 1)[0])
    return {"resultats": results, "run": bool(run)}


# ---- data copies (all D) ----

def import_data_files(folder: Path, files: list, note: str) -> dict:
    made, ignored = [], []
    for name, data in files:
        base = Path(name.replace("\\", "/")).name
        if not base.lower().endswith(".csv"):
            ignored.append(base)
            continue
        try:
            values, plc = dl.parse_data_csv(data.decode("utf-8", "replace"))
        except dl.DeltaError as e:
            raise _drv().DriverError(f"{base} : {e}") from None
        text = dl.data_csv(values, plc or "", time.strftime("%Y-%m-%d %H:%M:%S"))
        made.append(tsx_web.save_data(folder, text, plc or "", note or f"importe de {base}", f"{len(values)} D"))
    if not made:
        raise _drv().DriverError("Aucun fichier .csv de donnees Delta dans les fichiers choisis")
    return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}


class DeltaOps:
    """Session calls for a Delta DVP (status, diagnostic, watch table, clock, D data copies)."""

    def __init__(self, session):
        self.s = session

    @property
    def data(self) -> Path:
        return self.s.tsx_backups.parent / "delta" / "donnees"

    def status(self, plc):
        return delta_status(plc)

    def diagnostic(self):
        return self.s._call(delta_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def set_clock(self):
        def run(plc):
            plc.set_clock(datetime.datetime.now())
            time.sleep(1.1)
            return {"horloge": clock_text(plc)}
        return self.s._call(run)

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": tsx_web.list_copies(self.data, tsx_web.DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            values = plc.read_all_d(lambda n: progress(f"lecture D : {n} mots", 0, 0))
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
            current = plc.read_all_d()
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            return {"zone": "D", "mots": len(saved), "differents": len(diffs), "zone_automate": len(current),
                    "hors_zone": sum(1 for a in saved if a >= len(current)), "kw_ignores": 0,
                    "automate_fichier": plc_in_file, "automate": plc.nom,
                    "run": bool(_get(lambda: plc.read_bits("M", 1000, 1)[0])),
                    "exemples": [{"adresse": f"D{a}", "automate": signed16(current[a]), "fichier": signed16(diffs[a])}
                                 for a in sorted(diffs)[:12]]}
        return self.s._call(run)

    def data_restore(self, backup_id):
        saved, _ = self._saved(backup_id)

        def work(plc, progress):
            progress("lecture des D de l'automate", 0, 0)
            current = plc.read_all_d()
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            runs = dl.changed_runs(diffs)
            for i, (start, length) in enumerate(runs, 1):
                progress(f"ecriture D{start} ({length} mot(s))", i, len(runs))
                plc.write_words("D", start, [saved[a] for a in range(start, start + length)])
            progress("verification", len(runs), len(runs))
            after = plc.read_all_d()
            wrong = [f"D{a}" for a in sorted(saved) if a < len(after) and after[a] != saved[a]]
            return {"ecrits": len(diffs), "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_delta")

    def data_import(self, files, note):
        self.data.mkdir(parents=True, exist_ok=True)
        return import_data_files(self.data, files, note)

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction pas encore disponible pour Delta (programme : capture WPLSoft a faire)")
