#!/usr/bin/env python3
"""TSX Uni-Telway - premier dialogue : test miroir, identification, etat.

Le PC se comporte en esclave (adresse 1, comme PL7) : il attend que l'automate
le scrute, envoie sa requete, puis lit la reponse. Trois requetes, toutes en
LECTURE : rien n'est modifie dans l'automate.

  1. Miroir (FA)          : l'automate renvoie les octets envoyes -> prouve tout le lien
  2. Identification (0F)  : type d'automate, version, nom
  3. Etat (31)            : octets d'etat bruts (RUN/STOP, defauts) a decoder

Fermer PL7 / Unity avant. Cable TSX PCX 1031 : meme position qu'avec PL7.

    python ut_test.py --port COM6
    python ut_test.py --port COM6 --trace        (affiche chaque octet envoye/recu)

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


def ascii_part(data) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresse", type=int, default=1, help="adresse esclave du PC (1 comme PL7)")
    p.add_argument("--categorie", type=int, default=0, choices=range(8), help="code categorie UNI-TE (0..7)")
    p.add_argument("--trace", action="store_true", help="affiche chaque octet envoye et recu")
    a = p.parse_args()

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    print(f"{a.port} @ {a.baud} 8{a.parite}1, PC = esclave {a.adresse}, categorie {a.categorie}\n")
    slave = Slave(ser, a.adresse, a.categorie, a.trace)
    try:
        print("1. Miroir")
        sent = list(b"PLC-ONLINE")
        try:
            echo = slave.unite(MIRROR, sent)
        except UniteError as e:
            print(f"   ECHEC : {e}")
            print("   Pistes : --parite E ou N, --categorie 7, --trace pour voir les octets.")
            return
        ok = echo == sent
        print(f"   recu : {hexs(echo)}  '{ascii_part(echo)}'  -> {'OK, le lien fonctionne' if ok else 'DIFFERENT'}")

        print("\n2. Identification")
        try:
            ident = slave.unite(IDENTIFICATION)
            print(f"   brut  : {hexs(ident)}")
            print(f"   texte : '{ascii_part(ident)}'")
        except UniteError as e:
            print(f"   ECHEC : {e}")

        print("\n3. Etat")
        try:
            status = slave.unite(STATUS)
            print(f"   brut : {hexs(status)}")
            for i, b in enumerate(status[:8]):
                print(f"   octet {i} = {b:02X} = {b:08b}")
        except UniteError as e:
            print(f"   ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
