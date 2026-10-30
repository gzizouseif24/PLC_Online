#!/usr/bin/env python3
"""TSX Uni-Telway - sonde des entrees/sorties des modules, sans PL7. LECTURE SEULE.

Les requetes V1 02, 03 et 07 sont refusees a l'adresse 0 (la CPU n'a pas d'E/S).
Elles lisent probablement les E/S : on les essaie a l'adresse des modules, avec
plusieurs codages possibles de "position p, voie 0" :
    p*16    p*32    p*100    p*256    p
Positions 1 a 6. Seuls les codes 02, 03, 07 sont envoyes (lectures).

    python ut_io_probe.py --port COM6
    python ut_io_probe.py --port COM6 --positions 1,3      (si on connait les positions)

Environ 2,5 s par requete. Fichier autonome (pip install pyserial).
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

READ_ONLY = {0x02, 0x03, 0x07}
ENCODINGS = {
    "p*16": lambda p: p * 16,
    "p*32": lambda p: p * 32,
    "p*100": lambda p: p * 100,
    "p*256": lambda p: p * 256,
    "p": lambda p: p,
}


def parse_list(text):
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += range(int(a), int(b) + 1)
        elif part:
            out.append(int(part))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--positions", default="1-6")
    p.add_argument("--codes", default="2,3,7", help="parmi 2, 3, 7")
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()
    codes = parse_list(a.codes)
    if not set(codes) <= READ_ONLY:
        p.error("seuls les codes 2, 3 et 7 sont autorises")

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, (1,), 0, a.trace)
    accepted = []
    seen = set()
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - lectures seulement\n")
    try:
        for code in codes:
            for pos in parse_list(a.positions):
                for name, enc in ENCODINGS.items():
                    addr = enc(pos)
                    if (code, addr) in seen:
                        continue
                    seen.add((code, addr))
                    assert code in READ_ONLY
                    label = f"code {code:02X}  position {pos}  ({name:<5} = adresse {addr:4d})"
                    try:
                        ans = slave.request([code, 0, addr & 0xFF, addr >> 8], timeout=8)
                    except UniteError as e:
                        print(f"  {label} -> {e}")
                        continue
                    if ans and ans[0] == NEGATIVE:
                        print(f"  {label} -> refuse (FD)")
                    else:
                        print(f"  {label} -> ACCEPTE : {hexs(ans)}")
                        accepted.append((label, ans))
    except KeyboardInterrupt:
        print("  (arrete)")
    finally:
        ser.close()
    print("\nRequetes acceptees :")
    for label, ans in accepted:
        print(f"  {label}  {hexs(ans)}")
    if not accepted:
        print("  aucune")


if __name__ == "__main__":
    main()
