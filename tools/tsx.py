#!/usr/bin/env python3
"""TSX Micro / Premium par le port TER (Uni-Telway), sans PL7.

    python tools/tsx.py etat                          identification, defauts, pile, horloge
    python tools/tsx.py lire MW 0 20                  lire %MW0..%MW19  (zones : MW MD KW KD SW S M)
    python tools/tsx.py surveiller MW 0 100           afficher ce qui change (Ctrl+C pour arreter)
    python tools/tsx.py ecrire MW 10 1234             ecrire UN mot, avec confirmation et relecture
    python tools/tsx.py run | stop                    passer l'automate en RUN / STOP, avec confirmation
    python tools/tsx.py sauvegarder [FICHIER]         copier le programme de l'automate dans un fichier
    python tools/tsx.py comparer FICHIER              le programme a-t-il change depuis cette sauvegarde ?
    python tools/tsx.py restaurer FICHIER             remettre un programme sauvegarde dans l'automate
    python tools/tsx.py sauvegarder-donnees [FICHIER] copier tous les %MW et %KW dans un fichier CSV
    python tools/tsx.py restaurer-donnees FICHIER     remettre les %MW du fichier CSV dans l'automate

Options : --port COM6, --trace (octets), --adresses 1-3 (plus rapide, a l'essai : 1 par defaut).
Fermer PL7 avant. Cable TSX PCX 1031 : meme position qu'avec PL7.

Confirme sur TSX 3721 : etat, lecture %S, %SW. Probable : %MW, %MD, %KW, %KD, %M.
Teste sur TSX 3721 : toutes les commandes ci-dessus.

pip install pyserial
"""
import argparse
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

    def run(self) -> None:
        self._ask([RUN, self.slave.category], OK_ANSWER)

    def stop(self) -> None:
        self._ask([STOP, self.slave.category], OK_ANSWER)


# ---------- affichage ----------

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


def fmt(zone, addr, v):
    if zone in BIT_ZONES:
        return f"%{zone}{addr:<5} = {v}"
    size = WORD_ZONES[zone][2]
    return f"%{zone}{addr:<5} = {v:{10 if size == 4 else 5}d}   (signe {signed(v, size)}, hex {v:0{2 * size}X})"


def cmd_etat(tsx):
    ident = tsx.identification()
    print(f"Automate : {ident['nom']}")
    bits = tsx.read("S", 0, 72)
    print("\nDefauts (bits systeme) :")
    alarms = 0
    for n, (label, bad) in SYSTEM_BITS.items():
        flag = bits[n] == bad
        alarms += flag
        print(f"   {'!!' if flag else 'ok'}  %S{n:<3} = {bits[n]}   {label}")
    if not alarms:
        print("   aucun defaut signale par les bits systeme")
    sw = tsx.read("SW", 49, 5)
    print(f"\nHorloge automate : {sw[3] & 0xFF:02X}/{sw[3] >> 8:02X}/{sw[4]:04X} "
          f"{sw[2] >> 8:02X}:{sw[2] & 0xFF:02X}:{sw[1] >> 8:02X}  (jour {sw[0]})")
    print(f"Chien de garde : {tsx.read('SW', 11, 1)[0]} ms")
    show_flash_backup(tsx.read("SW", 96, 2))


BACKUP_ERRORS = {
    1: "plus de %MW demandes que de %MW configures",
    2: "nombre de %MW hors 0..1000",
    3: "plus de %MW a restituer que de %MW configures",
    4: "application en RAM > 15 Kmots",
    5: "service interdit en RUN",
    6: "cartouche Backup presente",
    7: "defaut d'ecriture Flash EPROM",
}


def show_flash_backup(sw):
    """%SW96 / %SW97 (TSX 37 uniquement) : sauvegarde du programme en Flash EPROM interne."""
    sw96, sw97 = sw
    valid = sw96 >> 6 & 1
    print("\nSauvegarde interne (Flash EPROM, TSX 37) :")
    print(f"   programme : {'VALIDE' if valid else 'aucune sauvegarde valide'}   (%SW96 bit 6 = {valid})")
    if sw97 == 0xFFFF:
        print("   %MW sauvegardes : aucun   (%SW97 = -1)")
    else:
        print(f"   %MW sauvegardes : {sw97} premiers %MW   (%SW97 = {sw97})")
    if sw96 >> 1 & 1:
        if sw96 >> 2 & 1:
            code = sw96 >> 8
            print(f"   derniere sauvegarde : ERREUR {code} {BACKUP_ERRORS.get(code, '')}")
        else:
            print("   derniere sauvegarde : terminee sans erreur")


def cmd_lire(tsx, zone, start, count):
    for i, v in enumerate(tsx.read(zone, start, count)):
        print("   " + fmt(zone, start + i, v))


def cmd_surveiller(tsx, zone, start, count):
    print(f"Surveillance %{zone}{start}..%{zone}{start + count - 1} (Ctrl+C pour arreter)\n")
    base = tsx.read(zone, start, count)
    t0 = time.time()
    while True:
        cur = tsx.read(zone, start, count)
        for i, (old, new) in enumerate(zip(base, cur)):
            if old != new:
                print(f"   {time.time() - t0:8.1f} s   %{zone}{start + i}: {old} -> {new}")
        base = cur


def confirm(question):
    return input(f"{question} (tapez OUI) > ").strip() == "OUI"


def cmd_ecrire(tsx, zone, addr, value):
    old = tsx.read(zone, addr, 1)[0]
    print(f"%{zone}{addr} actuel = {old}")
    if not confirm(f"Ecrire %{zone}{addr} : {old} -> {value} ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    tsx.write(zone, addr, value)
    back = tsx.read(zone, addr, 1)[0]
    size = WORD_ZONES[zone][2]
    ok = back == value & ((1 << 8 * size) - 1)
    print(f"Relu : %{zone}{addr} = {back}  {'OK' if ok else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python tools/tsx.py ecrire {zone} {addr} {old}")


def cmd_run_stop(tsx, what):
    if not confirm(f"Passer l'automate en {what.upper()} ? Machine en securite ?"):
        print("Annule.")
        return
    (tsx.run if what == "run" else tsx.stop)()
    print(f"{what.upper()} accepte par l'automate. Verifier le voyant RUN.")


def do_upload(tsx):
    """Reserve, lit l'application, libere. Renvoie (infos, contenu)."""
    ident = tsx.identification()
    tsx.reserve()
    try:
        info = tsx.app_info()
        print(f"Automate {ident['nom']}, application '{info['nom']}' : lecture...")
        t0 = time.time()
        blob = tsx.upload(lambda seg, n: print(f"\r   segment {seg:4d}   {n:7d} octets", end="", flush=True))
        print(f"\r   {len(blob)} octets lus en {time.time() - t0:.0f} s" + " " * 20)
    finally:
        try:
            tsx.release()
        except UniteError:
            pass
    return ident, info, blob


def cmd_sauvegarder(tsx, filename=None):
    import hashlib
    ident, info, blob = do_upload(tsx)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = filename or f"{info['nom'] or 'application'}_{stamp}.tsxbak"
    with open(filename, "wb") as f:
        f.write(blob)
    with open(filename + ".txt", "w", encoding="utf-8") as f:
        f.write(f"date            : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"automate        : {ident['nom']}\n")
        f.write(f"application     : {info['nom']}\n")
        f.write(f"taille          : {len(blob)} octets\n")
        f.write(f"sha256          : {hashlib.sha256(blob).hexdigest()}\n")
        f.write(f"identification  : {ident['brut']}\n")
        f.write(f"infos appli (92): {info['brut']}\n")
    print(f"Sauvegarde : {filename}  (+ {filename}.txt)")


def cmd_restaurer(tsx, filename):
    """Remet un .tsxbak dans l'automate, comme le transfert PC -> automate de PL7."""
    with open(filename, "rb") as f:
        blob = f.read()
    if len(blob) < 16 or not blob[:16].rstrip(b"\0 ").strip():
        raise UniteError(f"{filename} ne ressemble pas a une sauvegarde de programme")
    name = blob[:14].split(b"\0")[0].decode("ascii", "replace").strip()
    print(f"Fichier {filename} : {len(blob)} octets, application '{name}'")
    print("Avant de remplacer le programme, sauvegarde automatique du programme actuel :")
    cmd_sauvegarder(tsx, f"avant_restauration_{time.strftime('%Y%m%d_%H%M%S')}.tsxbak")
    if not confirm("Remplacer le programme de l'automate ? Il passera en STOP. Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    tsx.stop()
    tsx.reserve(RESERVE_DOWNLOAD)
    try:
        t0 = time.time()
        tsx.download(blob, lambda seg, n: print(f"\r   segment {seg:4d}   {n:7d} / {len(blob)} octets", end="", flush=True))
        print(f"\r   {len(blob)} octets ecrits en {time.time() - t0:.0f} s" + " " * 20)
    finally:
        try:
            tsx.release()
        except UniteError:
            pass
    print("Verification : relecture du programme...")
    _, _, back = do_upload(tsx)
    if back == blob:
        print("Restauration OK : le programme relu est identique au fichier. L'automate est en STOP (python tools/tsx.py run).")
    else:
        print(f"!! Le programme relu ({len(back)} octets) differe du fichier ({len(blob)} octets).")
        print("   Le programme d'avant est dans le fichier avant_restauration_*.tsxbak.")


def cmd_comparer(tsx, filename):
    with open(filename, "rb") as f:
        saved = f.read()
    _, _, blob = do_upload(tsx)
    if blob == saved:
        print(f"IDENTIQUE : le programme de l'automate est le meme que {filename}")
        return
    diffs = [i for i in range(min(len(blob), len(saved))) if blob[i] != saved[i]]
    print(f"DIFFERENT de {filename} : automate {len(blob)} octets, fichier {len(saved)} octets, "
          f"{len(diffs)} octets differents")
    if diffs:
        print(f"   premiere difference a l'octet {diffs[0]}, derniere a l'octet {diffs[-1]}")


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


def cmd_sauvegarder_donnees(tsx, filename=None):
    ident = tsx.identification()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = filename or f"donnees_{stamp}.csv"
    zones = {}
    for zone in ("MW", "KW"):
        print(f"Lecture %{zone}...", end="", flush=True)
        zones[zone] = read_whole_zone(tsx, zone)
        print(f" {len(zones[zone])} mots")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# date;{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# automate;{ident['nom']}\n")
        f.write("zone;adresse;valeur;signe;hex\n")
        for zone, values in zones.items():
            for i, v in enumerate(values):
                f.write(f"%{zone};{i};{v};{signed(v, 2)};{v:04X}\n")
    print(f"Donnees : {filename}")


def read_data_csv(filename):
    """CSV de sauvegarder-donnees -> ({zone: {adresse: valeur}}, nom de l'automate)."""
    zones, plc = {}, None
    with open(filename, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# automate;"):
                plc = line.split(";", 1)[1]
            if not line or line.startswith("#") or line.startswith("zone;"):
                continue
            zone, addr, value = line.split(";")[:3]
            zones.setdefault(zone.lstrip("%").upper(), {})[int(addr)] = int(value) & 0xFFFF
    return zones, plc


def changed_runs(addrs):
    """[3, 4, 5, 9] -> [(3, 3), (9, 1)] : suites d'adresses consecutives (debut, longueur)."""
    runs = []
    for a in sorted(addrs):
        if runs and a == runs[-1][0] + runs[-1][1]:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((a, 1))
    return runs


def cmd_restaurer_donnees(tsx, filename):
    zones, plc_in_file = read_data_csv(filename)
    ident = tsx.identification()
    if plc_in_file and plc_in_file != ident["nom"]:
        print(f"ATTENTION : fichier pris sur {plc_in_file}, automate connecte : {ident['nom']}")
    if "KW" in zones:
        print(f"%KW du fichier ignores ({len(zones['KW'])} mots) : restauration des constantes pas encore validee.")
    saved = zones.get("MW", {})
    if not saved:
        print("Aucun %MW dans le fichier.")
        return
    current = read_whole_zone(tsx, "MW")
    beyond = [a for a in saved if a >= len(current)]
    if beyond:
        print(f"ATTENTION : {len(beyond)} %MW du fichier n'existent pas dans l'automate "
              f"(il a {len(current)} %MW) : ils seront ignores.")
    diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
    if not diffs:
        print(f"Rien a restaurer : les {len(saved) - len(beyond)} %MW de l'automate sont deja identiques au fichier.")
        return
    print(f"{len(diffs)} %MW different du fichier, par exemple :")
    for a in sorted(diffs)[:10]:
        print(f"   %MW{a:<5} automate = {current[a]:6d}   fichier = {diffs[a]:6d}")
    if not confirm(f"Ecrire ces {len(diffs)} %MW dans l'automate ? Automate en STOP / machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    for start, length in changed_runs(diffs):
        tsx.write_words("MW", start, [saved[a] for a in range(start, start + length)])
    after = read_whole_zone(tsx, "MW")
    wrong = [a for a in saved if a < len(after) and after[a] != saved[a]]
    if wrong:
        print(f"!! {len(wrong)} %MW differents apres ecriture, premier : %MW{wrong[0]}")
    else:
        print(f"Restauration OK : {len(diffs)} %MW ecrits et verifies.")


def parse_addrs(text):
    if "-" in text:
        a, b = text.split("-")
        return tuple(range(int(a), int(b) + 1))
    return tuple(int(x) for x in text.split(","))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["etat", "lire", "surveiller", "ecrire", "run", "stop",
                                        "sauvegarder", "comparer", "sauvegarder-donnees",
                                        "restaurer-donnees", "restaurer"])
    p.add_argument("args", nargs="*", help="zone, adresse, nombre ou valeur")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresses", default="1", help="adresses esclave du PC : 1 (sur) ou 1-3 (plus rapide, a l'essai)")
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()

    zones = sorted(WORD_ZONES) + sorted(BIT_ZONES)
    if a.commande in ("lire", "surveiller", "ecrire"):
        if len(a.args) != 3 or a.args[0].upper() not in zones:
            p.error(f"usage : {a.commande} ZONE ADRESSE {'VALEUR' if a.commande == 'ecrire' else 'NOMBRE'}"
                    f"   (zones : {' '.join(zones)})")
        zone, n1, n2 = a.args[0].upper(), int(a.args[1]), int(a.args[2])
    if a.commande == "comparer" and len(a.args) != 1:
        p.error("usage : comparer FICHIER.tsxbak")
    if a.commande == "restaurer" and len(a.args) != 1:
        p.error("usage : restaurer FICHIER.tsxbak")
    if a.commande == "restaurer-donnees" and len(a.args) != 1:
        p.error("usage : restaurer-donnees FICHIER.csv")

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    tsx = Tsx(Slave(ser, parse_addrs(a.adresses), a.categorie, a.trace))
    try:
        if a.commande == "etat":
            cmd_etat(tsx)
        elif a.commande == "lire":
            cmd_lire(tsx, zone, n1, n2)
        elif a.commande == "surveiller":
            cmd_surveiller(tsx, zone, n1, n2)
        elif a.commande == "ecrire":
            cmd_ecrire(tsx, zone, n1, n2)
        elif a.commande == "sauvegarder":
            cmd_sauvegarder(tsx, a.args[0] if a.args else None)
        elif a.commande == "comparer":
            cmd_comparer(tsx, a.args[0])
        elif a.commande == "sauvegarder-donnees":
            cmd_sauvegarder_donnees(tsx, a.args[0] if a.args else None)
        elif a.commande == "restaurer":
            cmd_restaurer(tsx, a.args[0])
        elif a.commande == "restaurer-donnees":
            cmd_restaurer_donnees(tsx, a.args[0])
        else:
            cmd_run_stop(tsx, a.commande)
    except UniteError as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
