"""Schneider Modicon driver: Modbus RTU (serial) or Modbus TCP (Ethernet).

Schneider mapping (usual, check per model):
  %MW n  -> holding register n   (function 03; some tools call it 40001 + n)
  %M n   -> coil n               (function 01)
  %I/%Q  -> depends on the model: often not in the Modbus map, or only through
            a mapping table configured in the project.

Every reply is checked before its data is used: CRC (RTU) or MBAP header (TCP),
slave number, function code and byte count. A corrupted reply is retried;
a Modbus exception from the PLC is not.

Registers are big-endian (high byte first). Schneider 32-bit values (%MD, %MF)
are two registers, low word first.
"""
import socket
import struct

MAX_REGISTERS = 125   # per request (Modbus limit)
MAX_BITS = 2000

READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING, READ_INPUT_REGISTERS = 1, 2, 3, 4
WRITE_SINGLE_REGISTER = 6

EXCEPTIONS = {
    1: "fonction non supportée",
    2: "adresse invalide (hors de la table Modbus de l'automate)",
    3: "valeur ou nombre invalide",
    4: "défaut de l'automate",
    6: "automate occupé",
    10: "passerelle : chemin indisponible",
    11: "passerelle : pas de réponse de l'esclave",
}


class ModbusError(Exception):
    """Missing, corrupted or unexpected reply."""


class PlcRefused(ModbusError):
    """The PLC answered with a Modbus exception."""

    def __init__(self, code: int):
        super().__init__(f"l'automate a refusé la requête (exception {code} : "
                         f"{EXCEPTIONS.get(code, 'code inconnu')})")
        self.code = code


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def open_serial(port: str, baud: int = 19200, parity: str = "E", stopbits: int = 1,
                timeout: float = 1.0):
    """Serial port for Modbus RTU: 8 data bits; parity and stop bits as set in the PLC."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, bytesize=8, parity=parity, stopbits=stopbits, timeout=timeout)


def dint_lw(words: list, i: int) -> int:
    """Signed 32-bit value, Schneider order: words[i] low, words[i + 1] high."""
    v = words[i] | (words[i + 1] << 16)
    return v - (1 << 32) if v & 0x80000000 else v


def real_lw(words: list, i: int) -> float:
    """32-bit float (%MF), low word first."""
    return struct.unpack("<f", struct.pack("<HH", words[i], words[i + 1]))[0]


def signed16(w: int) -> int:
    return w - 0x10000 if w & 0x8000 else w


# ---------- transports: carry one PDU (function code + data) to a slave and back ----------

class RtuTransport:
    """Modbus RTU over a serial port: slave + PDU + CRC (low byte first)."""

    def __init__(self, ser):
        self.ser = ser

    def exchange(self, unit: int, pdu: bytes) -> bytes:
        adu = bytes([unit]) + pdu
        self.ser.reset_input_buffer()
        self.ser.write(adu + struct.pack("<H", crc16(adu)))

        head = self.ser.read(3)
        if len(head) < 3:
            raise ModbusError("pas de réponse (timeout)" if not head else f"réponse trop courte : {head.hex()}")
        func = head[1]
        if func & 0x80:
            rest_len = 2                      # exception code already in head[2], then CRC
        elif func in (READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING, READ_INPUT_REGISTERS):
            rest_len = head[2] + 2            # byte count, then CRC
        elif func == WRITE_SINGLE_REGISTER:
            rest_len = 5                      # rest of the echo, then CRC
        else:
            raise ModbusError(f"code fonction inattendu : {head.hex()}")
        rest = self.ser.read(rest_len)
        frame = head + rest
        if len(rest) < rest_len:
            raise ModbusError(f"réponse coupée : {frame.hex()}")
        body, crc = frame[:-2], struct.unpack("<H", frame[-2:])[0]
        if crc16(body) != crc:
            raise ModbusError(f"CRC faux : {frame.hex()}")
        if body[0] != unit:
            raise ModbusError(f"réponse d'un autre esclave ({body[0]} au lieu de {unit})")
        return body[1:]

    def close(self):
        self.ser.close()


class TcpTransport:
    """Modbus TCP: MBAP header (transaction, protocol 0, length, unit) + PDU. Port 502."""

    def __init__(self, host: str, port: int = 502, timeout: float = 2.0, sock=None):
        self.sock = sock or socket.create_connection((host, port), timeout=timeout)
        self.tid = 0

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
            except socket.timeout:
                chunk = b""
            if not chunk:
                raise ModbusError("pas de réponse (timeout)" if not data else f"réponse coupée : {data.hex()}")
            data += chunk
        return data

    def exchange(self, unit: int, pdu: bytes) -> bytes:
        self.tid = (self.tid + 1) & 0xFFFF
        self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, unit) + pdu)
        tid, proto, length, r_unit = struct.unpack(">HHHB", self._recv_exact(7))
        if length < 2 or length > 254:
            raise ModbusError(f"longueur MBAP invalide : {length}")
        body = self._recv_exact(length - 1)
        if tid != self.tid or proto != 0:
            raise ModbusError(f"en-tête MBAP inattendu (transaction {tid}, protocole {proto})")
        if r_unit != unit:
            raise ModbusError(f"réponse d'une autre unité ({r_unit} au lieu de {unit})")
        return body

    def close(self):
        self.sock.close()


# ---------- the device ----------

class Modicon:
    """One Modbus slave (unit) reached through a transport."""

    def __init__(self, transport, unit: int = 1, retries: int = 2):
        self.transport = transport
        self.unit = unit
        self.retries = retries

    def _request(self, pdu: bytes, check, retry: bool = True) -> bytes:
        """Send pdu, return the reply data after check(reply) passes."""
        last = None
        for _ in range(1 + (self.retries if retry else 0)):
            try:
                reply = self.transport.exchange(self.unit, pdu)
            except ModbusError as e:
                last = e
                continue
            if not reply:
                last = ModbusError("réponse vide")
                continue
            if reply[0] == pdu[0] | 0x80:
                raise PlcRefused(reply[1] if len(reply) > 1 else 0)
            if reply[0] != pdu[0]:
                last = ModbusError(f"code fonction inattendu ({reply[0]} au lieu de {pdu[0]})")
                continue
            try:
                return check(reply[1:])
            except ModbusError as e:
                last = e
        raise last

    def _read(self, func: int, start: int, count: int, nbytes: int) -> bytes:
        def check(data):
            if not data or data[0] != nbytes or len(data) != nbytes + 1:
                raise ModbusError(f"réponse coupée : {len(data) - 1 if data else 0} octets "
                                  f"de données au lieu de {nbytes}")
            return data[1:]
        return self._request(struct.pack(">BHH", func, start, count), check)

    def _read_registers(self, func, start, count):
        words = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_REGISTERS, end - addr)
            data = self._read(func, addr, n, 2 * n)
            words += struct.unpack(f">{n}H", data)
            addr += n
        return words

    def _read_bits(self, func, start, count):
        bits = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_BITS, end - addr)
            data = self._read(func, addr, n, (n + 7) // 8)
            bits += [bool(data[i // 8] >> (i % 8) & 1) for i in range(n)]
            addr += n
        return bits

    def read_holding(self, start: int, count: int) -> list:
        """%MW start .. start + count - 1 (function 03)."""
        return self._read_registers(READ_HOLDING, start, count)

    def read_input_registers(self, start: int, count: int) -> list:
        """Function 04."""
        return self._read_registers(READ_INPUT_REGISTERS, start, count)

    def read_coils(self, start: int, count: int) -> list:
        """%M start .. start + count - 1 (function 01)."""
        return self._read_bits(READ_COILS, start, count)

    def read_discrete_inputs(self, start: int, count: int) -> list:
        """Function 02."""
        return self._read_bits(READ_DISCRETE_INPUTS, start, count)

    def write_register(self, addr: int, value: int) -> None:
        """Write one %MW (function 06). Not retried: the caller re-reads to confirm."""
        pdu = struct.pack(">BHH", WRITE_SINGLE_REGISTER, addr, value & 0xFFFF)

        def check(data):
            if data != pdu[1:]:
                raise ModbusError(f"écho d'écriture différent : {data.hex()}")
            return data
        self._request(pdu, check, retry=False)

    def close(self):
        self.transport.close()
