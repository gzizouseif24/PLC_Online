#!/usr/bin/env python3
"""FP0R - lecture du programme automate via MEWTOCOL (commande RP).

Lit la zone programme et la sauvegarde en .bin (mots 16 bits little-endian)
et en _hex.txt (un pas par ligne) pour inspection.

    python scripts/panasonic/fp0r_read_program.py --port COM6 --baud 115200
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.panasonic import Fp0r, MewtocolError, open_port  # noqa: E402

DEFAULT_CAPACITY = 5000  # FP0R-C16 si RT ne repond pas


def save_as_bin(words, filename):
    with open(filename, "wb") as f:
        for w in words:
            f.write(struct.pack("<H", w))
    print(f"  Enregistre : {filename} ({len(words)} mots, {len(words) * 2} octets)")


def save_as_hex(words, filename):
    with open(filename, "w") as f:
        for i, w in enumerate(words):
            f.write(f"{i:5d}  {w:04X}\n")
    print(f"  Enregistre : {filename}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--parite", default="O")
    p.add_argument("--station", type=int, default=1)
    a = p.parse_args()

    print("=" * 50)
    print("FP0R - lecture du programme (commande RP)")
    print("=" * 50)
    print(f"Port : {a.port}  Vitesse : {a.baud}  Parite : {a.parite}  Station : {a.station}\n")

    ser = open_port(a.port, a.baud, a.parite, timeout=5)
    plc = Fp0r(ser, a.station)
    try:
        print("Lecture de l'etat automate (RT)...")
        try:
            status = plc.read_status()
            for label, key in (("Modele", "model"), ("Version", "version"), ("Mode", "mode")):
                print(f"  {label} : {status.get(key, '?')}")
            cap = status.get("capacity_steps", 0)
            print(f"  Capacite : {cap} pas ({status.get('capacity_k', '?')}k)")
        except MewtocolError as e:
            print(f"  RT en echec : {e}")
            cap = int(input("  Capacite programme en pas (ex. 2700 pour 2,7k) > ").strip())
        cap = cap or DEFAULT_CAPACITY

        print(f"\nLecture du programme (jusqu'a {cap} pas)...")
        try:
            words = plc.read_program(
                cap, progress=lambda s, e: print(f"\r  Pas {s:5d}..{e:5d}", end="", flush=True))
        except MewtocolError as e:
            print(f"\n  Erreur : {e}")
            return
        print()
        if not words:
            print("Aucune donnee programme recue.")
            return
        print(f"\nTotal : {len(words)} pas lus")

        name = input("Nom du fichier (defaut : fp0r_live) > ").strip() or "fp0r_live"
        save_as_bin(words, f"{name}.bin")
        save_as_hex(words, f"{name}_hex.txt")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
