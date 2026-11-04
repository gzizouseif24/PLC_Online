"""Panasonic FP0R in the web interface: status, special relays / registers, built-in I/O, watch table,
RUN / PROG, DT data copies.

Status comes from the standard FP special relays and registers (R9000.., DT90000..): they are
the same on every FP-series PLC. The RT reply is shown raw too, its decoding is not confirmed yet.
"""
import re
import time
from pathlib import Path

from plc_online.checksum import xor_checksum
from plc_online.protocols import mewtocol as mw
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM", "defaut": "COM3", "type": "port"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 9600},
            {"cle": "parite", "nom": "Parite (E, O ou N)", "defaut": "O", "type": "texte"},
            {"cle": "bits", "nom": "Bits de donnees (7 ou 8)", "defaut": 8},
            {"cle": "stop", "nom": "Bits de stop (1 ou 2)", "defaut": 1},
            {"cle": "station", "nom": "Station", "defaut": 1}]

# special relays (WR900 / WR901 / WR902) and registers read for the status
RELAYS = {
    (900, 0x0): "erreur d'autodiagnostic (code dans DT90000)",
    (900, 0x7): "erreur d'operation, memorisee (adresse dans DT90017)",
    (900, 0x8): "erreur d'operation, derniere (adresse dans DT90018)",
    (900, 0xE): "erreur de communication port outil / RS-232C",
    (900, 0xF): "erreur de cycle constant",
    (901, 0x0): "toujours ON",
    (901, 0x3): "impulsion au demarrage (1er cycle)",
    (901, 0xC): "horloge 1 s",
    (902, 0x0): "mode RUN",
    (902, 0x6): "message (F149 MSG)",
    (902, 0x7): "mode commande a distance",
    (902, 0x9): "forcage en cours",
}
BAD_RELAYS = {(900, 0x0), (900, 0x7), (900, 0x8), (900, 0xE), (900, 0xF), (902, 0x9)}
REGISTERS = {
    90000: "code d'autodiagnostic (E..)",
    90017: "adresse (pas) de l'erreur d'operation, memorisee",
    90018: "adresse (pas) de l'erreur d'operation, derniere",
    90022: "cycle actuel (0,1 ms)",
    90023: "cycle mini (0,1 ms)",
    90024: "cycle maxi (0,1 ms)",
}

# self-diagnostic codes (DT90000), from the FP0R manual
SELF_DIAG = {
    1: "erreur de syntaxe", 2: "sortie en double", 3: "instructions non appariees (MC/MCE, JP/LBL...)",
    4: "parametres incoherents (registres systeme)", 5: "zone de programme (instruction hors zone)",
    6: "memoire de compilation pleine", 7: "type d'instruction evoluee", 8: "operandes d'instruction evoluee",
    26: "erreur memoire programme (ROM utilisateur)", 27: "trop de modules d'extension",
    28: "trop de modules enregistres", 45: "erreur d'operation (arret sur erreur d'operation)",
    50: "erreur pile de sauvegarde",
}

# built-in I/O by model (inputs, outputs) - FP0R-C16 when the model is unknown
DEFAULT_IO = (8, 8)
DATA_SIZES = (32765, 12315)      # DT area: F32 / T32, then C10..C32


def self_diag_text(code: int) -> str:
    if not code:
        return "aucune"
    if 100 <= code <= 299:
        return f"E{code} : erreur definie par le programme (F148 ERR)"
    return f"E{code} : {SELF_DIAG.get(code, 'voir la liste des codes du manuel FP0R')}"


def bit_name(area: str, n) -> str:
    """(901, 12) or 12 (word 0) -> 'R901C': word in decimal + bit in hex."""
    word, bit = n if isinstance(n, tuple) else divmod(n, 16)
    return f"{area}{word}{bit:X}" if word else f"{area}{bit:X}"


def open_fp0r(settings: dict):
    port = str(settings.get("port") or "COM3").strip()
    parity = str(settings.get("parite") or "O").strip().upper()[:1]
    if parity not in ("E", "O", "N"):
        raise _drv().DriverError("Parite : E, O ou N")
    try:
        baud, station = int(settings.get("baud") or 9600), int(settings.get("station") or 1)
        bits, stop = int(settings.get("bits") or 8), int(settings.get("stop") or 1)
    except (TypeError, ValueError):
        raise _drv().DriverError("Vitesse, bits et station : des nombres") from None
    if bits not in (7, 8) or stop not in (1, 2):
        raise _drv().DriverError("Bits de donnees : 7 ou 8, bits de stop : 1 ou 2")
    try:
        ser = mw.open_port(port, baud, parity, timeout=0.6, bytesize=bits, stopbits=stop)
    except Exception as e:                     # serial errors: port missing or busy
        raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    plc = mw.Fp0r(ser, station)
    try:
        plc.read_dt(90000, 1)
    except mw.PlcRefused:
        pass
    except mw.MewtocolError as e:
        ser.close()
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({baud} {bits}{parity}{stop}, station {station}) : {e}. "
                                 "Cable sur le port outil (mini-DIN 5) ? FPWIN ferme ? Reglages du port changes : "
                                 "bouton Chercher les reglages.") from None
    plc.nom = "Panasonic FP0R"
    plc.close = ser.close
    return plc


# ---- search: tool port settings unknown ----

SPEEDS = [9600, 115200, 19200, 38400, 57600, 4800, 2400]
FORMATS = [(8, "O", 1), (8, "N", 1), (8, "E", 1), (7, "O", 1), (7, "E", 1), (7, "N", 1),
           (8, "O", 2), (8, "N", 2), (8, "E", 2), (7, "O", 2), (7, "E", 2), (7, "N", 2)]


def _serial(port, baud, bits, parity, stop=1):
    return mw.open_port(port, baud, parity, timeout=0.25, bytesize=bits, stopbits=stop)


def mewtocol_frame(station: str) -> bytes:
    """RT (read status) to station '01' .. '99', or 'EE' (any station on the tool port)."""
    body = f"%{station}#RT"
    return (body + xor_checksum(body) + "\r").encode("ascii")


def mewtocol_answer(got: bytes):
    """Station number of a valid MEWTOCOL reply (BCC checked), else None. An error reply counts:
    the settings are right."""
    text = got.decode("ascii", "replace").strip()
    i = text.find("%")
    if i < 0:
        return None
    text = text[i:]
    if len(text) < 6 or text[3] not in "$!" or xor_checksum(text[:-2]) != text[-2:].upper():
        return None
    return int(text[1:3]) if text[1:3].isdigit() else None


def modbus_frame(unit=1) -> bytes:
    import struct
    from plc_online.protocols.modbus import crc16
    body = bytes([unit, 3, 0, 0, 0, 1])
    return body + struct.pack("<H", crc16(body))


def modbus_answer(got: bytes) -> bool:
    import struct
    from plc_online.protocols.modbus import crc16
    for n in (7, 5):                               # registers read, or an exception reply
        if len(got) >= n and struct.unpack("<H", got[n - 2:n])[0] == crc16(got[:n - 2]):
            return True
    return False


def probe(ser, frame: bytes) -> bytes:
    ser.reset_input_buffer()
    ser.write(frame)
    got = ser.read_until(b"\r") if frame.endswith(b"\r") else ser.read(16)
    return got or b""


def search(port: str, progress=None, opener=_serial) -> dict:
    """Try every tool port setting: MEWTOCOL (stations EE and 01), then Modbus RTU (the tool port
    can be set to Modbus in the system registers). Returns the settings found, or a diagnosis."""
    tried, echo, noise = 0, [], []
    total = len(SPEEDS) * len(FORMATS) + len(SPEEDS) * 3
    for baud in SPEEDS:
        for bits, parity, stop in FORMATS:
            tried += 1
            if progress:
                progress(f"MEWTOCOL {baud} {bits}{parity}{stop}", tried, total)
            try:
                ser = opener(port, baud, bits, parity, stop)
            except Exception as e:                 # port missing or busy: no use trying further
                raise _drv().DriverError(tsx_web.port_error(port, e)) from None
            try:
                for st in ("EE", "01"):
                    frame = mewtocol_frame(st)
                    got = probe(ser, frame)
                    station = mewtocol_answer(got)
                    if station is not None:
                        return {"trouve": True, "protocole": "MEWTOCOL",
                                "reglages": {"port": port, "baud": baud, "bits": bits, "parite": parity,
                                             "stop": stop, "station": station},
                                "texte": f"FP0R trouve : {baud} bauds, {bits} bits, parite {parity}, station {station}."
                                         + (" 2 bits de stop." if stop == 2 else "")}
                    if got and got.startswith(frame.strip()):
                        echo.append(f"{baud} {bits}{parity}{stop}")
                    elif got:
                        noise.append(f"{baud} {bits}{parity}{stop}")
            finally:
                ser.close()
    for baud in SPEEDS:
        for parity in ("N", "O", "E"):
            tried += 1
            if progress:
                progress(f"Modbus RTU {baud} 8{parity}1", tried, total)
            ser = opener(port, baud, 8, parity, 1)
            try:
                if modbus_answer(probe(ser, modbus_frame())):
                    return {"trouve": False, "protocole": "Modbus RTU",
                            "texte": f"Le port outil repond en Modbus RTU ({baud} bauds, 8{parity}1, station 1), pas en "
                                     "MEWTOCOL : il a ete regle en Modbus dans les registres systeme (412..). Le "
                                     "remettre en MEWTOCOL (liaison ordinateur) avec FPWIN, ou passer par le port USB "
                                     "de l'automate s'il en a un."}
            finally:
                ser.close()
    if echo and not noise:
        why = ("Seulement l'echo de nos trames : le cable renvoie ce qu'il envoie, l'automate ne repond pas. "
               "Cable branche sur le port outil ? Automate sous tension ?")
    elif noise:
        why = (f"Des octets arrivent mais illisibles ({', '.join(noise[:4])}) : autre protocole sur le port outil "
               "(mode general / liaison PC programmee), ou autre appareil sur la ligne.")
    else:
        why = ("Silence total sur toutes les vitesses. Mauvais port COM (voir le Gestionnaire de peripheriques), "
               "cable debranche, ou port outil en mode general (utilise par le programme : F159 MREC / F144 TRNS). "
               "Avec FPWIN : Hors ligne -> Registres systeme -> Port outil.")
    return {"trouve": False, "protocole": "", "texte": f"Aucune reponse ({tried} essais). {why}"}


# ---- demo ----

class DemoPort:
    """Serial port answering MEWTOCOL like an FP0R-C16 in RUN."""

    def __init__(self):
        self.dt = {i: 0 for i in list(range(12315)) + list(range(90000, 90100))}
        self.dt.update({0: 1250, 1: 42, 10: 300, 90000: 0, 90017: 0, 90018: 0, 90022: 18, 90023: 12, 90024: 41})
        self.contacts = {("X", 0): 0b0000_0000_0000_0101, ("Y", 0): 0b0000_0000_0000_0010, ("R", 0): 0b1,
                         ("R", 900): 0, ("R", 901): 0x0001 | 0x1000, ("R", 902): 0x0001}   # R9010, R901C, R9020
        self.run = True
        self.program = [0x1234 + i for i in range(40)]
        self.buf = b""

    def reset_input_buffer(self):
        self.buf = b""

    def _answer(self, cmd: str) -> str:
        if cmd.startswith("RDD"):
            a, b = int(cmd[3:8]), int(cmd[8:13])
            if any(i not in self.dt for i in range(a, b + 1)):
                return "!66"
            return "$RD" + "".join(mw.encode_word(self.dt[i]) for i in range(a, b + 1))
        if cmd.startswith("WDD"):
            a, b = int(cmd[3:8]), int(cmd[8:13])
            for i, v in enumerate(mw.decode_words(cmd[13:])):
                self.dt[a + i] = v
            return "$WD"
        if cmd.startswith("RCC"):
            area, a, b = cmd[3], int(cmd[4:8]), int(cmd[8:12])
            return "$RC" + "".join(mw.encode_word(self.contacts.get((area, i), 0)) for i in range(a, b + 1))
        if cmd.startswith("RCS"):
            area, w, bit = cmd[3], int(cmd[4:7]), int(cmd[7], 16)
            return "$RC" + str((self.contacts.get((area, w), 0) >> bit) & 1)
        if cmd.startswith("WCS"):
            area, w, bit, v = cmd[3], int(cmd[4:7]), int(cmd[7], 16), cmd[8] == "1"
            if area == "X":
                return "!61"
            old = self.contacts.get((area, w), 0)
            self.contacts[(area, w)] = old | (1 << bit) if v else old & ~(1 << bit)
            return "$WC"
        if cmd.startswith("WCC"):
            area, w = cmd[3], int(cmd[4:8])
            self.contacts[(area, w)] = mw.decode_words(cmd[12:16])[0]
            return "$WC"
        if cmd in ("RMG", "RMP"):
            self.run = cmd == "RMG"
            r902 = self.contacts[("R", 902)]
            self.contacts[("R", 902)] = r902 | 1 if self.run else r902 & ~1
            return "$RM"
        if cmd == "RT":
            return "$RT" + "46" + "27" + "16" + ("81" if self.run else "80") + "00" + "00" + "0000"
        if cmd.startswith("RP"):
            a, b = int(cmd[2:7]), int(cmd[7:12])
            return "$RP" + "".join(mw.encode_word(self.program[i] if i < len(self.program) else 0xFFFF)
                                   for i in range(a, b + 1))
        return "!42"

    def write(self, frame: bytes):
        text = frame.decode("ascii").strip()
        cmd = text[4:-2]
        reply = "%01" + self._answer(cmd)
        self.buf = (reply + xor_checksum(reply) + "\r").encode("ascii")

    def read_until(self, end=b"\r"):
        out, self.buf = self.buf, b""
        return out

    def close(self):
        pass


def demo_fp0r():
    port = DemoPort()
    plc = mw.Fp0r(port, 1)
    plc.nom = "Panasonic FP0R (demo)"
    plc.close = port.close
    return plc


# ---- status / diagnostic ----

def _relays(plc) -> dict:
    """{(900, 0): bool, ...} from WR900..WR902."""
    words = plc.read_contact_words("R", 900, 3)
    return {(w, b): bool((words[w - 900] >> b) & 1) for w, b in RELAYS}


def _registers(plc) -> dict:
    """DT90000.. of REGISTERS; one the PLC refuses reads as 0 (the link stays up)."""
    w = {}
    for n in REGISTERS:
        try:
            w[n] = plc.read_dt(n, 1)[0]
        except mw.PlcRefused:
            w[n] = 0
    return w


def _rt(plc) -> dict:
    try:
        return plc.read_status()
    except mw.MewtocolError:
        return {}


def faults(r, w) -> list:
    out = []
    if r[(900, 0x0)] or w[90000]:
        out.append(f"Erreur d'autodiagnostic (R9000) : DT90000 = {self_diag_text(w[90000])}")
    if r[(900, 0x7)] or r[(900, 0x8)]:
        out.append(f"Erreur d'operation (R9007 / R9008) au pas {w[90017] if r[(900, 0x7)] else w[90018]} "
                   "(DT90017 / DT90018)")
    if r[(900, 0xF)]:
        out.append("Cycle constant depasse (R900F)")
    if r[(902, 0x9)]:
        out.append("Forcage en cours (R9029) : des entrees / sorties sont forcees")
    return out


def fp0r_status(plc) -> dict:
    r, w, rt = _relays(plc), _registers(plc), _rt(plc)
    errs = faults(r, w)
    run = r[(902, 0x0)]
    ref = f"version {rt['version']}" if rt.get("version") else ""
    out = {"marque": "Panasonic", "modele": plc.nom, "reference": ref, "mode": "RUN" if run else "PROG",
           "alertes": errs}
    err_led = bool(r[(900, 0x0)] or r[(900, 0x7)] or r[(900, 0x8)])
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": run, "clignote": bool(r[(902, 0x9)]), "defaut": False},
        {"nom": "PROG", "texte": "PROG", "allume": not run, "clignote": False, "defaut": False},
        {"nom": "ERROR", "texte": "erreur d'autodiagnostic / d'operation", "allume": err_led,
         "clignote": bool(r[(900, 0x7)] or r[(900, 0x8)]), "defaut": err_led},
    ]
    out["details"] = [("Cycle", f"{w[90022] / 10:.1f} ms (mini {w[90023] / 10:.1f}, maxi {w[90024] / 10:.1f})"),
                      ("Autodiagnostic", self_diag_text(w[90000]))]
    if rt.get("capacity_k"):
        out["details"].append(("Capacite programme", f"{rt['capacity_k']}k pas"))
    if rt:
        out["details"].append(("Commande a distance", "oui" if r[(902, 0x7)] else "non"))
    return out


def io_row(plc, area, count, label):
    try:
        word = plc.read_contact_words(area, 0, 1)[0]
        values, ok, etat = " ".join(str((word >> i) & 1) for i in range(count)), True, "ok"
    except mw.MewtocolError as e:
        values, ok, etat = f"non lu ({e})", False, "non lu"
    return {"emplacement": 0, "type": label, "adresse": f"{area}0..{bit_name(area, count - 1)}",
            "configure": {"code": "", "nom": label, "references": f"{count} voies"},
            "en_place": {"code": "", "nom": values, "references": ""}, "etat": etat, "ok": ok,
            "variables": [{"adresse": bit_name(area, i), "libelle": f"{label} {i + 1}"} for i in range(count)]}


def fp0r_diagnostic(plc) -> dict:
    r, w, rt = _relays(plc), _registers(plc), _rt(plc)
    out = {"alertes": faults(r, w), "parametres_cpu": [], "config_compilee": "", "systeme": [], "modules": []}
    for n, text in RELAYS.items():
        out["systeme"].append({"adresse": bit_name("R", n), "valeur": str(int(r[n])), "texte": text,
                               "ok": not (n in BAD_RELAYS and r[n])})
    for n, text in REGISTERS.items():
        extra = f" : {self_diag_text(w[n])}" if n == 90000 and w[n] else ""
        out["systeme"].append({"adresse": f"DT{n}", "valeur": str(w[n]), "texte": text + extra,
                               "ok": not (n == 90000 and w[n])})
    n_in, n_out = DEFAULT_IO
    out["modules"] = [io_row(plc, "X", n_in, "Entrees integrees"), io_row(plc, "Y", n_out, "Sorties integrees")]
    if rt:
        out["parametres_cpu"] = [("Code modele (RT)", rt.get("model", "?")), ("Version", rt.get("version", "?")),
                                 ("Capacite programme", f"{rt.get('capacity_k', '?')}k pas"),
                                 ("Reponse RT brute", rt.get("raw", "")),
                                 ("Decodage RT", "a confirmer sur un FP0R (le mode affiche vient de R9020)")]
    else:
        out["parametres_cpu"] = [("Etat RT", "non lu")]
    return out


# ---- watch table ----

ADDRESS = re.compile(r"^(DT|WX|WY|WR|X|Y|R)(\d+)$", re.IGNORECASE)
BIT_ADDRESS = re.compile(r"^([XYR])(\d*)([0-9A-F])$", re.IGNORECASE)
WORD_FORMATS = ["dec", "ud", "hex", "bin", "dint"]


class Fp0rVar:
    """'DT100', 'DT100:hex', 'DT90300:dint', 'WX0', 'WY0', 'WR10', 'X0', 'X1F', 'Y3', 'R901C'."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        address = address.strip().upper()
        m = ADDRESS.match(address)
        b = BIT_ADDRESS.match(address)
        if m and m.group(1) in ("DT", "WX", "WY", "WR"):
            self.area, self.n, self.word = m.group(1), int(m.group(2)), True
        elif b:
            self.area, self.n, self.word = b.group(1), int(b.group(2) or 0), False
            self.bit = int(b.group(3), 16)
        else:
            raise ValueError("adresse FP0R : DT100, WX0, WY0, WR10, X0, X1F, Y3, R901C")
        self.fmt = (fmt.strip().lower() or "dec") if self.word else ""
        if self.word and self.fmt not in WORD_FORMATS:
            raise ValueError(f"format {self.fmt} : {', '.join(WORD_FORMATS)}")
        if self.fmt == "dint" and self.area != "DT":
            raise ValueError("format dint : DT seulement")

    def read(self, plc):
        if not self.word:
            return int(plc.read_contact(self.area, self.n, self.bit))
        count = 2 if self.fmt == "dint" else 1
        words = plc.read_dt(self.n, count) if self.area == "DT" else plc.read_contact_words(self.area[1], self.n, 1)
        return mw.dint(words, 0) if self.fmt == "dint" else words[0]

    def decode(self, v) -> str:
        if not self.word or self.fmt in ("ud", "dint"):
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:04X}"
        if self.fmt == "bin":
            t = f"{v:016b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, 16, 4))
        return str(v - 0x10000 if v & 0x8000 else v)

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
        if self.fmt == "dint":
            if not -(1 << 31) <= v < (1 << 32):
                raise ValueError("hors limites 32 bits")
            return v
        if not -32768 <= v <= 65535:
            raise ValueError("hors -32768..65535")
        return v & 0xFFFF

    def write(self, plc, value):
        if not self.word:
            plc.write_contact(self.area, self.n, self.bit, bool(value))
        elif self.area == "DT":
            if self.fmt == "dint":
                v = value & 0xFFFFFFFF
                plc.write_dt_block(self.n, [v & 0xFFFF, v >> 16])
            else:
                plc.write_dt(self.n, value)
        else:
            plc.write_contact_word(self.area[1], self.n, value)


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        try:
            v = Fp0rVar(str(t))
            out["valeurs"][str(t)] = v.decode(v.read(plc))
        except (ValueError, mw.PlcRefused) as e:
            out["erreurs"][str(t)] = str(e)
    return out


def _running(plc) -> bool:
    try:
        return plc.read_contact("R", 902, 0)
    except mw.MewtocolError:
        return False


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = Fp0rVar(text)
            if v.area in ("X", "WX"):
                raise ValueError("les entrees X ne s'ecrivent pas")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = v.read(plc)
        v.write(plc, value)
        back = v.read(plc)
        if v.fmt == "dint":
            ok = (back & 0xFFFFFFFF) == (value & 0xFFFFFFFF)
        else:
            ok = back == value
        results.append({"adresse": text, "avant": v.decode(old), "relu": v.decode(back), "ok": ok})
    return {"resultats": results, "run": _running(plc)}


# ---- data copies (all DT) ----

def data_size(plc) -> int:
    """Words of the DT area: 32765 (F32 / T32) or 12315 (C10..C32)."""
    for size in DATA_SIZES:
        try:
            plc.read_dt(size - 1, 1)
            return size
        except mw.PlcRefused:
            continue
    return DATA_SIZES[-1]


def read_all_dt(plc, progress=None) -> list:
    size = data_size(plc)
    out = []
    step = mw.MAX_WORDS_PER_FRAME * 10
    for start in range(0, size, step):
        if progress:
            progress(f"lecture DT : {start} / {size} mots", start, size)
        out += plc.read_dt(start, min(step, size - start))
    return out


def data_csv(values, plc: str, stamp: str) -> str:
    items = sorted(values.items()) if isinstance(values, dict) else enumerate(values)
    lines = [f"# date;{stamp}", f"# automate;{plc}", "zone;adresse;valeur;signe;hex"]
    lines += [f"DT;{i};{v};{v - 0x10000 if v & 0x8000 else v};{v:04X}" for i, v in items]
    return "\n".join(lines) + "\n"


def parse_data_csv(text: str) -> tuple:
    out, plc = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# automate;"):
            plc = line.split(";", 1)[1]
        if not line or line.startswith("#") or line.lower().startswith("zone;"):
            continue
        parts = line.split(";")
        if len(parts) < 3 or parts[0].strip().upper() != "DT":
            continue
        try:
            addr, value = int(parts[1]), int(parts[2])
        except ValueError:
            raise mw.MewtocolError(f"ligne illisible : {line[:60]}") from None
        out[addr] = value & 0xFFFF
    if not out:
        raise mw.MewtocolError("aucun mot DT dans le fichier")
    return out, plc


def changed_runs(addrs, limit=mw.MAX_WORDS_PER_FRAME) -> list:
    runs = []
    for a in sorted(addrs):
        if runs and runs[-1][0] + runs[-1][1] == a and runs[-1][1] < limit:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return runs


def import_data_files(folder: Path, files: list, note: str) -> dict:
    made, ignored = [], []
    for name, data in files:
        base = Path(name.replace("\\", "/")).name
        if not base.lower().endswith(".csv"):
            ignored.append(base)
            continue
        try:
            values, plc = parse_data_csv(data.decode("utf-8", "replace"))
        except mw.MewtocolError as e:
            raise _drv().DriverError(f"{base} : {e}") from None
        text = data_csv(values, plc or "", time.strftime("%Y-%m-%d %H:%M:%S"))
        made.append(tsx_web.save_data(folder, text, plc or "", note or f"importe de {base}", f"{len(values)} DT"))
    if not made:
        raise _drv().DriverError("Aucun fichier .csv de donnees FP0R dans les fichiers choisis")
    return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}


class Fp0rOps:
    """Session calls for a Panasonic FP0R."""

    def __init__(self, session):
        self.s = session

    @property
    def data(self) -> Path:
        return self.s.tsx_backups.parent / "panasonic" / "donnees"

    def status(self, plc):
        return fp0r_status(plc)

    def diagnostic(self):
        return self.s._call(fp0r_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def start_cpu(self):
        return self.s._call(lambda plc: (plc.set_mode(True), {"ok": True})[1])

    def stop_cpu(self):
        return self.s._call(lambda plc: (plc.set_mode(False), {"ok": True})[1])

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": tsx_web.list_copies(self.data, tsx_web.DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            values = read_all_dt(plc, progress)
            text = data_csv(values, plc.nom, time.strftime("%Y-%m-%d %H:%M:%S"))
            return tsx_web.save_data(self.data, text, plc.nom, note, f"{len(values)} DT")
        return self.s.start_job("sauvegarde des donnees", work)

    def _saved(self, backup_id):
        path = _drv().backup_path(self.data, backup_id)
        try:
            return parse_data_csv((path / tsx_web.DATA_FILE).read_text(encoding="utf-8"))
        except (OSError, mw.MewtocolError) as e:
            raise _drv().DriverError(f"copie illisible : {e}") from None

    def data_plan(self, backup_id):
        saved, plc_in_file = self._saved(backup_id)

        def run(plc):
            current = read_all_dt(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            sign = lambda v: v - 0x10000 if v & 0x8000 else v
            return {"zone": "DT", "mots": len(saved), "differents": len(diffs), "zone_automate": len(current),
                    "hors_zone": sum(1 for a in saved if a >= len(current)), "kw_ignores": 0,
                    "automate_fichier": plc_in_file, "automate": plc.nom, "run": _running(plc),
                    "exemples": [{"adresse": f"DT{a}", "automate": sign(current[a]), "fichier": sign(diffs[a])}
                                 for a in sorted(diffs)[:12]]}
        return self.s._call(run)

    def data_restore(self, backup_id):
        saved, _ = self._saved(backup_id)

        def work(plc, progress):
            progress("lecture des DT de l'automate", 0, 0)
            current = read_all_dt(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            runs = changed_runs(diffs)
            for i, (start, length) in enumerate(runs, 1):
                progress(f"ecriture DT{start} ({length} mot(s))", i, len(runs))
                plc.write_dt_block(start, [saved[a] for a in range(start, start + length)])
            progress("verification", len(runs), len(runs))
            after = read_all_dt(plc)
            wrong = [f"DT{a}" for a in sorted(saved) if a < len(after) and after[a] != saved[a]]
            return {"ecrits": len(diffs), "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_fp0r")

    def data_import(self, files, note):
        self.data.mkdir(parents=True, exist_ok=True)
        return import_data_files(self.data, files, note)

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction pas encore disponible pour le FP0R (programme : python tools/panasonic.py "
                                 "programme)")
