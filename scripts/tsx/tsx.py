#!/usr/bin/env python3
"""TSX Micro / Premium par le port TER (Uni-Telway), sans PL7.

    python tsx.py etat                          identification, defauts, pile, horloge
    python tsx.py lire MW 0 20                  lire %MW0..%MW19  (zones : MW MD KW KD SW S M)
    python tsx.py surveiller MW 0 100           afficher ce qui change (Ctrl+C pour arreter)
    python tsx.py ecrire MW 10 1234             ecrire UN mot, avec confirmation et relecture
    python tsx.py run | stop                    passer l'automate en RUN / STOP, avec confirmation

Options : --port COM6, --adresses 1-3 (adresses esclave utilisees par le PC, comme PL7).
Fermer PL7 avant. Cable TSX PCX 1031 : meme position qu'avec PL7.

Confirme sur TSX 3721 : etat, lecture %S, %SW. Probable : %MW, %MD, %KW, %KD, %M.
Experimental (format suppose, a valider sur un automate d'essai) : ecrire, run, stop.

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

    def __init__(self, ser, addrs=(1, 2, 3), category=0, trace=False):
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
        nak = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.ser.read(self.ser.in_waiting or 1)
            if data:
                self.log("API ->", data)
            for ev in self.parser.feed(data):
                kind = ev[0]
                if kind == "poll" and ev[1] in self.addrs and state == "attente scrutation":
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

    def write(self, zone: str, addr: int, value: int) -> None:
        if zone not in WRITABLE:
            raise UniteError(f"ecriture non autorisee sur %{zone}")
        seg, typ, size = WORD_ZONES[zone]
        data = list((value & ((1 << 8 * size) - 1)).to_bytes(size, "little"))
        self._ask([WRITE_OBJECTS, self.slave.category, seg, typ,
                   addr & 0xFF, addr >> 8, 1, 0, *data], OK_ANSWER)

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
    print(f"Pour revenir en arriere : python tsx.py ecrire {zone} {addr} {old}")


def cmd_run_stop(tsx, what):
    if not confirm(f"Passer l'automate en {what.upper()} ? Machine en securite ?"):
        print("Annule.")
        return
    (tsx.run if what == "run" else tsx.stop)()
    print(f"{what.upper()} accepte par l'automate. Verifier le voyant RUN.")


def parse_addrs(text):
    if "-" in text:
        a, b = text.split("-")
        return tuple(range(int(a), int(b) + 1))
    return tuple(int(x) for x in text.split(","))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["etat", "lire", "surveiller", "ecrire", "run", "stop"])
    p.add_argument("args", nargs="*", help="zone, adresse, nombre ou valeur")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresses", default="1-3", help="adresses esclave du PC, ex. 1-3 ou 1")
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()

    zones = sorted(WORD_ZONES) + sorted(BIT_ZONES)
    if a.commande in ("lire", "surveiller", "ecrire"):
        if len(a.args) != 3 or a.args[0].upper() not in zones:
            p.error(f"usage : {a.commande} ZONE ADRESSE {'VALEUR' if a.commande == 'ecrire' else 'NOMBRE'}"
                    f"   (zones : {' '.join(zones)})")
        zone, n1, n2 = a.args[0].upper(), int(a.args[1]), int(a.args[2])

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
