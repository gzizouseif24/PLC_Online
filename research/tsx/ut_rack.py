#!/usr/bin/env python3
"""TSX Uni-Telway - sonde du rack : identification (requete 0F) envoyee a differentes
adresses X-WAY (porte, module, voie), pour trouver comment adresser chaque emplacement.

LECTURE SEULE : la seule requete envoyee est l'identification (0F).
Rack vide : seule la CPU devrait repondre. Avec un module en place, il devrait
repondre avec sa reference (ex. TSX DEZ ...).

Premier essai (TSX 3721) : porte 0 = CPU (ignore ext1/ext2), porte 8 = CPU aussi,
porte 5 refusee avec une raison differente (07) : probablement la porte modules/voies.

    python ut_rack.py --port COM6                              (portes 5 et 8, ext1 0-5, ext2 0-3)
    python ut_rack.py --port COM6 --portes 0-15 --ext1 0 --ext2 0

Fichier autonome (pip install pyserial). Fermer PL7 avant. Environ 2 s par adresse.
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

    def request(self, unite: list, timeout: float = 10.0, xway=None) -> list:
        """Envoie une requete UNI-TE a la prochaine scrutation d'une de nos adresses."""
        payload = list(xway or XWAY_TO_PLC) + list(unite)
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


# ---------- sonde ----------

def parse_list(text):
    """'0-4,16' -> [0, 1, 2, 3, 4, 16]"""
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += range(int(a), int(b) + 1)
        elif part:
            out.append(int(part))
    return out


def candidates(gates, ext1s, ext2s):
    """(libelle, en-tete X-WAY) : 0x20, reseau, station, porte, ext1, ext2."""
    for gate in gates:
        for e1 in ext1s:
            for e2 in ext2s:
                yield f"porte {gate:2d}, ext1 {e1:3d}, ext2 {e2:3d}", [0x20, 0x00, 0xFE, gate, e1, e2]


def text(data):
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--portes", default="5,8", help="portes X-WAY, ex. 5,8 ou 0-15")
    p.add_argument("--ext1", default="0-5", help="valeurs ext1 (module ?)")
    p.add_argument("--ext2", default="0-3", help="valeurs ext2 (voie ?)")
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, (1,), 0, a.trace)
    answered = []
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - identification seulement\n")
    try:
        for label, xway in candidates(parse_list(a.portes), parse_list(a.ext1), parse_list(a.ext2)):
            try:
                ans = slave.request([IDENTIFICATION, 0], timeout=8, xway=xway)
            except UniteError as e:
                msg = str(e)
                if "refuse par X-WAY" in msg:
                    raw = msg.split(":")[-1].split()
                    msg = f"refuse X-WAY, raison {raw[-1]}   ({' '.join(raw)})"
                print(f"  {label:<32} -> {msg}")
                continue
            if ans and ans[0] == 0x3F:
                print(f"  {label:<32} -> REPOND : '{text(ans[1:])}'  {hexs(ans)}")
                answered.append((label, ans))
            else:
                print(f"  {label:<32} -> {hexs(ans)}")
    except KeyboardInterrupt:
        print("  (arrete)")
    finally:
        ser.close()
    print("\nAdresses qui repondent a l'identification :")
    for label, ans in answered:
        print(f"  {label:<32} '{text(ans[1:])}'")
    if not answered:
        print("  aucune")


if __name__ == "__main__":
    main()
