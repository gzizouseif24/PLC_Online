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
  D4096..D11999 0x9000          registers (D9999 on the SV, D11999 on the SV2)

Delta answers at most 100 words or 256 bits per request.
"""
from plc_online.protocols.modbus import (AsciiTransport, Modicon, ModbusError, PlcRefused,  # noqa: F401
                                         RtuTransport, open_serial, signed16)

MAX_WORDS, MAX_BITS = 100, 256
MAX_D = 12000          # DVP-SV2: D0..D11999 (DVP-SV: D0..D9999, the read stops where the PLC refuses)

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
        if not 0 <= n < MAX_D:
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
# right-side modules and expansion I/O (DVP-SV): read when present, ignored if refused
EXPANSION = {1140: "modules speciaux a droite", 1142: "entrees X d'extension", 1143: "sorties Y d'extension"}
MODULE_IDS = 1320          # D1320..D1327 : code des modules speciaux 1..8

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
    # runtime errors (D1067), DVP manual list
    0x0E18: "erreur de conversion BCD",
    0x0E19: "division par zero",
    0x0E1A: "operande hors plage (adresse indexee E / F trop grande ?)",
    0x0E1B: "racine carree d'un nombre negatif",
    0x0E1C: "erreur FROM / TO : le module special adresse ne repond pas (absent, en defaut, ou numero de module faux)",
}

# COM settings word (D1036 for COM1, D1120 for COM2), DVP manual:
#   b0 data bits (0 = 7, 1 = 8), b2..b1 parity (00 none, 01 odd, 11 even), b3 stop bits (0 = 1, 1 = 2),
#   b7..b4 speed. Default H86 = 9600 7E1.
SPEEDS = {1: 110, 2: 150, 3: 300, 4: 600, 5: 1200, 6: 2400, 7: 4800, 8: 9600, 9: 19200, 10: 38400,
          11: 57600, 12: 115200}
COM_REGS = {"COM1 (RS-232)": (1036, 1138, 1139), "COM2 (RS-485)": (1120, 1120, 1143)}
STATION = 1121


def com_text(word: int, rtu: bool) -> str:
    """D1120 / D1036 + RTU flag -> '9600 7E1 ASCII'."""
    speed = SPEEDS.get(word >> 4 & 0x0F, f"? (code {word >> 4 & 0x0F})")
    parity = {0: "N", 1: "O", 3: "E"}.get(word >> 1 & 3, "?")
    return f"{speed} {8 if word & 1 else 7}{parity}{2 if word & 8 else 1} {'RTU' if rtu else 'ASCII'}"


def com_word(speed: int, bits: int, parity: str, stop: int) -> int:
    code = {v: k for k, v in SPEEDS.items()}[speed]
    return code << 4 | (8 if stop == 2 else 0) | {"N": 0, "O": 2, "E": 6}[parity] | (1 if bits == 8 else 0)


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

    def com_settings(self) -> list:
        """[(port, text, kept)] of COM1 / COM2, and the station number."""
        out = []
        for port, (reg, keep, rtu) in COM_REGS.items():
            try:
                word = self.read_words("D", reg, 1)[0]
                flags = {n: self.read_bits("M", n, 1)[0] for n in {keep, rtu}}
                out.append((port, com_text(word, flags[rtu]) + f"  (D{reg} = 16#{word:04X})", bool(flags[keep])))
            except PlcRefused:
                pass
        return out

    def clock(self) -> list:
        return self.read_words("D", CLOCK, 7)

    def set_clock(self, when) -> None:
        """D1313..D1319 from a datetime (Delta: seconds, minutes, hours, day, month, weekday 0 = Sunday,
        year - 2000)."""
        self.write_words("D", CLOCK, [when.second, when.minute, when.hour, when.day, when.month,
                                      (when.weekday() + 1) % 7, when.year % 100])

    def read_all_d(self, progress=None) -> list:
        """D0.. until the PLC refuses (end of its D area), 100 words at a time."""
        values = []
        while len(values) < MAX_D:
            n = min(MAX_WORDS, MAX_D - len(values))
            try:
                values += self.read_words("D", len(values), n)
            except PlcRefused:
                try:                                 # last block crosses the end: one word at a time
                    while len(values) < MAX_D:
                        values += self.read_words("D", len(values), 1)
                except PlcRefused:
                    pass
                break
            if progress:
                progress(len(values))
        return values

    def close(self):
        self.mb.close()


def changed_runs(addrs) -> list:
    """Sorted addresses -> [start, length] runs, cut at D4096 (address jump)."""
    runs = []
    for a in sorted(addrs):
        if runs and runs[-1][0] + runs[-1][1] == a and a != 4096 and runs[-1][1] < MAX_WORDS:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return runs


def data_csv(values, plc: str, stamp: str) -> str:
    """values: list (D0..) or {address: value} (sparse, from an imported file)."""
    items = sorted(values.items()) if isinstance(values, dict) else enumerate(values)
    lines = [f"# date;{stamp}", f"# automate;{plc}", "zone;adresse;valeur;signe;hex"]
    lines += [f"D;{i};{v};{signed16(v)};{v:04X}" for i, v in items]
    return "\n".join(lines) + "\n"


def parse_data_csv(text: str) -> tuple:
    """CSV of data_csv (or of tools/delta.py) -> ({address: value} of the D, PLC name or '')."""
    out, plc = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# automate;"):
            plc = line.split(";", 1)[1]
        if not line or line.startswith("#") or line.lower().startswith("zone;"):
            continue
        parts = line.split(";")
        if len(parts) < 3 or parts[0].strip().upper() != "D":
            continue
        try:
            out[int(parts[1])] = int(parts[2]) & 0xFFFF
        except ValueError:
            raise DeltaError(f"ligne illisible : {line}") from None
    if not out:
        raise DeltaError("aucun mot D dans le fichier")
    return out, plc


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


# ---- listening to a master (HMI) on the RS-485 line ----

LISTEN_SETTINGS = [(mode, baud, bits, parity, stop)
                   for baud in (9600, 19200, 38400, 57600, 115200, 4800)
                   for mode, bits, parity, stop in (("ascii", 7, "E", 1), ("rtu", 8, "E", 1), ("rtu", 8, "N", 2),
                                                    ("rtu", 8, "N", 1), ("ascii", 7, "O", 1), ("rtu", 8, "O", 1),
                                                    ("ascii", 7, "N", 2), ("ascii", 8, "N", 1))]


def ascii_frames(buf: bytes) -> list:
    """Valid Modbus ASCII frames (LRC checked) in what was heard: [(station, function)]."""
    import re
    out = []
    for m in re.finditer(rb":([0-9A-Fa-f]{6,})\r\n", buf):
        try:
            data = bytes.fromhex(m.group(1).decode())
        except ValueError:
            continue
        if len(data) >= 3 and (-sum(data[:-1]) & 0xFF) == data[-1]:
            out.append((data[0], data[1]))
    return out


def rtu_frames(buf: bytes) -> list:
    """Modbus RTU requests 01..06 (8 bytes, CRC checked) in what was heard: [(station, function)]."""
    from plc_online.protocols.modbus import crc16
    out = []
    for i in range(len(buf) - 7):
        f = buf[i:i + 8]
        if 1 <= f[0] <= 247 and f[1] in (1, 2, 3, 4, 5, 6) and crc16(f[:6]) == f[6] | f[7] << 8:
            out.append((f[0], f[1]))
    return out
