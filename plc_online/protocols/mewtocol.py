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


class PlcRefused(MewtocolError):
    """The PLC answered with an error code (%01!xx)."""

    def __init__(self, code: str, reply: str):
        super().__init__(f"l'automate a refusé la commande (code {code}) : {reply!r}")
        self.code = code


def open_port(port: str, baud: int = 9600, parity: str = "O", timeout: float = 1.0):
    """Open the serial port with the FP0R tool-port settings (8 data bits, 1 stop)."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, parity=parity, bytesize=8, stopbits=1, timeout=timeout)


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

    # ---------- status and program ----------

    def read_status(self) -> dict:
        """RT command: model, version, program capacity, mode."""
        data = self._transact("RT", "RT")
        info = {}
        if len(data) >= 4:
            info["model"] = data[:4]
        if len(data) >= 8:
            info["version"] = data[4:8]
        if len(data) >= 12:
            info["capacity_k"] = int(data[8:12], 16)
            info["capacity_steps"] = info["capacity_k"] * 1024
        if len(data) >= 14:
            info["mode"] = {"00": "PROG", "01": "RUN", "02": "RUN"}.get(data[12:14], data[12:14])
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
