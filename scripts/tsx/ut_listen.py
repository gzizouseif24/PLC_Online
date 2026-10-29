#!/usr/bin/env python3
"""TSX Uni-Telway - ECOUTE SEULE du port TER (n'envoie jamais rien).

Montre ce que l'automate (maitre du bus) envoie : les scrutations "DLE ENQ adresse"
de chaque esclave, et ce qui y repond. Sert a verifier cable, vitesse, parite,
et quelles adresses d'esclave l'automate scrute (la future adresse du PC).

Fermer PL7 / Unity avant : ils utilisent le meme port COM.
Cable TSX PCX 1031 : laisser le selecteur dans la position utilisee avec PL7.

    python ut_listen.py --port COM6                    (9600 8 O 1, defaut TER)
    python ut_listen.py --port COM6 --essai            (essaie vitesses et parites)
    python ut_listen.py --port COM6 --brut             (affiche chaque octet recu)

pip install pyserial
"""
import argparse
import time
from collections import Counter, defaultdict

DLE, STX, EOT, ENQ, ACK, NAK = 0x10, 0x02, 0x04, 0x05, 0x06, 0x15
MAX_LENGTH = 134  # longueur max des donnees d'une trame


# ---------- decodage du flux (aucun envoi) ----------

class Parser:
    """Decoupe le flux recu en evenements : scrutation, trame, EOT, ACK, NAK, octet isole."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes) -> list:
        self.buf += data
        events = []
        while self.buf:
            ev, used = self._next()
            if used == 0:  # evenement incomplet : attendre la suite
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
        return self._frame()

    def _frame(self):
        """DLE STX adresse longueur [DLE] donnees (DLE doubles) BCC."""
        b = self.buf
        if len(b) < 4:
            return None, 0
        length = b[3]
        i = 4
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
        bcc_ok = sum(raw[:-1]) % 256 == raw[-1]
        return ("frame", b[2], bytes(data), bcc_ok, raw), i + 1


# ---------- statistiques ----------

class Stats:
    def __init__(self):
        self.polls = Counter()
        self.answers = defaultdict(Counter)   # adresse scrutee -> ce qui a suivi
        self.frames = Counter()
        self.bad_bcc = 0
        self.stray = 0
        self.ack = self.nak = 0
        self._last_poll = None

    def add(self, ev):
        kind = ev[0]
        if kind == "poll":
            if self._last_poll is not None:
                self.answers[self._last_poll]["rien"] += 1
            self._last_poll = ev[1]
            self.polls[ev[1]] += 1
            return
        if kind == "frame":
            self.frames[ev[1]] += 1
            if not ev[3]:
                self.bad_bcc += 1
        elif kind == "stray":
            self.stray += 1
        elif kind == "ack":
            self.ack += 1
        elif kind == "nak":
            self.nak += 1
        if self._last_poll is not None and kind in ("eot", "frame"):
            self.answers[self._last_poll]["EOT" if kind == "eot" else "trame"] += 1
            self._last_poll = None

    def report(self, seconds: float) -> str:
        if not self.polls and not self.frames:
            return f"  rien de reconnu en {seconds:.1f} s (octets isoles : {self.stray})"
        lines = [f"  {sum(self.polls.values())} scrutations en {seconds:.1f} s, "
                 f"octets isoles : {self.stray}, BCC faux : {self.bad_bcc}, ACK {self.ack} / NAK {self.nak}"]
        for addr in sorted(self.polls):
            ans = self.answers[addr]
            detail = ", ".join(f"{k} {v}" for k, v in ans.most_common()) or "-"
            lines.append(f"    adresse {addr:3d} (0x{addr:02X}) : scrutee {self.polls[addr]:5d} fois"
                         f"  -> reponses : {detail}")
        for addr, n in sorted(self.frames.items()):
            lines.append(f"    trames pour/de l'adresse {addr} : {n}")
        return "\n".join(lines)


# ---------- port serie ----------

def open_port(port, baud, parity, stop, rts, dtr):
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.bytesize = port, baud, 8
    ser.parity, ser.stopbits, ser.timeout = parity, stop, 0.05
    if rts is not None:
        ser.rts = rts
    if dtr is not None:
        ser.dtr = dtr
    ser.open()
    return ser


def describe(ev) -> str:
    kind = ev[0]
    if kind == "poll":
        return f"scrutation adresse {ev[1]}"
    if kind == "frame":
        return (f"TRAME adresse {ev[1]} {'BCC ok' if ev[3] else 'BCC FAUX'} : "
                f"{' '.join(f'{x:02X}' for x in ev[4])}")
    if kind == "stray":
        return f"octet isole {ev[1]:02X}"
    return kind.upper()


def listen(ser, seconds, raw=False, show_frames=True):
    """Ecoute `seconds` secondes (None = jusqu'a Ctrl+C). Ne fait que lire."""
    parser, stats = Parser(), Stats()
    start = last_report = time.time()
    try:
        while seconds is None or time.time() - start < seconds:
            data = ser.read(ser.in_waiting or 1)
            if data and raw:
                print(f"{time.time() - start:9.3f}  {' '.join(f'{x:02X}' for x in data)}")
            for ev in parser.feed(data):
                stats.add(ev)
                if show_frames and ev[0] == "frame":
                    print("  " + describe(ev))
            if seconds is None and time.time() - last_report >= 3:
                print(stats.report(time.time() - last_report))
                print()
                stats, last_report = Stats(), time.time()
    except KeyboardInterrupt:
        pass
    return stats, time.time() - last_report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--stop", type=int, default=1, choices=[1, 2])
    p.add_argument("--essai", action="store_true", help="essaie plusieurs vitesses et parites, 3 s chacune")
    p.add_argument("--brut", action="store_true", help="affiche chaque octet recu en hexa")
    p.add_argument("--rts", type=int, choices=[0, 1], help="forcer la ligne RTS (si rien n'est recu)")
    p.add_argument("--dtr", type=int, choices=[0, 1], help="forcer la ligne DTR (si rien n'est recu)")
    a = p.parse_args()
    rts = None if a.rts is None else bool(a.rts)
    dtr = None if a.dtr is None else bool(a.dtr)

    if a.essai:
        print(f"Essai des reglages sur {a.port} (ecoute seule, 3 s par reglage, Ctrl+C pour arreter)\n")
        results = []
        try:
            for baud in (9600, 19200, 4800, 2400, 1200):
                for parity in ("O", "E", "N"):
                    ser = open_port(a.port, baud, parity, 1, rts, dtr)
                    try:
                        stats, _ = listen(ser, 3, show_frames=False)
                    finally:
                        ser.close()
                    n = sum(stats.polls.values())
                    print(f"  {baud:5d} 8{parity}1 : {n:5d} scrutations, octets isoles {stats.stray}")
                    results.append((n, baud, parity))
        except KeyboardInterrupt:
            print("  (arrete)")
        n = max((r[0] for r in results), default=0)
        if n:
            best = [r for r in results if r[0] > 0]
            bauds = sorted({r[1] for r in best})
            print(f"\n-> Vitesse : {', '.join(map(str, bauds))} baud.")
            print("   La parite ne se voit pas en ecoute (Windows ne signale pas les erreurs de parite) :")
            print("   garder O (defaut du port TER) ; le premier echange avec l'automate la confirmera.")
        else:
            print("\n-> Aucune scrutation reconnue. Verifier : cable/selecteur PCX 1031, port COM, "
                  "PL7 ferme, port TER en Uni-Telway maitre. Essayer --rts 0 ou --dtr 1.")
        return

    print(f"Ecoute {a.port} @ {a.baud} 8{a.parite}{a.stop} - rien n'est envoye. Ctrl+C pour arreter.\n")
    ser = open_port(a.port, a.baud, a.parite, a.stop, rts, dtr)
    try:
        stats, seconds = listen(ser, None, raw=a.brut)
    finally:
        ser.close()
    print(stats.report(seconds))


if __name__ == "__main__":
    main()
