"""Siemens S7-300 driver: libnodave through STEP 7's S7ONLINE driver.

Chain:  Python -> libnodave.dll -> S7onlinx.dll (STEP 7) -> PC Adapter USB -> S7-300

Requirements (see README, "Siemens"):
  - Windows with STEP 7 installed (it provides S7onlinx.dll and the S7ONLINE driver),
  - "Set PG/PC Interface" set to USB.S7USB.1,
  - 32-bit Python: both DLLs are 32-bit,
  - SIMATIC Manager closed: only one program can own the interface.

Data is big-endian and byte-addressed: MW0 = bytes 0-1, high byte first.
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
