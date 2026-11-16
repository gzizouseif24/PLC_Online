"""Siemens S7-200 over PPI (RS-232/PPI cable on a COM port), in Python, no Siemens software needed.

Built from libnodave 0.8.5 (nodave.c, testPPI.c, testPPIload.c), which reads, writes, stops / starts
and uploads blocks from S7-200 CPUs over PPI.

Link (PPI, 9600 bauds 8E1, PLC address 2, PC address 0):
  request   68 L L 68  DA SA 6C  <S7 PDU>  FCS 16     L = 3 + PDU length, FCS = sum(DA..PDU) & FF
  PLC       E5                                         short acknowledgement: request accepted
  PC        10 DA SA 5C FCS 16                         "request data" (FC 5C or 7C, alternating if E5 again)
  PLC       68 L L 68  SA DA 08  <S7 PDU>  FCS 16      the reply
S7 PDU: 32 type 00 00 ref(2) param_len(2) data_len(2) [error(2) for replies type 2 / 3] param data.

S7-200 memory areas (S7 area codes, libnodave nodave.h): I 81, Q 82, M 83, V = DB1 in area 84,
SM 05, AI 06, AQ 07, IEC counters 1E, IEC timers 1F.
"""
import time

SD2, SD1, SYN, SC = 0x68, 0x10, 0x16, 0xE5

AREAS = {"I": (0x81, 0), "Q": (0x82, 0), "M": (0x83, 0), "V": (0x84, 1), "SM": (0x05, 0),
         "AI": (0x06, 0), "AQ": (0x07, 0)}
TIMER200, COUNTER200 = 0x1F, 0x1E

# block types of the S7 upload request (letters of the S7 "file name")
BLOCK_TYPES = {"OB": 0x38, "DB": 0x41, "SDB": 0x42}

PDU_ERRORS = {0x8104: "fonction non supportee", 0x8500: "PDU trop long", 0xD209: "bloc inexistant",
              0xD20E: "pas de bloc", 0xD210: "bloc en cours de modification", 0x8404: "erreur de contexte",
              0xD802: "fonction impossible dans ce mode (RUN / STOP ?)", 0xD60A: "mot de passe necessaire",
              0x8000: "fonction occupee"}
ITEM_ERRORS = {0x05: "adresse hors zone", 0x06: "type de donnee non supporte", 0x07: "type incoherent",
               0x0A: "objet inexistant", 0x03: "acces refuse", 0x01: "erreur materiel"}


class PpiError(Exception):
    """Missing, corrupted or unexpected reply."""


class PlcRefused(PpiError):
    """The PLC answered with an error code."""

    def __init__(self, text, code=0):
        super().__init__(text)
        self.code = code


def fcs(data) -> int:
    return sum(data) & 0xFF


def sd2_frame(da: int, sa: int, fc: int, pdu: bytes) -> bytes:
    body = bytes([da, sa, fc]) + pdu
    return bytes([SD2, len(body), len(body), SD2]) + body + bytes([fcs(body), SYN])


def sd1_frame(da: int, sa: int, fc: int) -> bytes:
    body = bytes([da, sa, fc])
    return bytes([SD1]) + body + bytes([fcs(body), SYN])


def s7_header(ref: int, param: bytes, data: bytes = b"", pdu_type: int = 1) -> bytes:
    return (bytes([0x32, pdu_type, 0, 0, ref >> 8 & 0xFF, ref & 0xFF, len(param) >> 8, len(param) & 0xFF,
                   len(data) >> 8, len(data) & 0xFF]) + param + data)


def parse_pdu(pdu: bytes) -> dict:
    if len(pdu) < 10 or pdu[0] != 0x32:
        raise PpiError(f"PDU S7 illisible : {pdu[:16].hex(' ')}")
    kind = pdu[1]
    hlen = 12 if kind in (2, 3) else 10
    plen, dlen = pdu[6] << 8 | pdu[7], pdu[8] << 8 | pdu[9]
    if len(pdu) < hlen + plen + dlen:
        raise PpiError(f"PDU S7 coupe : {len(pdu)} octets au lieu de {hlen + plen + dlen}")
    error = (pdu[10] << 8 | pdu[11]) if hlen == 12 else 0
    return {"type": kind, "ref": pdu[4] << 8 | pdu[5], "erreur": error,
            "param": pdu[hlen:hlen + plen], "data": pdu[hlen + plen:hlen + plen + dlen]}


def open_port(port: str, baud: int = 9600, timeout: float = 0.3):
    """RS-232/PPI cable: 8 data bits, even parity, 1 stop bit."""
    import serial  # imported here so the rest of the module works without pyserial

    return serial.Serial(port, baud, bytesize=8, parity="E", stopbits=1, timeout=timeout)


class Ppi:
    """PPI master (the PC) talking to one S7-200."""

    def __init__(self, ser, plc: int = 2, local: int = 0, retries: int = 2, log=None):
        self.ser, self.plc, self.local, self.retries = ser, plc, local, retries
        self.log = log
        self.ref = 0

    def _read_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            got = self.ser.read(n - len(out))
            if not got:
                break
            out += got
        return out

    def _reply(self) -> bytes:
        """Ask for the reply (request data) until the PLC sends an SD2 frame; returns the S7 PDU."""
        fc = 0x5C
        for _ in range(30):
            self.ser.write(sd1_frame(self.plc, self.local, fc))
            first = self._read_exact(1)
            if not first:
                raise PpiError("pas de reponse a la demande de donnees")
            if first[0] == SC:                       # not ready yet: ask again, other FC (as libnodave)
                fc = 0x7C if fc == 0x5C else 0x5C
                time.sleep(0.01)
                continue
            head = first + self._read_exact(3)
            if len(head) < 4 or head[0] != SD2 or head[3] != SD2 or head[1] != head[2]:
                raise PpiError(f"trame inattendue : {head.hex(' ')}")
            rest = self._read_exact(head[1] + 2)
            if len(rest) != head[1] + 2 or rest[-1] != SYN:
                raise PpiError(f"trame coupee : {(head + rest).hex(' ')}")
            body = rest[:-2]
            if fcs(body) != rest[-2]:
                raise PpiError("somme de controle fausse")
            if self.log:
                self.log("<-", head + rest)
            return body[3:]                           # skip DA SA FC
        raise PpiError("l'automate repond toujours 'pas pret' (E5)")

    def exchange(self, param: bytes, data: bytes = b"", pdu_type: int = 1) -> dict:
        self.ref = (self.ref + 1) & 0xFFFF or 1
        frame = sd2_frame(self.plc, self.local, 0x6C, s7_header(self.ref, param, data, pdu_type))
        error = None
        for _ in range(self.retries + 1):
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            if self.log:
                self.log("->", frame)
            ack = self._read_exact(1)
            if not ack:
                error = PpiError("pas de reponse (cable PPI branche ? adresse de l'automate ? vitesse ?)")
                continue
            if ack[0] != SC:
                error = PpiError(f"acquittement inattendu : {ack.hex()}")
                time.sleep(0.05)
                continue
            try:
                return parse_pdu(self._reply())
            except PpiError as e:
                error = e
        raise error

    def close(self):
        self.ser.close()


class S7200:
    """S7-200 CPU: read / write memory, RUN / STOP, list and upload blocks."""

    nom = "Siemens S7-200"

    def __init__(self, ppi: Ppi):
        self.ppi = ppi
        self.pdu_size = 240

    def close(self):
        self.ppi.close()

    def _call(self, param: bytes, data: bytes = b"", pdu_type: int = 1) -> dict:
        r = self.ppi.exchange(param, data, pdu_type)
        if r["erreur"]:
            raise PlcRefused(f"refuse par l'automate (code {r['erreur']:04X} : "
                             f"{PDU_ERRORS.get(r['erreur'], 'inconnu')})", r["erreur"])
        return r

    def connect(self):
        """Negotiate the PDU size (S7 'setup communication', F0)."""
        r = self._call(bytes([0xF0, 0, 0, 1, 0, 1, 0x07, 0x80]))
        if len(r["param"]) >= 8:
            self.pdu_size = min(r["param"][6] << 8 | r["param"][7], 960) or 240
        return self

    # ---- memory ----

    def _item(self, area: str, start: int, count: int, bit=None, writing=False) -> bytes:
        code, db = AREAS[area]
        if area in ("AI", "AQ"):                 # libnodave: length in bytes to read, in words to write
            transport, address, length = 4, start * 8, ((count + 1) // 2 if writing else count)
        elif bit is not None:
            transport, address, length = 1, start * 8 + bit, count
        else:
            transport, address, length = 2, start * 8, count
        return bytes([0x12, 0x0A, 0x10, transport, length >> 8, length & 0xFF, db >> 8, db & 0xFF, code,
                      address >> 16 & 0xFF, address >> 8 & 0xFF, address & 0xFF])

    @staticmethod
    def _check_item(data: bytes, what: str) -> bytes:
        if not data:
            raise PpiError(f"{what} : reponse vide")
        if data[0] != 0xFF:
            raise PlcRefused(f"{what} : {ITEM_ERRORS.get(data[0], f'code {data[0]:02X}')}", data[0])
        if len(data) < 4:
            raise PpiError(f"{what} : reponse coupee")
        n = data[2] << 8 | data[3]
        if data[1] in (3, 4, 5):
            n = (n + 7) // 8 if data[1] != 3 else n
        return data[4:4 + n]

    def read(self, area: str, start: int, count: int) -> bytes:
        """count bytes of I, Q, M, V, SM (AI / AQ: count bytes too, 2 per channel)."""
        if area not in AREAS:
            raise PpiError(f"zone {area} : {', '.join(AREAS)}")
        out, block = b"", self.pdu_size - 18
        while len(out) < count:
            n = min(block, count - len(out))
            what = f"{area}B{start + len(out)}..{start + len(out) + n - 1}"
            r = self._call(bytes([0x04, 1]) + self._item(area, start + len(out), n))
            got = self._check_item(r["data"], what)
            if len(got) != n:
                raise PpiError(f"{what} : {len(got)} octets recus au lieu de {n}")
            out += got
        return out

    def write(self, area: str, start: int, data: bytes):
        block = self.pdu_size - 28
        for i in range(0, len(data), block):
            part = data[i:i + block]
            head = bytes([0, 4, len(part) * 8 >> 8, len(part) * 8 & 0xFF])
            r = self._call(bytes([0x05, 1]) + self._item(area, start + i, len(part), writing=True), head + part)
            if not r["data"] or r["data"][0] != 0xFF:
                code = r["data"][0] if r["data"] else 0
                raise PlcRefused(f"ecriture {area}B{start + i} : {ITEM_ERRORS.get(code, f'code {code:02X}')}", code)

    def write_bit(self, area: str, byte: int, bit: int, value: bool):
        r = self._call(bytes([0x05, 1]) + self._item(area, byte, 1, bit), bytes([0, 3, 0, 1, 1 if value else 0]))
        if not r["data"] or r["data"][0] != 0xFF:
            code = r["data"][0] if r["data"] else 0
            raise PlcRefused(f"ecriture {area}{byte}.{bit} : {ITEM_ERRORS.get(code, f'code {code:02X}')}", code)

    def read_raw_area(self, code: int, start: int, count: int) -> bytes:
        """Any area code (e.g. 03 'system info of the 200 family', libnodave daveSysInfo), for research."""
        item = bytes([0x12, 0x0A, 0x10, 2, count >> 8, count & 0xFF, 0, 0, code, (start * 8) >> 16 & 0xFF,
                      (start * 8) >> 8 & 0xFF, (start * 8) & 0xFF])
        return self._check_item(self._call(bytes([0x04, 1]) + item)["data"], f"zone {code:02X}")

    # ---- RUN / STOP (libnodave daveStart / daveStop) ----

    def start(self):
        self._call(bytes([0x28, 0, 0, 0, 0, 0, 0, 0xFD, 0, 0, 9]) + b"P_PROGRAM")

    def stop(self):
        self._call(bytes([0x29, 0, 0, 0, 0, 0, 9]) + b"P_PROGRAM")

    # ---- blocks (libnodave daveListBlocksOfType, initUpload / doUpload / endUpload) ----

    def list_blocks(self, kind: str) -> list:
        """Numbers of the blocks of one type ('OB', 'DB', 'SDB')."""
        r = self._call(bytes([0, 1, 0x12, 4, 0x11, 0x43, 2, 0]), bytes([0xFF, 9, 0, 2, 0x30, BLOCK_TYPES[kind]]),
                       pdu_type=7)
        entries = []
        while True:
            data = r["data"]
            if not data or data[0] != 0xFF:
                if data and data[0] == 0x0A:            # object does not exist: no block of this type
                    return entries
                raise PlcRefused(f"liste des blocs {kind} : code {data[0] if data else 0:02X}", data[0] if data else 0)
            n = data[2] << 8 | data[3] if len(data) >= 4 else 0
            raw = data[4:4 + n]
            entries += [raw[i] << 8 | raw[i + 1] for i in range(0, len(raw) - 3, 4)]
            if len(r["param"]) < 10 or r["param"][9] == 0:     # no more data (libnodave: param[9])
                return entries
            r = self._call(bytes([0, 1, 0x12, 8, 0x12, 0x43, 2, r["param"][7], 0, 0, 0, 0]),
                           bytes([0x0A, 0, 0, 0]), pdu_type=7)

    def upload(self, kind: str, number: int, progress=None) -> bytes:
        name = bytes([0x5F, 0x30, BLOCK_TYPES[kind]]) + f"{number:05d}".encode() + b"A"
        r = self._call(bytes([0x1D, 0, 0, 0, 0, 0, 0, 0, 9]) + name)
        upload_id = r["param"][7] if len(r["param"]) > 7 else 1
        blob = b""
        try:
            while True:
                r = self._call(bytes([0x1E, 0, 0, 0, 0, 0, 0, upload_id]))
                n = r["data"][0] << 8 | r["data"][1] if len(r["data"]) >= 2 else 0
                blob += r["data"][4:4 + n]
                if progress:
                    progress(len(blob))
                if len(r["param"]) < 2 or not r["param"][1]:
                    break
        finally:
            self._call(bytes([0x1F, 0, 0, 0, 0, 0, 0, upload_id]))
        return blob
