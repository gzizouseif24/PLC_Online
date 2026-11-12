"""Mitsubishi MELSEC FX (FX1S / FX1N / FX2N) over the programming port (mini-DIN 8, RS-422, SC-09 cable).

Protocol of the programming port, 9600 bauds 7E1 (Mitsubishi FX-232AW manual BT5096-C, tables 1 to 7):
  request  STX cmd payload ETX sum     sum = 2 hex digits, low byte of the sum of cmd..ETX
  cmd '0'  read  : address (4 hex) + byte count (2 hex, 1..64)  -> STX data(hex) ETX sum
  cmd '1'  write : address + count + data(hex)                  -> ACK (06) or NAK (15)
  cmd '7' / '8'  force ON / OFF one bit: bit address, 4 hex, LOW BYTE FIRST -> ACK / NAK
  ENQ (05) alone -> ACK when the PLC is there.

Byte addresses of the classic map (the only one documented):
  bits read as bytes (8 per byte):  S 0000, X 0080, Y 00A0, T 00C0, M 0100, C 01C0, M8000 01E0
  bit address for force:            S 0000, X 0400, Y 0500, T 0600, M 0800, C 0E00, M8000 0F00
  words (little-endian):            T 0800, C0-199 0A00, C200-255 0C00 (32 bits), D8000 0E00, D 1000
Ranges of this map: X / Y 0..177 (octal), M 0..1023 and 8000..8255, S 0..999, T / C 0..255,
D 0..511 and 8000..8255. D512 and above (FX1N: up to D7999) are not in the documented map.
"""
import time

STX, ETX, ENQ, ACK, NAK = b"\x02", b"\x03", b"\x05", b"\x06", b"\x15"
MAX_BYTES = 64


class FxError(Exception):
    """Missing, corrupted or unexpected reply."""


class FxRefused(FxError):
    """The PLC answered NAK (address or command refused)."""


# area -> (bit image byte address, force bit address, number of points)
BIT_AREAS = {"S": (0x0000, 0x0000, 1000), "X": (0x0080, 0x0400, 128), "Y": (0x00A0, 0x0500, 128),
             "T": (0x00C0, 0x0600, 256), "M": (0x0100, 0x0800, 1024), "C": (0x01C0, 0x0E00, 256)}
SPECIAL_M = (0x01E0, 0x0F00)          # M8000..M8255
WORD_AREAS = ("D", "T", "C")
OCTAL = ("X", "Y")


def checksum(body: bytes) -> bytes:
    return f"{sum(body) & 0xFF:02X}".encode()


def frame(cmd: str, payload: str = "") -> bytes:
    body = (cmd + payload).encode() + ETX
    return STX + body + checksum(body)


def parse_address(text: str):
    """'X27' -> ('X', 23) (octal), 'M8000' -> ('M', 8000), 'D100' -> ('D', 100)."""
    t = text.strip().upper()
    if len(t) < 2 or t[0] not in "XYMSTCD" or not t[1:].isdigit():
        raise FxError(f"adresse {text!r} : X0, Y17 (octal), M100, S0, T5, C2, D100")
    area, digits = t[0], t[1:]
    if area in OCTAL:
        if any(c in "89" for c in digits):
            raise FxError(f"{text} : X et Y sont en octal (X0..X7, X10..X17, ... pas de 8 ni 9)")
        n = int(digits, 8)
    else:
        n = int(digits)
    ok = {"X": n < 128, "Y": n < 128, "S": n < 1000, "T": n < 256, "C": n < 256,
          "M": n < 1024 or 8000 <= n < 8256, "D": n < 512 or 8000 <= n < 8256}[area]
    if not ok:
        limits = {"X": "X0..X177", "Y": "Y0..Y177", "S": "S0..S999", "T": "T0..T255", "C": "C0..C255",
                  "M": "M0..M1023 et M8000..M8255", "D": "D0..D511 et D8000..D8255"}[area]
        raise FxError(f"{text} : hors de la zone lisible par le port de programmation ({limits})")
    return area, n


def name(area: str, n: int) -> str:
    return f"{area}{n:o}" if area in OCTAL else f"{area}{n}"


def bit_image(area: str, n: int):
    """Byte address and bit of a contact in the bit image (read with '0')."""
    if area == "M" and n >= 8000:
        return SPECIAL_M[0] + (n - 8000) // 8, (n - 8000) % 8
    if area not in BIT_AREAS:
        raise FxError(f"{area} : pas un contact")
    return BIT_AREAS[area][0] + n // 8, n % 8


def force_address(area: str, n: int) -> int:
    if area == "M" and n >= 8000:
        return SPECIAL_M[1] + n - 8000
    if area not in BIT_AREAS:
        raise FxError(f"{area} : pas un contact")
    return BIT_AREAS[area][1] + n


def data_address(area: str, n: int):
    """(byte address, size in bytes) of a word: D, T or C current value (C200..C255 are 32 bits)."""
    if area == "D":
        return (0x0E00 + (n - 8000) * 2 if n >= 8000 else 0x1000 + n * 2), 2
    if area == "T":
        return 0x0800 + n * 2, 2
    if area == "C":
        return (0x0C00 + (n - 200) * 4, 4) if n >= 200 else (0x0A00 + n * 2, 2)
    raise FxError(f"{area} : pas un mot (D, T ou C)")


def open_port(port: str, baud: int = 9600, timeout: float = 0.5):
    """Programming port: 7 data bits, even parity, 1 stop bit."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, bytesize=7, parity="E", stopbits=1, timeout=timeout)


class FxPlc:
    """One FX CPU on the programming port. Every reply is checked (sum, length) before use."""

    nom = "Mitsubishi FX"

    def __init__(self, ser, retries: int = 2):
        self.ser = ser
        self.retries = retries

    def close(self):
        self.ser.close()

    def _once(self, request: bytes) -> bytes:
        self.ser.reset_input_buffer()
        self.ser.write(request)
        first = self.ser.read(1)
        if first == ACK:
            return b""
        if first == NAK:
            raise FxRefused("l'automate a refuse la commande (NAK) : adresse ou commande non acceptee")
        if first != STX:
            raise FxError("pas de reponse" if not first else f"reponse inattendue {first!r}")
        body = self.ser.read_until(ETX, 2 * MAX_BYTES + 2)
        if not body.endswith(ETX):
            raise FxError(f"reponse coupee : {body!r}")
        if self.ser.read(2) != checksum(body):
            raise FxError(f"somme de controle fausse : {body!r}")
        try:
            return bytes.fromhex(body[:-1].decode("ascii"))
        except ValueError:
            raise FxError(f"donnees illisibles : {body!r}") from None

    def _exchange(self, cmd: str, payload: str = "", expect: int = 0) -> bytes:
        request = frame(cmd, payload)
        error = None
        for _ in range(self.retries + 1):
            try:
                data = self._once(request)
            except FxRefused:
                raise
            except FxError as e:
                error = e
                time.sleep(0.05)
                continue
            if len(data) != expect:
                error = FxError(f"{len(data)} octet(s) recu(s) au lieu de {expect}")
                continue
            return data
        raise error

    def ping(self) -> bool:
        self.ser.reset_input_buffer()
        self.ser.write(ENQ)
        return self.ser.read(1) == ACK

    def read_bytes(self, address: int, count: int) -> bytes:
        out = b""
        while count > 0:
            n = min(count, MAX_BYTES)
            out += self._exchange("0", f"{address:04X}{n:02X}", n)
            address, count = address + n, count - n
        return out

    def write_bytes(self, address: int, data: bytes):
        for i in range(0, len(data), MAX_BYTES):
            part = data[i:i + MAX_BYTES]
            self._exchange("1", f"{address + i:04X}{len(part):02X}{part.hex().upper()}")

    def force(self, area: str, n: int, on: bool):
        a = force_address(area, n)
        self._exchange("7" if on else "8", f"{a & 0xFF:02X}{a >> 8:02X}")

    def read_bits(self, area: str, start: int, count: int) -> list:
        first, bit = bit_image(area, start)
        last, _ = bit_image(area, start + count - 1)
        raw = self.read_bytes(first, last - first + 1)
        return [bool(raw[(bit + i) // 8] >> ((bit + i) % 8) & 1) for i in range(count)]

    def read_words(self, area: str, n: int, count: int) -> list:
        address, size = data_address(area, n)
        raw = self.read_bytes(address, size * count)
        return [int.from_bytes(raw[i:i + size], "little") for i in range(0, len(raw), size)]

    def write_words(self, area: str, n: int, values: list):
        address, size = data_address(area, n)
        self.write_bytes(address, b"".join((v & ((1 << 8 * size) - 1)).to_bytes(size, "little") for v in values))

    def status(self) -> dict:
        """Special relays M8000..M8079 and registers D8000..D8069."""
        m = self.read_bits("M", 8000, 80)
        d = self.read_words("D", 8000, 70)
        return {"m": {8000 + i: v for i, v in enumerate(m)}, "d": {8000 + i: v for i, v in enumerate(d)}}

    def clock(self):
        """(second, minute, hour, day, month, year, weekday) from D8013..D8019."""
        return tuple(self.read_words("D", 8013, 7))

    def set_clock(self, when):
        """M8015 ON (clock stopped, time setting), D8013..D8019, M8015 OFF (manual 6.3). The year keeps the
        format already in use (2 digits, or 1980..2079)."""
        year = self.read_words("D", 8018, 1)[0]
        year = when.year if year >= 1980 else when.year % 100
        self.force("M", 8015, True)
        try:
            self.write_words("D", 8013, [when.second, when.minute, when.hour, when.day, when.month, year,
                                         (when.weekday() + 1) % 7])
        finally:
            self.force("M", 8015, False)

    def set_mode(self, run: bool, wait: float = 1.0) -> bool:
        """Remote RUN / STOP with the forced RUN / STOP flags (programming manual 10.3):
        RUN = M8035 + M8036 ON, STOP = M8037 ON. Returns the RUN state read back (M8000)."""
        if run:
            self.force("M", 8035, True)
            self.force("M", 8036, True)
        else:
            self.force("M", 8037, True)
        deadline = time.time() + wait
        while True:
            state = self.read_bits("M", 8000, 1)[0]
            if state == run or time.time() > deadline:
                return state
            time.sleep(0.1)

    def read_program_memory(self, steps: int, progress=None) -> bytes:
        """Parameters (92 bytes) + program (2 bytes per step) + 4 bytes, from 8000h (classic layout)."""
        length = 92 + steps * 2 + 4
        out = b""
        for offset in range(0, length, MAX_BYTES):
            out += self.read_bytes(0x8000 + offset, min(MAX_BYTES, length - offset))
            if progress:
                progress(len(out), length)
        return out


# ---- error codes (programming manual JY992D88101, 6.18) ----

HARDWARE_ERRORS = {6101: "erreur RAM", 6102: "erreur du circuit de calcul",
                   6103: "erreur de bus d'E/S (extension mal enfichee ?)", 6104: "defaut 24 V d'une unite d'extension",
                   6105: "chien de garde (cycle plus long que D8000)"}
PARAMETER_ERRORS = {6401: "somme de controle du programme fausse", 6402: "capacite memoire mal reglee",
                    6403: "zone des memoires maintenues mal reglee", 6404: "zone de commentaires mal reglee",
                    6405: "zone des registres fichiers mal reglee", 6409: "autre erreur de parametre"}


def error_text(register: int, code: int) -> str:
    if not code:
        return "aucune"
    if register == 8061:
        return HARDWARE_ERRORS.get(code, f"erreur materiel {code}")
    if register == 8064:
        return PARAMETER_ERRORS.get(code, f"erreur de parametre {code}")
    if register == 8065:
        return f"erreur de syntaxe {code} (instruction ou operande invalide)"
    if register == 8066:
        return f"erreur de structure du programme {code} (LD / ANB, MC / MCR, FOR / NEXT, saut...)"
    if register == 8067:
        return f"erreur d'execution {code} (operande hors zone, division par 0...)"
    return str(code)


CPU_FAMILIES = {22: "FX1S", 26: "FX1N", 24: "FX2N / FX2NC"}
MEMORY_TYPES = {0x00: "cassette RAM", 0x01: "cassette EPROM", 0x02: "cassette EEPROM",
                0x0A: "cassette EEPROM protegee", 0x10: "memoire interne"}


def cpu_text(d8001: int) -> str:
    family = CPU_FAMILIES.get(d8001 // 1000, f"type {d8001 // 1000}")
    v = d8001 % 1000
    return f"{family} V{v // 100}.{v % 100:02d}"
