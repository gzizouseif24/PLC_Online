#!/usr/bin/env python3
"""Omron Host Link - lecture de mots DM (commande RD), lecture seule.

Fermer CX-Programmer avant : il utilise le meme port COM.

    python scripts/omron/hl_read.py --port COM6 --debut 0 --nombre 4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.omron import Cj1m, HostLinkError, open_port, signed16  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--unite", type=int, default=0)
    p.add_argument("--debut", type=int, default=0, help="premier mot D lu")
    p.add_argument("--nombre", type=int, default=4, help="nombre de mots")
    a = p.parse_args()

    ser = open_port(a.port, a.baud)
    try:
        words = Cj1m(ser, a.unite).read_dm(a.debut, a.nombre)
    except HostLinkError as e:
        raise SystemExit(f"-> {e}")
    finally:
        ser.close()
    for i, w in enumerate(words):
        print(f"D{a.debut + i:<5} = {w:5d}   (signe {signed16(w):6d}, hex {w:04X})")


if __name__ == "__main__":
    main()
