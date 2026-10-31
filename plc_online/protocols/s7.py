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


def parse_identity(records: list) -> dict:
    """SZL 0x0111 records (§34.5): index, order number (20 bytes), BGTyp, Ausbg1, Ausbg2."""
    out = {}
    for r in records:
        if len(r) < 28:
            continue
        index, a1, a2 = word_be(r, 0), word_be(r, 24), word_be(r, 26)
        if index == 1:
            out["reference"] = _text(r[2:22])
            out["version_module"] = a1
        elif index in (6, 7):
            key = "materiel" if index == 6 else "firmware"
            out[key] = f"{chr(a1 >> 8)}{a1 & 0xFF}.{a2 >> 8}.{a2 & 0xFF}"   # 'V' + major, then two numbers
            if index == 6:
                out.setdefault("reference", _text(r[2:22]))
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


class S7300:
    """Connection to one S7-300 CPU. Use as a context manager."""

    def __init__(self, mpi: int = 2, rack: int = 0, slot: int = 2, dll=DEFAULT_DLL, log=None):
        self.mpi, self.rack, self.slot = mpi, rack, slot
        self.dll_path = dll
        self.log = log or (lambda msg: None)
        self.nd = None
        self.handle = -1
        self.dc = None

    def _err(self, code: int) -> str:
        return self.nd.daveStrerror(code).decode(errors="replace")

    def open(self):
        self.nd = nd = load_dll(self.dll_path)

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
                info.update(parse_identity(self.read_szl(0x0111, index)[1]))
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
