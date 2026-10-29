"""Omron CJ1M driver: Host Link (C-mode) over the peripheral port.

PLC settings (see docs/rapport-technique-2026-10-23.md §4.2): peripheral port in
Host Link, 9600 baud, 7 data bits, even parity, 2 stop bits, unit 0, DIP 4 ON.

Frame:  @ + unit (2 digits) + header + text + FCS + '*' + CR
Every reply is checked before its data is used: terminator, FCS, header,
end code and length. A corrupted reply is retried; a PLC end code is not.

Words are hex, most significant digit first: "04D2" -> 1234 (no byte swap,
unlike Panasonic). 32-bit values are two words, low word first.
"""
from ..checksum import xor_checksum

# A single-frame RD reply holds up to 30 words; longer reads are split.
MAX_WORDS_PER_FRAME = 30

END_CODES = {
    "01": "impossible en mode RUN",
    "02": "impossible en mode MONITOR",
    "04": "dépassement d'adresse",
    "0B": "impossible en mode PROGRAM",
    "13": "erreur FCS (trame envoyée mal reçue)",
    "14": "erreur de format",
    "15": "erreur de donnée (adresse ou nombre invalide)",
    "18": "trame trop longue",
    "A3": "abandon : erreur FCS en transmission",
}


class HostLinkError(Exception):
    """Missing, corrupted or unexpected reply."""


class PlcRefused(HostLinkError):
    """The PLC answered with a non-zero end code."""

    def __init__(self, code: str, reply: str):
        meaning = END_CODES.get(code, "code inconnu")
        super().__init__(f"l'automate a refusé la commande (code {code} : {meaning}) : {reply!r}")
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


class Cj1m:
    """One Omron PLC on an open serial port."""

    def __init__(self, ser, unit: int = 0, retries: int = 2):
        self.ser = ser
        self.unit = unit
        self.retries = retries

    def _frame(self, header: str, text: str) -> str:
        body = f"@{self.unit:02d}{header}{text}"
        return body + xor_checksum(body) + "*\r"

    def _exchange(self, frame: str) -> str:
        self.ser.reset_input_buffer()
        self.ser.write(frame.encode("ascii"))
        return self.ser.read_until(b"\r").decode("ascii", "replace")

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
            reply = self._exchange(self._frame(header, text))
            try:
                data = self._check_reply(reply, header)
            except PlcRefused:
                raise
            except HostLinkError as e:
                last = e
                continue
            if data_len is not None and len(data) != data_len:
                last = HostLinkError(
                    f"réponse coupée : {len(data) // 4} mots reçus au lieu de {data_len // 4} : {reply!r}")
                continue
            return data
        raise last

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
        """Read D[start] .. D[start + count - 1], in blocks of at most 30 words."""
        words = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_WORDS_PER_FRAME, end - addr)
            words += decode_words(self._transact("RD", f"{addr:04d}{n:04d}", data_len=4 * n))
            addr += n
        return words
