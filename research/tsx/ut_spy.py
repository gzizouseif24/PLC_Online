#!/usr/bin/env python3
"""TSX Uni-Telway - espion entre PL7 et l'automate, pour apprendre les codes des requetes.

PL7 -> port virtuel (com0com) -> ce script -> port reel -> automate
Le script relaie chaque octet dans les deux sens sans le modifier, et affiche
les requetes de PL7 et les reponses de l'automate, decodees.

Installation (une fois) : com0com cree une paire de ports relies, ex. COM10 <-> COM11.
  1. PL7 : regler la liaison Uni-Telway sur COM11 (au lieu de COM6).
  2. python ut_spy.py --plc COM6 --pl7 COM10
  3. Dans PL7 : se connecter, ouvrir une table d'animation (%I1.0, %IW3.0, defauts module...).
Tout est aussi enregistre dans un fichier (--log, par defaut ut_spy.log).

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


# ---------- decodage ----------

def describe(direction, ev):
    """Une ligne lisible pour une trame : adresse, en-tete X-WAY, message UNI-TE."""
    addr, npdu = ev[1], ev[2]
    bcc = "" if ev[3] else "  !! BCC FAUX"
    if len(npdu) >= 6 and npdu[0] in (0x20, 0x22):
        xway, unite = npdu[:6], npdu[6:]
        return (f"{direction}  adr {addr:02X} | X-WAY {hexs(xway)} | UNI-TE {hexs(unite)}{bcc}")
    return f"{direction}  adr {addr:02X} | {hexs(npdu)}{bcc}"


class Relay:
    def __init__(self, plc, pl7, log, show_all=False):
        self.plc, self.pl7, self.log, self.show_all = plc, pl7, log, show_all
        self.from_plc, self.from_pl7 = Parser(), Parser()
        self.t0 = time.time()
        self.frames = 0

    def out(self, line):
        line = f"{time.time() - self.t0:9.3f}  {line}"
        print(line)
        self.log.write(line + "\n")
        self.log.flush()

    def step(self):
        moved = False
        data = self.plc.read(self.plc.in_waiting or 0) if self.plc.in_waiting else b""
        if data:
            self.pl7.write(data)  # relayer d'abord, decoder ensuite : ne pas retarder PL7
            moved = True
            for ev in self.from_plc.feed(data):
                self.show("API -> PL7", ev)
        data = self.pl7.read(self.pl7.in_waiting or 0) if self.pl7.in_waiting else b""
        if data:
            self.plc.write(data)
            moved = True
            for ev in self.from_pl7.feed(data):
                self.show("PL7 -> API", ev)
        return moved

    def show(self, direction, ev):
        if ev[0] == "frame":
            self.frames += 1
            self.out(describe(direction, ev))
        elif self.show_all and ev[0] != "poll":
            self.out(f"{direction}  {ev[0].upper()}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plc", default="COM6", help="port reel vers l'automate")
    p.add_argument("--pl7", default="COM10", help="port virtuel cote script (PL7 utilise l'autre de la paire)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--log", default="ut_spy.log")
    p.add_argument("--tout", action="store_true", help="afficher aussi ACK / NAK / EOT")
    a = p.parse_args()

    import serial
    plc = serial.Serial(a.plc, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0)
    pl7 = serial.Serial(a.pl7, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0)
    print(f"Relais {a.plc} (automate) <-> {a.pl7} (PL7). Ctrl+C pour arreter. Journal : {a.log}\n")
    with open(a.log, "a", encoding="utf-8") as log:
        relay = Relay(plc, pl7, log, a.tout)
        try:
            while True:
                if not relay.step():
                    time.sleep(0.0005)
        except KeyboardInterrupt:
            print(f"\n{relay.frames} trames decodees, enregistrees dans {a.log}")
        finally:
            plc.close()
            pl7.close()


if __name__ == "__main__":
    main()
