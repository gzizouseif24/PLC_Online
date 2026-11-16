"""Siemens S7-200 in the web interface (RS-232/PPI cable on a COM port, plc_online/protocols/ppi.py).

Status, special memory (SM) explained, CPU + expansion modules from SMB8..SMB21 with their addresses
and live values, watch table, V memory copies, RUN / STOP, read-only program copy (OB / DB / SDB
blocks uploaded as the CPU gives them).

SM meanings, CPU and module identification, memory sizes and I/O addressing: S7-200 System Manual
(A5E00307987-04), appendix D and chapter 4. Program decoding and loading a program back: work on the
bench (docs/s7-200.md)."""
import re
import struct
import time
from pathlib import Path

from plc_online.protocols import ppi
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "port", "nom": "Port COM (cable RS-232/PPI)", "defaut": "COM6", "type": "port"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 9600},
            {"cle": "adresse", "nom": "Adresse PPI de l'automate", "defaut": 2},
            {"cle": "local", "nom": "Adresse PPI du PC", "defaut": 0}]

# SMB6 bits 4..7 (manual table D-7)
CPU_IDS = {0b0000: "CPU 222", 0b0010: "CPU 224 / 224XP", 0b0110: "CPU 221", 0b1001: "CPU 226"}
# built-in I/O (manual table 1-1): digital in, digital out, analog in, analog out
CPU_IO = {"CPU 221": (6, 4, 0, 0), "CPU 222": (8, 6, 0, 0), "CPU 224": (14, 10, 0, 0),
          "CPU 224XP": (14, 10, 2, 1), "CPU 226": (24, 16, 0, 0)}
V_SIZES = (10240, 8192, 2048)

SM_BITS = {(0, 0): "toujours a 1 (RUN)", (0, 1): "premier cycle", (0, 2): "donnees remanentes perdues",
           (0, 3): "passage en RUN a la mise sous tension", (0, 5): "horloge 1 s", (0, 6): "horloge de cycle",
           (0, 7): "selecteur sur RUN (0 = TERM)", (1, 1): "depassement / valeur invalide",
           (1, 3): "division par zero", (4, 3): "probleme de programmation a l'execution",
           (4, 7): "forcage en cours", (5, 0): "erreur d'E/S", (5, 1): "trop d'E/S TOR sur le bus",
           (5, 2): "trop d'E/S analogiques sur le bus", (5, 3): "trop de modules intelligents"}
BAD_BITS = {(0, 2), (4, 3), (4, 7), (5, 0), (5, 1), (5, 2), (5, 3)}
MODULE_ERRORS = {7: "erreur de configuration", 4: "defaut bus ou parite", 3: "hors plage",
                 2: "pas d'alimentation utilisateur", 1: "fusible", 0: "bornier mal enfiche"}


def open_s7200(settings: dict):
    port = str(settings.get("port") or "COM6").strip()
    try:
        baud = int(settings.get("baud") or 9600)
        plc_addr, local = int(settings.get("adresse") or 2), int(settings.get("local") or 0)
    except (TypeError, ValueError):
        raise _drv().DriverError("Vitesse et adresses : des nombres") from None
    try:
        ser = ppi.open_port(port, baud)
    except Exception as e:
        raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    plc = ppi.S7200(ppi.Ppi(ser, plc_addr, local))
    plc.ppi.journal_path = Path.home() / "PLC Online" / "sauvegardes" / "s7-200" / "journal_ppi.txt"
    try:
        plc.connect()
    except ppi.PpiError as e:
        ser.close()
        raise _drv().DriverError(f"L'automate ne repond pas sur {port} ({baud} 8E1, adresse {plc_addr}) : {e}. "
                                 "Cable RS-232/PPI : commutateurs sur 9600 et mode PPI ? Micro/WIN ferme ? "
                                 "Selecteur de l'automate sur RUN ou TERM (pas en mode Freeport) ?") from None
    _identify(plc)
    return plc


def _identify(plc):
    sm6 = plc.read("SM", 6, 1)[0]
    cpu = CPU_IDS.get(sm6 >> 4, f"CPU inconnue (SMB6 = 16#{sm6:02X})")
    plc.v_size = 0
    for size in V_SIZES:
        try:
            plc.read("V", size - 1, 1)
            plc.v_size = size
            break
        except ppi.PlcRefused:
            continue
    if cpu == "CPU 224 / 224XP":
        cpu = "CPU 224XP" if plc.v_size == 10240 else "CPU 224"
    plc.cpu, plc.firmware = cpu, ""
    try:                         # area 03: 'CPU 224' padded to 16 characters, then '02010100' (bench 16/11)
        info = plc.read_raw_area(0x03, 0, 24)
        name = info[:16].decode("ascii", "replace").strip()
        digits = info[16:24].decode("ascii", "replace")
        if name.startswith("CPU"):
            plc.cpu = name if name.replace(" ", "") in (c.replace(" ", "") for c in CPU_IO) else cpu
        if digits.isdigit():
            plc.firmware = f"{digits[0:2]}.{digits[2:4]} (brut {digits})"
    except ppi.PpiError:
        pass
    cpu = plc.cpu
    plc.nom = f"Siemens S7-200 {cpu}"


# ---- demo: frame-level simulated CPU 224 with an EM 223 (16 DI / 16 DO) and an EM 235 ----

class DemoPort:
    """Answers PPI frames like an S7-200: E5, then the reply on 'request data'."""

    def __init__(self):
        self.mem = {"I": bytearray(16), "Q": bytearray(16), "M": bytearray(32), "V": bytearray(8192),
                    "SM": bytearray(550), "AI": bytearray(64), "AQ": bytearray(64)}
        self.mem["SM"][6] = 0b0010_0000                       # CPU 224
        self.mem["SM"][0] = 0b1000_0001                       # SM0.0, SM0.7 (switch on RUN)
        self.mem["SM"][8:22] = bytes([0b0000_1010, 0, 0b0001_1001, 0] + [0x80, 0] * 5)   # EM 223 16/16, EM 235, absent
        struct.pack_into(">HHH", self.mem["SM"], 22, 3, 2, 7)
        self.mem["I"][0], self.mem["I"][2] = 0b0000_0101, 0b0000_0010
        self.mem["V"][0:4] = bytes([0x04, 0xD2, 0, 42])
        struct.pack_into(">h", self.mem["AI"], 0, 6400)
        self.running = True
        # the blocks of the bench CPU 224 after Micro/WIN loaded an empty project (16/11); SDB2 is listed, not uploadable
        self.blocks = unpack_blocks((Path(__file__).parent / "s7200_demo_program.bin").read_bytes())
        self.hidden = {("SDB", 2)}
        self.uploads = {}
        self.jobs = []                                         # requests of the PLC during a block load (1B / 1C)
        self.load = None
        self.job_ref = 0x100
        self.buf = b""
        self.pending = None
        self.lose_next = 0
        self.scans = 0

    def reset_input_buffer(self):
        self.buf = b""

    def read(self, n=1):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass

    def write(self, frame):
        if frame[0] == ppi.SD1:
            if self.pending is None and self.jobs:
                self.pending = self.jobs.pop(0)
            if self.pending is None:
                self.buf = b""
                return
            reply, self.pending = self.pending, None
            if self.lose_next:                               # reply lost on the line: kept for the next ask
                self.lose_next -= 1
                self.pending = reply
                self.buf = b""
                return
            self.buf = ppi.sd2_frame(frame[2], frame[1], 0x08, reply)
            return
        if self.pending is not None:                         # bench S7-200: busy until the reply is collected
            self.buf = ppi.sd1_frame(frame[5], frame[4], 0x02)
            return
        body = frame[4:-2]
        assert ppi.fcs(body) == frame[-2], "le PC a envoye une somme fausse"
        if body[2] == 0x5C:                                  # the PC answers a request of the PLC (block load)
            self._load_answer(ppi.parse_pdu(body[3:]))
            self.buf = bytes([ppi.SC])
            return
        self.pending = self._answer(ppi.parse_pdu(body[3:]))
        self.buf = bytes([ppi.SC])

    def _tick(self):
        if self.running:
            self.mem["Q"][0] = self.mem["I"][0] & 0x0F            # demo program: Q0.0..Q0.3 = I0.0..I0.3
            self.scans += 1
            self.mem["SM"][0] ^= 0b0100_0000                 # SM0.6: scan clock
            self.mem["SM"][0] |= 1
        else:
            self.mem["SM"][0] &= ~1 & 0xFF
            if not getattr(self, "was_stopped", False):
                self.mem["Q"][:] = bytes(len(self.mem["Q"]))       # STOP: outputs to 0
        self.was_stopped = not self.running

    def _reply(self, ref, param, data=b"", error=0, kind=3):
        head = bytes([0x32, kind, 0, 0, ref >> 8, ref & 0xFF, len(param) >> 8, len(param) & 0xFF,
                      len(data) >> 8, len(data) & 0xFF])
        return head + (bytes([error >> 8, error & 0xFF]) if kind in (2, 3) else b"") + param + data

    def _job(self, param):
        self.job_ref += 1
        self.jobs.append(self._reply(self.job_ref, param, kind=1))

    def _load_answer(self, p):
        if self.load is None or p["type"] != 3:
            return
        if p["param"][:1] == b"\x1b":
            d = p["data"]
            assert d[3] == 0xFB, "prefixe 00 FB attendu (libnodave)"
            self.load["buf"] += d[4:4 + (d[0] << 8 | d[1])]
            self._job(bytes([0x1B, 0, 0, 0, 0, 0, 0, 1]) if p["param"][1] else bytes([0x1C, 0, 0, 0, 0, 0, 0, 1]))
        elif p["param"][:1] == b"\x1c":
            self.load["fini"] = True

    def _area(self, code, db):
        return next((a for a, (c, d) in ppi.AREAS.items() if c == code and (a != "V" or db == 1)), None)

    def _answer(self, p):
        self._tick()
        ref, param, data = p["ref"], p["param"], p["data"]
        f = param[0] if param else 0
        if p["type"] == 7 and len(param) > 5 and param[5] == 0x48:   # outputs in STOP (daveForce200)
            if not self.running:
                self.mem["Q"][data[4 + 9] << 13 | data[4 + 10] << 5 | data[4 + 11] >> 3] = data[16 + 4]
            return self._reply(ref, bytes([0, 1, 0x12, 8, 0x12, 0x88, 0x0E, 0, 0, 0, 0, 0]), bytes([0xFF, 9, 0, 0]), kind=7)
        if p["type"] == 7:                                     # userdata: list blocks of a type
            kind = {v: k for k, v in ppi.BLOCK_TYPES.items()}.get(data[5])
            nums = sorted(n for (k, n) in set(self.blocks) | self.hidden if k == kind)
            raw = b"".join(struct.pack(">HBB", n, 0x22, 0x05) for n in nums)
            body = bytes([0xFF, 9, len(raw) >> 8, len(raw) & 0xFF]) + raw if nums else bytes([0x0A, 0, 0, 0])
            return self._reply(ref, bytes([0, 1, 0x12, 8, 0x12, 0x83, 2, 1, 0, 0, 0, 0]), body, kind=7)
        if f == 0xF0:
            return self._reply(ref, bytes([0xF0, 0, 0, 1, 0, 1, 0, 240]))
        if f in (0x04, 0x05):
            item = param[2:14]
            transport, length, db, code = item[3], item[4] << 8 | item[5], item[6] << 8 | item[7], item[8]
            address = item[9] << 16 | item[10] << 8 | item[11]
            area = self._area(code, db)
            if code == 0x03 and f == 0x04:                     # system info, as the bench CPU 224
                got = (b"CPU 224".ljust(16) + b"02010100" + bytes([0x2A, 0, 0, 0, 0, 0, 0xFF, 0, 0, 0, 0, 0, 0xFF, 0, 0, 0]))[:length]
                return self._reply(ref, bytes([4, 1]), bytes([0xFF, 4, len(got) * 8 >> 8, len(got) * 8 & 0xFF]) + got)
            mem = self.mem.get(area)
            start, nbytes = address // 8, (length * 2 if (f == 0x05 and transport == 4) else length)
            if mem is None or (transport != 1 and start + nbytes > len(mem)):
                return self._reply(ref, bytes([f, 1]), bytes([0x05, 0, 0, 0]) if f == 0x04 else bytes([0x05]))
            if f == 0x04:
                got = bytes(mem[start:start + nbytes])
                return self._reply(ref, bytes([4, 1]), bytes([0xFF, 4, len(got) * 8 >> 8, len(got) * 8 & 0xFF]) + got)
            if transport == 1:
                bit = address % 8
                mem[start] = (mem[start] | 1 << bit) if data[4] else (mem[start] & ~(1 << bit))
            else:
                n = (data[2] << 8 | data[3]) // 8
                mem[start:start + n] = data[4:4 + n]
            return self._reply(ref, bytes([5, 1]), bytes([0xFF]))
        if f == 0x1A:                                         # load request: _0<type><number>P + sizes
            if self.running:
                return self._reply(ref, bytes([0x1A]), error=0xD802, kind=3)
            self.load = {"nom": param[10:18], "taille": int(param[20:26]), "buf": b"", "fini": False}
            self._job(bytes([0x1B, 0, 0, 0, 0, 0, 0, 1]))
            return self._reply(ref, bytes([0x1A]))
        if f == 0x28 and b"_INSE" in param:
            load, self.load = self.load, None
            if not load or not load["fini"] or param[12:20] != load["nom"] or len(load["buf"]) != load["taille"]:
                return self._reply(ref, bytes([0x28]), error=0xD209, kind=3)
            blob = load["buf"]
            self.blocks[(ppi.TYPE_CODES[blob[5]], blob[6] << 8 | blob[7])] = blob
            return self._reply(ref, bytes([0x28]))
        if f == 0x28 and b"P_PROGRAM" in param:
            self.running = True
            return self._reply(ref, bytes([0x28]))
        if f == 0x29:
            self.running = False
            return self._reply(ref, bytes([0x29]))
        if f == 0x1D:
            kind = {v: k for k, v in ppi.BLOCK_TYPES.items()}.get(param[11])
            key = (kind, int(param[12:17].decode()))
            if key not in self.blocks:
                return self._reply(ref, bytes([0x1D]), error=0xD209, kind=2)
            self.uploads[7] = [self.blocks[key], 0]
            return self._reply(ref, bytes([0x1D, 0, 0, 0, 0, 0, 0, 7]))
        if f == 0x1E:
            blob, pos = self.uploads[param[7]]
            part = blob[pos:pos + 100]
            self.uploads[param[7]][1] = pos + len(part)
            more = 1 if pos + len(part) < len(blob) else 0
            return self._reply(ref, bytes([0x1E, more]), bytes([len(part) >> 8, len(part) & 0xFF, 0, 0]) + part)
        if f == 0x1F:
            self.uploads.pop(param[7], None)
            return self._reply(ref, bytes([0x1F]))
        return self._reply(ref, bytes([f]), error=0x8104, kind=2)


def demo_s7200():
    plc = ppi.S7200(ppi.Ppi(DemoPort(), 2, 0))
    plc.connect()
    _identify(plc)
    plc.nom += " (demo)"
    return plc


# ---- status / diagnostic ----

def _sm(plc):
    return plc.read("SM", 0, 30)


def _bit(sm, byte, bit) -> bool:
    return bool(sm[byte] >> bit & 1)


def running(plc) -> bool:
    """RUN when the scan clock SM0.6 or the 1 s clock SM0.5 moves over a few reads (SMB0 is updated at
    the end of each scan, manual table D-1). Deduced, to confirm on the bench."""
    first = plc.read("SM", 0, 1)[0]
    for _ in range(5):
        time.sleep(0.03)
        if (plc.read("SM", 0, 1)[0] ^ first) & 0b0110_0000:
            return True
    return False


def modules(sm) -> list:
    """Expansion modules from SMB8..SMB21 (manual table D-8)."""
    out = []
    for i in range(7):
        ident, err = sm[8 + 2 * i], sm[9 + 2 * i]
        if ident >> 7 or ident == 0:         # m = 1: not present (0: no I/O at all, nothing to show)
            continue
        smart, analog = (ident >> 5 & 3) == 1, bool(ident >> 4 & 1)
        ins, outs = ident >> 2 & 3, ident & 3
        n_in = {0: 0, 1: 2, 2: 4, 3: 8}[ins] if analog else {0: 0, 1: 8, 2: 16, 3: 32}[ins]
        n_out = {0: 0, 1: 2, 2: 4, 3: 8}[outs] if analog else {0: 0, 1: 8, 2: 16, 3: 32}[outs]
        out.append({"position": i, "intelligent": smart, "analogique": analog, "entrees": n_in, "sorties": n_out,
                    "erreurs": [t for b, t in MODULE_ERRORS.items() if err >> b & 1], "id": ident, "err": err})
    return out


def layout(cpu: str, mods: list) -> list:
    """Addresses of the CPU and modules (manual 4: digital per byte, analog per 2 channels)."""
    di, dq, ai, aq = CPU_IO.get(cpu, (16, 16, 0, 0))
    units = [{"nom": cpu, "position": "CPU", "di": (0, di), "dq": (0, dq), "ai": (0, ai), "aq": (0, aq), "erreurs": []}]
    ib, qb = (di + 7) // 8, (dq + 7) // 8
    aiw, aqw = ((ai + 1) // 2) * 4, ((aq + 1) // 2) * 4
    for m in mods:
        u = {"nom": "module intelligent" if m["intelligent"] else ("module analogique" if m["analogique"] else "module TOR"),
             "position": m["position"], "di": (ib * 8, 0), "dq": (qb * 8, 0), "ai": (aiw, 0), "aq": (aqw, 0),
             "erreurs": m["erreurs"]}
        if m["analogique"]:
            u["ai"], u["aq"] = (aiw, m["entrees"]), (aqw, m["sorties"])
            aiw += ((m["entrees"] + 1) // 2) * 4
            aqw += ((m["sorties"] + 1) // 2) * 4
        elif not m["intelligent"]:
            u["di"], u["dq"] = (ib * 8, m["entrees"]), (qb * 8, m["sorties"])
            ib += m["entrees"] // 8
            qb += m["sorties"] // 8
        units.append(u)
    return units


def bit_name(area, n):
    return f"{area}{n // 8}.{n % 8}"


def faults(sm) -> list:
    out = [f"{SM_BITS[(b, i)]} (SM{b}.{i})" for (b, i) in sorted(BAD_BITS) if _bit(sm, b, i) and (b, i) != (0, 2)]
    for m in modules(sm):
        if m["erreurs"]:
            out.append(f"Module {m['position']} : {', '.join(m['erreurs'])} (SMB{9 + 2 * m['position']})")
    return out


def s7200_status(plc) -> dict:
    sm = _sm(plc)
    run = running(plc)
    errs = faults(sm)
    last, low, high = struct.unpack_from(">HHH", sm, 22)
    out = {"marque": "Siemens", "modele": plc.nom,
           "reference": (f"firmware {plc.firmware}, " if plc.firmware else "") + f"memoire V : {plc.v_size} octets",
           "mode": "RUN" if run else "STOP", "alertes": errs}
    out["voyants"] = [
        {"nom": "RUN", "texte": "RUN", "allume": run, "clignote": False, "defaut": False},
        {"nom": "SF", "texte": "defaut systeme / E/S", "allume": bool(errs), "clignote": False, "defaut": bool(errs)},
    ]
    out["details"] = [("Selecteur", "RUN" if _bit(sm, 0, 7) else "TERM ou STOP (SM0.7 = 0)"),
                      ("Cycle", f"{last} ms (mini {low}, maxi {high})"),
                      ("Forcage", "OUI (SM4.7)" if _bit(sm, 4, 7) else "non")]
    return out


def _io_text(plc, area, first, count):
    if not count:
        return ""
    raw = plc.read(area, first // 8, (count + 7) // 8)
    bits = [raw[i // 8] >> (i % 8) & 1 for i in range(count)]
    return f"{bit_name(area, first)}..{bit_name(area, first + count - 1)} : " + " ".join(map(str, bits))


def s7200_diagnostic(plc) -> dict:
    sm = _sm(plc)
    out = {"alertes": faults(sm), "config_compilee": "", "systeme": [], "modules": []}
    for (b, i), text in SM_BITS.items():
        out["systeme"].append({"adresse": f"SM{b}.{i}", "valeur": str(int(_bit(sm, b, i))), "texte": text,
                               "ok": not ((b, i) in BAD_BITS and _bit(sm, b, i))})
    last, low, high = struct.unpack_from(">HHH", sm, 22)
    out["systeme"] += [{"adresse": "SMB6", "valeur": f"16#{sm[6]:02X}", "texte": f"identification CPU : {plc.cpu}", "ok": True},
                       {"adresse": "SMW22..26", "valeur": f"{last} / {low} / {high} ms", "texte": "cycle dernier / mini / maxi",
                        "ok": True}]
    try:
        bus = struct.unpack(">H", plc.read("SM", 98, 2))[0]
        out["systeme"].append({"adresse": "SMW98", "valeur": str(bus), "texte": "erreurs de parite sur le bus d'extension",
                               "ok": bus == 0})
    except ppi.PpiError:
        pass
    for u in layout(plc.cpu, modules(sm)):
        parts, variables = [], []
        for area, key in (("I", "di"), ("Q", "dq")):
            first, count = u[key]
            if count:
                parts.append(_io_text(plc, area, first, count))
                variables += [{"adresse": bit_name(area, first + k), "libelle": f"{u['nom']} {'entree' if area == 'I' else 'sortie'}"}
                              for k in range(count)]
        for area, key in (("AI", "ai"), ("AQ", "aq")):
            first, count = u[key]
            if count:
                words = struct.unpack(f">{count}h", plc.read(area, first, 2 * count)) if area == "AI" else None
                names = [f"{area}W{first + 2 * k}" for k in range(count)]
                parts.append("  ".join(f"{n} = {v}" for n, v in zip(names, words)) if words else ", ".join(names))
                variables += [{"adresse": n, "libelle": u["nom"]} for n in names]
        addresses = [f"{a}{u[k][0] // 8 if a in 'IQ' else u[k][0]}" for a, k in (("I", "di"), ("Q", "dq"), ("AIW", "ai"), ("AQW", "aq")) if u[k][1]]
        out["modules"].append({"emplacement": u["position"], "type": u["nom"], "adresse": " / ".join(addresses),
                               "configure": {"code": "", "nom": u["nom"],
                                             "references": "SMB8..SMB21" if u["position"] != "CPU" else "SMB6"},
                               "en_place": {"code": "", "nom": "  /  ".join(parts) or "-", "references": ""},
                               "etat": ", ".join(u["erreurs"]) or "ok", "ok": not u["erreurs"], "variables": variables})
    out["parametres_cpu"] = [("CPU", plc.cpu), ("Memoire V", f"{plc.v_size} octets (VB0..VB{plc.v_size - 1})"),
                             ("Taille PDU", str(plc.pdu_size))]
    out["parametres_cpu"].append(("Echanges repetes", f"{plc.ppi.failures} depuis la connexion "
                                  "(une repetition = trame perdue ou brouillee, puis reussie)"))
    other = list(plc.ppi.skipped)
    if other:
        out["parametres_cpu"].append(("Autres stations sur la ligne",
                                      f"{len(other)} trame(s) ignoree(s) (automate en maitre PPI / NETR-NETW, ou IHM), "
                                      f"derniere : {other[-1].hex(' ')}"))
    try:
        out["parametres_cpu"].append(("Zone systeme 03 (40 octets, brut)", plc.read_raw_area(0x03, 0, 40).hex(" ")))
    except ppi.PpiError as e:
        out["parametres_cpu"].append(("Zone systeme 03", f"non lue : {e}"))
    return out


# ---- watch table ----

ADDRESS = re.compile(r"^(SM|AI|AQ|[IQMV])(B|W|D)?(\d+)(?:\.([0-7]))?$", re.IGNORECASE)
FORMATS = {"B": ["dec", "ud", "hex", "bin"], "W": ["dec", "ud", "hex", "bin"], "D": ["dec", "ud", "hex", "bin", "real"]}


class S7200Var:
    """'I0.0', 'Q0.1', 'M1.2', 'V100.3', 'SM0.5', 'VB10', 'VW100', 'VD200:real', 'MW0', 'SMW22', 'AIW0', 'AQW0'."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        m = ADDRESS.match(address.strip().upper())
        if not m:
            raise ValueError("adresse S7-200 : I0.0, Q0.1, M1.2, V100.3, SM0.5, VB10, VW100, VD200, SMW22, AIW0, AQW0")
        self.area, size, self.n, bit = m.group(1).upper(), m.group(2), int(m.group(3)), m.group(4)
        if self.area in ("AI", "AQ"):
            if size != "W" or bit is not None:
                raise ValueError("entrees / sorties analogiques : AIW0, AQW2 (mots)")
        if bit is not None and size:
            raise ValueError("bit : I0.0, V100.3 (sans B / W / D)")
        if bit is None and not size:
            raise ValueError("preciser B, W ou D (ex. VW100) ou un bit (V100.3)")
        self.bit = int(bit) if bit is not None else None
        self.size = {"B": 1, "W": 2, "D": 4}.get(size, 0)
        self.fmt = (fmt.strip().lower() or "dec") if self.size else ""
        if self.size and self.fmt not in FORMATS[size]:
            raise ValueError(f"format {self.fmt} : {', '.join(FORMATS[size])}")

    def read(self, plc):
        if self.bit is not None:
            return plc.read(self.area, self.n, 1)[0] >> self.bit & 1
        return int.from_bytes(plc.read(self.area, self.n, self.size), "big")

    def decode(self, v) -> str:
        if self.bit is not None:
            return str(v)
        bits = 8 * self.size
        if self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:0{bits // 4}X}"
        if self.fmt == "bin":
            t = f"{v:0{bits}b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, bits, 4))
        if self.fmt == "real":
            return f"{struct.unpack('>f', v.to_bytes(4, 'big'))[0]:.6g}"
        return str(v - (1 << bits) if v >> (bits - 1) else v)

    def encode(self, text: str) -> int:
        t = text.strip().lower().replace("_", "").replace(",", ".")
        if self.bit is not None:
            if t not in ("0", "1"):
                raise ValueError("0 ou 1")
            return int(t)
        try:
            if self.fmt == "real":
                return int.from_bytes(struct.pack(">f", float(t)), "big")
            if t.startswith("16#") or t.startswith("0x"):
                v = int(t[3:] if t.startswith("16#") else t[2:], 16)
            elif t.startswith("2#"):
                v = int(t[2:], 2)
            else:
                v = int(t, 16) if self.fmt == "hex" else int(t, 2) if self.fmt == "bin" else int(t)
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
            v = S7200Var(str(t))
            out["valeurs"][str(t)] = v.decode(v.read(plc))
        except ValueError as e:
            out["erreurs"][str(t)] = str(e)
        except ppi.PlcRefused as e:
            out["erreurs"][str(t)] = str(e)
    return out


WRITABLE = ("Q", "M", "V", "SM", "AQ")


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = S7200Var(text)
            if v.area not in WRITABLE:
                raise ValueError("les entrees (I, AI) ne s'ecrivent pas")
            if v.area == "SM" and v.n < 30:
                raise ValueError("SMB0..SMB29 en lecture seule (manuel, annexe D)")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = v.read(plc)
        stopped = v.area == "Q" and not running(plc)
        if stopped:
            # CPU in STOP: the outputs only follow the "outputs in STOP" function (libnodave daveForce200):
            # set the whole byte, back to 0 when the CPU goes to RUN or STOP again
            current = plc.read("Q", v.n, v.size or 1)
            if v.bit is not None:
                b = current[0]
                plc.set_outputs_in_stop("Q", v.n, (b | 1 << v.bit) if value else (b & ~(1 << v.bit) & 0xFF))
            else:
                for k, byte in enumerate(value.to_bytes(v.size, "big")):
                    plc.set_outputs_in_stop("Q", v.n + k, byte)
        elif v.bit is not None:
            plc.write_bit(v.area, v.n, v.bit, bool(value))
        else:
            plc.write(v.area, v.n, value.to_bytes(v.size, "big"))
        back = v.read(plc) if v.area != "AQ" else value          # AQ: write only (manual)
        results.append({"adresse": text, "avant": v.decode(old) if v.area != "AQ" else "?", "relu": v.decode(back),
                        "ok": back == value})
    run = running(plc)
    out = {"resultats": results, "run": run}
    if run and any(S7200Var(str(i.get("adresse", ""))).area == "Q" for i in items):
        out["note"] = ("En RUN le programme recalcule les sorties a chaque cycle. Pour tester une sortie : passer "
                       "l'automate en STOP, puis ecrire Q (test des sorties en STOP, annule au passage en RUN).")
    return out


# ---- program copies: every OB / DB / SDB block as the CPU gives it ----

PROGRAM_FILE = "programme_s7200.bin"
MAGIC = b"S7200BK1"


def pack_blocks(blocks: dict) -> bytes:
    out = MAGIC
    for (kind, n), data in sorted(blocks.items()):
        out += kind.encode().ljust(3, b" ") + struct.pack(">HI", n, len(data)) + data
    return out


def unpack_blocks(blob: bytes) -> dict:
    if not blob.startswith(MAGIC):
        raise ValueError("pas une copie S7-200 de cet outil")
    out, i = {}, len(MAGIC)
    while i < len(blob):
        kind = blob[i:i + 3].decode().strip()
        n, size = struct.unpack_from(">HI", blob, i + 3)
        out[(kind, n)] = blob[i + 9:i + 9 + size]
        i += 9 + size
    return out


def read_program(plc, progress) -> bytes:
    """Every listed block; a block the CPU refuses is noted, not fatal (the others are kept)."""
    blocks, refused, listed = {}, [], []
    for kind in ("OB", "DB", "SDB"):
        progress(f"liste des blocs {kind}", 0, 0)
        try:
            numbers = plc.list_blocks(kind)
        except ppi.PlcRefused as e:
            refused.append(f"liste {kind} : {e}")
            numbers = []
        listed += [f"{kind}{n}" for n in numbers]
        for n in numbers:
            try:
                blocks[(kind, n)] = plc.upload(kind, n, lambda size: progress(f"lecture {kind}{n} : {size} octets", 0, 0))
            except ppi.PlcRefused as e:
                refused.append(f"{kind}{n} : {e}")
    plc.upload_refused = refused
    if refused:
        plc.ppi.write_journal()                 # the refused requests stay readable in journal_ppi.txt
    if not blocks:
        raise _drv().DriverError("Aucun bloc lu. Blocs annonces par l'automate : " + (", ".join(listed) or "aucun")
                                 + (" ; refus : " + " ; ".join(refused) if refused else "")
                                 + ". Programme protege par mot de passe ? (journal des trames : bouton Diagnostic)")
    return pack_blocks(blocks)


def block_text(kind, n, data) -> str:
    try:
        info = ppi.block_info(data)
    except ValueError:
        return f"{kind}{n} ({len(data)} o)"
    names = " ".join(info["noms"])
    return (f"{kind}{n} ({len(data)} o" + (f" : {names}" if names else "") + (", protege" if info["protege"] else "")
            + ")")


def describe(blob: bytes) -> str:
    try:
        blocks = unpack_blocks(blob)
    except ValueError:
        return f"{len(blob)} octets"
    dates = sorted({ppi.block_info(d)["modifie"] for d in blocks.values() if d[:2] == b"\x70\x70"} - {""})
    return ", ".join(block_text(k, n, d) for (k, n), d in sorted(blocks.items())) + (
        f" ; charge par Micro/WIN le {dates[-1]}" if dates else "")


LOAD_ORDER = {"OB": 0, "DB": 1, "SDB": 2}
# SDB2: same size as SDB0, kept by the CPU in its EEPROM; loading it is refused with D207 "block exists in EEPROM"
# (bench 16/11). Micro/WIN's download does not need it: the CPU writes it itself.
NOT_LOADED = {("SDB", 2)}


def load_program(plc, blob, progress, done=None) -> list:
    """Load every block of a copy (CPU in STOP). Returns the names loaded (also appended to done as it goes)."""
    done = [] if done is None else done
    for (kind, n), data in sorted(unpack_blocks(blob).items(), key=lambda kv: (LOAD_ORDER.get(kv[0][0], 9), kv[0][1])):
        if (kind, n) in NOT_LOADED:
            continue
        plc.load_block(data, lambda size: progress(f"chargement {kind}{n} : {size} / {len(data)} octets", size, len(data)))
        done.append(f"{kind}{n}")
    return done


def diff(now: bytes, saved: bytes) -> list:
    a, b = unpack_blocks(now), unpack_blocks(saved)
    out = []
    for key in sorted(set(a) | set(b)):
        name = f"{key[0]}{key[1]}"
        if key not in a:
            out.append(f"{name} absent de l'automate")
        elif key not in b:
            out.append(f"{name} absent de la copie")
        elif a[key] != b[key]:
            first = next((i for i in range(min(len(a[key]), len(b[key]))) if a[key][i] != b[key][i]), min(len(a[key]), len(b[key])))
            out.append(f"{name} modifie (premier octet different : {first})")
    return out


# ---- data copies: V memory ----

def read_all_v(plc, progress=None) -> bytes:
    out = b""
    while len(out) < plc.v_size:
        out += plc.read("V", len(out), min(200, plc.v_size - len(out)))
        if progress:
            progress(f"lecture V : {len(out)} / {plc.v_size} octets", len(out), plc.v_size)
    return out


def data_csv(values, model, stamp) -> str:
    lines = [f"# date;{stamp}", f"# automate;{model}", "zone;adresse;valeur;signe;hex"]
    items = sorted(values.items()) if isinstance(values, dict) else enumerate(values)
    lines += [f"VB;{i};{v};{v - 256 if v > 127 else v};{v:02X}" for i, v in items]
    return "\n".join(lines) + "\n"


def parse_data_csv(text):
    values, model = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# automate;"):
            model = line.split(";", 1)[1]
        if not line or line.startswith("#") or line.lower().startswith("zone;"):
            continue
        parts = line.split(";")
        if parts[0].strip().upper() != "VB":
            continue
        try:
            values[int(parts[1])] = int(parts[2]) & 0xFF
        except (ValueError, IndexError):
            raise _drv().DriverError(f"ligne illisible : {line}") from None
    if not values:
        raise _drv().DriverError("aucun octet VB dans le fichier")
    return values, model


class S7200Ops:
    def __init__(self, session):
        from plc_online.web.tools_web import ProgramCopies
        self.s = session
        self.prog = ProgramCopies(
            session, lambda: self.s.tsx_backups.parent / "s7-200" / "programmes", PROGRAM_FILE, read_program,
            describe, diff, "s7200", lambda data: data.startswith(MAGIC),
            "")
        for name in ("list_backups", "compare", "export_backup", "import_backup"):
            setattr(self, name, getattr(self.prog, name))

    @property
    def programs(self):
        return self.prog.programs

    @property
    def data(self) -> Path:
        return self.s.tsx_backups.parent / "s7-200" / "donnees"

    def save_program(self, note):
        return self.prog.save_program(note, lambda plc: plc.cpu)

    def _copy(self, backup_id):
        path = _drv().backup_path(self.programs, backup_id)
        return self.prog._image(backup_id), tsx_web._meta(path)

    def backup_blocks(self, backup_id):
        blob, meta = self._copy(backup_id)
        rows = [{"bloc": "CPU", "taille": meta.get("cible", ""), "date": "", "somme": ""}]
        for (kind, n), data in sorted(unpack_blocks(blob).items()):
            try:
                info = ppi.block_info(data)
            except ValueError:
                info = {"noms": [], "modifie": "", "protege": False}
            rows.append({"bloc": f"{kind}{n}", "taille": f"{len(data)} octets" + (" : " + " ".join(info["noms"]) if info["noms"] else "")
                         + (" (protege)" if info["protege"] else ""), "date": info["modifie"], "somme": ""})
        return {"id": backup_id, "blocs": rows}

    def restore_plan(self, backup_id, with_db=False):
        blob, meta = self._copy(backup_id)
        blocks = unpack_blocks(blob)

        def run(plc):
            target = meta.get("cible") or ""
            return {"charger": [block_text(k, n, d) for (k, n), d in sorted(blocks.items()) if (k, n) not in NOT_LOADED],
                    "en_trop": [],
                    "db_ignorees": False, "run": running(plc), "cible": target, "automate": plc.nom, "vide": False,
                    "autre_automate": bool(target) and target.replace(" ", "") != plc.cpu.replace(" ", ""),
                    "avert": list(filter(None, [
                        "SDB2 n'est pas recharge : la CPU le garde elle-meme en EEPROM." if ("SDB", 2) in blocks else "",
                        "DB1 remet les valeurs initiales de la memoire V (bloc de donnees de Micro/WIN) : faire "
                        "d'abord une copie des donnees (onglet Donnees) si des reglages ont change."
                        if ("DB", 1) in blocks else ""]))}
        return self.s._call(run)

    def restore(self, backup_id, with_db=False, delete_extra=False):
        blob, _ = self._copy(backup_id)

        def work(plc, progress):
            out = {"charges": [], "supprimes": [], "erreur": "", "differences": [], "etait_en_run": running(plc)}
            before = read_program(plc, progress)
            out["sauvegarde_avant"] = self.prog._save(before, plc.nom, "avant restauration", plc.cpu)["id"]
            if out["etait_en_run"]:
                progress("passage en STOP", 0, 0)
                plc.stop()
                time.sleep(0.3)
                if running(plc):
                    out["erreur"] = "l'automate reste en RUN (selecteur sur RUN ? le mettre sur STOP ou TERM)"
                    return out
            try:
                load_program(plc, blob, progress, out["charges"])
            except ppi.PpiError as e:
                out["erreur"] = str(e)
                return out
            back = read_program(plc, progress)
            out["differences"] = [d for d in diff(back, blob) if "absent de la copie" not in d and not d.startswith("SDB2")]
            return out
        return self.s.start_job("restauration du programme", work)

    def status(self, plc):
        return s7200_status(plc)

    def diagnostic(self):
        return self.s._call(s7200_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def _mode(self, run):
        def go(plc):
            plc.start() if run else plc.stop()
            time.sleep(0.3)
            state = running(plc)
            if state != run:
                raise _drv().DriverError(f"L'automate est reste en {'RUN' if state else 'STOP'} : le selecteur doit "
                                         "etre sur TERM (ou RUN) pour piloter a distance ; STOP l'emporte.")
            return {"ok": True}
        return self.s._call(go)

    def start_cpu(self):
        return self._mode(True)

    def stop_cpu(self):
        return self._mode(False)

    def data_list(self):
        return {"dossier": str(self.data), "sauvegardes": tsx_web.list_copies(self.data, tsx_web.DATA_FILE)}

    def data_save(self, note):
        def work(plc, progress):
            values = read_all_v(plc, progress)
            text = data_csv(list(values), plc.nom, time.strftime("%Y-%m-%d %H:%M:%S"))
            return tsx_web.save_data(self.data, text, plc.nom, note, f"{len(values)} VB")
        return self.s.start_job("sauvegarde des donnees", work)

    def _saved(self, backup_id):
        path = _drv().backup_path(self.data, backup_id)
        try:
            return parse_data_csv((path / tsx_web.DATA_FILE).read_text(encoding="utf-8"))
        except OSError as e:
            raise _drv().DriverError(f"copie illisible : {e}") from None

    def data_plan(self, backup_id):
        saved, model = self._saved(backup_id)

        def run(plc):
            current = read_all_v(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            return {"zone": "VB", "mots": len(saved), "differents": len(diffs), "zone_automate": len(current),
                    "hors_zone": sum(1 for a in saved if a >= len(current)), "kw_ignores": 0,
                    "automate_fichier": model, "automate": plc.nom, "run": running(plc),
                    "exemples": [{"adresse": f"VB{a}", "automate": current[a], "fichier": diffs[a]} for a in sorted(diffs)[:12]]}
        return self.s._call(run)

    def data_restore(self, backup_id):
        saved, _ = self._saved(backup_id)

        def work(plc, progress):
            from plc_online.web.tools_web import ranges_text  # noqa: F401
            current = read_all_v(plc)
            diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
            runs = []
            for a in sorted(diffs):
                if runs and runs[-1][0] + runs[-1][1] == a and runs[-1][1] < 150:
                    runs[-1][1] += 1
                else:
                    runs.append([a, 1])
            for i, (start, length) in enumerate(runs, 1):
                progress(f"ecriture VB{start} ({length} octet(s))", i, len(runs))
                plc.write("V", start, bytes(saved[a] for a in range(start, start + length)))
            after = read_all_v(plc)
            wrong = [f"VB{a}" for a in sorted(saved) if a < len(after) and after[a] != saved[a]]
            return {"ecrits": len(diffs), "differents_apres": wrong[:20], "nb_differents_apres": len(wrong)}
        return self.s.start_job("restauration des donnees", work)

    def data_export(self, backup_id):
        return _drv().export_zip(self.data, backup_id, "donnees_s7200")

    def data_import(self, files, note):
        self.data.mkdir(parents=True, exist_ok=True)
        made, ignored = [], []
        for name, data in files:
            base = Path(name.replace("\\", "/")).name
            if not base.lower().endswith(".csv"):
                ignored.append(base)
                continue
            values, model = parse_data_csv(data.decode("utf-8", "replace"))
            text = data_csv(values, model, time.strftime("%Y-%m-%d %H:%M:%S"))
            made.append(tsx_web.save_data(self.data, text, model, note or f"importe de {base}", f"{len(values)} VB"))
        if not made:
            raise _drv().DriverError("Aucun fichier .csv de donnees S7-200 dans les fichiers choisis")
        return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction pas encore disponible pour le S7-200")
