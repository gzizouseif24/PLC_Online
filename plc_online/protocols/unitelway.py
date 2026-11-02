"""Uni-Telway (UNI-TE over X-WAY) for TSX Micro / Premium through the TER port: the PC is a
slave polled by the PLC, as PL7 does. Used by tools/tsx.py and the web interface.
Sequences and parameters as captured from PL7 (docs/unitelway.md).
"""
import re
import time

DLE, STX, EOT, ENQ, ACK, NAK = 0x10, 0x02, 0x04, 0x05, 0x06, 0x15
MAX_LENGTH = 134

# En-tete X-WAY d'une requete esclave -> automate : message standard (0x20),
# puis adresse 5 niveaux de l'automate local : reseau 0, station 0xFE, porte 0, 0, 0.
XWAY_TO_PLC = [0x20, 0x00, 0xFE, 0x00, 0x00, 0x00]

MIRROR, IDENTIFICATION, STATUS = 0xFA, 0x0F, 0x31
ANSWER = {MIRROR: 0xFB, IDENTIFICATION: 0x3F, STATUS: 0x61}
NEGATIVE = 0xFD


def hexs(data) -> str:
    return " ".join(f"{x:02X}" for x in data)


# ---------- trames ----------

def build_frame(addr: int, payload: list) -> bytes:
    """DLE STX adresse longueur [DLE] donnees (DLE doubles) BCC = somme modulo 256."""
    frame = [DLE, STX, addr]
    if len(payload) == DLE:
        frame.append(DLE)
    frame.append(len(payload))
    for b in payload:
        frame.append(b)
        if b == DLE:
            frame.append(DLE)
    frame.append(sum(frame) % 256)
    return bytes(frame)


class Parser:
    """Decoupe le flux recu : scrutation, trame, EOT, ACK, NAK, octet isole."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list:
        self.buf += data
        events = []
        while self.buf:
            ev, used = self._next()
            if used == 0:
                break
            del self.buf[:used]
            events.append(ev)
        return events

    def _next(self):
        b = self.buf
        if b[0] in (EOT, ACK, NAK):
            return ({EOT: ("eot",), ACK: ("ack",), NAK: ("nak",)}[b[0]]), 1
        if b[0] != DLE:
            return ("stray", b[0]), 1
        if len(b) < 2:
            return None, 0
        if b[1] == ENQ:
            return (("poll", b[2]), 3) if len(b) >= 3 else (None, 0)
        if b[1] != STX:
            return ("stray", b[0]), 1
        if len(b) < 4:
            return None, 0
        length, i = b[3], 4
        if length == DLE:
            if len(b) < 5:
                return None, 0
            if b[4] != DLE:
                return ("stray", b[0]), 1
            i = 5
        if not 1 <= length <= MAX_LENGTH:
            return ("stray", b[0]), 1
        data = bytearray()
        while len(data) < length:
            if i >= len(b):
                return None, 0
            if b[i] == DLE:
                if i + 1 >= len(b):
                    return None, 0
                if b[i + 1] != DLE:
                    return ("stray", b[0]), 1
                i += 1
            data.append(b[i])
            i += 1
        if i >= len(b):
            return None, 0
        raw = bytes(b[:i + 1])
        return ("frame", b[2], bytes(data), sum(raw[:-1]) % 256 == raw[-1], raw), i + 1


class UniteError(Exception):
    pass


# ---------- esclave Uni-Telway ----------

class Slave:
    """Le PC repond aux scrutations de plusieurs adresses (comme PL7) : plus de requetes par cycle."""

    def __init__(self, ser, addrs=(1,), category=0, trace=False):
        self.ser, self.addrs, self.category, self.trace = ser, tuple(addrs), category, trace
        self.parser = Parser()
        self.t0 = time.time()

    def log(self, direction, data):
        if self.trace:
            print(f"   {time.time() - self.t0:8.3f} {direction} {hexs(data)}")

    def send(self, data: bytes):
        self.log("PC  ->", data)
        self.ser.write(data)

    def request(self, unite: list, timeout: float = 10.0) -> list:
        """Envoie une requete UNI-TE a la prochaine scrutation d'une de nos adresses."""
        payload = XWAY_TO_PLC + list(unite)
        state, frame, used = "attente scrutation", None, None
        nak = resent = 0
        deadline = time.time() + timeout
        # Oublier les scrutations accumulees pendant l'attente (ex. saisie de OUI) :
        # repondre a une scrutation perimee ferait parler le PC hors de son tour.
        self.ser.reset_input_buffer()
        self.parser = Parser()
        while time.time() < deadline:
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                self.log("API ->", data)
            for ev in self.parser.feed(data):
                kind = ev[0]
                if kind == "poll" and ev[1] == used and state == "attente ACK":
                    resent += 1
                    if resent > 2:
                        raise UniteError(f"l'automate ne confirme pas la trame (adresse {used}, renvoyee 2 fois)")
                    self.send(frame)  # scrute a nouveau sans ACK : trame perdue, on la renvoie
                elif kind == "poll" and ev[1] in self.addrs and state == "attente reponse":
                    self.send(bytes([EOT]))  # rien a dire : EOT, comme PL7 (cycle plus rapide)
                elif kind == "poll" and ev[1] in self.addrs and state == "attente scrutation":
                    used = ev[1]
                    frame = build_frame(used, payload)
                    self.send(frame)
                    state = "attente ACK"
                elif kind == "frame" and ev[4] == frame:
                    pass  # echo de notre propre trame par le convertisseur RS-485
                elif kind == "ack" and state == "attente ACK":
                    state = "attente reponse"
                elif kind == "nak" and state == "attente ACK":
                    nak += 1
                    if nak >= 3:
                        raise UniteError("l'automate a refuse la trame 3 fois (NAK)")
                    state = "attente scrutation"
                elif kind == "frame" and ev[1] == used and state in ("attente ACK", "attente reponse"):
                    if not ev[3]:
                        self.send(bytes([NAK]))
                        continue
                    self.send(bytes([ACK]))
                    return self._unwrap(ev[2])
        raise UniteError(f"delai depasse, bloque a l'etape : {state}")

    @staticmethod
    def _unwrap(npdu: bytes) -> list:
        if not npdu:
            raise UniteError("trame vide")
        if npdu[0] == 0x22:
            raise UniteError(f"message refuse par X-WAY : {hexs(npdu)}")
        if npdu[0] == 0x20 and len(npdu) >= 6:
            return list(npdu[6:])
        if npdu[0] == 0x00:
            return list(npdu[1:])
        raise UniteError(f"en-tete X-WAY inconnu : {hexs(npdu)}")


# ---------- objets TSX ----------

READ_BIT, READ_SYSTEM_BIT = 0x00, 0x01
READ_OBJECTS, WRITE_OBJECTS = 0x36, 0x37
RUN, STOP = 0x24, 0x25
READ_CPU = 0x4F
READ_STATUS_MODULE = 0x44
MONITOR = 0x93           # requete de mise au point de PL7 (sous-codes a decoder)
WRITE_SYSTEM_BIT = 0x11  # liste des requetes UNI-TE (aide PL7) : READ_SYSTEM_BIT 01 / WRITE 11
DEBUG_MEMORY = 0x92      # memoire de mise au point de PL7 : 92 07 <01 lire|02 ecrire> <segment> 00 <offset> <n>
DEBUG_SYSTEM_WORDS = 0x58          # segment 58 : %SWn a l'offset 0x50 + 2n (%SW50 = 0xB4, capture 02/11)
CPU_IO_FAULT = 0x20      # bit de l'octet d'etat (READ_CPU / identification) : defaut E/S
OK_ANSWER = 0xFE
WORD, DWORD = 0x07, 0x08

# Sauvegarde : requetes et parametres exactement comme PL7 (capture du 29/10/2026)
PL7_CATEGORY = 0x07
RESERVE, RELEASE, APP_INFO = 0x1D, 0x1E, 0x92
OPEN_UPLOAD, READ_UPLOAD, CLOSE_UPLOAD = 0x3D, 0x3E, 0x3F
OPEN_DOWNLOAD, WRITE_DOWNLOAD, CLOSE_DOWNLOAD = 0x3A, 0x3B, 0x3C
DOWNLOAD_SEGMENT = 0x7A  # 122 octets par segment, comme PL7
RESERVE_UPLOAD = [0xFF, 0xFE, 0x90, 0x36]    # parametres de RESERVE vus avant un upload PL7
RESERVE_DOWNLOAD = [0xFF, 0xFE, 0x80, 0x0F]  # ... et avant un download PL7
SEGMENT_SIZE = 0x77      # 119 octets par segment, comme PL7
UPLOAD_LAST = 0x0F       # compte rendu du dernier segment
MAX_SEGMENTS = 4000

# zone -> (segment, type, taille en octets)
WORD_ZONES = {
    "MW": (0x68, WORD, 2), "MD": (0x68, DWORD, 4),
    "KW": (0x69, WORD, 2), "KD": (0x69, DWORD, 4),
    "SW": (0x6A, WORD, 2),
}
BIT_ZONES = {"M": READ_BIT, "S": READ_SYSTEM_BIT}
WRITABLE = {"MW", "MD"}
MAX_DATA = 120  # octets de donnees par requete (trame <= 134 octets)


class Tsx:
    def __init__(self, slave: Slave):
        self.slave = slave

    def _ask(self, unite, expect):
        answer = self.slave.request(unite)
        if not answer:
            raise UniteError("reponse vide")
        if answer[0] == NEGATIVE:
            raise UniteError("refuse par l'automate (FD)")
        if answer[0] != expect:
            raise UniteError(f"reponse inattendue : {hexs(answer)}")
        return answer[1:]

    def identification(self) -> dict:
        data = self._ask([IDENTIFICATION, self.slave.category], 0x3F)
        n = data[3] if len(data) > 3 else 0
        return {"nom": bytes(data[4:4 + n]).decode("ascii", "replace"), "brut": hexs(data)}

    def read(self, zone: str, start: int, count: int) -> list:
        if zone in BIT_ZONES:
            return self._read_bits(zone, start, count)
        seg, typ, size = WORD_ZONES[zone]
        values = []
        addr, end = start, start + count
        while addr < end:
            n = min(MAX_DATA // size, end - addr)
            data = self._ask([READ_OBJECTS, self.slave.category, seg, typ,
                              addr & 0xFF, addr >> 8, n & 0xFF, n >> 8], 0x66)
            if not data or data[0] != typ or len(data) - 1 != n * size:
                raise UniteError(f"reponse coupee ou inattendue : {hexs(data)}")
            raw = data[1:]
            for i in range(0, len(raw), size):
                v = int.from_bytes(bytes(raw[i:i + size]), "little")
                values.append(v)
            addr += n
        return values

    def _read_bits(self, zone, start, count) -> list:
        code = BIT_ZONES[zone]
        values = {}
        group = start - start % 8
        while group < start + count:
            data = self._ask([code, self.slave.category, group & 0xFF, group >> 8], code + 0x30)
            byte = data[0]  # %S : 1 octet ; %M : valeur puis forcage (ordre a confirmer)
            for i in range(8):
                values[group + i] = byte >> i & 1
            group += 8
        return [values[a] for a in range(start, start + count)]

    def write_words(self, zone: str, start: int, values: list) -> None:
        """Ecrit des mots consecutifs (%MW), par blocs de 60 au plus."""
        if zone != "MW":
            raise UniteError(f"ecriture en bloc non autorisee sur %{zone}")
        seg, typ, _ = WORD_ZONES[zone]
        i = 0
        while i < len(values):
            chunk = values[i:i + MAX_DATA // 2]
            addr = start + i
            data = [b for v in chunk for b in (v & 0xFFFF).to_bytes(2, "little")]
            self._ask([WRITE_OBJECTS, self.slave.category, seg, typ, addr & 0xFF, addr >> 8,
                       len(chunk) & 0xFF, len(chunk) >> 8, *data], OK_ANSWER)
            i += len(chunk)

    def write(self, zone: str, addr: int, value: int) -> None:
        if zone not in WRITABLE:
            raise UniteError(f"ecriture non autorisee sur %{zone}")
        seg, typ, size = WORD_ZONES[zone]
        data = list((value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
        self._ask([WRITE_OBJECTS, self.slave.category, seg, typ,
                   addr & 0xFF, addr >> 8, 1, 0, *data], OK_ANSWER)

    # ----- sauvegarde du programme (sequence relevee sur PL7) -----

    def reserve(self, params=RESERVE_UPLOAD) -> None:
        self._ask([RESERVE, PL7_CATEGORY, *params], OK_ANSWER)

    def release(self) -> None:
        self._ask([RELEASE, PL7_CATEGORY], OK_ANSWER)

    def app_info(self) -> dict:
        """Requete 92 bloc 0A (lue par PL7 avant le transfert) : nom et signature de l'application."""
        data = self._ask([APP_INFO, PL7_CATEGORY, 0x0A, 0, 0, 0, 0, 0, 0, 0x60], APP_INFO + 0x30)
        raw = bytes(data[2:])
        name = raw[:raw.index(0)] if 0 in raw else raw
        return {"nom": name.decode("ascii", "replace"), "brut": hexs(data)}

    def upload(self, progress=None) -> bytes:
        """Lit toute l'application : OPEN_UPLOAD, READ_UPLOAD segment par segment, CLOSE_UPLOAD."""
        self._ask([OPEN_UPLOAD, PL7_CATEGORY, 0, 0, 0, 0, 0, 0, 0, 0, SEGMENT_SIZE, 0], OPEN_UPLOAD + 0x30)
        blob = bytearray()
        try:
            seg = 1
            while True:
                data = self._ask([READ_UPLOAD, PL7_CATEGORY, seg & 0xFF, seg >> 8], READ_UPLOAD + 0x30)
                if len(data) < 5:
                    raise UniteError(f"segment {seg} : reponse trop courte {hexs(data)}")
                status, got, length = data[0], data[1] | data[2] << 8, data[3] | data[4] << 8
                chunk = bytes(data[5:])
                if got != seg or len(chunk) != length:
                    raise UniteError(f"segment {seg} : recu segment {got}, {len(chunk)} octets au lieu de {length}")
                if status not in (0x00, UPLOAD_LAST):
                    raise UniteError(f"segment {seg} : compte rendu {status:02X}")
                blob += chunk
                if progress:
                    progress(seg, len(blob))
                if status == UPLOAD_LAST:
                    return bytes(blob)
                seg += 1
                if seg > MAX_SEGMENTS:
                    raise UniteError("trop de segments : arret par securite")
        finally:
            try:
                self._ask([CLOSE_UPLOAD, PL7_CATEGORY], CLOSE_UPLOAD + 0x30)
            except UniteError:
                pass

    def download(self, blob: bytes, progress=None) -> None:
        """Ecrit toute l'application : OPEN_DOWNLOAD, WRITE_DOWNLOAD par segment, CLOSE_DOWNLOAD.
        L'automate doit etre en STOP et reserve."""
        self._ask([OPEN_DOWNLOAD, PL7_CATEGORY, 0, 0, 0, 0, 0, 0, 0, 1], OPEN_DOWNLOAD + 0x30)
        closed = False
        try:
            seg = 1
            for i in range(0, len(blob), DOWNLOAD_SEGMENT):
                chunk = blob[i:i + DOWNLOAD_SEGMENT]
                data = self._ask([WRITE_DOWNLOAD, PL7_CATEGORY, seg & 0xFF, seg >> 8,
                                  len(chunk) & 0xFF, len(chunk) >> 8, *chunk], WRITE_DOWNLOAD + 0x30)
                if len(data) < 3 or data[0] != 0 or (data[1] | data[2] << 8) != seg:
                    raise UniteError(f"segment {seg} refuse ou mal acquitte : {hexs(data)}")
                if progress:
                    progress(seg, i + len(chunk))
                seg += 1
            data = self._ask([CLOSE_DOWNLOAD, PL7_CATEGORY], CLOSE_DOWNLOAD + 0x30)
            closed = True
            if data[:1] != [0]:
                raise UniteError(f"fermeture du chargement refusee : {hexs(data)}")
        finally:
            if not closed:
                try:
                    self._ask([CLOSE_DOWNLOAD, PL7_CATEGORY], CLOSE_DOWNLOAD + 0x30)
                except UniteError:
                    pass

    def read_io_faults(self) -> list:
        """Defaut par position du rack (0..15), 1 = module en defaut. Requete vue dans PL7 en ligne
        (capture du 02/11/2026) : 93 07 02 68 00 04 02 10 -> C3 02 00 + 16 octets ; DEZ 32D2 non
        alimente en positions 1-2 -> 00 01 01 00 ..., configuration vide -> tout a 0."""
        request = [MONITOR, PL7_CATEGORY, 0x02, 0x68, 0x00, 0x04, 0x02, 0x10]
        try:
            data = self._ask(request, MONITOR + 0x30)
        except UniteError as e:
            if "FD" not in str(e):
                raise
            # PL7 always sends 93 07 0B first (-> C3 0B 00) when it goes online: debug session;
            # and it had reserved the PLC (1D) in the captures: try that last
            try:
                self._ask([MONITOR, PL7_CATEGORY, 0x0B], MONITOR + 0x30)
                data = self._ask(request, MONITOR + 0x30)
            except UniteError as e2:
                if "FD" not in str(e2):
                    raise
                self.reserve()
                try:
                    self._ask([MONITOR, PL7_CATEGORY, 0x0B], MONITOR + 0x30)
                    data = self._ask(request, MONITOR + 0x30)
                finally:
                    try:
                        self.release()
                    except UniteError:
                        pass
        if len(data) < 18 or data[:2] != [0x02, 0x00]:
            raise UniteError(f"defauts modules : reponse inattendue {hexs(data)}")
        return data[2:18]

    def write_system_bit(self, n: int, value: int) -> None:
        self._ask([WRITE_SYSTEM_BIT, self.slave.category, n & 0xFF, n >> 8, 1 if value else 0], OK_ANSWER)

    def write_system_words(self, start: int, values: list) -> str:
        """Ecrit des %SW : WRITE_OBJECTS sur le segment des mots systeme, sinon comme PL7 dans
        une table d'animation (92 07 02 58 00 <offset> <n> <donnees>, vu le 02/11 pour %SW50).
        Renvoie la methode acceptee."""
        data = [b for v in values for b in (v & 0xFFFF).to_bytes(2, "little")]
        seg, typ, _ = WORD_ZONES["SW"]
        try:
            self._ask([WRITE_OBJECTS, self.slave.category, seg, typ, start & 0xFF, start >> 8,
                       len(values) & 0xFF, len(values) >> 8, *data], OK_ANSWER)
            return "37"
        except UniteError as e:
            if "FD" not in str(e):
                raise
        offset = 0x50 + 2 * start
        answer = self._ask([DEBUG_MEMORY, PL7_CATEGORY, 0x02, DEBUG_SYSTEM_WORDS, 0x00,
                            offset & 0xFF, offset >> 8, len(data), *data], DEBUG_MEMORY + 0x30)
        if answer[:1] != [0x02]:
            raise UniteError(f"ecriture %SW refusee : {hexs(answer)}")
        return "92"

    def module_status(self, position: int) -> dict:
        """READ_STATUS_MODULE (44), as PL7 does when a module is opened in the online configuration
        (capture 02/11/2026, client TSX 3722): 44 07 02 00 <position> ->
          74 01 03 <04 00 type configure> <04 00 type en place> <detail>   module present
          74 01 02 <04 00 type configure>                                   module absent
        Returns {'configure': code, 'en_place': code or None, 'detail': bytes}."""
        request = [READ_STATUS_MODULE, PL7_CATEGORY, 0x02, 0x00, position]
        try:
            data = self._ask(request, READ_STATUS_MODULE + 0x30)
        except UniteError as e:
            if "FD" not in str(e):
                raise
            self._ask([MONITOR, PL7_CATEGORY, 0x0B], MONITOR + 0x30)
            data = self._ask(request, READ_STATUS_MODULE + 0x30)
        if len(data) < 6 or data[0] != 0x01:
            raise UniteError(f"etat module {position} : reponse inattendue {hexs(data)}")
        configured = data[4] << 8 | data[5]
        present = data[8] << 8 | data[9] if data[1] >= 3 and len(data) >= 10 else None
        return {"configure": configured, "en_place": present, "detail": bytes(data[10:]), "brut": hexs(data)}

    def read_cpu(self) -> list:
        """READ_CPU (4F, categorie PL7) : diagnostic systeme, 77 octets sur le banc. Les octets
        1-2 changent avec l'etat (02 0B, 04 08 pendant un chargement, 01 0A apres) : a decoder."""
        return self._ask([READ_CPU, PL7_CATEGORY], READ_CPU + 0x30)

    def run(self) -> None:
        self._ask([RUN, self.slave.category], OK_ANSWER)

    def stop(self) -> None:
        self._ask([STOP, self.slave.category], OK_ANSWER)


# ---------- bits systeme, fichiers ----------

SYSTEM_BITS = {
    10: ("defaut E/S", 0),
    11: ("debordement chien de garde", 1),
    16: ("defaut E/S dans la tache", 0),
    19: ("depassement periode de tache", 1),
    51: ("horloge temps reel perdue (heure fausse)", 1),
    67: ("pile carte memoire", 1),
    68: ("pile processeur", 1),
}


def signed(v, size):
    bits = 8 * size
    return v - (1 << bits) if v >> (bits - 1) & 1 else v


BACKUP_ERRORS = {
    1: "plus de %MW demandes que de %MW configures",
    2: "nombre de %MW hors 0..1000",
    3: "plus de %MW a restituer que de %MW configures",
    4: "application en RAM > 15 Kmots",
    5: "service interdit en RUN",
    6: "cartouche Backup presente",
    7: "defaut d'ecriture Flash EPROM",
}


def read_program_bytes(data: bytes, name: str = "fichier"):
    """(image, automate cible) d'un .tsxbak (copie de l'automate) ou d'un .stx PL7.
    Un .stx est un zip : EXE/APP/<appli>.APX = en-tete 192 octets + EXE/PROG/PL7.BIN (programme)
    + EXE/CONF/BAC.BIN (configuration), la meme image que celle lue dans l'automate."""
    import io
    import zipfile
    if data[:4] != b"PK\x03\x04":
        if data.startswith(b"[HEADER]"):
            raise UniteError(f"{name} est un fichier de donnees PL7 : utiliser restaurer-donnees")
        if len(data) < 16 or not data[:16].rstrip(b"\0 ").strip():
            raise UniteError(f"{name} ne ressemble pas a une sauvegarde de programme")
        return data, None
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UniteError(f"{name} : fichier .stx illisible") from None
    with z:
        apx = [n for n in z.namelist() if n.upper().startswith("EXE/APP/") and n.upper().endswith(".APX")]
        if len(apx) != 1:
            raise UniteError(f"{name} : pas d'application compilee (EXE/APP/*.APX) dans le .stx")
        blob = z.read(apx[0])
        target = None
        ini = next((n for n in z.namelist() if n.upper() == "STATION.INI"), None)
        if ini:
            for line in z.read(ini).decode("latin-1").splitlines():
                if line.startswith("RefProcess="):
                    target = " ".join(line.split("=", 1)[1].split()[0:2]) or None
    return blob, target


def read_program_file(filename):
    with open(filename, "rb") as f:
        return read_program_bytes(f.read(), filename)


CONFIG_MODULE = re.compile(rb"\xff([\x00-\x1f])(TSX [ -~]{3,10})\x00")


MODULE_CODES = {0x1009: "TSX DEZ 32D2", 0x1018: "TSX DSZ 32R5", 0x101B: "TSX DSZ 08T2", 0x1064: "TSX AEZ 414"}


def config_codes(blob: bytes) -> dict:
    """{position: type code} from the configuration: the 16-byte name field is followed by
    04 xx <type hi> <type lo> (DEZ 32D2 = 1009, DSZ 32R5 = 1018, DSZ 08T2 = 101B, AEZ 414 = 1064)."""
    out = {}
    for m in CONFIG_MODULE.finditer(blob):
        rec = blob[m.start(2) + 16:m.start(2) + 20]
        if len(rec) == 4 and rec[0] == 0x04:
            out.setdefault(m.group(1)[0], rec[2] << 8 | rec[3])
    return out


def parse_config(blob: bytes) -> list:
    """[(position, reference)] des modules configures, lus dans la partie configuration
    (EXE/CONF/BAC.BIN, a la fin de l'image) : 'FF <position> TSX DEZ 32D2'. Un module format
    standard (32 voies) occupe 2 positions et apparait 2 fois (1 et 2), comme dans PL7."""
    seen, out = set(), []
    for m in CONFIG_MODULE.finditer(blob):
        pos = m.group(1)[0]
        if pos not in seen:
            seen.add(pos)
            out.append((pos, m.group(2).decode("ascii").strip()))
    return out


def bcd(v: int) -> int:
    return int(str(v), 16)


def clock_words(when) -> list:
    """%SW50..53 (BCD) pour une date : secondes (16#SS00), heures-minutes (16#HHMM),
    mois-jour (16#MMJJ), annee (16#AAAA)."""
    return [bcd(when.second) << 8, bcd(when.hour) << 8 | bcd(when.minute),
            bcd(when.month) << 8 | bcd(when.day), bcd(when.year)]


def set_clock(tsx, when=None) -> dict:
    """Mise a l'heure du TSX Micro / Premium : %S50 = 1 (mode mise a jour), ecriture de %SW50..53,
    %S50 = 0. Sans %S50 = 1 l'automate recopie son horloge dans %SW50..53 a chaque cycle (une
    ecriture seule est effacee aussitot, vu dans la capture du 02/11). Chaque etape est relue."""
    import datetime
    when = when or datetime.datetime.now()
    tsx.write_system_bit(50, 1)
    if tsx.read("S", 50, 1)[0] != 1:
        raise UniteError("%S50 n'est pas passe a 1 (mise a jour de l'horloge refusee)")
    try:
        method = tsx.write_system_words(50, clock_words(when))
    finally:
        tsx.write_system_bit(50, 0)
    return {"methode": method, "mots": clock_words(when)}


def app_name(blob: bytes) -> str:
    """Nom de l'application au debut de l'image ('STATION')."""
    return blob[:14].split(b"\0")[0].decode("ascii", "replace").strip()


def read_whole_zone(tsx, zone, limit=32768):
    """Lit une zone depuis l'adresse 0 jusqu'a ce que l'automate refuse (fin de la zone configuree)."""
    values = []
    addr = 0
    while addr < limit:
        n = min(60, limit - addr)
        try:
            values += tsx.read(zone, addr, n)
            addr += n
            continue
        except UniteError as e:
            if "FD" not in str(e):
                raise
        for a in range(addr, addr + n):       # la fin est dans ce bloc : mot par mot
            try:
                values += tsx.read(zone, a, 1)
            except UniteError as e:
                if "FD" in str(e):
                    return values
                raise
        return values
    return values


DAT_LINE = re.compile(r"\s*AT\s+%(MW|KW)(\d+)\s*:\s*\w+\s*:=\s*\(?\s*(-?\d+)", re.IGNORECASE)


def parse_data_text(text: str, name: str = "fichier"):
    """CSV de sauvegarder-donnees ou .dat PL7 (PLCopen : 'AT %MW12 : WORD := (3248);')
    -> ({zone: {adresse: valeur}}, nom de l'automate ou None)."""
    zones, plc = {}, None
    if text.lstrip().startswith("[HEADER]"):
        for line in text.splitlines():
            m = DAT_LINE.match(line)
            if m:
                zones.setdefault(m.group(1).upper(), {})[int(m.group(2))] = int(m.group(3)) & 0xFFFF
    else:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# automate;"):
                plc = line.split(";", 1)[1]
            if not line or line.startswith("#") or line.startswith("zone;"):
                continue
            try:
                zone, addr, value = line.split(";")[:3]
                zones.setdefault(zone.lstrip("%").upper(), {})[int(addr)] = int(value) & 0xFFFF
            except ValueError:
                raise UniteError(f"{name} : ligne illisible '{line[:40]}'") from None
    if not zones:
        raise UniteError(f"{name} : aucune valeur %MW dans le fichier")
    return zones, plc


def read_data_dat(filename):
    with open(filename, encoding="latin-1") as f:
        return parse_data_text(f.read(), filename)


def read_data_csv(filename):
    """CSV de sauvegarder-donnees (ou .dat de PL7) -> ({zone: {adresse: valeur}}, nom de l'automate)."""
    with open(filename, encoding="utf-8" if not filename.lower().endswith(".dat") else "latin-1") as f:
        return parse_data_text(f.read(), filename)


def data_csv(zones: dict, plc: str, stamp: str) -> str:
    """Texte du CSV de sauvegarde des donnees (meme format que tools/tsx.py sauvegarder-donnees)."""
    lines = [f"# date;{stamp}", f"# automate;{plc}", "zone;adresse;valeur;signe;hex"]
    for zone, values in zones.items():
        for i, v in enumerate(values):
            lines.append(f"%{zone};{i};{v};{signed(v, 2)};{v:04X}")
    return "\n".join(lines) + "\n"


def changed_runs(addrs):
    """[3, 4, 5, 9] -> [(3, 3), (9, 1)] : suites d'adresses consecutives (debut, longueur)."""
    runs = []
    for a in sorted(addrs):
        if runs and a == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((a, 1))
    return runs


def parse_addrs(text):
    if "-" in text:
        a, b = text.split("-")
        return tuple(range(int(a), int(b) + 1))
    return tuple(int(x) for x in text.split(","))


