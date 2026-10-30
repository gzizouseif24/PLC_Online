#!/usr/bin/env python3
"""TSX Uni-Telway - sonde des requetes MODULES, d'apres la liste officielle PL7 (aide tcom1).
LECTURE SEULE : seuls ces 4 codes de lecture documentes peuvent partir :

    4F READ_CPU                   diagnostic systeme                    -> 7F
    44 READ_STATUS_MODULE         structure physique et etat complet    -> 74
    49 READ_DIGITAL_MODULE_IMAGE  bits d'E/S d'un module TOR simple     -> 79
    43 READ_IO_CHANNEL            objets d'E/S niveau voie (status...)  -> 73

Le codage des parametres n'est pas dans l'aide PL7 (manuel TSX DR NET) : on essaie
sans parametre, [module], [rack, module], [module, voie], pour les positions 0 a 6.
Conseil : mettre une entree du TSX DEZ 32D2 a 1 (24 V sur l'entree 0) avant de lancer.

    python ut_modules.py --port COM6
    python ut_modules.py --port COM6 --codes 49 --positions 1-4

Environ 2,5 s par requete. Fichier autonome (pip install pyserial). Fermer PL7 avant.
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

READ_SAFE = {0x4F, 0x44, 0x49, 0x43}


def parse_list(text, base=10):
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += range(int(a, base), int(b, base) + 1)
        elif part:
            out.append(int(part, base))
    return out


def candidates(codes, positions):
    for code in codes:
        yield f"{code:02X} sans parametre", [code, 0]
        if code == 0x4F:
            continue
        for p in positions:
            yield f"{code:02X} [module {p}]", [code, 0, p]
            yield f"{code:02X} [rack 0, module {p}]", [code, 0, 0, p]
            yield f"{code:02X} [module {p}, 0]", [code, 0, p, 0]
            if code == 0x43:
                yield f"{code:02X} [module {p}, voie 0, 0]", [code, 0, p, 0, 0]


def text(data):
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--codes", default="4F,44,49,43", help="hexa, parmi 4F 44 49 43")
    p.add_argument("--positions", default="0-6")
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()
    codes = parse_list(a.codes, 16)
    if not set(codes) <= READ_SAFE:
        p.error("seuls les codes 4F, 44, 49, 43 sont autorises")

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, (1,), 0, a.trace)
    accepted = []
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - lectures seulement\n")
    try:
        for label, unite in candidates(codes, parse_list(a.positions)):
            assert unite[0] in READ_SAFE
            try:
                ans = slave.request(unite, timeout=8)
            except UniteError as e:
                print(f"  {label:<28} -> {e}")
                continue
            if ans and ans[0] == NEGATIVE:
                print(f"  {label:<28} -> refuse (FD) {hexs(ans[1:])}")
            else:
                print(f"  {label:<28} -> ACCEPTE : {hexs(ans)}   '{text(ans[1:])}'")
                accepted.append((label, ans))
    except KeyboardInterrupt:
        print("  (arrete)")
    finally:
        ser.close()
    print("\nRequetes acceptees :")
    for label, ans in accepted:
        print(f"  {label:<28} {hexs(ans)}")
    if not accepted:
        print("  aucune")


if __name__ == "__main__":
    main()
