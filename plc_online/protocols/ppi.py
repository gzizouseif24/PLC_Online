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
import random
import time
from collections import deque
from pathlib import Path

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

    def __init__(self, ser, plc: int = 2, local: int = 0, retries: int = 4, log=None):
        self.ser, self.plc, self.local, self.retries = ser, plc, local, retries
        self.log = log
        self.skipped = deque(maxlen=50)   # frames of other stations seen on the line (master PLC, HMI)
        self.journal = deque(maxlen=400)  # last frames both ways, written to journal_path when the link fails
        self.journal_path = None
        self.failures = 0                 # exchanges that needed a retry (shown in Diagnostic)
        self.ref = 0

    def _note(self, kind: str, data: bytes = b"", text: str = ""):
        self.journal.append((time.time(), kind, data.hex(" ") if data else text))

    def write_journal(self) -> str:
        if not self.journal_path:
            return ""
        path = Path(self.journal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t * 1000) % 1000:03d}  {k:8} {d}"
                 for t, k, d in self.journal]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _quiet(self, gap: float = 0.015, limit: float = 0.3):
        """Wait for a silent line before sending (another master may be talking), keeping what passes."""
        waiting = getattr(self.ser, "in_waiting", None)
        if waiting is None:
            self.ser.reset_input_buffer()
            return
        end, last = time.time() + limit, time.time()
        while time.time() < end:
            n = self.ser.in_waiting
            if n:
                self._note("ligne", self.ser.read(n))
                last = time.time()
            elif time.time() - last >= gap:
                return
            time.sleep(0.002)

    def _read_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            got = self.ser.read(n - len(out))
            if not got:
                break
            out += got
        return out

    def _next(self, wait: float = 1.0):
        """Next frame for us: ('E5', b''), ('SD2', S7 PDU) or ('SD1', function code: 02 = busy). Frames of other stations on the line are
        skipped: an S7-200 in PPI master mode (NETR / NETW) or an HMI polls every address with short
        frames (SD1: 10 DA SA FC FCS 16) and passes the token (SD4: DC DA SA). Returns None on timeout."""
        deadline = time.time() + wait
        while time.time() < deadline:
            first = self._read_exact(1)
            if not first:
                continue
            b = first[0]
            if b == SC:
                self._note("<- E5")
                return "E5", b""
            if b == SD1:
                frame = first + self._read_exact(5)
                # a short ANSWER of our PLC (bit 6 = 0, e.g. 02 = busy); a request (bit 6 = 1, e.g. 49 = status
                # poll of a PLC in PPI master mode) is skipped like the frames of other stations
                if len(frame) == 6 and frame[1] == self.local and frame[2] == self.plc and not frame[3] & 0x40:
                    self._note("<- SD1", frame)
                    return "SD1", bytes([frame[3]])
                self.skipped.append(frame)
                self._note("autre", frame)
                continue
            if b == 0xDC:                                # SD4 token frame
                frame = first + self._read_exact(2)
                self.skipped.append(frame)
                self._note("jeton", frame)
                continue
            if b != SD2:
                self.skipped.append(first)               # stray byte
                self._note("octet", first)
                continue
            head = first + self._read_exact(3)
            if len(head) < 4 or head[3] != SD2 or head[1] != head[2]:
                self.skipped.append(head)
                self._note("octets", head)
                continue
            rest = self._read_exact(head[1] + 2)
            if len(rest) != head[1] + 2 or rest[-1] != SYN:
                self._note("coupee", head + rest)
                raise PpiError(f"trame coupee : {(head + rest).hex(' ')}")
            body = rest[:-2]
            if fcs(body) != rest[-2]:
                self._note("FCS?", head + rest)
                raise PpiError("somme de controle fausse")
            if body[0] != self.local or body[1] != self.plc:   # SD2 between other stations
                self.skipped.append(head + rest)
                self._note("autre", head + rest)
                continue
            self._note("<-", head + rest)
            if self.log:
                self.log("<-", head + rest)
            return "SD2", body[3:]                       # skip DA SA FC
        return None

    TURNAROUND = 0.012         # idle line before each frame: PROFIBUS Tsyn = 33 bits (3.4 ms at 9600), with margin

    def _ask(self, fc: int):
        time.sleep(self.TURNAROUND)
        frame = sd1_frame(self.plc, self.local, fc)
        self.ser.write(frame)
        self._note("-> SD1", frame)

    def _reply(self) -> bytes:
        """Ask for the reply (request data) until the PLC sends its SD2 frame; returns the S7 PDU.
        A lost reply is asked again (the PLC keeps it until it is collected)."""
        fc, lost = 0x5C, 0
        for _ in range(40):
            self._ask(fc)
            got = self._next(0.6)
            if got is None:
                lost += 1
                if lost > 3:
                    raise PpiError("pas de reponse a la demande de donnees")
                self._note("perdu", text="reponse perdue : nouvelle demande")
                continue
            if got[0] in ("E5", "SD1"):                  # not ready yet: ask again, other FC (as libnodave)
                fc = 0x7C if fc == 0x5C else 0x5C
                time.sleep(0.02)
                continue
            return got[1]
        raise PpiError("l'automate repond toujours 'pas pret'")

    def _collect_pending(self):
        """The PLC answered 'busy' (SD1 FC 02): it still holds the reply to an earlier request whose
        reply was lost. Collect it and drop it, so the PLC accepts the next request."""
        try:
            self._reply()
            self._note("vide", text="reponse en attente recuperee et ignoree")
        except PpiError:
            pass

    def exchange(self, param: bytes, data: bytes = b"", pdu_type: int = 1) -> dict:
        self.ref = (self.ref + 1) & 0xFFFF or 1
        frame = sd2_frame(self.plc, self.local, 0x6C, s7_header(self.ref, param, data, pdu_type))
        error = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.failures += 1
                self._note("ERREUR", text=str(error))
                time.sleep(random.uniform(0.03, 0.15))      # other master on the line: try at another moment
            self._quiet()
            time.sleep(self.TURNAROUND)
            self.ser.write(frame)
            self._note("->", frame)
            if self.log:
                self.log("->", frame)
            try:
                got = self._next()
                if got is None:
                    error = PpiError("pas de reponse (cable PPI branche ? adresse de l'automate ? vitesse ?)")
                    continue
                if got[0] == "SD1":
                    error = PpiError(f"automate occupe (code {got[1][0]:02X}) : reponse precedente pas recuperee")
                    self._collect_pending()
                    continue
                if got[0] != "E5":
                    error = PpiError("reponse sans acquittement E5")
                    continue
                reply = parse_pdu(self._reply())
                if reply["ref"] != self.ref:                 # an older reply: ours was not taken
                    error = PpiError(f"reponse a une autre demande (n° {reply['ref']} au lieu de {self.ref})")
                    continue
                return reply
            except PpiError as e:
                error = e
        self._note("ECHEC", text=str(error))
        path = self.write_journal()
        raise PpiError(f"{error} (apres {self.retries + 1} essais ; journal des trames : {path})" if path else str(error))

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

    # ---- outputs in STOP (libnodave daveForce200) ----

    def set_outputs_in_stop(self, area: str, byte: int, value: int):
        """Set one output byte while the CPU is in STOP (libnodave daveForce200, ChangeLog 29/04/2005): not a
        force, gone as soon as the CPU goes to RUN; back in STOP all outputs are 0 again."""
        code, _ = AREAS[area]
        address = byte * 8
        item = bytes([0, 1, 0x10, 2, 0, 1, 0, 0, code, address >> 16 & 0xFF, address >> 8 & 0xFF, address & 0xFF])
        data = bytes([0xFF, 9, 0, len(item)]) + item + bytes([0, 4, 0, 8, value & 0xFF, 0])
        r = self._call(bytes([0, 1, 0x12, 8, 0x12, 0x48, 0x0E, 0, 0, 0, 0, 0]), data, pdu_type=7)
        code = r["param"][10] << 8 | r["param"][11] if len(r["param"]) >= 12 else 0
        if code:
            raise PlcRefused(f"sorties en STOP : refuse (code {code:04X})", code)

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
        """Upload one block. The S7 file name ends with 'A' (libnodave); when the CPU answers 'block does not
        exist' (D209), 'P' and 'B' are tried too (other S7 file systems), to learn which the S7-200 uses."""
        r = None
        for system in b"APB":
            name = bytes([0x5F, 0x30, BLOCK_TYPES[kind]]) + f"{number:05d}".encode() + bytes([system])
            try:
                r = self._call(bytes([0x1D, 0, 0, 0, 0, 0, 0, 0, 9]) + name)
                self.upload_system = chr(system)
                break
            except PlcRefused as e:
                if e.code != 0xD209 or system == ord("B"):
                    raise
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
