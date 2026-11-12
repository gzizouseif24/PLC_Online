"""Omron CJ1M in the web interface (Host Link on the peripheral port): status, error log, PLC Setup and
I/O table, cards with their CIO words, watch table, D / H / W data copies, read-only program copy,
RUN / MONITOR / PROGRAM.

Confirmed on the bench CJ1M: echo (TS), D read (RD), PLC Setup and I/O table read (FINS 0201).
The rest follows Omron W342 (C-mode and FINS in Host Link) and is not yet confirmed on the PLC."""
import re
import time
from pathlib import Path

from plc_online.checksum import xor_checksum
from plc_online.protocols import hostlink as hl
from plc_online.protocols.hostlink import AREAS, decode_words
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM (port peripherique)", "defaut": "COM6", "type": "port"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 9600},
            {"cle": "unite", "nom": "Numero d'unite Host Link", "defaut": 0}]


def open_omron(settings: dict):
    port = str(settings.get("port") or "COM6").strip()
    try:
        baud, unit = int(settings.get("baud") or 9600), int(settings.get("unite") or 0)
    except (TypeError, ValueError):
        raise _drv().DriverError("Vitesse et unite : des nombres") from None
    try:
        ser = hl.open_port(port, baud)
    except hl.HostLinkError as e:
        raise _drv().DriverError(str(e)) from None
    except Exception as e:
        raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    plc = hl.Cj1m(ser, unit)
    try:
        if not plc.test():
            raise hl.HostLinkError("echo different")
    except hl.HostLinkError as e:
        ser.close()
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({baud} 7E2, unite {unit}) : {e}. Port "
                                 "peripherique en Host Link (DIP 4 ON) ? CX-Programmer ferme ? Bon port COM ?") from None
    plc.nom = "Omron CJ1M"
    plc.close = ser.close
    return plc


# ---- demo: CJ1M-CPU12 with a CJ1W-ID211 and a CJ1W-OC211 ----

CMODE_READ = {"RR": "CIO", "RH": "H", "RJ": "A", "RD": "D"}
CMODE_WRITE = {"WR": "CIO", "WH": "H", "WJ": "A", "WD": "D"}
FINS_AREA = {0xB0: "CIO", 0xB1: "W", 0xB2: "H", 0xB3: "A", 0x82: "D"}
MAX_FRAME = 131


class Refuse(Exception):
    pass


def bcd(n):
    return (n // 10) << 4 | n % 10


class DemoPort:
    """Answers like a CJ1M: C-mode commands, FINS in 'FA' frames, replies over 131 characters
    split in several frames (each intermediate frame waits for a CR from the host)."""

    def __init__(self, unit=0, d_size=32768):
        self.unit = unit
        self.mem = {area: [0] * spec["size"] for area, spec in AREAS.items()}
        self.mem["D"] = [0] * d_size
        self.mem["D"][0:3] = [1250, 42, 0xFFFB]
        self.mem["CIO"][0] = 0b1000_0000_0000_0101       # ID211 inputs 0.00, 0.02, 0.15
        self.mem["CIO"][1] = 0b0000_0000_0000_0011       # OC211 outputs 1.00, 1.01
        self.mode = 0x04
        self.fatal, self.non_fatal = 0, 0
        self.program = bytes(range(256)) * 4 + b"END"
        self.program_unit = 2          # bytes per program address
        self.errors = [(0x00F7, 0, (26, 11, 10, 8, 12, 30)), (0x4105, 0, (26, 11, 12, 9, 40, 2))]
        self.corrupt_next = 0
        self.fa_end_code = True    # bench CJ1M: '@00FA00 40...' (False: W342 diagram, no end code)
        self.battery = True
        self.setup = [0] * 512
        self.setup[144] = 0x8000              # peripheral port: PLC Setup, Host Link, 7 bits, 2 stop, even
        self.io_table = [0] * 1280
        self.io_table[0:2] = [0x0402, 0x0450]       # bench: CJ1W-ID211, CJ1W-OC211
        self.unit_list = [(0x00, b"CJ1M_CPU11")]
        self.end_code = "00"
        self.cut_to = None   # return fewer words than asked
        self.requests = []
        self._out = []

    # serial port side
    def reset_input_buffer(self):
        self._out = []

    def write(self, data):
        if data == b"\r":                     # host asks for the next frame
            return
        frame = data.decode("ascii")
        assert frame.endswith("*\r")
        body, fcs = frame[:-4], frame[-4:-2]
        assert xor_checksum(body) == fcs, "the driver sent a wrong FCS"
        self.requests.append(body)
        header, text = body[3:5], body[5:]
        if header == "TS":
            self._out = [data]
            return
        reply = f"@{self.unit:02d}{header}"
        try:
            if self.end_code != "00":
                raise Refuse(self.end_code)
            answer = self.answer(header, text)
            # bench CJ1M sends '00' after 'FA'; the W342 §3-5-3 diagram shows none
            reply += answer if header == "FA" and not self.fa_end_code else "00" + answer
        except Refuse as e:
            reply += str(e)
        self._send(reply)

    def read_until(self, terminator):
        return self._out.pop(0) if self._out else b""

    def _send(self, text):
        frames = []
        first = True
        while True:
            room = (MAX_FRAME if first else 128) - 4
            if len(text) <= room:
                frames.append(text + xor_checksum(text) + "*\r")
                break
            part, text = text[:room + 1], text[room + 1:]
            frames.append(part + xor_checksum(part) + "\r")
            first = False
        if self.corrupt_next:
            self.corrupt_next -= 1
            f = frames[0]
            frames[0] = f[:-4] + f"{int(f[-4:-2], 16) ^ 0xFF:02X}" + f[-2:]
        self._out = [f.encode("ascii") for f in frames]

    # PLC side
    def answer(self, header, text):
        if header in CMODE_READ:
            area, start, count = CMODE_READ[header], int(text[0:4]), int(text[4:8])
            if self.cut_to:
                count = self.cut_to
            if start + count > len(self.mem[area]):
                raise Refuse("04")
            return "".join(f"{w:04X}" for w in self.mem[area][start:start + count])
        if header in CMODE_WRITE:
            if self.mode == 0x04:
                raise Refuse("01")
            area, start = CMODE_WRITE[header], int(text[0:4])
            for i, w in enumerate(decode_words(text[4:])):
                self.mem[area][start + i] = w
            return ""
        if header == "SC":
            self.mode = {"00": 0x00, "02": 0x02, "03": 0x04}[text]
            return ""
        if header == "MM":
            return "30"
        if header == "MS":
            ms_mode = {0x00: 0b00, 0x04: 0b10, 0x02: 0b11}[self.mode]
            return f"{ms_mode << 8 | 0x30:04X}"
        if header == "FA":
            assert text[0:9] == "000000000"
            cmd, params = text[9:13], text[13:]
            code, data = self.fins(cmd, params)
            return f"40000000{cmd}{code:04X}{data}"
        raise Refuse("IC")

    def fins(self, cmd, p):
        if cmd in ("0101", "0102"):
            area_code, addr, count = int(p[0:2], 16), int(p[2:6], 16), int(p[8:12], 16)
            if area_code == 0x89:
                area, addr = ("C", addr - 0x8000) if addr >= 0x8000 else ("T", addr)
            else:
                area = FINS_AREA[area_code]
            if addr + count > len(self.mem[area]):
                return 0x1104, ""
            if cmd == "0101":
                return 0x0040 if self.non_fatal else 0, "".join(f"{w:04X}" for w in self.mem[area][addr:addr + count])
            for i, w in enumerate(decode_words(p[12:])):
                self.mem[area][addr + i] = w
            return 0, ""
        if cmd == "0201":
            area, start, count = int(p[0:4], 16), int(p[4:8], 16), int(p[8:12], 16)
            table = {0x8010: self.setup, 0x8012: self.io_table}[area]
            words = table[start:start + count]
            last = 0x8000 if start + len(words) >= len(table) else 0
            code = 0x1104 if start + count > len(table) else 0
            return code, f"{area:04X}{start:04X}{len(words) | last:04X}" + "".join(f"{w:04X}" for w in words)
        if cmd == "0502":
            first, count = int(p[0:2], 16), int(p[2:4], 16)
            units = [u for u in self.unit_list if u[0] >= first][:count]
            raw = bytes([first, len(units) | 0x80])
            for addr, model in units:
                raw += bytes([addr]) + model.ljust(20, b" ")
            return 0, raw.hex().upper()
        if cmd == "0501":
            return 0, (b"CJ1M-CPU12".ljust(20, b" ") + b"02.00".ljust(20, b" ") + bytes(40)).hex().upper()
        if cmd == "0601":
            status = (0x01 if self.mode else 0x00) | (0x04 if self.battery else 0)
            raw = bytes([status, self.mode]) + self.fatal.to_bytes(2, "big") + self.non_fatal.to_bytes(2, "big")
            code = 0x4105 if self.non_fatal & 0x8000 else 0
            raw += bytes(2) + code.to_bytes(2, "big") + (b"DEFAUT CONVOYEUR" if code else bytes(16))
            return 0, raw.hex().upper()
        if cmd == "0701":
            return 0, bytes([bcd(26), bcd(10), bcd(30), bcd(14), bcd(5), bcd(9), 5]).hex().upper()
        if cmd == "2102":
            first, count = int(p[0:4], 16), int(p[4:8], 16)
            if first >= len(self.errors):
                return 0x1103, ""            # W342: command and end code only
            recs = self.errors[first:first + count]
            raw = (20).to_bytes(2, "big") + len(self.errors).to_bytes(2, "big") + len(recs).to_bytes(2, "big")
            for c1, c2, (y, mo, d, h, mi, s) in recs:
                raw += c1.to_bytes(2, "big") + c2.to_bytes(2, "big") + bytes(bcd(x) for x in (mi, s, d, h, y, mo))
            return (0x1104 if len(recs) < count else 0), raw.hex().upper()
        if cmd == "0306":
            assert p[0:4] == "FFFF"
            addr, count = int(p[4:12], 16), int(p[12:16], 16)
            assert addr % 4 == 0 and count % 4 == 0 and count <= 528, "W342: multiples of 4, 538-byte reply max"
            start = addr * self.program_unit
            if start >= len(self.program):
                return 0x1104, ""
            chunk = self.program[start:start + count]
            last = 0x8000 if start + count >= len(self.program) else 0
            return 0, f"FFFF{addr:08X}{len(chunk) | last:04X}" + chunk.hex().upper()
        return 0x0401, ""




def demo_omron():
    port = DemoPort()
    plc = hl.Cj1m(port, 0)
    plc.nom = "Omron CJ1M (demo)"
    plc.close = lambda: None
    return plc


# ---- status / diagnostic ----

def _status(plc) -> dict:
    try:
        return plc.controller_status()
    except hl.HostLinkError:
        return plc.status_area_a()


def mode_name(s) -> str:
    return hl.FINS_MODES.get(s["mode"], "?")


def flags(value, table) -> list:
    return [table.get(n, f"bit {n}") for n in range(15, -1, -1) if value >> n & 1]


def faults(s) -> list:
    out = [f"Erreur fatale : {f}" for f in flags(s["fatal"], hl.FATAL_BITS)]
    out += [f"Erreur non fatale : {f}" for f in flags(s["non_fatal"], hl.NON_FATAL_BITS)]
    if s["code_erreur"]:
        out.append(f"Erreur la plus grave : code {s['code_erreur']:04X} : {hl.error_log_text(s['code_erreur'])}"
                   + (f" ({s['message']})" if s.get("message") else ""))
    if s.get("pile_presente") is False:
        out.append("Pile absente")
    return out


def clock_text(plc) -> str:
    try:
        c = plc.clock()
    except hl.HostLinkError:
        try:
            c = plc.clock_area_a()
        except hl.HostLinkError:
            return "non lue"
    return f"{c['date']} {c['heure']}"


def omron_status(plc) -> dict:
    s = _status(plc)
    mode = mode_name(s)
    errs = faults(s)
    try:
        cd = plc.controller_data()
        model, ref = cd["modele"] or plc.nom, f"version {cd['version']}"
    except hl.HostLinkError:
        model, ref = plc.nom, ""
    fatal = bool(s["fatal"])
    out = {"marque": "Omron", "modele": model, "reference": ref,
           "mode": "DEFAUT" if fatal else ("STOP" if mode == "PROGRAM" else mode), "alertes": errs}
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN / MONITOR", "allume": mode in ("RUN", "MONITOR") and not fatal, "clignote": False,
         "defaut": False},
        {"nom": "ERR/ALM", "texte": "fixe : erreur fatale, clignote : non fatale", "allume": bool(s["fatal"] or s["non_fatal"]),
         "clignote": bool(s["non_fatal"]) and not fatal, "defaut": bool(s["fatal"] or s["non_fatal"])},
    ]
    out["details"] = [("Mode", mode), ("Horloge", clock_text(plc))]
    if s.get("pile_presente") is not None:
        out["details"].append(("Pile", "presente" if s["pile_presente"] else "ABSENTE"))
    return out


def card_rows(plc) -> list:
    rows = []
    try:
        cards = hl.rack_layout(plc.io_table(40))
    except hl.HostLinkError as e:
        return [{"emplacement": 0, "type": "table E/S", "adresse": "", "configure": {"code": "", "nom": "non lue",
                 "references": str(e)}, "en_place": None, "etat": "non lue", "ok": False, "variables": []}]
    for c in cards:
        row = {"emplacement": f"{c['rack']}.{c['slot']}", "type": c["kind"] or "carte",
               "configure": {"code": f"{c['code']:04X}", "nom": c["model"], "references": "table E/S enregistree"},
               "en_place": None, "etat": "ok", "ok": True, "variables": [], "adresse": ""}
        if c["cio"] is None:
            row["etat"] = "carte inconnue de l'outil : taille et mots CIO non calcules"
        else:
            words = plc.read("CIO", c["cio"], c["words"])
            row["adresse"] = f"CIO {c['cio']}" + (f"..{c['cio'] + c['words'] - 1}" if c["words"] > 1 else "")
            row["en_place"] = {"code": "", "nom": "  ".join(f"{c['cio'] + k} : " + " ".join(str(w >> b & 1) for b in range(16))
                                                         for k, w in enumerate(words)), "references": "bits 0..15"}
            row["variables"] = [{"adresse": f"{c['cio'] + k}.{b:02d}", "libelle": f"{c['model']} voie {b}"}
                                for k in range(c["words"]) for b in range(16)]
        rows.append(row)
    return rows


def error_log(plc):
    try:
        return plc.error_log(), "FINS 2102"
    except hl.HostLinkError:
        return plc.error_log_area_a(), "A100..A199"


def omron_diagnostic(plc) -> dict:
    s = _status(plc)
    out = {"alertes": faults(s), "config_compilee": "", "systeme": [], "modules": card_rows(plc)}
    records, source = error_log(plc)
    for i, r in enumerate(records, 1):
        out["systeme"].append({"adresse": f"journal {i}", "valeur": r["date"],
                               "texte": f"code {r['code']:04X} / {r['code2']:04X} : {hl.error_log_text(r['code'])}",
                               "ok": False})
    if not records:
        out["systeme"].append({"adresse": "journal", "valeur": "vide", "texte": f"journal d'erreurs ({source})", "ok": True})
    out["systeme"].append({"adresse": "fatal", "valeur": f"16#{s['fatal']:04X}", "texte": ", ".join(flags(s["fatal"], hl.FATAL_BITS)) or "aucune",
                           "ok": not s["fatal"]})
    out["systeme"].append({"adresse": "non fatal", "valeur": f"16#{s['non_fatal']:04X}",
                           "texte": ", ".join(flags(s["non_fatal"], hl.NON_FATAL_BITS)) or "aucune", "ok": not s["non_fatal"]})
    params = [("Mode", mode_name(s)), ("Horloge", clock_text(plc))]
    try:
        params += hl.decode_plc_setup(plc.plc_setup())
    except hl.HostLinkError as e:
        params.append(("Reglages automate (PLC Setup)", f"non lus : {e}"))
    try:
        for addr, model in plc.units():
            params.append((f"Unite {addr:02X}", model))
    except hl.HostLinkError:
        pass
    out["parametres_cpu"] = params
    return out


# ---- watch table ----

WORD_FORMATS = ["dec", "ud", "hex", "bin"]
WRITABLE = ("CIO", "W", "H", "D", "T", "C", "A")


class OmronVar:
    """'D100', 'D100:hex', 'W10', 'H5', 'A400', 'T5', 'C2', '0' or 'CIO 0' (word), '0.03' / 'W10.05' (bits)."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        try:
            self.area, self.n, self.bit = hl.parse_address(address)
        except ValueError as e:
            raise ValueError(f"{e} (ex. D100, W10, H5, A400, T5, C2, 0.03, W10.05)") from None
        self.word = self.bit is None
        self.fmt = (fmt.strip().lower() or "dec") if self.word else ""
        if self.word and self.fmt not in WORD_FORMATS:
            raise ValueError(f"format {self.fmt} : {', '.join(WORD_FORMATS)}")

    def read(self, plc):
        w = plc.read(self.area, self.n, 1)[0]
        return w if self.word else w >> self.bit & 1

    def decode(self, v) -> str:
        if not self.word or self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:04X}"
        if self.fmt == "bin":
            t = f"{v:016b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, 16, 4))
        return str(hl.signed16(v))

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
            else:
                v = int(t, 16) if self.fmt == "hex" else int(t, 2) if self.fmt == "bin" else int(t)
        except ValueError:
            raise ValueError("valeur illisible") from None
        if not -32768 <= v <= 65535:
            raise ValueError("hors -32768..65535")
        return v & 0xFFFF


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        try:
            v = OmronVar(str(t))
            out["valeurs"][str(t)] = v.decode(v.read(plc))
        except ValueError as e:
            out["erreurs"][str(t)] = str(e)
        except (hl.PlcRefused, hl.FinsRefused) as e:
            out["erreurs"][str(t)] = str(e)
    return out


def _refused_in_run(e) -> bool:
    return getattr(e, "code", None) in ("01", 0x2201, 0x2202)


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = OmronVar(text)
            if v.area not in WRITABLE:
                raise ValueError("zone non ecrivable")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = v.read(plc)
        try:
            if v.word:
                plc.write(v.area, v.n, [value])
            else:                       # read-modify-write of the word: another bit may change in between
                w = plc.read(v.area, v.n, 1)[0]
                plc.write(v.area, v.n, [(w | 1 << v.bit) if value else (w & ~(1 << v.bit))])
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        except (hl.PlcRefused, hl.FinsRefused) as e:
            if _refused_in_run(e):
                raise _drv().DriverError("En RUN l'automate refuse les ecritures : passer en MONITOR (bouton "
                                         "Passer en MONITOR, le programme continue de tourner)") from None
            raise
        back = v.read(plc)
        results.append({"adresse": text, "avant": v.decode(old), "relu": v.decode(back), "ok": back == value})
    return {"resultats": results, "run": mode_name(_status(plc)) in ("RUN", "MONITOR")}


# ---- data copies (D, H, W) ----

DATA_ZONES = ("D", "H", "W")


def read_area_to_end(plc, area, progress=None) -> list:
    """Whole area; stops where the PLC refuses (memory smaller than the table says)."""
    values, size = [], AREAS[area]["size"]
    n = plc.max_block(area)
    while len(values) < size:
        n = min(n, size - len(values))
        try:
            values += plc.read(area, len(values), n)
        except (hl.PlcRefused, hl.FinsRefused) as e:
            if e.code not in ("04", "15", 0x1103, 0x1104):
                raise
            if n > 1:
                n //= 2
                continue
            break
        if progress:
            progress(f"lecture {area} : {len(values)} mots", len(values), size)
    return values


def data_csv(zones: dict, model: str, stamp: str) -> str:
    lines = [f"# date;{stamp}", f"# automate;{model}", "zone;adresse;valeur;signe;hex"]
    for area, values in zones.items():
        items = sorted(values.items()) if isinstance(values, dict) else enumerate(values)
        lines += [f"{area};{i};{v};{hl.signed16(v)};{v:04X}" for i, v in items]
    return "\n".join(lines) + "\n"


def parse_data_csv(text: str):
    zones, model = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# automate;"):
            model = line.split(";", 1)[1]
        if not line or line.startswith("#") or line.lower().startswith("zone;"):
            continue
        parts = line.split(";")
        try:
            zones.setdefault(parts[0].strip().upper(), {})[int(parts[1])] = int(parts[2]) & 0xFFFF
        except (ValueError, IndexError):
            raise _drv().DriverError(f"ligne illisible : {line}") from None
    if not zones:
        raise _drv().DriverError("aucun mot dans le fichier")
    return zones, model


def read_span(plc, area, addrs) -> dict:
    lo, hi = min(addrs), max(addrs)
    values = []
    while lo + len(values) <= hi:
        n = min(300, hi + 1 - lo - len(values))
        values += plc.read(area, lo + len(values), n)
    return dict(zip(range(lo, hi + 1), values))


def changed_runs(addrs) -> list:
    runs = []
    for a in sorted(addrs):
        if runs and runs[-1][0] + runs[-1][1] == a:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return runs


def model_name(plc) -> str:
    try:
        return plc.controller_data()["modele"] or plc.nom
    except hl.HostLinkError:
        return plc.nom


PROGRAM_FILE = "programme_cj1m.bin"


class OmronOps:
    """Session calls for an Omron CJ1M."""

    def __init__(self, session):
        from plc_online.web.tools_web import ProgramCopies, ranges_text
        self.s = session
        self.prog = ProgramCopies(
            session, lambda: self.s.tsx_backups.parent / "omron" / "programmes", PROGRAM_FILE,
            lambda plc, progress: plc.read_program(progress=lambda n: progress(f"lecture du programme : {n} octets", 0, 0)),
            lambda blob: f"{len(blob)} octets", lambda a, b: ranges_text(
                [i for i in range(min(len(a), len(b))) if a[i] != b[i]], "octet"),
            "cj1m", lambda data: len(data) > 0,
            "Rechargement d'un programme dans le CJ1M : pas encore valide, utiliser CX-Programmer. La copie sert a "
            "comparer et a garder une trace (lecture du programme : experimentale).")
        for name in ("list_backups", "compare", "backup_blocks", "restore_plan", "restore", "export_backup",
                     "import_backup"):
            setattr(self, name, getattr(self.prog, name))

    @property
    def programs(self):
        return self.prog.programs

    @property
    def data(self) -> Path:
        return self.s.tsx_backups.parent / "omron" / "donnees"

    def save_program(self, note):
        return self.prog.save_program(note, model_name)

    def status(self, plc):
        return omron_status(plc)

    def diagnostic(self):
        return self.s._call(omron_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def _mode(self, mode):
        def go(plc):
            plc.set_mode(mode)
            return {"ok": True, "mode": mode_name(_status(plc))}
        return self.s._call(go)

    def start_cpu(self):
        return self._mode("run")

    def stop_cpu(self):
        return self._mode("programme")

    def monitor_cpu(self):
        return self._mode("monitor")

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": tsx_web.list_copies(self.data, tsx_web.DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            zones = {area: read_area_to_end(plc, area, progress) for area in DATA_ZONES}
            text = data_csv(zones, model_name(plc), time.strftime("%Y-%m-%d %H:%M:%S"))
            return tsx_web.save_data(self.data, text, plc.nom, note,
                                     ", ".join(f"{len(v)} {a}" for a, v in zones.items()))
        return self.s.start_job("sauvegarde des donnees", work)

    def _saved(self, backup_id):
        path = _drv().backup_path(self.data, backup_id)
        try:
            return parse_data_csv((path / tsx_web.DATA_FILE).read_text(encoding="utf-8"))
        except OSError as e:
            raise _drv().DriverError(f"copie illisible : {e}") from None

    def _diffs(self, plc, zones):
        out = {}
        for area in DATA_ZONES:
            saved = {a: v for a, v in zones.get(area, {}).items() if a < AREAS[area]["size"]}
            if saved:
                current = read_span(plc, area, saved)
                d = {a: v for a, v in saved.items() if current[a] != v}
                if d:
                    out[area] = (d, current)
        return out

    def data_plan(self, backup_id):
        zones, model_in_file = self._saved(backup_id)

        def run(plc):
            diffs = self._diffs(plc, zones)
            examples = [{"adresse": f"{area}{a}", "automate": hl.signed16(cur[a]), "fichier": hl.signed16(d[a])}
                        for area, (d, cur) in diffs.items() for a in sorted(d)[:6]][:12]
            ignored = [z for z in zones if z not in DATA_ZONES]
            return {"zone": "/".join(DATA_ZONES), "mots": sum(len(v) for v in zones.values()),
                    "differents": sum(len(d) for d, _ in diffs.values()), "zone_automate": 0, "hors_zone": 0,
                    "kw_ignores": sum(len(zones[z]) for z in ignored), "automate_fichier": model_in_file,
                    "automate": model_name(plc), "run": mode_name(_status(plc)) == "RUN", "exemples": examples}
        return self.s._call(run)

    def data_restore(self, backup_id):
        zones, _ = self._saved(backup_id)

        def work(plc, progress):
            progress("lecture des mots de l'automate", 0, 0)
            diffs = self._diffs(plc, zones)
            back_to_run = False
            if diffs and mode_name(_status(plc)) == "RUN":
                progress("passage en MONITOR (le programme continue)", 0, 0)
                plc.set_mode("monitor")
                back_to_run = True
            total = sum(len(d) for d, _ in diffs.values())
            done = 0
            for area, (d, _) in diffs.items():
                for start, length in changed_runs(d):
                    plc.write(area, start, [d[a] for a in range(start, start + length)])
                    done += length
                    progress(f"ecriture {area}{start} ({length} mot(s))", done, total)
            wrong = []
            for area, (d, _) in diffs.items():
                after = read_span(plc, area, d)
                wrong += [f"{area}{a}" for a in d if after[a] != d[a]]
            if back_to_run:
                plc.set_mode("run")
            return {"ecrits": total, "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_omron")

    def data_import(self, files, note):
        self.data.mkdir(parents=True, exist_ok=True)
        made, ignored = [], []
        for name, data in files:
            base = Path(name.replace("\\", "/")).name
            if not base.lower().endswith(".csv"):
                ignored.append(base)
                continue
            zones, model = parse_data_csv(data.decode("utf-8", "replace"))
            text = data_csv(zones, model, time.strftime("%Y-%m-%d %H:%M:%S"))
            made.append(tsx_web.save_data(self.data, text, model, note or f"importe de {base}",
                                          ", ".join(f"{len(v)} {a}" for a, v in zones.items())))
        if not made:
            raise _drv().DriverError("Aucun fichier .csv de donnees Omron dans les fichiers choisis")
        return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction pas encore disponible pour Omron")
