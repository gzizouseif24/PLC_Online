# -*- coding: utf-8 -*-
"""TSX Uni-Telway - proxy espion entre PL7 et l'automate (Python 2.7 ou 3, pyserial 2.7).

PL7 -> COM11 = com0com = COM10 -> ce script -> port reel -> TSX

Relaie chaque octet dans les deux sens SANS LE MODIFIER, et enregistre :
  - ut_proxy.log     : les requetes de PL7 et les reponses de l'automate, decodees
  - ut_proxy_raw.log : tous les octets bruts, horodates (polls compris), en secours

Pendant la capture, taper une note puis Entree (ex. "upload") : elle est inscrite
dans le journal a ce moment-la. Ctrl+C pour arreter.

    python ut_proxy27.py --plc COM1 --pl7 COM10

Dans PL7 : regler le driver Uni-Telway sur COM11 (l'autre moitie de la paire com0com).
"""
from __future__ import print_function

import argparse
import sys
import threading
import time

import serial

DLE, STX, EOT, ENQ, ACK, NAK = 0x10, 0x02, 0x04, 0x05, 0x06, 0x15
MAX_LENGTH = 134

# Liste officielle des requetes UNI-TE (aide PL7, tcom1 "Liste des requetes")
REQUESTS = {
    0x00: "READ_INTERNAL_BIT", 0x01: "READ_SYSTEM_BIT", 0x04: "READ_INTERNAL_WORD",
    0x05: "READ_CONSTANT_WORD", 0x06: "READ_SYSTEM_WORD", 0x0F: "IDENTIFICATION",
    0x10: "WRITE_INTERNAL_BIT", 0x11: "WRITE_SYSTEM_BIT", 0x14: "WRITE_INTERNAL_WORD",
    0x15: "WRITE_SYSTEM_WORD", 0x1B: "FORCE_INTERNAL_BIT", 0x1D: "RESERVE", 0x1E: "RELEASE",
    0x24: "RUN", 0x25: "STOP", 0x2A: "READ_GRAFCET_BIT", 0x2D: "I_AM_ALIVE",
    0x33: "INIT", 0x36: "READ_OBJECT", 0x37: "WRITE_OBJECT", 0x38: "READ_OBJECT_LIST",
    0x39: "WRITE_OBJECT_LIST", 0x3A: "OPEN_DOWNLOAD", 0x3B: "WRITE_DOWNLOAD",
    0x3C: "CLOSE_DOWNLOAD", 0x3D: "OPEN_UPLOAD", 0x3E: "READ_UPLOAD", 0x3F: "CLOSE_UPLOAD",
    0x43: "READ_IO_CHANNEL", 0x44: "READ_STATUS_MODULE", 0x45: "BACKUP",
    0x48: "WRITE_IO_CHANNEL", 0x49: "READ_DIGITAL_MODULE_IMAGE",
    0x4A: "WRITE_DIGITAL_MODULE_IMAGE", 0x4F: "READ_CPU", 0x83: "WRITE_GENERIC_OBJECT",
    0x9F: "ACTION_GENERIC_OBJECT", 0xFA: "MIRROR",
}


def hexs(data):
    return " ".join("%02X" % x for x in bytearray(data))


class Parser(object):
    """Decoupe un flux : scrutation, trame, EOT, ACK, NAK, octet isole."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += bytearray(data)
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
        if b[0] == EOT:
            return ("eot",), 1
        if b[0] == ACK:
            return ("ack",), 1
        if b[0] == NAK:
            return ("nak",), 1
        if b[0] != DLE:
            return ("stray", b[0]), 1
        if len(b) < 2:
            return None, 0
        if b[1] == ENQ:
            if len(b) < 3:
                return None, 0
            return ("poll", b[2]), 3
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
        raw = bytearray(b[:i + 1])
        return ("frame", b[2], data, sum(raw[:-1]) % 256 == raw[-1], raw), i + 1


def describe(direction, ev):
    addr, npdu, bcc_ok = ev[1], ev[2], ev[3]
    tail = "" if bcc_ok else "   !! BCC FAUX"
    if len(npdu) >= 6 and npdu[0] in (0x20, 0x22):
        xway, unite = npdu[:6], npdu[6:]
        name = ""
        if unite and direction.startswith("PL7"):
            name = REQUESTS.get(unite[0], "?")
        return "%s  adr %02X | X-WAY %s | UNI-TE %s   %s%s" % (
            direction, addr, hexs(xway), hexs(unite), name, tail)
    return "%s  adr %02X | %s%s" % (direction, addr, hexs(npdu), tail)


class Spy(object):
    def __init__(self, log, raw):
        self.log, self.raw = log, raw
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.frames = 0

    def stamp(self):
        return "%9.3f" % (time.time() - self.t0)

    def line(self, text, screen=True):
        with self.lock:
            text = "%s  %s" % (self.stamp(), text)
            if screen:
                print(text)
            self.log.write(text + "\n")
            self.log.flush()

    def raw_bytes(self, direction, data):
        with self.lock:
            self.raw.write("%s  %s  %s\n" % (self.stamp(), direction, hexs(data)))
            self.raw.flush()


def pump(src, dst, direction, spy, stop):
    """Lit src, ecrit tout de suite dans dst, puis decode."""
    parser = Parser()
    while not stop.is_set():
        try:
            data = src.read(1)
            if not data:
                continue
            n = src.inWaiting()
            if n:
                data += src.read(n)
            dst.write(data)
        except serial.SerialException as e:
            spy.line("ERREUR port %s : %s" % (direction, e))
            stop.set()
            return
        spy.raw_bytes(direction, data)
        for ev in parser.feed(data):
            if ev[0] == "frame":
                spy.frames += 1
                spy.line(describe(direction, ev))
            elif ev[0] == "nak":
                spy.line("%s  NAK" % direction)


def notes(spy, stop):
    read = raw_input if sys.version_info[0] == 2 else input  # noqa: F821
    while not stop.is_set():
        try:
            text = read()
        except (EOFError, KeyboardInterrupt):
            return
        if text.strip():
            spy.line("########## NOTE : %s" % text.strip())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plc", default="COM1", help="port reel relie a l'automate")
    p.add_argument("--pl7", default="COM10", help="moitie com0com cote proxy (PL7 utilise l'autre)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--log", default="ut_proxy.log")
    p.add_argument("--raw", default="ut_proxy_raw.log")
    a = p.parse_args()

    plc = serial.Serial(a.plc, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.05)
    pl7 = serial.Serial(a.pl7, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.05)
    plc.flushInput()
    pl7.flushInput()
    log = open(a.log, "a")
    raw = open(a.raw, "a")
    spy = Spy(log, raw)
    spy.line("===== debut capture  %s (automate) <-> %s (PL7)  %s" % (
        a.plc, a.pl7, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("Taper une note + Entree pour marquer le journal. Ctrl+C pour arreter.\n")

    stop = threading.Event()
    threads = [
        threading.Thread(target=pump, args=(plc, pl7, "API -> PL7", spy, stop)),
        threading.Thread(target=pump, args=(pl7, plc, "PL7 -> API", spy, stop)),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    note_thread = threading.Thread(target=notes, args=(spy, stop))
    note_thread.daemon = True
    note_thread.start()
    try:
        while not stop.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in threads:
        t.join(1)
    spy.line("===== fin capture, %d trames decodees" % spy.frames)
    plc.close()
    pl7.close()
    log.close()
    raw.close()
    print("Journaux : %s et %s" % (a.log, a.raw))


if __name__ == "__main__":
    main()
