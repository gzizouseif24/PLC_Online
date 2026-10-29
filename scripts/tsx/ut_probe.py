#!/usr/bin/env python3
"""TSX Uni-Telway - sonde des requetes de LECTURE, pour trouver les codes objets du TSX.

Envoie une liste fixe de requetes de lecture a l'adresse 0 et affiche celles que
l'automate accepte. Seuls les codes de lecture listes dans READ_ONLY sont autorises :
aucune ecriture, aucun RUN/STOP ne peut partir.

Compte environ 2 a 3 s par requete (attente de la scrutation).
Fichier autonome (pip install pyserial).

    python ut_probe.py --port COM6
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
    def __init__(self, ser, addr=1, category=0, trace=False):
        self.ser, self.addr, self.category, self.trace = ser, addr, category, trace
        self.parser = Parser()
        self.t0 = time.time()

    def log(self, direction, data):
        if self.trace:
            print(f"   {time.time() - self.t0:8.3f} {direction} {hexs(data)}")

    def send(self, data: bytes):
        self.log("PC  ->", data)
        self.ser.write(data)

    def request(self, unite: list, timeout: float = 10.0) -> list:
        """Envoie une requete UNI-TE a la prochaine scrutation, renvoie la reponse UNI-TE."""
        frame = build_frame(self.addr, XWAY_TO_PLC + unite)
        state = "attente scrutation"
        nak = 0
        deadline = time.time() + timeout
        self.ser.reset_input_buffer()
        self.parser = Parser()
        while time.time() < deadline:
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                self.log("API ->", data)
            for ev in self.parser.feed(data):
                kind = ev[0]
                if kind == "poll" and ev[1] == self.addr and state == "attente scrutation":
                    self.send(frame)
                    state = "attente ACK"
                elif kind == "frame" and ev[4] == frame:
                    pass  # echo de notre propre trame par le convertisseur RS-485 : ignore
                elif kind == "ack" and state == "attente ACK":
                    state = "attente reponse"
                elif kind == "nak" and state == "attente ACK":
                    nak += 1
                    if nak >= 3:
                        raise UniteError("l'automate a refuse la trame 3 fois (NAK) : parite ou format ?")
                    state = "attente scrutation"
                elif kind == "frame" and ev[1] == self.addr and state in ("attente ACK", "attente reponse"):
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
            raise UniteError(f"message refuse par X-WAY (adresse automate ?) : {hexs(npdu)}")
        if npdu[0] == 0x20 and len(npdu) >= 6:
            return list(npdu[6:])
        if npdu[0] == 0x00:
            return list(npdu[1:])
        raise UniteError(f"en-tete X-WAY inconnu : {hexs(npdu)}")

    def unite(self, code: int, params: list = ()) -> list:
        answer = self.request([code, self.category, *params])
        if not answer:
            raise UniteError("reponse UNI-TE vide")
        if answer[0] == NEGATIVE:
            raise UniteError(f"compte rendu negatif (FD) : {hexs(answer)}")
        if answer[0] != ANSWER[code]:
            raise UniteError(f"code reponse {answer[0]:02X} au lieu de {ANSWER[code]:02X} : {hexs(answer)}")
        return answer[1:]


# ---------- sonde ----------

# Requetes autorisees : lectures uniquement.
#   0x00..0x07 : lectures UNI-TE V1 (0x00 = bit interne %M, confirme sur TSX par x-way-uni-te ;
#                les autres sont des lectures de bits/mots, role exact a confirmer)
#   0x36       : lecture d'objets (segment, type, adresse, nombre)
READ_ONLY = set(range(0x00, 0x08)) | {0x36}

# Segments et types candidats pour 0x36 (0x64 bits internes et 0x68 mots internes probables)
SEGMENTS = [0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B]
TYPES = {0x05: "bit", 0x06: "octet", 0x07: "mot", 0x08: "double mot"}


def candidates():
    for code in sorted(READ_ONLY - {0x36}):
        yield f"V1 code {code:02X} adresse 0", [code, None, 0x00, 0x00]
    for seg in SEGMENTS:
        for typ, name in TYPES.items():
            yield (f"36 segment {seg:02X} type {typ:02X} ({name}) adresse 0 x1",
                   [0x36, None, seg, typ, 0x00, 0x00, 0x01, 0x00])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresse", type=int, default=1)
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    a = p.parse_args()

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, a.adresse, a.categorie)
    accepted = []
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - lectures seulement\n")
    try:
        for label, unite in candidates():
            assert unite[0] in READ_ONLY, "requete hors liste de lecture"
            unite[1] = a.categorie
            try:
                answer = slave.request(unite, timeout=8)
            except UniteError as e:
                print(f"  {label:<45} -> pas de reponse : {e}")
                continue
            if answer and answer[0] == 0xFD:
                print(f"  {label:<45} -> refuse (FD)")
            else:
                print(f"  {label:<45} -> ACCEPTE : {hexs(answer)}")
                accepted.append((label, answer))
    except KeyboardInterrupt:
        print("  (arrete)")
    finally:
        ser.close()

    print("\nRequetes acceptees :")
    for label, answer in accepted:
        print(f"  {label:<45} {hexs(answer)}")
    if not accepted:
        print("  aucune")


if __name__ == "__main__":
    main()
