"""Omron CJ1M driver: Host Link (C-mode) over the peripheral port, plus FINS inside Host Link.

PLC settings (see docs/rapport-technique-2026-10-23.md §4.2): peripheral port in
Host Link, 9600 baud, 7 data bits, even parity, 2 stop bits, unit 0, DIP 4 ON.

Frame:  @ + unit (2 digits) + header + text + FCS + '*' + CR
Every reply is checked before its data is used: terminator, FCS, header,
end code and length. A corrupted reply is retried; a PLC end code is not.

Words are hex, most significant digit first: "04D2" -> 1234 (no byte swap,
unlike Panasonic). 32-bit values are two words, low word first.

Two command sets share the same frame (notes: docs/omron.md):
- C-mode commands (RD, RR, RH, RJ, WD, SC, MS, MM, TS): confirmed on the bench for TS and RD.
- FINS commands wrapped in a Host Link 'FA' frame: needed for W, T, C, D10000+,
  model, status, clock, error log and program read. To verify on the bench.
"""
from ..checksum import xor_checksum

# A single-frame RD reply holds up to 30 words; longer reads are split.
MAX_WORDS_PER_FRAME = 30
# FINS reply in one frame: 27 characters of overhead + 4 per word <= 131.
MAX_FINS_WORDS = 26
# C-mode write command in one frame: 13 characters of overhead + 4 per word <= 131.
MAX_WRITE_WORDS = 29

END_CODES = {
    "01": "impossible en mode RUN (passer en MONITOR)",
    "02": "impossible en mode MONITOR",
    "04": "dépassement d'adresse",
    "0B": "impossible en mode PROGRAM",
    "13": "erreur FCS (trame envoyée mal reçue)",
    "14": "erreur de format",
    "15": "erreur de donnée (adresse ou nombre invalide)",
    "16": "commande non supportée",
    "18": "trame trop longue",
    "19": "non exécutable",
    "23": "mémoire utilisateur protégée",
    "A3": "abandon : erreur FCS en transmission",
    "IC": "commande inconnue de l'automate",
}

FINS_CODES = {
    0x0401: "commande FINS non supportée",
    0x1001: "commande trop longue",
    0x1002: "commande trop courte",
    0x1003: "nombre d'éléments et données ne correspondent pas",
    0x1101: "zone mémoire inexistante",
    0x1103: "adresse hors zone",
    0x1104: "dépassement de la fin de zone",
    0x2101: "zone en lecture seule",
    0x2102: "zone protégée",
    0x2201: "impossible dans le mode actuel",
    0x2202: "impossible : programme en cours d'exécution",
    0x2203: "impossible en mode PROGRAM",
    0x2502: "erreur mémoire",
    0x3001: "pas de droit d'accès (autre logiciel connecté ?)",
}

# Memory areas. Address limits are those of a CJ1M (CPU11/12/13).
#   cmode: C-mode read/write headers (4-digit address field, so up to 9999)
#   fins:  FINS memory area code for word access, and address offset
#   size:  number of words
#   bits:  bit area, shown and watched bit by bit
AREAS = {
    "CIO": {"cmode": ("RR", "WR"), "fins": (0xB0, 0), "size": 6144, "bits": True,
            "label": "entrées / sorties / relais internes (CIO)"},
    "W": {"cmode": None, "fins": (0xB1, 0), "size": 512, "bits": True, "label": "relais de travail"},
    "H": {"cmode": ("RH", "WH"), "fins": (0xB2, 0), "size": 512, "bits": True, "label": "relais maintenus"},
    "A": {"cmode": ("RJ", "WJ"), "fins": (0xB3, 0), "size": 960, "bits": True, "label": "relais auxiliaires (système)"},
    "D": {"cmode": ("RD", "WD"), "fins": (0x82, 0), "size": 32768, "bits": False, "label": "mémoire de données DM"},
    "T": {"cmode": None, "fins": (0x89, 0), "size": 4096, "bits": False, "label": "valeur courante des tempos"},
    "C": {"cmode": None, "fins": (0x89, 0x8000), "size": 4096, "bits": False, "label": "valeur courante des compteurs"},
}

MODES = {"programme": "00", "monitor": "02", "run": "03"}   # SC (status change)
FINS_MODES = {0x00: "PROGRAM", 0x02: "MONITOR", 0x04: "RUN"}
FINS_STATUS = {0x00: "programme arrêté", 0x01: "programme en cours", 0x80: "CPU en attente (standby)"}

# Fatal (A401) and non-fatal (A402) error flags, as returned by FINS 0601.
FATAL_BITS = {
    15: "erreur mémoire",
    14: "erreur bus E/S (capot CJ1W-TER01 ?)",
    13: "numéro d'unité en double",
    11: "trop de points E/S",
    10: "erreur de configuration E/S (table E/S)",
    9: "erreur programme",
    8: "temps de cycle trop long",
    6: "FALS (erreur fatale du programme)",
}
NON_FATAL_BITS = {
    15: "FAL (erreur non fatale du programme)",
    4: "pile (batterie) faible ou absente",
}


def error_log_text(code: int) -> str:
    """Usual meaning of an error log code (CS/CJ series)."""
    if 0x4101 <= code <= 0x42FF:
        return f"FAL {code - 0x4100}"
    if 0xC101 <= code <= 0xC2FF:
        return f"FALS {code - 0xC100}"
    if 0x80C0 <= code <= 0x80C7:
        return "erreur bus E/S"
    return {
        0x80F1: "erreur mémoire",
        0x80E0: "erreur de configuration E/S",
        0x80E1: "trop de points E/S",
        0x80E9: "numéro d'unité en double",
        0x80F0: "erreur programme",
        0x809F: "temps de cycle trop long",
        0x00F7: "erreur pile",
        0x009B: "erreur de paramètres automate (PLC Setup)",
    }.get(code, "voir le manuel (journal d'erreurs) ou CX-Programmer")


class HostLinkError(Exception):
    """Missing, corrupted or unexpected reply."""


class PlcRefused(HostLinkError):
    """The PLC answered with a non-zero C-mode end code."""

    def __init__(self, code: str, reply: str):
        meaning = END_CODES.get(code, "code inconnu")
        super().__init__(f"l'automate a refusé la commande (code {code} : {meaning}) : {reply!r}")
        self.code = code


class FinsRefused(HostLinkError):
    """The PLC answered a FINS command with a non-zero response code."""

    def __init__(self, command: str, code: int):
        meaning = FINS_CODES.get(code, "code inconnu")
        super().__init__(f"l'automate a refusé la commande FINS {command} (code {code:04X} : {meaning})")
        self.code = code


def open_port(port: str, baud: int = 9600, timeout: float = 2.0):
    """Open the serial port with the Host Link 'Standard' settings (7, E, 2)."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, bytesize=7, parity="E", stopbits=2, timeout=timeout)


def decode_words(hexdata: str) -> list:
    """'04D20000' -> [1234, 0]."""
    if len(hexdata) % 4:
        raise HostLinkError(f"données de longueur impaire : {hexdata!r}")
    try:
        return [int(hexdata[i:i + 4], 16) for i in range(0, len(hexdata), 4)]
    except ValueError:
        raise HostLinkError(f"données non hexadécimales : {hexdata!r}") from None


def signed16(w: int) -> int:
    return w - 0x10000 if w & 0x8000 else w


def bcd(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


def parse_address(text: str):
    """Address as typed in CX-Programmer -> (area, word, bit or None).

    'D100' -> ('D', 100, None)    '0.03' or 'CIO 0.03' -> ('CIO', 0, 3)
    'W10.05' -> ('W', 10, 5)      'T5' -> ('T', 5, None)   'A402.04' -> ('A', 402, 4)
    """
    t = text.strip().upper().replace(" ", "")
    area = "CIO"
    for name in sorted(AREAS, key=len, reverse=True):
        if t.startswith(name):
            area, t = name, t[len(name):]
            break
    word, _, bit = t.partition(".")
    if not word.isdigit() or (bit and not bit.isdigit()):
        raise ValueError(f"adresse non reconnue : {text!r}")
    word, bit = int(word), (int(bit) if bit else None)
    if bit is not None and (not AREAS[area]["bits"] or bit > 15):
        raise ValueError(f"bit non valide : {text!r}")
    if word >= AREAS[area]["size"]:
        raise ValueError(f"{area}{word} : au-delà de la zone ({AREAS[area]['size']} mots)")
    return area, word, bit


class Cj1m:
    """One Omron PLC on an open serial port."""

    brand = "omron"

    def __init__(self, ser, unit: int = 0, retries: int = 2):
        self.ser = ser
        self.unit = unit
        self.retries = retries

    # ---- framing ----

    def _frame(self, header: str, text: str) -> str:
        body = f"@{self.unit:02d}{header}{text}"
        return body + xor_checksum(body) + "*\r"

    def _exchange(self, frame: str) -> str:
        """Send one frame, return the reply as one frame (multi-frame replies are joined)."""
        self.ser.reset_input_buffer()
        self.ser.write(frame.encode("ascii"))
        reply = self.ser.read_until(b"\r").decode("ascii", "replace")
        if not reply.endswith("\r") or reply.endswith("*\r") or len(reply) < 4:
            return reply
        # Long reply: each intermediate frame ends with FCS + CR (no '*'); a CR asks for the next.
        parts = []
        while True:
            text, fcs = reply[:-3], reply[-3:-1]
            if xor_checksum(text) != fcs.upper():
                raise HostLinkError(f"FCS faux dans une trame intermédiaire : {reply!r}")
            parts.append(text)
            self.ser.write(b"\r")
            reply = self.ser.read_until(b"\r").decode("ascii", "replace")
            if not reply:
                raise HostLinkError("réponse en plusieurs trames : trame suivante manquante")
            if reply.endswith("*\r"):
                text, fcs = reply[:-4], reply[-4:-2]
                if xor_checksum(text) != fcs.upper():
                    raise HostLinkError(f"FCS faux dans la dernière trame : {reply!r}")
                body = "".join(parts) + text
                return body + xor_checksum(body) + "*\r"

    def _check_reply(self, reply: str, header: str) -> str:
        """Return the text after the end code, or raise."""
        if not reply:
            raise HostLinkError("pas de réponse (timeout, mauvais mode ou mauvais câble)")
        if not reply.endswith("*\r"):
            raise HostLinkError(f"réponse incomplète : {reply!r}")
        body, fcs = reply[:-4], reply[-4:-2]
        if xor_checksum(body) != fcs.upper():
            raise HostLinkError(f"FCS faux (reçu {fcs}, calculé {xor_checksum(body)}) : {reply!r}")
        expected = f"@{self.unit:02d}{header}"
        if not body.startswith(expected):
            raise HostLinkError(f"en-tête inattendu (attendu {expected!r}) : {reply!r}")
        end_code = body[len(expected):len(expected) + 2]
        if end_code != "00":
            raise PlcRefused(end_code, reply)
        return body[len(expected) + 2:]

    def _transact(self, header: str, text: str, data_len=None) -> str:
        last = None
        for _ in range(1 + self.retries):
            try:
                reply = self._exchange(self._frame(header, text))
                data = self._check_reply(reply, header)
            except PlcRefused:
                raise
            except HostLinkError as e:
                last = e
                continue
            if data_len is not None and len(data) != data_len:
                last = HostLinkError(
                    f"réponse coupée : {len(data)} caractères reçus au lieu de {data_len} : {reply!r}")
                continue
            return data
        raise last

    def fins(self, command: str, params: str = "", data_len=None) -> str:
        """FINS command inside a Host Link 'FA' frame. Returns the response data (hex).

        Frame text: response wait (1 digit) + ICF DA2 SA2 SID (4 bytes) + command + parameters.
        Reply text: ICF DA2 SA2 SID (4 bytes) + command echo + response code + data.
        """
        full = None if data_len is None else 16 + data_len
        data = self._transact("FA", "0" + "00000000" + command + params, full)
        if len(data) < 16 or data[8:12] != command:
            raise HostLinkError(f"réponse FINS inattendue à {command} : {data!r}")
        code = int(data[12:16], 16)
        # Bit 7 of each byte flags a relay or CPU error, not a failure of the command.
        code &= 0x7F3F
        if code:
            raise FinsRefused(command, code)
        return data[16:]

    # ---- C-mode commands ----

    def test(self, text: str = "HELLO CJ1M") -> bool:
        """TS echo test: True if the PLC sends back exactly the frame sent.

        Proves cable, port settings and FCS. Raises if there is no answer.
        """
        frame = self._frame("TS", text)
        reply = self._exchange(frame)
        if not reply:
            raise HostLinkError("pas de réponse (timeout, mauvais mode ou mauvais câble)")
        return reply == frame

    def read_dm(self, start: int, count: int) -> list:
        """Read D[start] .. D[start + count - 1]."""
        return self.read("D", start, count)

    def model_code(self) -> str:
        """MM: 2-digit model code (C-mode)."""
        return self._transact("MM", "")[:2]

    def status_cmode(self) -> str:
        """MS: raw status data (C-mode), for when FINS is not available."""
        return self._transact("MS", "")

    def set_mode(self, mode: str) -> None:
        """SC: 'programme', 'monitor' or 'run'."""
        self._transact("SC", MODES[mode], data_len=0)

    # ---- memory areas ----

    def _check_range(self, area: str, start: int, count: int):
        if area not in AREAS:
            raise ValueError(f"zone inconnue : {area} (zones : {' '.join(AREAS)})")
        if start < 0 or count < 1 or start + count > AREAS[area]["size"]:
            raise ValueError(f"{area}{start}..{area}{start + count - 1} : hors zone "
                             f"({area}0..{area}{AREAS[area]['size'] - 1})")

    def _use_cmode(self, area: str, end: int) -> bool:
        return AREAS[area]["cmode"] is not None and end <= 9999

    def max_block(self, area: str) -> int:
        return MAX_WORDS_PER_FRAME if AREAS[area]["cmode"] else MAX_FINS_WORDS

    def read(self, area: str, start: int, count: int) -> list:
        """Read count words of an area, in blocks the PLC accepts in one frame."""
        self._check_range(area, start, count)
        words = []
        addr, end = start, start + count
        while addr < end:
            if self._use_cmode(area, end - 1):
                n = min(MAX_WORDS_PER_FRAME, end - addr)
                header = AREAS[area]["cmode"][0]
                words += decode_words(self._transact(header, f"{addr:04d}{n:04d}", data_len=4 * n))
            else:
                n = min(MAX_FINS_WORDS, end - addr)
                code, offset = AREAS[area]["fins"]
                words += decode_words(self.fins("0101", f"{code:02X}{addr + offset:04X}00{n:04X}", 4 * n))
            addr += n
        return words

    read_block = read

    def write(self, area: str, start: int, values: list) -> None:
        """Write words. C-mode writes are refused in RUN mode (end code 01): use MONITOR."""
        self._check_range(area, start, len(values))
        values = [v & 0xFFFF for v in values]
        end = start + len(values)
        i = 0
        while i < len(values):
            addr = start + i
            if self._use_cmode(area, end - 1):
                chunk = values[i:i + MAX_WRITE_WORDS]
                header = AREAS[area]["cmode"][1]
                self._transact(header, f"{addr:04d}" + "".join(f"{v:04X}" for v in chunk), data_len=0)
            else:
                chunk = values[i:i + MAX_FINS_WORDS]
                code, offset = AREAS[area]["fins"]
                self.fins("0102", f"{code:02X}{addr + offset:04X}00{len(chunk):04X}"
                          + "".join(f"{v:04X}" for v in chunk), 0)
            i += len(chunk)

    # ---- FINS: identification, status, clock, error log ----

    def controller_data(self) -> dict:
        """FINS 0501: CPU model and version (reply in several frames)."""
        raw = bytes.fromhex(self.fins("0501", "00"))
        text = lambda b: b.split(b"\x00")[0].decode("ascii", "replace").strip()
        return {"modele": text(raw[0:20]), "version": text(raw[20:40])}

    def controller_status(self) -> dict:
        """FINS 0601: run status, mode, fatal and non-fatal error flags, FAL/FALS number, message."""
        raw = bytes.fromhex(self.fins("0601", ""))
        if len(raw) < 10:
            raise HostLinkError(f"état automate trop court : {raw.hex()}")
        return {
            "etat": raw[0],
            "mode": raw[1],
            "fatal": int.from_bytes(raw[2:4], "big"),
            "non_fatal": int.from_bytes(raw[4:6], "big"),
            "messages": int.from_bytes(raw[6:8], "big"),
            "fal": int.from_bytes(raw[8:10], "big"),
            "message": raw[10:26].split(b"\x00")[0].decode("ascii", "replace").strip(),
        }

    def status(self) -> dict:
        s = self.controller_status()
        return {**s, "code_mode": s["mode"], "mode": FINS_MODES.get(s["mode"], f"{s['mode']:02X}")}

    def clock(self) -> dict:
        """FINS 0701: PLC clock (BCD)."""
        raw = bytes.fromhex(self.fins("0701", ""))
        if len(raw) < 6:
            raise HostLinkError(f"horloge trop courte : {raw.hex()}")
        y, mo, d, h, mi, s = (bcd(b) for b in raw[:6])
        return {"date": f"{d:02d}/{mo:02d}/{2000 + y}", "heure": f"{h:02d}:{mi:02d}:{s:02d}",
                "jour": raw[6] if len(raw) > 6 else None}

    def error_log(self, chunk: int = 4) -> list:
        """FINS 2102: every stored error log record, as dicts."""
        records, first, stored = [], 0, None
        while stored is None or first < stored:
            raw = bytes.fromhex(self.fins("2102", f"{first:04X}{chunk:04X}"))
            if len(raw) < 6:
                raise HostLinkError(f"journal d'erreurs trop court : {raw.hex()}")
            stored = int.from_bytes(raw[2:4], "big")
            got = int.from_bytes(raw[4:6], "big")
            for i in range(got):
                r = raw[6 + 10 * i:16 + 10 * i]
                if len(r) < 10:
                    break
                mi, s, d, h, y, mo = (bcd(b) for b in r[4:10])
                records.append({"code": int.from_bytes(r[0:2], "big"), "code2": int.from_bytes(r[2:4], "big"),
                                "date": f"{d:02d}/{mo:02d}/{2000 + y} {h:02d}:{mi:02d}:{s:02d}"})
            if got == 0:
                break
            first += got
        return records

    # ---- FINS: program read (experimental) ----

    def _program_read(self, addr: int, count: int):
        """FINS 0306 on the user program (number FFFF). Returns (bytes, last block flag)."""
        data = self.fins("0306", f"FFFF{addr:08X}{count:04X}")
        if len(data) < 16 or int(data[4:12], 16) != addr:
            raise HostLinkError(f"réponse de lecture programme inattendue : {data[:40]!r}")
        n = int(data[12:16], 16)
        blob = bytes.fromhex(data[16:])
        if len(blob) != n & 0x7FFF:
            raise HostLinkError(f"lecture programme : {len(blob)} octets reçus au lieu de {n & 0x7FFF}")
        return blob, bool(n & 0x8000)

    def _program_address_unit(self) -> int:
        """Bytes per program address: read 64 bytes at 0, then 16 at address 8, and compare."""
        first, _ = self._program_read(0, 64)
        second, _ = self._program_read(8, 16)
        matches = [u for u in (1, 2, 4) if first[8 * u:8 * u + 16] == second]
        if len(matches) != 1:
            raise HostLinkError("unité d'adresse du programme non reconnue "
                                f"(début {first.hex()}, adresse 8 : {second.hex()})")
        return matches[0]

    def read_program(self, block: int = 200, progress=None, limit: int = 4 << 20) -> bytes:
        """Read the user program area from the start until the PLC flags the last block."""
        unit = self._program_address_unit()
        blob, n = b"", block
        while len(blob) < limit:
            try:
                data, last = self._program_read(len(blob) // unit, n)
            except FinsRefused as e:
                if e.code in (0x1103, 0x1104) and n > 2:   # past the end: smaller blocks
                    n //= 2
                    continue
                if e.code in (0x1103, 0x1104) and blob:
                    break
                raise
            blob += data
            if progress:
                progress(len(blob))
            if last or not data:
                break
        return blob
