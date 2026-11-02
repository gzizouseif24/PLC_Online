"""Schneider TSX Micro / Premium in the web interface (Uni-Telway on the TER port).

Same page as the S7-300: status, diagnostic (system bits and words), watch table, program
copies (PLC image, also from a PL7 .stx) and a "Donnees" tab (%MW / %KW to CSV, PL7 .dat).
The protocol is plc_online/protocols/unitelway.py, the same code as tools/tsx.py.
"""
import hashlib
import json
import random
import re
import struct
import time
import zipfile
from pathlib import Path

from plc_online.protocols import unitelway as ut
from plc_online.protocols.unitelway import UniteError

# DriverError and the copy-folder helpers live in drivers.py, which imports this module:
# imported late (in functions) to keep the import order simple.


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM", "defaut": "COM6", "type": "port"},
            {"cle": "adresses", "nom": "Adresse(s) du PC", "defaut": "1", "type": "texte"}]

# CPU state from READ_CPU (4F), byte 1 of the answer. Confirmed by the PL7 capture of 02/11/2026
# (TSX 3721): 04 08 with no application, 01 0A in STOP after a download, 02 0B after RUN.
# The identification answer carries the same byte (08 05 04 / 08 06 01 / 08 03 02).
CPU_STATES = {1: "STOP", 2: "RUN", 4: "VIDE"}

FLASH_ERRORS = ut.BACKUP_ERRORS


# ---- link ----

class TsxLink(ut.Tsx):
    """Tsx on a serial port, with what the page needs around it."""

    def __init__(self, port: str, addrs=(1,)):
        try:
            import serial
        except ImportError:
            raise _drv().DriverError("pyserial manque : pip install pyserial") from None
        try:
            self.ser = serial.Serial(port, 9600, bytesize=8, parity="O", stopbits=1, timeout=0.02)
        except (serial.SerialException, OSError, ValueError) as e:
            raise _drv().DriverError(f"Port {port} : {e}") from None
        super().__init__(ut.Slave(self.ser, addrs))
        self.nom = ""

    def close(self):
        self.ser.close()


def open_tsx(settings: dict):
    port = str(settings.get("port") or "COM6").strip()
    try:
        addrs = ut.parse_addrs(str(settings.get("adresses") or "1"))
    except ValueError:
        raise _drv().DriverError("Adresse(s) du PC : 1, ou 1-3") from None
    link = TsxLink(port, addrs)
    try:
        link.nom = link.identification()["nom"]
    except (UniteError, OSError) as e:
        link.close()
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({e}). PL7 ferme ? Cable TSX PCX 1031 "
                                 "branche sur la prise TER, meme position du selecteur qu'avec PL7 ?") from None
    return link


def list_ports() -> list:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [{"port": p.device, "description": p.description or ""} for p in sorted(lp.comports(), key=lambda p: p.device)]


# ---- demo ----

class DemoTsx:
    """Simulated TSX 3721 (bench values) with the same calls as unitelway.Tsx."""

    def __init__(self):
        self.nom = "TSX 3721"
        self.mw = [0] * 1500
        self.mw[12], self.mw[15], self.mw[16], self.mw[17] = 3248, 13568, 18690, 1033
        self.kw = [0] * 256
        self.kw[0:4] = [100, 250, 5, 1]
        self.md_float = 0
        self.sw = [0] * 128
        self.sw[11], self.sw[30], self.sw[31], self.sw[32] = 250, 3, 7, 2
        self.sw[96:98] = [0x0000, 0xFFFF]
        self.s = [0] * 128
        self.s[10], self.s[16] = 1, 1                              # 1 = no I/O fault
        self.running = True
        self.app = (b"STATION\0" + bytes(6) + bytes((i * 7 + 3) % 256 for i in range(4300))
                    + b"\x04\x00TSX 3721\0" + b"".join(b"\xff" + bytes([p]) + r.ljust(16, b"\0") + b"\x04\x00" + c + bytes(20)
                               for p, r, c in ((1, b"TSX DEZ 32D2", b"\x10\x09"), (2, b"TSX DEZ 32D2", b"\x10\x09"),
                                               (3, b"TSX DSZ 32R5", b"\x10\x18"), (4, b"TSX DSZ 32R5", b"\x10\x18")))
                    + bytes(100))
        self.io_faults = [0] * 16
        self.installed = {}                                 # position -> type in place (None = absent)
        self.clock_offset = -86400 * 400 - 3 * 3600         # clock lost after a battery fault
        self.s50 = 0
        self.tick = 0
        self.slave = type("S", (), {"category": 0})()

    def _simulate(self):
        self.tick += 1
        if self.running:
            self.mw[20] = (self.mw[20] + 1) & 0xFFFF               # piece counter
            self.mw[21] = 200 + random.randint(-3, 3)              # temperature x10
            self.s[10] = 1
        t = time.localtime(time.time() + self.clock_offset)
        bcd = lambda v: int(str(v), 16)                            # noqa: E731
        self.sw[49:54] = [t.tm_wday + 1, bcd(t.tm_sec) << 8, bcd(t.tm_hour) << 8 | bcd(t.tm_min),
                          bcd(t.tm_mon) << 8 | bcd(t.tm_mday), bcd(t.tm_year)]
        self.sw[30] = random.randint(2, 4) if self.running else 0

    def identification(self):
        return {"nom": self.nom, "brut": ""}

    def _zone(self, zone):
        return {"MW": self.mw, "KW": self.kw, "SW": self.sw, "MD": self.mw, "KD": self.kw}[zone]

    def read(self, zone, start, count):
        self._simulate()
        if zone in ("M", "S"):
            bits = self.s if zone == "S" else [(self.mw[100 + a // 16] >> (a % 16)) & 1 for a in range(512)]
            if start + count > len(bits):
                raise UniteError("refuse par l'automate (FD)")
            return bits[start:start + count]
        mem, size = self._zone(zone), ut.WORD_ZONES[zone][2]
        words = size // 2
        if (start + count) * words > len(mem):
            raise UniteError("refuse par l'automate (FD)")
        if words == 1:
            return mem[start:start + count]
        return [mem[2 * a] | mem[2 * a + 1] << 16 for a in range(start, start + count)]

    def write(self, zone, addr, value):
        if zone not in ut.WRITABLE:
            raise UniteError(f"ecriture non autorisee sur %{zone}")
        if zone == "MW":
            self.mw[addr] = value & 0xFFFF
        else:
            self.mw[2 * addr], self.mw[2 * addr + 1] = value & 0xFFFF, value >> 16 & 0xFFFF

    def write_words(self, zone, start, values):
        if start + len(values) > len(self.mw):
            raise UniteError("refuse par l'automate (FD)")
        time.sleep(0.02)
        self.mw[start:start + len(values)] = [v & 0xFFFF for v in values]

    def read_cpu(self):
        state = (2 if self.running else 1) | (0x20 if any(self.io_faults) else 0)
        return [0, state, 0x0B if self.running else 0x0A] + [0] * 74

    def write_system_bit(self, n, value):
        if n != 50:
            raise UniteError("refuse par l'automate (FD)")
        self.s50 = self.s[50] = 1 if value else 0

    def write_system_words(self, start, values):
        if start != 50 or not self.s50:
            raise UniteError("refuse par l'automate (FD)")
        import datetime
        d = lambda v: int(f"{v:X}")                                # noqa: E731  BCD -> int
        when = datetime.datetime(d(values[3]), d(values[2] >> 8), d(values[2] & 0xFF),
                                 d(values[1] >> 8), d(values[1] & 0xFF), d(values[0] >> 8))
        self.clock_offset = when.timestamp() - time.time()
        return "37"

    def module_status(self, position):
        codes = ut.config_codes(self.app)
        if position not in codes:
            raise UniteError("refuse par l'automate (FD)")
        present = self.installed.get(position, codes[position])
        return {"configure": codes[position], "en_place": present, "detail": b"", "brut": ""}

    def read_io_faults(self):
        return list(self.io_faults)

    def reserve(self, params=None):
        pass

    def release(self):
        pass

    def app_info(self):
        return {"nom": ut.app_name(self.app), "brut": f"{len(self.app)}-{random.randint(0, 9)}"}   # changes, like the PLC

    def app_checksums(self):
        return (f"{sum(self.app[-200:]) & 0xFFFF:04x}", f"{sum(self.app) & 0xFFFF:04x}")

    def upload(self, progress=None):
        for seg, i in enumerate(range(0, len(self.app), ut.SEGMENT_SIZE), 1):
            time.sleep(0.01)
            if progress:
                progress(seg, min(i + ut.SEGMENT_SIZE, len(self.app)))
        return self.app

    def download(self, blob, progress=None):
        if self.running:
            raise UniteError("refuse par l'automate (FD)")
        for seg, i in enumerate(range(0, len(blob), ut.DOWNLOAD_SEGMENT), 1):
            time.sleep(0.01)
            if progress:
                progress(seg, min(i + ut.DOWNLOAD_SEGMENT, len(blob)))
        self.app = bytes(blob)

    def run(self):
        self.running = True

    def stop(self):
        self.running = False

    def close(self):
        pass


# ---- status and diagnostic ----

def read_bits(plc, zone, numbers) -> dict:
    """{n: 0/1} for the wanted %S / %M numbers, one request per group of 8."""
    out = {}
    for group in sorted({n - n % 8 for n in numbers}):
        for i, v in enumerate(plc.read(zone, group, 8)):
            out[group + i] = v
    return out


def read_system_words(plc) -> dict:
    """{n: value} of %SW0..59 and %SW96..97 (single reads if a block is refused)."""
    out = {}
    for start, count in ((0, 60), (96, 2)):
        try:
            out.update(zip(range(start, start + count), plc.read("SW", start, count)))
        except UniteError:
            for n in (11, 30, 31, 32, 49, 50, 51, 52, 53) if start == 0 else (96, 97):
                try:
                    out[n] = plc.read("SW", n, 1)[0]
                except UniteError:
                    pass
    return out


def clock_text(sw: dict) -> str:
    """%SW49..53 (BCD): day of week, seconds, hours-minutes, month-day, year."""
    if not all(n in sw for n in range(49, 54)):
        return ""
    return (f"{sw[52] & 0xFF:02X}/{sw[52] >> 8:02X}/{sw[53]:04X} "
            f"{sw[51] >> 8:02X}:{sw[51] & 0xFF:02X}:{sw[50] >> 8:02X}")


def cpu_state(plc, with_flags=False):
    """(mode, raw text) from READ_CPU; ('?', '') when the PLC refuses it. The state byte is the
    mode in the low bits and 0x20 when the CPU signals an I/O fault (I/O light): 21 seen with an
    unpowered input module, 01 once its supply monitoring was switched off (capture 02/11)."""
    try:
        data = plc.read_cpu()
    except UniteError:
        return ("?", "", False) if with_flags else ("?", "")
    raw = " ".join(f"{b:02X}" for b in data[:3])
    state = data[1] if len(data) > 1 else -1
    mode = CPU_STATES.get(state & 0x0F, "?") if state >= 0 else "?"
    if with_flags:
        return mode, raw, bool(state >= 0 and state & ut.CPU_IO_FAULT)
    return mode, raw


# ---- configured modules (from the application image) and faults per position ----

MODULE_KINDS = [("DMZ", "entrees / sorties TOR", "%I", "%Q"), ("DEZ", "entrees TOR", "%I", None),
                ("DEY", "entrees TOR", "%I", None), ("DSZ", "sorties TOR", "%Q", None),
                ("DSY", "sorties TOR", "%Q", None), ("AEZ", "entrees analogiques", "%IW", None),
                ("AEY", "entrees analogiques", "%IW", None), ("ASZ", "sorties analogiques", "%QW", None),
                ("ASY", "sorties analogiques", "%QW", None), ("CTZ", "comptage", "%ID", None),
                ("SCP", "communication", "", None)]

MODULE_NAMES = {
    "TSX DEZ 32D2": "32 entrees 24 V cc (connecteurs HE10)",
    "TSX DEZ 12D2": "12 entrees 24 V cc",
    "TSX DEZ 08A4": "8 entrees 120 V ca",
    "TSX DEZ 08A5": "8 entrees 220 V ca",
    "TSX DSZ 32R5": "32 sorties relais",
    "TSX DSZ 32T2": "32 sorties transistors 24 V cc",
    "TSX DSZ 08R5": "8 sorties relais",
    "TSX DSZ 08T2": "8 sorties transistors 24 V cc",
    "TSX DSZ 04T22": "4 sorties transistors 24 V cc 2 A",
    "TSX DMZ 28DR": "16 entrees 24 V cc + 12 sorties relais",
    "TSX DMZ 28DT": "16 entrees 24 V cc + 12 sorties transistors",
    "TSX DMZ 28DTK": "16 entrees + 12 sorties transistors (HE10)",
    "TSX DMZ 64DTK": "32 entrees + 32 sorties transistors (HE10)",
    "TSX AEZ 414": "4 entrees analogiques (thermocouple, sonde, +/-10 V)",
    "TSX AEZ 801": "8 entrees analogiques tension",
    "TSX AEZ 802": "8 entrees analogiques courant",
    "TSX ASZ 401": "4 sorties analogiques +/-10 V",
    "TSX ASZ 200": "2 sorties analogiques",
    "TSX CTZ 1A": "1 voie de comptage",
    "TSX CTZ 2A": "2 voies de comptage",
}


# Fault byte per position (request 93 07 02 68 ...): 01 seen for an unpowered input module
# (supply monitoring on). PL7 tells "alimentation ou actionneur" apart from "module absent ou
# different": the code should differ; collected from the field before naming them.
FAULT_CODES = {}


def fault_text(code: int) -> str:
    known = FAULT_CODES.get(code)
    return f"DEFAUT (code {code:02X}{' : ' + known if known else ''})"


def code_name(code, names) -> str:
    return names.get(code) or ut.MODULE_CODES.get(code) or f"module type {code:04X}"


def module_rows(config, faults, statuses=None, names=None) -> list:
    """Rack table rows (same shape as the S7-300 one): configured modules, module in place
    (READ_STATUS_MODULE: same type, other type or nothing) and fault per position."""
    statuses, names = statuses or {}, names or {}
    rows = []
    for pos, ref in config:
        kind, first, second = next(((k[1], k[2], k[3]) for k in MODULE_KINDS if ref.split()[-2:][0].startswith(k[0])),
                                   ("module", "", None))
        prefix = first
        if second and any(p == pos - 1 and r == ref for p, r in config):
            prefix = second                     # second position of a mixed module: its outputs
        address = f"{prefix}{pos}.0 ..." if prefix else ""
        fault = faults[pos] if faults is not None and pos < len(faults) else None
        st = statuses.get(pos)
        en_place, etat, ok = None, ("ok" if fault == 0 else fault_text(fault) if fault else "non lu"), not fault
        if st:
            if st["en_place"] is None:
                etat, ok = "MODULE ABSENT (rien a cette position)", False
            elif st["en_place"] != st["configure"]:
                found = code_name(st["en_place"], names)
                en_place = {"code": "", "nom": found, "references": MODULE_NAMES.get(found, "")}
                etat, ok = f"MODULE DIFFERENT DE LA CONFIGURATION ({found} en place)", False
            else:
                en_place = {"code": "", "nom": ref, "references": "meme type que configure"}
                if fault:
                    etat = "DEFAUT EXTERNE : alimentation capteurs / actionneurs ou voie en defaut"
        rows.append({"emplacement": pos, "type": kind, "adresse": address,
                     "configure": {"code": "", "nom": ref, "references": MODULE_NAMES.get(ref, "")},
                     "en_place": en_place, "etat": etat, "ok": ok})
    if faults is not None:
        configured = {p for p, _ in config}
        for pos, f in enumerate(faults):
            if f and pos not in configured and pos > 0:
                rows.append({"emplacement": pos, "type": "?", "adresse": "", "configure": None, "en_place": None,
                             "etat": f"position non configuree, {fault_text(f)}", "ok": False})
    return sorted(rows, key=lambda r: r["emplacement"])


def flash_lines(sw: dict) -> list:
    if 96 not in sw or 97 not in sw:
        return []
    sw96, sw97 = sw[96], sw[97]
    lines = [("Sauvegarde Flash du programme", "valide" if sw96 >> 6 & 1 else "aucune"),
             ("%MW sauvegardes en Flash", "aucun" if sw97 == 0xFFFF else f"{sw97} premiers %MW")]
    if sw96 >> 1 & 1:
        code = sw96 >> 8
        lines.append(("Derniere sauvegarde Flash", f"ERREUR {code} {FLASH_ERRORS.get(code, '')}"
                      if sw96 >> 2 & 1 else "terminee sans erreur"))
    return lines


def tsx_status(plc) -> dict:
    out = {"marque": "Schneider TSX", "modele": plc.nom or "TSX", "reference": "", "alertes": [],
           "voyants": [], "details": []}
    bits = read_bits(plc, "S", ut.SYSTEM_BITS)
    sw = read_system_words(plc)
    mode, raw, io_fault = cpu_state(plc, with_flags=True)
    out["mode"] = mode
    if io_fault:
        out["alertes"].append("Defaut E/S signale par la CPU (voyant I/O) : voir l'onglet Diagnostic")
    if mode == "VIDE":
        out["alertes"].append("Pas d'application dans l'automate (vide : programme efface ou chargement refuse)")
    faults = {n: label for n, (label, bad) in ut.SYSTEM_BITS.items() if bits.get(n) == bad}
    out["alertes"] += [f"%S{n} : {label}" for n, label in faults.items()] if mode != "VIDE" else []
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": mode == "RUN", "clignote": False, "defaut": False},
        {"nom": "I/O", "texte": "Defaut E/S", "allume": io_fault or 10 in faults, "clignote": False, "defaut": True},
        {"nom": "ERR", "texte": "Chien de garde (%S11)", "allume": 11 in faults, "clignote": False, "defaut": True},
        {"nom": "BAT", "texte": "Pile (%S68)", "allume": 68 in faults, "clignote": False, "defaut": True},
    ]
    clock = clock_text(sw)
    if clock:
        out["details"].append(("Horloge automate", clock + (" (perdue : %S51)" if 51 in faults else "")))
    if 11 in sw:
        out["details"].append(("Chien de garde", f"{sw[11]} ms"))
    if all(n in sw for n in (30, 31, 32)):
        out["details"].append(("Cycle (dernier / max / min)", f"{sw[30]} / {sw[31]} / {sw[32]} ms"))
    out["details"] += flash_lines(sw)
    if raw:
        out["details"].append(("Etat CPU (requete 4F)", raw))
    return out


EXTRA_BITS = {0: "demarrage a froid", 1: "reprise a chaud", 13: "premier cycle apres RUN"}


def tsx_diagnostic(plc, config=None) -> dict:
    """config = {'modules': [(position, reference)], 'codes': {position: type code}} or None."""
    out = {"alertes": [], "systeme": [], "parametres_cpu": [], "config_compilee": ""}
    try:
        faults = plc.read_io_faults()
    except UniteError:
        faults = None
    modules = (config or {}).get("modules") or []
    names = {code: ref for pos, ref in modules for p, code in (config or {}).get("codes", {}).items() if p == pos}
    statuses = {}
    for pos, _ in modules:
        if hasattr(plc, "module_status"):
            try:
                statuses[pos] = plc.module_status(pos)
            except UniteError:
                pass
    if config and config.get("erreur"):
        out["alertes"].append(f"Configuration non lue (lecture du programme) : {config['erreur']}")
    if config is not None or faults is not None:
        out["modules"] = module_rows(modules, faults, statuses, names)
        bad = [r for r in out["modules"] if not r["ok"]]
        if bad:
            out["alertes"].append(f"{len(bad)} position(s) en defaut dans le rack : "
                                  + ", ".join(str(r["emplacement"]) for r in bad))
        if faults is None:
            out["alertes"].append("Defauts des modules non lus (requete refusee)")
    numbers = sorted(set(ut.SYSTEM_BITS) | set(EXTRA_BITS))
    bits = read_bits(plc, "S", numbers)
    for n in numbers:
        if n in ut.SYSTEM_BITS:
            label, bad = ut.SYSTEM_BITS[n]
            ok = bits.get(n) != bad
        else:
            label, ok = EXTRA_BITS[n], True
        out["systeme"].append({"adresse": f"%S{n}", "valeur": str(bits.get(n, "?")), "texte": label, "ok": ok})
    bad = [r for r in out["systeme"] if not r["ok"]]
    if bad:
        out["alertes"].append(f"{len(bad)} defaut(s) signale(s) par les bits systeme")
    sw = read_system_words(plc)
    words = {11: "chien de garde (ms)", 30: "duree du dernier cycle (ms)", 31: "cycle max (ms)",
             32: "cycle min (ms)", 96: "sauvegarde Flash (%SW96)", 97: "%MW en Flash (%SW97)"}
    for n, label in words.items():
        if n in sw:
            out["systeme"].append({"adresse": f"%SW{n}", "valeur": str(sw[n] if n != 96 else f"{sw[n]:04X}"),
                                   "texte": label, "ok": True})
    out["parametres_cpu"].append(("Automate", plc.nom))
    try:
        plc.reserve()
        try:
            out["parametres_cpu"].append(("Application", plc.app_info()["nom"]))
        finally:
            plc.release()
    except UniteError:
        pass
    clock = clock_text(sw)
    if clock:
        out["parametres_cpu"].append(("Horloge", clock))
    out["parametres_cpu"] += flash_lines(sw)
    mode, raw = cpu_state(plc)
    if raw:
        out["parametres_cpu"].append(("Etat CPU (4F)", f"{raw} -> {mode}"))
    return out


# ---- watch table ----

ADDRESS = re.compile(r"^%?(MW|MD|KW|KD|SW|M|S)(\d+)$", re.IGNORECASE)
WORD_FORMATS = ["dec", "ud", "hex", "bin", "char"]
DWORD_FORMATS = ["dec", "ud", "hex", "bin", "real"]
MAX_RUN = {2: 60, 4: 30}


class TsxVar:
    """'%MW10', 'MD4:real', '%M5'... as typed in the watch table."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        m = ADDRESS.match(address.strip())
        if not m:
            raise ValueError("adresse TSX non reconnue (ex. %MW10, %MD4, %KW0, %SW30, %M5, %S10)")
        self.zone, self.addr = m.group(1).upper(), int(m.group(2))
        self.bit = self.zone in ("M", "S")
        self.size = 0 if self.bit else ut.WORD_ZONES[self.zone][2]
        formats = [] if self.bit else (WORD_FORMATS if self.size == 2 else DWORD_FORMATS)
        self.fmt = fmt.strip().lower() or (formats[0] if formats else "")
        if formats and self.fmt not in formats:
            raise ValueError(f"format {self.fmt} : {', '.join(formats)}")

    def decode(self, v: int) -> str:
        if self.bit:
            return str(v)
        bits = 8 * self.size
        if self.fmt == "dec":
            return str(ut.signed(v, self.size))
        if self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:0{self.size * 2}X}"
        if self.fmt == "bin":
            text = f"{v:0{bits}b}"
            return "2#" + "_".join(text[i:i + 4] for i in range(0, bits, 4))
        if self.fmt == "real":
            return f"{struct.unpack('<f', struct.pack('<I', v))[0]:.6g}"
        chars = v.to_bytes(self.size, "little")              # %MW: first character in the low byte
        return "'" + "".join(chr(c) if 32 <= c < 127 else "." for c in chars) + "'"

    def encode(self, text: str) -> int:
        t = text.strip().replace("_", "")
        if self.bit:
            if t not in ("0", "1"):
                raise ValueError("0 ou 1")
            return int(t)
        bits = 8 * self.size
        try:
            if self.fmt == "real":
                return struct.unpack("<I", struct.pack("<f", float(t.replace(",", "."))))[0]
            if self.fmt == "char":
                raw = t.strip("'").encode("latin-1")
                if len(raw) > self.size:
                    raise ValueError(f"{self.size} caracteres au plus")
                return int.from_bytes(raw.ljust(self.size, b"\0"), "little")
            low = t.lower()
            if low.startswith("16#") or low.startswith("0x"):
                v = int(low[3:] if low.startswith("16#") else low[2:], 16)
            elif low.startswith("2#") or low.startswith("0b"):
                v = int(low[2:], 2)
            elif self.fmt == "hex":
                v = int(low, 16)
            elif self.fmt == "bin":
                v = int(low, 2)
            else:
                v = int(low)
        except (ValueError, struct.error):
            raise ValueError(f"valeur illisible pour le format {self.fmt}") from None
        if not -(1 << (bits - 1)) <= v < (1 << bits):
            raise ValueError(f"hors limites pour %{self.zone} ({bits} bits)")
        return v & ((1 << bits) - 1)


def parse_tsx_vars(texts):
    good, bad = [], {}
    for t in texts:
        try:
            good.append(TsxVar(str(t)))
        except ValueError as e:
            bad[str(t)] = str(e)
    return good, bad


def read_tsx_vars(plc, variables) -> dict:
    """{(zone, addr): value or None}, grouped: one request per run of nearby addresses."""
    out = {}
    by_zone = {}
    for v in variables:
        by_zone.setdefault(v.zone, set()).add(v.addr)
    for zone, addrs in by_zone.items():
        if zone in ("M", "S"):
            for group in sorted({a - a % 8 for a in addrs}):
                try:
                    for i, val in enumerate(plc.read(zone, group, 8)):
                        out[(zone, group + i)] = val
                except UniteError:
                    for a in addrs:
                        if a - a % 8 == group:
                            out[(zone, a)] = None
            continue
        limit = MAX_RUN[ut.WORD_ZONES[zone][2]]
        runs = []
        for a in sorted(addrs):
            if runs and a - runs[-1][0] < limit and a - runs[-1][1] <= 8:
                runs[-1][1] = a
            else:
                runs.append([a, a])
        for start, end in runs:
            try:
                for i, val in enumerate(plc.read(zone, start, end - start + 1)):
                    out[(zone, start + i)] = val
            except UniteError:
                for a in sorted(addrs):
                    if start <= a <= end:
                        try:
                            out[(zone, a)] = plc.read(zone, a, 1)[0]
                        except UniteError:
                            out[(zone, a)] = None
    return out


def read_values(plc, texts) -> dict:
    texts = [str(t) for t in texts][:_drv().MAX_WATCH]
    variables, errors = parse_tsx_vars(texts)
    raw = read_tsx_vars(plc, variables) if variables else {}
    values = {}
    for v in variables:
        val = raw.get((v.zone, v.addr))
        if val is None:
            errors[v.text] = "lecture refusee par l'automate (adresse hors de la zone configuree ?)"
        else:
            values[v.text] = v.decode(val)
    return {"valeurs": values, "erreurs": errors}


def write_values(plc, items) -> dict:
    pairs = []
    for it in items:
        try:
            var = TsxVar(str(it.get("adresse", "")))
            if var.zone not in ut.WRITABLE:
                raise ValueError(f"ecriture possible sur %MW et %MD seulement (pas %{var.zone})")
            pairs.append((var, var.encode(str(it.get("valeur", "")))))
        except ValueError as e:
            raise _drv().DriverError(f"{it.get('adresse', '')} : {e}") from None
    if not pairs:
        raise _drv().DriverError("Rien a ecrire")
    variables = [v for v, _ in pairs]
    old = read_tsx_vars(plc, variables)
    for v, value in pairs:
        plc.write(v.zone, v.addr, value)
    back = read_tsx_vars(plc, variables)
    out = []
    for v, value in pairs:
        got = back.get((v.zone, v.addr))
        before = old.get((v.zone, v.addr))
        out.append({"adresse": v.text, "ancien": v.decode(before) if before is not None else None,
                    "demande": v.decode(value), "relu": v.decode(got) if got is not None else None,
                    "ok": got == value})
    return {"resultats": out}


# ---- copies on the PC ----

PROGRAM_FILE = "programme.tsxbak"
DATA_FILE = "donnees.csv"


def _meta(folder: Path) -> dict:
    try:
        return json.loads((folder / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(folder: Path, meta: dict):
    (folder / "info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def list_copies(folder: Path, file_name: str) -> list:
    out = []
    if not folder.is_dir():
        return out
    for d in folder.iterdir():
        if not (d / file_name).is_file():
            continue
        meta = _meta(d)
        out.append({"id": d.name, "date": meta.get("date") or time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(d.stat().st_mtime)),
                    "cpu": meta.get("cible", ""), "modele": meta.get("automate", ""), "note": meta.get("note", ""),
                    "resume": meta.get("resume", ""), "blocs": meta.get("resume", "")})
    return sorted(out, key=lambda b: b["date"], reverse=True)


def save_image(folder: Path, blob: bytes, automate: str, note: str, target: str = "") -> dict:
    d = _drv()
    target_dir = d._new_folder(folder, "tsx")
    target_dir.mkdir(parents=True)
    (target_dir / PROGRAM_FILE).write_bytes(blob)
    name = ut.app_name(blob)
    meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "automate": automate, "cible": target,
            "application": name, "taille": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "note": note.strip()[:200], "resume": f"{name or '?'}, {len(blob)} octets"}
    _write_meta(target_dir, meta)
    return {"id": target_dir.name, "comptes": meta["resume"]}


def save_data(folder: Path, text: str, automate: str, note: str, summary: str, original: str = "") -> dict:
    d = _drv()
    target_dir = d._new_folder(folder, "donnees")
    target_dir.mkdir(parents=True)
    (target_dir / DATA_FILE).write_text(text, encoding="utf-8")
    if original:
        (target_dir / "original.dat").write_text(original, encoding="latin-1")
    meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "automate": automate, "note": note.strip()[:200],
            "resume": summary}
    _write_meta(target_dir, meta)
    return {"id": target_dir.name, "comptes": summary}


def zones_summary(zones: dict) -> str:
    return ", ".join(f"{len(v)} %{z}" for z, v in zones.items() if v)


def zones_to_csv(zones: dict, automate: str) -> str:
    """{zone: {addr: value}} (sparse, from a .dat) -> CSV text of tools/tsx.py."""
    lines = [f"# date;{time.strftime('%Y-%m-%d %H:%M:%S')}", f"# automate;{automate}", "zone;adresse;valeur;signe;hex"]
    for zone, values in zones.items():
        for a in sorted(values):
            v = values[a]
            lines.append(f"%{zone};{a};{v};{ut.signed(v, 2)};{v:04X}")
    return "\n".join(lines) + "\n"


def import_program_files(folder: Path, files: list, note: str) -> dict:
    """.stx (PL7), .tsxbak (tools/tsx.py) or a .zip of a copy from this page."""
    import io
    made, ignored = [], []
    for name, data in files:
        base = Path(name.replace("\\", "/")).name
        low = base.lower()
        if low.endswith(".zip") and data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                inner = {Path(n).name: n for n in z.namelist()}
                if PROGRAM_FILE in inner:
                    meta = {}
                    if "info.json" in inner:
                        try:
                            meta = json.loads(z.read(inner["info.json"]).decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            pass
                    blob = z.read(inner[PROGRAM_FILE])
                    made.append(save_image(folder, blob, str(meta.get("automate", "")),
                                           note or meta.get("note") or f"copie importee ({base})", str(meta.get("cible", ""))))
                    continue
        if low.endswith((".stx", ".tsxbak", ".zip")):
            try:
                blob, target = ut.read_program_bytes(data, base)
            except UniteError as e:
                raise _drv().DriverError(str(e)) from None
            made.append(save_image(folder, blob, "", note or f"importe de {base}", target or ""))
        else:
            ignored.append(base)
    if not made:
        raise _drv().DriverError("Aucun programme TSX (.stx de PL7, .tsxbak ou .zip) dans les fichiers choisis")
    return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}


def import_data_files(folder: Path, files: list, note: str) -> dict:
    made, ignored = [], []
    for name, data in files:
        base = Path(name.replace("\\", "/")).name
        if not base.lower().endswith((".dat", ".csv")):
            ignored.append(base)
            continue
        text = data.decode("utf-8") if base.lower().endswith(".csv") else data.decode("latin-1")
        try:
            zones, plc = ut.parse_data_text(text, base)
        except UniteError as e:
            raise _drv().DriverError(str(e)) from None
        made.append(save_data(folder, zones_to_csv(zones, plc or ""), plc or "", note or f"importe de {base}",
                              zones_summary(zones), text if base.lower().endswith(".dat") else ""))
    if not made:
        raise _drv().DriverError("Aucun fichier de donnees (.dat de PL7 ou .csv) dans les fichiers choisis")
    return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}


# ---- what the session calls for a TSX ----

class TsxOps:
    """Session calls for a TSX: same names as for the S7-300, plus the Donnees tab."""

    def __init__(self, session):
        self.s = session
        self.configs = {}                  # (PLC, application signature) -> configured modules

    @property
    def programs(self) -> Path:
        return self.s.tsx_backups / "programmes"

    @property
    def data(self) -> Path:
        return self.s.tsx_backups / "donnees"

    def status(self, plc):
        return tsx_status(plc)

    def diagnostic(self):
        return self.s._call(lambda plc: tsx_diagnostic(plc, self._config(plc)))

    def _config(self, plc):
        """Configured modules of the loaded application, read from the application image (a full
        program read). Kept while the application checksums (92 07 0C) stay the same; read again
        every time if the PLC does not give them."""
        if cpu_state(plc)[0] == "VIDE":
            return {"modules": [], "codes": {}}
        key = None
        try:
            plc.reserve()
            try:
                key = (plc.nom, plc.app_checksums())
            finally:
                plc.release()
        except UniteError:
            key = None
        if key and key in self.configs:
            return self.configs[key]
        try:
            blob = self._upload(plc, lambda *a: None)
        except UniteError as e:
            return {"modules": [], "codes": {}, "erreur": str(e)}
        config = {"modules": ut.parse_config(blob), "codes": ut.config_codes(blob)}
        if key:
            self.configs[key] = config
        return config

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        def run(plc):
            out = write_values(plc, items)
            out["run"] = cpu_state(plc)[0] == "RUN"
            return out
        return self.s._call(run)

    def start_cpu(self):
        return self.s._call(lambda plc: (plc.run(), {"ok": True})[1])

    def stop_cpu(self):
        return self.s._call(lambda plc: (plc.stop(), {"ok": True})[1])

    def set_clock(self):
        def run(plc):
            import datetime
            now = datetime.datetime.now()
            done = ut.set_clock(plc, now)
            sw = dict(zip(range(49, 54), plc.read("SW", 49, 5)))
            words = [sw[50], sw[51], sw[52], sw[53]]
            if words[1:] != done["mots"][1:] and words[1:] != ut.clock_words(datetime.datetime.now())[1:]:
                raise UniteError(f"horloge relue {clock_text(sw)} : la mise a l'heure n'a pas pris")
            return {"horloge": clock_text(sw), "methode": done["methode"]}
        return self.s._call(run)

    # program

    def list_backups(self):
        return {"dossier": str(self.programs), "sauvegardes": list_copies(self.programs, PROGRAM_FILE)}

    def _upload(self, plc, progress, text="lecture"):
        if cpu_state(plc)[0] == "VIDE":
            raise UniteError("l'automate n'a pas d'application (rien a copier)")
        step = "reservation de l'automate (1D)"
        try:
            plc.reserve()
        except UniteError as e:
            raise UniteError(f"{e}, a l'etape {step} : PL7 ou un autre PC connecte ?") from None
        try:
            step = "infos application (92)"
            info = plc.app_info()
            name = info["nom"]
            step = "lecture du programme (3D/3E)"
            blob = plc.upload(lambda seg, n: progress(f"{text} {name} : {n} octets", 0, 0))
        except UniteError as e:
            raise UniteError(f"{e}, a l'etape {step}") from None
        finally:
            try:
                plc.release()
            except UniteError:
                pass
        return blob

    def _image(self, backup_id) -> bytes:
        return (_drv().backup_path(self.programs, backup_id) / PROGRAM_FILE).read_bytes()

    def save_program(self, note):
        def work(plc, progress):
            blob = self._upload(plc, progress)
            return save_image(self.programs, blob, plc.nom, note)
        return self.s.start_job("sauvegarde du programme", work)

    def compare(self, backup_id):
        saved = self._image(backup_id)

        def work(plc, progress):
            now = self._upload(plc, progress)
            if now == saved:
                return {"id": backup_id, "identique": True, "differences": [], "valeurs_db": []}
            diffs = [i for i in range(min(len(now), len(saved))) if now[i] != saved[i]]
            detail = (f"automate {len(now)} octets, sauvegarde {len(saved)} octets"
                      + (f", {len(diffs)} octets differents (premier a l'octet {diffs[0]})" if diffs else ""))
            return {"id": backup_id, "identique": False, "valeurs_db": [],
                    "differences": [{"bloc": f"application {ut.app_name(now) or '?'}", "type": "", "numero": 0,
                                     "verdict": "modifiee", "detail": detail}]}
        return self.s.start_job("comparaison avec l'automate", work)

    def restore_plan(self, backup_id, with_db=False):
        blob = self._image(backup_id)
        meta = _meta(self.programs / backup_id)

        def run(plc):
            target = meta.get("cible") or ""
            return {"charger": [f"application {ut.app_name(blob) or '?'} ({len(blob)} octets)"], "en_trop": [],
                    "db_ignorees": False, "run": cpu_state(plc)[0] == "RUN", "cible": target,
                    "automate": plc.nom, "vide": cpu_state(plc)[0] == "VIDE",
                    "autre_automate": bool(target) and target.replace(" ", "") != plc.nom.replace(" ", "")}
        return self.s._call(run)

    def restore(self, backup_id, with_db=False, delete_extra=False):
        blob = self._image(backup_id)

        def work(plc, progress):
            out = {"charges": [], "supprimes": [], "erreur": "", "differences": [], "etait_en_run": False}
            mode = cpu_state(plc)[0]
            if mode == "VIDE":                     # nothing to keep: an empty PLC (failed load, lost program)
                out["automate_vide"] = True
            else:
                before = self._upload(plc, progress, "copie de securite")
                out["sauvegarde_avant"] = save_image(self.programs, before, plc.nom, "avant restauration")["id"]
            out["etait_en_run"] = mode == "RUN"
            step = "passage en STOP (25)"
            try:
                if mode != "STOP" and mode != "VIDE":   # PL7 sends no STOP to an empty PLC (capture 02/11)
                    progress("passage en STOP", 0, 0)
                    plc.stop()
                step = "reservation pour le chargement (1D)"
                plc.reserve(ut.RESERVE_DOWNLOAD)
            except UniteError as e:
                out["erreur"] = f"{e}, a l'etape {step}"
                return out
            try:
                plc.download(blob, lambda seg, n: progress(f"chargement : {n} / {len(blob)} octets", n, len(blob)))
            except UniteError as e:
                out["erreur"] = f"{e}, a l'etape chargement (3A/3B/3C)"
                return out
            finally:
                try:
                    plc.release()
                except UniteError:
                    pass
            out["charges"].append(f"application {ut.app_name(blob) or '?'}")
            back = self._upload(plc, progress, "verification")
            if back != blob:
                out["differences"].append(f"programme relu {len(back)} octets, fichier {len(blob)} octets")
            return out
        return self.s.start_job("restauration du programme", work)

    def backup_blocks(self, backup_id):
        meta = _meta(_drv().backup_path(self.programs, backup_id))
        blob = self._image(backup_id)
        rows = [("application", ut.app_name(blob)), ("taille", f"{len(blob)} octets"),
                ("faite pour", meta.get("cible", "")), ("automate", meta.get("automate", "")),
                ("sha256", hashlib.sha256(blob).hexdigest()[:16] + "...")]
        return {"id": backup_id, "blocs": [{"bloc": k, "taille": v, "date": "", "somme": ""} for k, v in rows if v]}

    def export_backup(self, backup_id):
        return _drv().export_zip(self.programs, backup_id, "programme_tsx")

    def import_backup(self, files, note):
        self.programs.mkdir(parents=True, exist_ok=True)
        return import_program_files(self.programs, files, note)

    # data (%MW, %KW)

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": list_copies(self.data, DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            zones = {}
            for zone in ("MW", "KW"):
                progress(f"lecture %{zone}", 0, 0)
                zones[zone] = ut.read_whole_zone(plc, zone)
            text = ut.data_csv(zones, plc.nom, time.strftime("%Y-%m-%d %H:%M:%S"))
            return save_data(self.data, text, plc.nom, note, zones_summary(zones))
        return self.s.start_job("sauvegarde des donnees", work)

    def _data(self, backup_id):
        path = _drv().backup_path(self.data, backup_id)
        try:
            return ut.parse_data_text((path / DATA_FILE).read_text(encoding="utf-8"), backup_id)
        except UniteError as e:
            raise _drv().DriverError(str(e)) from None

    def data_plan(self, backup_id):
        zones, plc_in_file = self._data(backup_id)
        saved = zones.get("MW", {})

        def run(plc):
            current = ut.read_whole_zone(plc, "MW")
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            return {"mots": len(saved), "differents": len(diffs), "zone_automate": len(current),
                    "hors_zone": sum(1 for a in saved if a >= len(current)), "kw_ignores": len(zones.get("KW", {})),
                    "automate_fichier": plc_in_file or "", "automate": plc.nom,
                    "run": cpu_state(plc)[0] == "RUN",
                    "exemples": [{"adresse": f"%MW{a}", "automate": ut.signed(current[a], 2),
                                  "fichier": ut.signed(diffs[a], 2)} for a in sorted(diffs)[:12]]}
        return self.s._call(run)

    def data_restore(self, backup_id):
        zones, _ = self._data(backup_id)
        saved = zones.get("MW", {})

        def work(plc, progress):
            progress("lecture des %MW de l'automate", 0, 0)
            current = ut.read_whole_zone(plc, "MW")
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            runs = ut.changed_runs(diffs)
            for i, (start, length) in enumerate(runs, 1):
                progress(f"ecriture %MW{start} ({length} mot(s))", i, len(runs))
                plc.write_words("MW", start, [saved[a] for a in range(start, start + length)])
            progress("verification", len(runs), len(runs))
            after = ut.read_whole_zone(plc, "MW")
            wrong = [f"%MW{a}" for a in sorted(saved) if a < len(after) and after[a] != saved[a]]
            return {"ecrits": len(diffs), "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_tsx")

    def data_import(self, files, note):
        self.data.mkdir(parents=True, exist_ok=True)
        return import_data_files(self.data, files, note)
