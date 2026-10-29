#!/usr/bin/env python3
"""TSX Uni-Telway - lecture de mots (requete 36 "lecture d'objets"), lecture seule.

Mode carte (par defaut) : identifie quel segment est %MW, %KW, %SW
  1. lit les 120 premiers mots des segments 68, 69, 6A et montre ceux qui ne sont pas a 0
  2. lit 3 fois %SW49..%SW53 (horodateur) sur le segment 6A : les secondes doivent bouger
  3. relit les memes mots avec les requetes V1 04, 05, 06 pour savoir laquelle lit quoi

Mode lecture : lit une plage, avec la correspondance supposee 68=%MW, 69=%KW, 6A=%SW
    python ut_words.py --port COM6                              (carte)
    python ut_words.py --port COM6 --zone MW --debut 0 --nombre 20

Fichier autonome (pip install pyserial). Fermer PL7 avant.
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


# ---------- lecture de mots ----------

READ_OBJECTS, WORD = 0x36, 0x07
SEGMENTS = {0x68: "MW", 0x69: "KW", 0x6A: "SW"}   # correspondance supposee, a confirmer
ZONES = {v: k for k, v in SEGMENTS.items()}
MAX_WORDS = 60  # une trame porte au plus 134 octets de donnees


def read_words(slave, segment, start, count):
    unite = [READ_OBJECTS, slave.category, segment, WORD,
             start & 0xFF, start >> 8, count & 0xFF, count >> 8]
    answer = slave.request(unite, timeout=8)
    if not answer or answer[0] == NEGATIVE:
        raise UniteError("refuse (FD)")
    if answer[0] != 0x66 or len(answer) < 2 or answer[1] != WORD:
        raise UniteError(f"reponse inattendue : {hexs(answer)}")
    data = answer[2:]
    if len(data) != 2 * count:
        raise UniteError(f"{len(data) // 2} mots recus au lieu de {count}")
    return [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]  # little-endian


def read_v1(slave, code, address):
    answer = slave.request([code, slave.category, address & 0xFF, address >> 8], timeout=8)
    if not answer or answer[0] == NEGATIVE:
        raise UniteError("refuse (FD)")
    if answer[0] != code + 0x30 or len(answer) != 3:
        raise UniteError(f"reponse inattendue : {hexs(answer)}")
    return answer[1] | answer[2] << 8


def show(name, start, words, only_nonzero=False):
    for i, w in enumerate(words):
        if only_nonzero and not w:
            continue
        signed = w - 0x10000 if w & 0x8000 else w
        print(f"   %{name}{start + i:<5} = {w:5d}   (signe {signed:6d}, hex {w:04X})")


def map_mode(slave):
    found = {}
    print("1. Mots non nuls dans les 120 premiers de chaque segment")
    for seg, name in SEGMENTS.items():
        print(f"\n   segment {seg:02X} (suppose %{name})")
        for start in (0, MAX_WORDS):
            try:
                words = read_words(slave, seg, start, MAX_WORDS)
            except UniteError as e:
                print(f"   mots {start}..{start + MAX_WORDS - 1} : {e}")
                break
            show(name, start, words, only_nonzero=True)
            if not any(words):
                print(f"   mots {start}..{start + MAX_WORDS - 1} : tous a 0")
            for i, w in enumerate(words):
                if w and seg not in found:
                    found[seg] = (start + i, w)

    print("\n2. Horodateur : segment 6A, mots 49..53, lus 3 fois (les secondes doivent bouger)")
    for _ in range(3):
        try:
            words = read_words(slave, 0x6A, 49, 5)
            print("   " + "  ".join(f"%SW{49 + i}={w:04X}" for i, w in enumerate(words)))
        except UniteError as e:
            print(f"   {e}")
        time.sleep(1)

    print("\n3. Requetes V1 04 / 05 / 06 sur un mot non nul de chaque segment")
    if not found:
        print("   aucun mot non nul trouve : comparaison impossible")
    for seg, (addr, value) in found.items():
        results = []
        for code in (0x04, 0x05, 0x06):
            try:
                results.append(f"{code:02X} -> {read_v1(slave, code, addr):04X}")
            except UniteError as e:
                results.append(f"{code:02X} -> {e}")
        print(f"   segment {seg:02X} mot {addr} = {value:04X} :  " + "   ".join(results))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresse", type=int, default=1)
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    p.add_argument("--zone", choices=sorted(ZONES), help="lire une plage au lieu du mode carte")
    p.add_argument("--debut", type=int, default=0)
    p.add_argument("--nombre", type=int, default=10)
    a = p.parse_args()

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, a.adresse, a.categorie)
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - lecture seule\n")
    try:
        if a.zone:
            seg = ZONES[a.zone]
            addr, end = a.debut, a.debut + a.nombre
            while addr < end:
                n = min(MAX_WORDS, end - addr)
                show(a.zone, addr, read_words(slave, seg, addr, n))
                addr += n
        else:
            map_mode(slave)
    except UniteError as e:
        print(f"   ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
