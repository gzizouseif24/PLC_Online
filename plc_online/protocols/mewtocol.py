"""Panasonic FP0R driver: MEWTOCOL-COM over the serial tool port.

Every reply is checked before its data is used:
  1. BCC of the reply (XOR from '%' to the end of the data),
  2. header (station number and command echo, or '!' error code),
  3. length (number of words received = number requested).
A reply that fails 1 or 3 is retried; an error code from the PLC is not.

Words are little-endian in hex: "D204" -> 0x04D2 = 1234.
32-bit values are two words, low word first.
"""
import time

from ..checksum import xor_checksum

# The FP0R cuts replies after about 28 words without reporting an error,
# so reads are split into blocks of this size.
MAX_WORDS_PER_FRAME = 24

# Program steps per RP request: small enough to never need a multi-frame reply.
PROGRAM_STEPS_PER_FRAME = 10

# Erased program memory reads as FFFF. This many in a row = end of program.
END_OF_PROGRAM_FFFF = 32


class MewtocolError(Exception):
    """Missing, corrupted or unexpected reply."""


# error codes of a %01!xx reply (MEWTOCOL-COM manual)
ERROR_CODES = {
    "40": "BCC faux", "41": "format de commande", "42": "commande non supportee", "43": "trames multiples",
    "60": "parametre invalide", "61": "donnee invalide", "62": "enregistrement (trop de contacts / registres)",
    "63": "mode de l'automate (commande impossible dans ce mode : selecteur RUN / PROG ?)",
    "65": "protection (mot de passe ou memoire protegee)", "66": "adresse hors plage", "67": "donnee manquante",
}


class PlcRefused(MewtocolError):
    """The PLC answered with an error code (%01!xx)."""

    def __init__(self, code: str, reply: str):
        text = ERROR_CODES.get(code, "")
        super().__init__(f"l'automate a refusé la commande (code {code}{' : ' + text if text else ''}) : {reply!r}")
        self.code = code


# contact areas: X inputs, Y outputs, R internal relays (word decimal + bit hex: R901C = word 901 bit C)
CONTACT_AREAS = ("X", "Y", "R")


def open_port(port: str, baud: int = 9600, parity: str = "O", timeout: float = 1.0, bytesize: int = 8,
              stopbits: int = 1):
    """Open the serial port with the FP0R tool-port settings (8 data bits, 1 stop by default)."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, parity=parity, bytesize=bytesize, stopbits=stopbits, timeout=timeout)


def decode_words(hexdata: str) -> list:
    """'D2040000' -> [1234, 0]."""
    if len(hexdata) % 4:
        raise MewtocolError(f"données de longueur impaire : {hexdata!r}")
    try:
        return [int(hexdata[i + 2:i + 4] + hexdata[i:i + 2], 16)
                for i in range(0, len(hexdata), 4)]
    except ValueError:
        raise MewtocolError(f"données non hexadécimales : {hexdata!r}") from None


def encode_word(value: int) -> str:
    """1234 -> 'D204'. Negative values are written as 16-bit two's complement."""
    v = value & 0xFFFF
    return f"{v & 0xFF:02X}{v >> 8:02X}"


def dint(words: list, i: int) -> int:
    """Signed 32-bit value from words[i] (low) and words[i + 1] (high)."""
    v = words[i] | (words[i + 1] << 16)
    return v - (1 << 32) if v & 0x80000000 else v


class Fp0r:
    """One FP0R on an open serial port."""

    def __init__(self, ser, station: int = 1, retries: int = 2):
        self.ser = ser
        self.station = station
        self.retries = retries

    # ---------- framing ----------

    def _frame(self, cmd: str) -> bytes:
        body = f"%{self.station:02d}#{cmd}"
        return (body + xor_checksum(body) + "\r").encode("ascii")

    def _check_reply(self, reply: str, cmd: str, data_len) -> str:
        """Return the data part of a valid reply, or raise."""
        if not reply:
            raise MewtocolError("pas de réponse (timeout)")
        if len(reply) < 6:
            raise MewtocolError(f"réponse trop courte : {reply!r}")
        body, bcc = reply[:-2], reply[-2:]
        if xor_checksum(body) != bcc.upper():
            raise MewtocolError(f"BCC faux (reçu {bcc}, calculé {xor_checksum(body)}) : {reply!r}")
        head = f"%{self.station:02d}"
        if body.startswith(head + "!"):
            raise PlcRefused(body[len(head) + 1:len(head) + 3], reply)
        expected = f"{head}${cmd}"
        if not body.startswith(expected):
            raise MewtocolError(f"en-tête inattendu (attendu {expected!r}) : {reply!r}")
        data = body[len(expected):]
        if "&" in data:
            raise MewtocolError("réponse en plusieurs trames non gérée : réduire la taille du bloc")
        if data_len is not None and len(data) != data_len:
            raise MewtocolError(
                f"réponse coupée : {len(data)} caractères reçus, {data_len} attendus : {reply!r}")
        return data

    def _transact(self, cmd: str, reply_cmd: str, data_len=None, retry: bool = True) -> str:
        attempts = 1 + (self.retries if retry else 0)
        last = None
        for _ in range(attempts):
            self.ser.reset_input_buffer()
            self.ser.write(self._frame(cmd))
            raw = self.ser.read_until(b"\r").decode("ascii", "replace").strip()
            try:
                return self._check_reply(raw, reply_cmd, data_len)
            except PlcRefused:
                raise
            except MewtocolError as e:
                last = e
        raise last

    # ---------- data registers ----------

    def read_dt(self, start: int, count: int) -> list:
        """Read DT[start] .. DT[start + count - 1], in blocks the PLC can handle."""
        words = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_WORDS_PER_FRAME, end - addr)
            data = self._transact(f"RDD{addr:05d}{addr + n - 1:05d}", "RD", data_len=4 * n)
            words += decode_words(data)
            addr += n
        return words

    def write_dt(self, addr: int, value: int) -> None:
        """Write one DT register. Not retried: the caller re-reads to confirm."""
        self._transact(f"WDD{addr:05d}{addr:05d}{encode_word(value)}", "WD",
                       data_len=0, retry=False)

    # ---------- contacts (X, Y, R) ----------

    def read_contact_words(self, area: str, start: int, count: int) -> list:
        """WX / WY / WR words (RCC), in blocks the PLC can handle; bit 0 = X0, X10..."""
        words = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_WORDS_PER_FRAME, end - addr)
            data = self._transact(f"RCC{area}{addr:04d}{addr + n - 1:04d}", "RC", data_len=4 * n)
            words += decode_words(data)
            addr += n
        return words

    def read_contact(self, area: str, word: int, bit: int) -> bool:
        """One contact (RCS): X1F = word 1, bit 15."""
        return self._transact(f"RCS{area}{word:03d}{bit:X}", "RC", data_len=1) == "1"

    def write_contact(self, area: str, word: int, bit: int, on: bool) -> None:
        """One Y / R contact (WCS). Not retried: the caller re-reads to confirm."""
        self._transact(f"WCS{area}{word:03d}{bit:X}{int(bool(on))}", "WC", data_len=0, retry=False)

    def write_contact_word(self, area: str, word: int, value: int) -> None:
        """One WY / WR word (WCC). Not retried."""
        self._transact(f"WCC{area}{word:04d}{word:04d}{encode_word(value)}", "WC", data_len=0, retry=False)

    def write_dt_block(self, start: int, values: list) -> None:
        """Consecutive DT registers (WD), at most MAX_WORDS_PER_FRAME per frame. Not retried."""
        for i in range(0, len(values), MAX_WORDS_PER_FRAME):
            chunk = values[i:i + MAX_WORDS_PER_FRAME]
            a = start + i
            self._transact(f"WDD{a:05d}{a + len(chunk) - 1:05d}" + "".join(encode_word(v) for v in chunk), "WD",
                           data_len=0, retry=False)

    # ---------- status, mode and program ----------

    def set_mode(self, run: bool) -> None:
        """Remote RUN (RMG) / PROG (RMP). Refused (code 63...) if the mode switch does not allow it."""
        self._transact("RMG" if run else "RMP", "RM", data_len=0, retry=False)

    def read_status(self) -> dict:
        """RT command, laid out as in the MEWTOCOL-COM manual: model code, version, program capacity
        (K steps), operation mode flags, link, error flags, self-diagnostic error code.
        'raw' keeps the reply: the field decoding is not confirmed on an FP0R yet."""
        data = self._transact("RT", "RT")
        info = {"raw": data}
        if len(data) >= 8:
            info["model"] = data[0:2]
            info["version"] = f"{data[2]}.{data[3]}"
            try:
                info["capacity_k"] = int(data[4:6])
            except ValueError:
                info["capacity_k"] = 0
            info["capacity_steps"] = info["capacity_k"] * 1024
            flags = int(data[6:8], 16)
            info["mode_flags"] = flags
            info["mode"] = "RUN" if flags & 0x01 else "PROG"
            info["remote"] = bool(flags & 0x80)
        if len(data) >= 12:
            info["error_flags"] = int(data[10:12], 16)
        if len(data) >= 16:
            info["self_diag"] = decode_words(data[12:16])[0]
        return info

    def read_program_steps(self, start: int, count: int) -> list:
        """RP command: one 16-bit word per program step (little-endian, like DT)."""
        data = self._transact(f"RP{start:05d}{start + count - 1:05d}", "RP", data_len=4 * count)
        return decode_words(data)

    def read_program(self, max_steps: int, progress=None, pause: float = 0.02) -> list:
        """Read the program until max_steps or the FFFF fill after the last step.

        progress(start, end) is called before each block, if given.
        Trailing FFFF words are removed.
        """
        words = []
        start = 0
        ffff_run = 0
        while start < max_steps:
            n = min(PROGRAM_STEPS_PER_FRAME, max_steps - start)
            if progress:
                progress(start, start + n - 1)
            block = self.read_program_steps(start, n)
            words += block
            for w in block:
                ffff_run = ffff_run + 1 if w == 0xFFFF else 0
            if ffff_run >= END_OF_PROGRAM_FFFF:
                break
            start += n
            time.sleep(pause)
        while words and words[-1] == 0xFFFF:
            words.pop()
        return words
