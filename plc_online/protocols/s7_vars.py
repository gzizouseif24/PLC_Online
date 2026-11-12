"""S7 variable addresses for the watch table: parse, group into few reads, decode, encode.

Accepted, in German / French (E, A, Z) or English (I, Q, C) mnemonics:
    E0.0  EB0  EW0  ED0      inputs          (I0.0  IB0  IW0  ID0)
    A4.1  AB4  AW4  AD4      outputs         (Q4.1  QB4  QW4  QD4)
    M10.3 MB10 MW10 MD10     flags
    DB10.DBX2.1  DB10.DBB2  DB10.DBW2  DB10.DBD2
    T5   Z2 (or C2)          timers, counters
Optional display format after a colon: MW10:hex, MD4:real, DB1.DBD0:real, MB0:bin, MW2:ud
(unsigned), MB3:char.
"""
import re
import struct

AREA_CODES = {"I": 0x81, "Q": 0x82, "M": 0x83, "DB": 0x84, "C": 0x1C, "T": 0x1D}
AREA_ALIASES = {"E": "I", "I": "I", "A": "Q", "Q": "Q", "M": "M", "Z": "C", "C": "C", "T": "T"}
SIZES = {"X": 0, "B": 1, "W": 2, "D": 4}
FORMATS = {"bool", "dec", "ud", "hex", "bin", "real", "char", "s5time", "bcd"}
MAX_READ = 200          # bytes per request: PDU 240 on the bench CPU, minus headers

_PATTERNS = [
    re.compile(r"^DB(?P<db>\d+)\.DB(?P<size>[XBWD])(?P<start>\d+)(?:\.(?P<bit>[0-7]))?$"),
    re.compile(r"^(?P<area>[EIAQM])(?P<size>[BWD])(?P<start>\d+)$"),
    re.compile(r"^(?P<area>[EIAQM])(?P<start>\d+)\.(?P<bit>[0-7])$"),
    re.compile(r"^(?P<area>[TZC])(?P<start>\d+)$"),
]


class AddressError(ValueError):
    """Address the watch table does not understand."""


class Var:
    """One watched variable."""

    def __init__(self, text, label=""):
        self.text, self.label = text, label
        addr, _, fmt = text.strip().upper().partition(":")
        for pat in _PATTERNS:
            m = pat.match(addr)
            if m:
                break
        else:
            raise AddressError(f"adresse non comprise : {text!r} (ex. E0.0, MW10, DB10.DBD4, T5)")
        g = m.groupdict()
        if g.get("db") is not None:
            self.area, self.db = "DB", int(g["db"])
        else:
            self.area, self.db = AREA_ALIASES[g["area"]], 0
        self.start = int(g["start"])
        size = g.get("size") or ("X" if g.get("bit") is not None else None)
        if self.area in ("T", "C"):
            self.size, self.bit = 2, None
        else:
            if size == "X" and g.get("bit") is None:
                raise AddressError(f"{text!r} : bit manquant (ex. DB10.DBX2.1)")
            self.size = SIZES[size]
            self.bit = int(g["bit"]) if size == "X" else None
        default = {"T": "s5time", "C": "bcd"}.get(self.area) or ("bool" if self.bit is not None else "dec")
        self.format = fmt.lower() or default
        if self.format not in FORMATS:
            raise AddressError(f"{text!r} : format inconnu {fmt!r} ({', '.join(sorted(FORMATS))})")
        if self.format == "real" and self.size != 4:
            raise AddressError(f"{text!r} : un REAL occupe 4 octets (MD, ED, DBD...)")
        self.name = addr

    @property
    def nbytes(self):
        return 1 if self.bit is not None else self.size

    def decode(self, raw: bytes) -> str:
        """Value as shown in the table, from the bytes of this variable."""
        if self.bit is not None:
            return str(raw[0] >> self.bit & 1)
        if self.format == "s5time":
            return s5time_text(int.from_bytes(raw[:2], "big"))
        if self.format == "bcd":
            return bcd_text(int.from_bytes(raw[:2], "big"))
        value = int.from_bytes(raw, "big")
        if self.format == "hex":
            return f"16#{value:0{2 * self.size}X}"
        if self.format == "bin":
            bits = f"{value:0{8 * self.size}b}"
            return "2#" + "_".join(bits[i:i + 4] for i in range(0, len(bits), 4))
        if self.format == "real":
            return f"{struct.unpack('>f', raw)[0]:.6g}"
        if self.format == "char":
            return "'" + "".join(chr(b) if 32 <= b < 127 else "." for b in raw) + "'"
        if self.format == "ud":
            return str(value)
        if self.size == 1:
            return str(value)               # bytes: unsigned, as STEP 7 shows them in decimal
        return str(value - (1 << 8 * self.size) if value >> (8 * self.size - 1) else value)

    def encode(self, text: str) -> bytes:
        """Bytes to write for a value typed by the user ('1', '-12', '16#FF', '2#1010', '3.5')."""
        text = text.strip()
        if self.area in ("T", "C"):
            raise AddressError("ecriture des temporisations / compteurs non prise en charge")
        if self.bit is not None:
            if text.upper() not in ("0", "1", "TRUE", "FALSE"):
                raise AddressError(f"{self.name} est un bit : 0 ou 1")
            return bytes([1 if text.upper() in ("1", "TRUE") else 0])
        if self.format == "real" or ("." in text and self.size == 4 and not text.upper().startswith(("16#", "2#"))):
            if self.size != 4:
                raise AddressError("un REAL occupe 4 octets")
            return struct.pack(">f", float(text))
        up = text.upper().replace("_", "")
        if up.startswith("16#"):
            value = int(up[3:], 16)
        elif up.startswith("2#"):
            value = int(up[2:], 2)
        else:
            value = int(up)
        bits = 8 * self.size
        if not -(1 << bits - 1) <= value < (1 << bits):
            raise AddressError(f"{value} ne tient pas dans {self.size} octet(s)")
        return (value & ((1 << bits) - 1)).to_bytes(self.size, "big")


def s5time_text(word: int) -> str:
    base = (10, 100, 1000, 10000)[word >> 12 & 3]
    count = bcd_value(word & 0x0FFF)
    if count is None:
        return f"16#{word:04X}"
    ms = count * base
    return f"S5T#{ms // 1000}S{ms % 1000}MS" if ms % 1000 else f"S5T#{ms // 1000}S"


def bcd_value(word: int):
    digits = [word >> s & 0xF for s in (8, 4, 0)]
    if any(d > 9 for d in digits):
        return None
    return digits[0] * 100 + digits[1] * 10 + digits[2]


def bcd_text(word: int) -> str:
    v = bcd_value(word & 0x0FFF)
    return f"C#{v}" if v is not None else f"16#{word:04X}"


def plan_reads(variables):
    """Group variables into few reads: same area / DB, close addresses, at most MAX_READ bytes.
    Returns [(area, db, start, count, [vars])]; for T / C start and count are element numbers."""
    groups = {}
    for v in variables:
        groups.setdefault((v.area, v.db), []).append(v)
    reads = []
    for (area, db), vs in groups.items():
        vs.sort(key=lambda v: v.start)
        unit = 1 if area in ("T", "C") else 0            # T / C: one element = one number
        cur = None
        for v in vs:
            end = v.start + (1 if unit else v.nbytes)
            if cur and v.start - cur[3] <= 8 and end - cur[2] <= (MAX_READ // 2 if unit else MAX_READ):
                cur[3] = max(cur[3], end)
                cur[4].append(v)
            else:
                cur = [area, db, v.start, end, [v]]
                reads.append(cur)
    return [(a, db, s, e - s, vs) for a, db, s, e, vs in reads]


def split_values(read, data: bytes) -> dict:
    """{var: bytes} for one planned read and the bytes it returned."""
    area, _, start, _, vs = read
    out = {}
    for v in vs:
        if area in ("T", "C"):
            off = 2 * (v.start - start)
            out[v] = data[off:off + 2]
        else:
            off = v.start - start
            out[v] = data[off:off + v.nbytes]
    return out


def load_table(path) -> list:
    """Watch table file: one address per line, then an optional label; '#' starts a comment.
        E0.0        bouton marche
        MW10:hex    mot d'etat
        DB10.DBD4:real  temperature four
    """
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("#") else ""
            if not line:
                continue
            addr, _, label = line.partition(" ")
            try:
                out.append(Var(addr, label.strip()))
            except AddressError as e:
                raise AddressError(f"{path} ligne {n} : {e}") from None
    return out
