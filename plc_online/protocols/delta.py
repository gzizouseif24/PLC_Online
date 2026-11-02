"""Delta DVP (SV, SS2, SA2, EH...) over its COM ports: standard Modbus, ASCII or RTU.

Default on COM1 (RS-232) and COM2 (RS-485): Modbus ASCII, 9600 baud, 7 bits, even parity,
1 stop bit, station 1. The PLC is the slave.

Modbus address of each device (Delta DVP manual, "communication address"):
  S0..S1023     0x0000          bits
  X0..X377      0x0400 + octal  bits (inputs, octal numbering: no X8 / X9)
  Y0..Y377      0x0500 + octal  bits (outputs)
  T0..T255      0x0600          bits = contacts, registers = current values
  M0..M1535     0x0800          bits
  M1536..M4095  0xB000          bits
  C0..C199      0x0E00          bits = contacts, registers = current values (16-bit)
  C200..C255    0x0EC8          registers are 32-bit (two words, low word first)
  D0..D4095     0x1000          registers
  D4096..D9999  0x9000          registers

Delta answers at most 100 words or 256 bits per request.
"""
from plc_online.protocols.modbus import (AsciiTransport, Modicon, ModbusError, PlcRefused,  # noqa: F401
                                         RtuTransport, open_serial, signed16)

MAX_WORDS, MAX_BITS = 100, 256

BIT_AREAS = ("X", "Y", "M", "S", "T", "C")
WORD_AREAS = ("D", "T", "C")
OCTAL = ("X", "Y")


class DeltaError(Exception):
    """Bad address or unexpected value."""


def parse_address(text: str) -> tuple:
    """'D100' -> ('D', 100); 'X17' -> ('X', 15) (X / Y are octal)."""
    text = text.strip().upper()
    area, num = text[:1], text[1:]
    if area not in ("X", "Y", "M", "S", "T", "C", "D") or not num.isdigit():
        raise DeltaError(f"adresse {text} invalide (ex. D100, M1000, X17, Y0, T5, C10)")
    if area in OCTAL:
        if any(c in "89" for c in num):
            raise DeltaError(f"{text} : X et Y sont en octal (pas de chiffre 8 ou 9)")
        return area, int(num, 8)
    return area, int(num)


def name(area: str, n: int) -> str:
    return f"{area}{n:o}" if area in OCTAL else f"{area}{n}"


def bit_address(area: str, n: int) -> int:
    limits = {"S": 1024, "X": 256, "Y": 256, "T": 256, "M": 4096, "C": 256}
    if not 0 <= n < limits[area]:
        raise DeltaError(f"{name(area, n)} hors plage")
    if area == "M":
        return 0x0800 + n if n < 1536 else 0xB000 + n - 1536
    return {"S": 0x0000, "X": 0x0400, "Y": 0x0500, "T": 0x0600, "C": 0x0E00}[area] + n


def word_address(area: str, n: int) -> int:
    if area == "D":
        if not 0 <= n < 10000:
            raise DeltaError(f"D{n} hors plage")
        return 0x1000 + n if n < 4096 else 0x9000 + n - 4096
    if area in ("T", "C") and 0 <= n < 256:
        return (0x0600 if area == "T" else 0x0E00) + n
    raise DeltaError(f"{name(area, n)} n'est pas un mot")


# Special relays and registers read by "etat" (DVP manual, special M / D list)
STATUS_BITS = {
    1000: "RUN (contact toujours ON en RUN)",
    1004: "erreur de syntaxe du programme (code dans D1004)",
    1008: "chien de garde depasse (WDT, pas dans D1008)",
    1009: "tension 24 V insuffisante (LV)",
    1067: "erreur d'execution (code dans D1067)",
    1068: "erreur d'execution verrouillee (pas dans D1068)",
}
STATUS_WORDS = {
    1000: "chien de garde (ms)",
    1001: "version firmware",
    1002: "capacite programme (pas)",
    1004: "code d'erreur de syntaxe",
    1008: "pas du depassement WDT",
    1010: "cycle actuel (0,1 ms)",
    1011: "cycle mini (0,1 ms)",
    1012: "cycle maxi (0,1 ms)",
    1067: "code d'erreur d'execution",
    1068: "pas de l'erreur d'execution",
    1137: "pas de l'erreur d'operande",
}
CLOCK = 1313          # D1313..D1319 : seconde, minute, heure, jour, mois, jour semaine, annee

SYNTAX_ERRORS = {
    0xC4EE: "pas d'instruction END dans le programme",
}


def error_text(code: int) -> str:
    if code == 0:
        return "aucune"
    if code in SYNTAX_ERRORS:
        return SYNTAX_ERRORS[code]
    if code & 0xFF00 == 0xC400:
        return "erreur de syntaxe / structure du programme"
    if code < 0x1000:
        return "operande hors plage dans le programme"
    return "voir la liste des codes du manuel DVP"


class Delta:
    """One DVP reached through an ASCII or RTU transport."""

    def __init__(self, transport, station: int = 1):
        self.mb = Modicon(transport, station, max_registers=MAX_WORDS, max_bits=MAX_BITS)

    def read_bits(self, area: str, start: int, count: int) -> list:
        bit_address(area, start + count - 1)
        return self.mb.read_coils(bit_address(area, start), count)

    def read_words(self, area: str, start: int, count: int) -> list:
        if area == "D" and start < 4096 < start + count:      # crosses the D4096 address jump
            first = 4096 - start
            return self.read_words("D", start, first) + self.read_words("D", 4096, count - first)
        word_address(area, start + count - 1)
        return self.mb.read_holding(word_address(area, start), count)

    def write_word(self, area: str, n: int, value: int) -> None:
        self.mb.write_register(word_address(area, n), value)

    def write_words(self, area: str, start: int, values: list) -> None:
        if area == "D" and start < 4096 < start + len(values):
            first = 4096 - start
            self.write_words("D", start, values[:first])
            self.write_words("D", 4096, values[first:])
            return
        self.mb.write_registers(word_address(area, start), values)

    def write_bit(self, area: str, n: int, on: bool) -> None:
        self.mb.write_coil(bit_address(area, n), on)

    def status(self) -> dict:
        """Special M and D; one the model does not have (refused) reads as 0."""
        def get(read, n):
            try:
                return read(n)
            except PlcRefused:
                return 0
        bits = {n: get(lambda k: self.read_bits("M", k, 1)[0], n) for n in STATUS_BITS}
        words = {n: get(lambda k: self.read_words("D", k, 1)[0], n) for n in STATUS_WORDS}
        return {"bits": bits, "mots": words}

    def clock(self) -> list:
        return self.read_words("D", CLOCK, 7)

    def close(self):
        self.mb.close()


def open_delta(port, mode="ascii", baud=9600, parity="E", stopbits=1, station=1, timeout=0.6, trace=False):
    if mode == "ascii":
        ser = open_serial(port, baud, parity, stopbits, timeout, bytesize=7)
        return Delta(AsciiTransport(ser, trace), station)
    ser = open_serial(port, baud, parity, stopbits, timeout)
    return Delta(RtuTransport(ser), station)


# settings tried by "chercher": Delta default first
SEARCH = [("ascii", 9600, "E", 1), ("rtu", 9600, "E", 1), ("ascii", 19200, "E", 1), ("rtu", 19200, "E", 1),
          ("ascii", 38400, "E", 1), ("rtu", 38400, "E", 1), ("rtu", 9600, "N", 2), ("rtu", 19200, "N", 2),
          ("ascii", 115200, "E", 1), ("rtu", 115200, "E", 1), ("rtu", 9600, "N", 1), ("rtu", 19200, "N", 1)]
