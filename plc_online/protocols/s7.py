"""Siemens S7-300 driver: libnodave through STEP 7's S7ONLINE driver.

Chain:  Python -> libnodave.dll -> S7onlinx.dll (STEP 7) -> PC Adapter USB -> S7-300

Requirements (see README, "Siemens"):
  - Windows with STEP 7 installed (it provides S7onlinx.dll and the S7ONLINE driver),
  - "Set PG/PC Interface" set to USB.S7USB.1,
  - 32-bit Python: both DLLs are 32-bit,
  - SIMATIC Manager closed: only one program can own the interface.

Data is big-endian and byte-addressed: MW0 = bytes 0-1, high byte first.

CPU information comes from the system status lists (SSL / SZL). Formats from Siemens
"System Software for S7-300/400, System and Standard Functions", vol. 2, chapter 34
(A5E02790053-01, 05/2010).
"""
import ctypes
from pathlib import Path

DEFAULT_DLL = Path(__file__).resolve().parents[2] / "vendor" / "libnodave" / "libnodave.dll"

# libnodave constants
PROTO_S7ONLINE = 50
SPEED_187K = 2
TIMEOUT_US = 5_000_000

AREAS = {
    "I": 0x81,   # inputs (E)
    "Q": 0x82,   # outputs (A)
    "M": 0x83,   # flags / merkers
    "DB": 0x84,  # data blocks
}


class S7Error(Exception):
    """A libnodave call failed."""


# ---- system status lists (SZL), manual chapter 34 ----

def parse_szl(raw: bytes, szl_id: int):
    """SZL answer -> (index, list of data records).

    Layout: SZL-ID, INDEX, LENTHDR (bytes per record), N_DR, then the records (big-endian).
    """
    if len(raw) < 8:
        raise S7Error(f"réponse SZL {szl_id:04X} trop courte : {raw.hex()}")
    rid, index, size, count = (word_be(raw, i) for i in (0, 2, 4, 6))
    if rid != szl_id or size == 0:
        raise S7Error(f"réponse SZL inattendue (demandé {szl_id:04X}) : {raw[:16].hex()}")
    body = raw[8:]
    count = min(count, len(body) // size)
    return index, [body[i * size:(i + 1) * size] for i in range(count)]


def _text(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("latin-1").strip()


def parse_identity(records: list, requested: int = 0) -> dict:
    """SZL 0x0111 records (§34.5): index, order number (20 bytes), BGTyp, Ausbg1, Ausbg2.

    Bench, CPU 315-2AF03: the record index is 0000 for index 1, and index 6 returns the
    same record as index 1 (no "V"). So the requested index is used when the record has none,
    and a version is only shown when it starts with "V".
    """
    out = {}
    for r in records:
        if len(r) < 28:
            continue
        index, a1, a2 = word_be(r, 0) or requested, word_be(r, 24), word_be(r, 26)
        order = _text(r[2:22])
        if order:
            out.setdefault("reference", order)
        if a1 >> 8 == ord("V") and index in (6, 7):
            out["materiel" if index == 6 else "firmware"] = f"V{a1 & 0xFF}.{a2 >> 8}.{a2 & 0xFF}"
        elif index == 1:
            out["version_module"] = a1
    return out


COMPONENTS = {1: "nom_automate", 2: "nom_module", 3: "repere_installation", 5: "numero_serie"}


def parse_component(records: list) -> dict:
    """SZL 0x011C records (§34.10): index word, then up to 32 bytes of text."""
    out = {}
    for r in records:
        if len(r) >= 4 and word_be(r, 0) & 0xFF in COMPONENTS:
            out[COMPONENTS[word_be(r, 0) & 0xFF]] = _text(r[2:34])
    return out


LEDS = {1: "SF", 4: "RUN", 5: "STOP", 6: "FRCE", 8: "BAF", 11: "BUS1F", 12: "BUS2F"}


def parse_led(records: list) -> tuple:
    """SZL 0x0174 record (§34.20): LED id word, on (0/1), blink (0, 1 = 2 Hz, 2 = 0.5 Hz)."""
    if not records or len(records[0]) < 4:
        raise S7Error("état de voyant vide")
    return records[0][2], records[0][3]


def _bcd(b: int) -> int:
    return (b >> 4) * 10 + (b & 0x0F)


def parse_date_and_time(b: bytes) -> str:
    """S7 DATE_AND_TIME, 8 bytes BCD: year, month, day, hour, minute, second, ms (3 digits), weekday."""
    y = _bcd(b[0])
    year = 1900 + y if y >= 90 else 2000 + y
    ms = _bcd(b[6]) * 10 + (b[7] >> 4)
    return f"{_bcd(b[2]):02d}/{_bcd(b[1]):02d}/{year} {_bcd(b[3]):02d}:{_bcd(b[4]):02d}:{_bcd(b[5]):02d}.{ms:03d}"


def parse_diag_entry(r: bytes) -> dict:
    """Diagnostic buffer entry, SZL 0x00A0 (§34.29): event ID, 5 words of info, DATE_AND_TIME."""
    from .s7_events import EVENT_CLASSES, event_text
    event = word_be(r, 0)
    return {
        "id": event,
        "classe": EVENT_CLASSES.get(event >> 12, f"classe {event >> 12:X}"),
        "texte": event_text(event),
        "entrant": bool(event >> 8 & 1),      # bit 8: event entering (1) / leaving (0) state
        "info": r[2:12].hex(" ").upper(),
        "date": parse_date_and_time(r[12:20]),
    }


IO_KIND = {0xB4: "entrees", 0xB5: "sorties", 0xFF: "interface DP externe"}


def parse_module(r: bytes) -> dict:
    """Module status record, SZL 0x0D91 (§34.23): adr1, adr2, logadr, solltyp, isttyp,
    reserved, eastat, ber_bgbr (8 words).

    The manual says solltyp / isttyp are for PROFINET only, but the bench CPU 315-2AF03 fills
    them for its central rack: configured type vs type of the module actually plugged in.
    """
    adr1, adr2, logadr, eastat, ber = word_be(r, 0), word_be(r, 2), word_be(r, 4), word_be(r, 12), word_be(r, 14)
    expected, actual = word_be(r, 6), word_be(r, 8)
    mismatch = logadr != 0x7FFF and expected and actual and expected != actual
    status = []
    if mismatch:
        status.append("TYPE DIFFERENT DE LA CONFIG")
    if eastat & 0x01:
        status.append("ERREUR (alarme de diagnostic)")
    if eastat & 0x04:
        status.append("non disponible" if eastat & 0x02 else "ABSENT")
    if eastat & 0x08:
        status.append("desactive")
    if eastat & 0x10:
        status.append("ERREUR STATION")
    if not status:
        status.append("ok" if eastat & 0x02 else "?")
    return {
        "rack": adr1, "emplacement": adr2 >> 8, "adresse": logadr,
        "type": "CPU" if logadr == 0x7FFF else IO_KIND.get(eastat >> 8, f"code {eastat >> 8:02X}"),
        "etat": ", ".join(status), "present": bool(eastat & 0x02), "brut": r.hex(" ").upper(),
        "type_config": expected, "type_reel": actual, "different": bool(mismatch),
        "largeur": ber & 0x07,
    }


KEY_SWITCH = {1: "RUN", 2: "RUN-P", 3: "STOP", 4: "MRES", 0: "inconnue"}


def parse_protection(records: list) -> dict:
    """SZL 0x0232 index 4 record (§34.18): protection levels, key switch, checksums."""
    if not records or len(records[0]) < 22:
        raise S7Error("protection : enregistrement trop court")
    w = [word_be(records[0], i) for i in range(0, 22, 2)]   # index + 10 words used
    return {
        "protection_cle": w[1], "protection_parametree": w[2], "protection_effective": w[3],
        "cle": KEY_SWITCH.get(w[4], f"code {w[4]}"),
        "sommes_valides": bool(w[6]),
        # checksums are in Intel format (low byte first): kept as read, only compared
        "somme_config_materielle": (w[7], w[8]),
        "somme_programme": (w[9], w[10]),
    }


# libnodave block types (nodave.h)
BLOCK_TYPES = {"OB": 0x38, "DB": 0x41, "SDB": 0x42, "FC": 0x43, "SFC": 0x44, "FB": 0x45, "SFB": 0x46}
UPLOAD_BUFFER = 256 * 1024    # daveGetProgramBlock does not check the buffer size


class _BlockEntry(ctypes.Structure):
    """daveBlockEntry (nodave.h): number already swapped to PC byte order by libnodave."""
    _fields_ = [("number", ctypes.c_ushort), ("type", ctypes.c_ubyte * 2)]


class _OSserial(ctypes.Structure):
    _fields_ = [("rfd", ctypes.c_int), ("wfd", ctypes.c_int)]


def load_dll(path=DEFAULT_DLL):
    if ctypes.sizeof(ctypes.c_void_p) != 4:
        raise S7Error("Python 64 bits détecté : libnodave.dll et S7onlinx.dll sont 32 bits. "
                      "Lancer avec un Python 32 bits (ex. py -3.14-32).")
    if not hasattr(ctypes, "WinDLL"):
        raise S7Error("Le pilote S7ONLINE n'existe que sous Windows.")
    nd = ctypes.WinDLL(str(path))
    nd.openS7online.restype = ctypes.c_int
    nd.daveNewInterface.restype = ctypes.c_void_p
    nd.daveNewConnection.restype = ctypes.c_void_p
    nd.daveStrerror.restype = ctypes.c_char_p
    nd.daveGetAnswLen.restype = ctypes.c_int
    return nd


def word_be(data: bytes, offset: int) -> int:
    """Unsigned 16-bit value, high byte first: b'\\x04\\xd2' -> 1234."""
    return (data[offset] << 8) | data[offset + 1]


def _quiet(msg):
    pass


class S7300:
    """Connection to one S7-300 CPU. Use as a context manager."""

    def __init__(self, mpi: int = 2, rack: int = 0, slot: int = 2, dll=DEFAULT_DLL, log=None):
        self.mpi, self.rack, self.slot = mpi, rack, slot
        self.dll_path = dll
        self.log = log or _quiet
        self.nd = None
        self.handle = -1
        self.dc = None

    def _err(self, code: int) -> str:
        return self.nd.daveStrerror(code).decode(errors="replace")

    def open(self):
        self.nd = nd = load_dll(self.dll_path)
        if self.log is _quiet:
            nd.daveSetDebug(0)          # libnodave prints its own debug lines otherwise

        h = nd.openS7online(b"S7ONLINE", None)
        self.log(f"1. openS7online  -> {h}")
        if h < 0:
            raise S7Error("pilote S7ONLINE non ouvert : STEP 7 est-il encore ouvert ? "
                          "S7onlinx.dll est-il installé ?")
        self.handle = h

        di = ctypes.c_void_p(nd.daveNewInterface(_OSserial(h, h), b"IF1", 0,
                                                 PROTO_S7ONLINE, SPEED_187K))
        nd.daveSetTimeout(di, TIMEOUT_US)
        r = nd.daveInitAdapter(di)
        self.log(f"2. initAdapter   -> {r} {self._err(r)}")
        if r != 0:
            self.close()
            raise S7Error(f"échec initialisation adaptateur : {self._err(r)}")

        dc = ctypes.c_void_p(nd.daveNewConnection(di, self.mpi, self.rack, self.slot))
        r = nd.daveConnectPLC(dc)
        self.log(f"3. connectPLC    -> {r} {self._err(r)}")
        if r != 0:
            self.close()
            raise S7Error(f"pas de CPU à l'adresse MPI {self.mpi} : {self._err(r)}")
        self.dc = dc
        return self

    def read_bytes(self, area: str, start: int, count: int, db: int = 0) -> bytes:
        """Read `count` bytes from an area ("I", "Q", "M" or "DB" with db=number)."""
        buf = ctypes.create_string_buffer(count)
        r = self.nd.daveReadBytes(self.dc, AREAS[area], db, start, count, buf)
        if r != 0:
            raise S7Error(f"lecture {area}{db or ''} octets {start}..{start + count - 1} : {self._err(r)}")
        return buf.raw

    def read_szl(self, szl_id: int, index: int = 0):
        """Read one system status list. Returns (index, list of data records)."""
        buf = ctypes.create_string_buffer(4096)
        r = self.nd.daveReadSZL(self.dc, szl_id, index, buf, len(buf))
        if r != 0:
            raise S7Error(f"lecture SZL {szl_id:04X} index {index:04X} : {self._err(r)}")
        n = self.nd.daveGetAnswLen(self.dc)
        raw = buf.raw[:n if 0 < n <= len(buf) else len(buf)]
        self.log(f"   SZL {szl_id:04X} index {index:04X} -> {n} octets : {raw[:48].hex(' ')}")
        return parse_szl(raw, szl_id)

    def identity(self) -> dict:
        info = {}
        for index in (1, 6, 7):
            try:
                info.update(parse_identity(self.read_szl(0x0111, index)[1], index))
            except S7Error:
                pass
        for index in COMPONENTS:
            try:
                info.update(parse_component(self.read_szl(0x011C, index)[1]))
            except S7Error:
                pass
        if not info:
            raise S7Error("identification de la CPU non lue (SZL 0111 / 011C)")
        return info

    def leds(self) -> dict:
        """{LED name: (on, blink)} for the LEDs this CPU has."""
        out = {}
        for led_id, name in LEDS.items():
            try:
                out[name] = parse_led(self.read_szl(0x0174, led_id)[1])
            except S7Error:
                pass
        return out

    def protection(self) -> dict:
        return parse_protection(self.read_szl(0x0232, 4)[1])

    def list_blocks(self, kind: str) -> list:
        """Numbers of the blocks of one type in the CPU ("SDB", "OB", "FC"...)."""
        entries = (_BlockEntry * 2048)()
        n = self.nd.daveListBlocksOfType(self.dc, BLOCK_TYPES[kind], entries)
        self.log(f"   liste {kind} -> {n}")
        if n < 0:
            raise S7Error(f"liste des blocs {kind} : {self._err(-n)}")
        return sorted(entries[i].number for i in range(min(n, len(entries))))

    def upload_block(self, kind: str, number: int) -> bytes:
        """Upload one block as the CPU stores it (what STEP 7 gets with 'upload to PG')."""
        buf = ctypes.create_string_buffer(UPLOAD_BUFFER)
        length = ctypes.c_int(0)
        r = self.nd.daveGetProgramBlock(self.dc, BLOCK_TYPES[kind], number, buf, ctypes.byref(length))
        self.log(f"   upload {kind}{number} -> {r}, {length.value} octets")
        if r != 0:
            raise S7Error(f"chargement {kind} {number} : {self._err(r)}")
        if not 0 <= length.value <= UPLOAD_BUFFER:
            raise S7Error(f"chargement {kind} {number} : longueur invalide {length.value}")
        return buf.raw[:length.value]

    def modules(self, rack: int = 0) -> list:
        """Modules of a rack with their slot, first address and state (SZL 0x0D91)."""
        return [parse_module(r) for r in self.read_szl(0x0D91, rack)[1] if len(r) >= 16]

    def diagnostic_buffer(self) -> list:
        """Diagnostic buffer entries, most recent first (S7-300: as many as set in HW Config)."""
        return [parse_diag_entry(r) for r in self.read_szl(0x00A0, 0)[1] if len(r) >= 20]

    def close(self):
        if self.nd is None:
            return
        if self.dc is not None:
            self.nd.daveDisconnectPLC(self.dc)
            self.dc = None
        if self.handle >= 0:
            self.nd.closeS7online(self.handle)
            self.handle = -1

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
