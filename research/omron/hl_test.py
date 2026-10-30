#!/usr/bin/env python3
"""Omron Host Link - test d'echo TS (CJ1M, port peripherique).

L'automate doit renvoyer exactement la trame envoyee : prouve cable, reglages et FCS.
Fermer CX-Programmer (ou passer hors ligne) avant : il utilise le meme port COM.

    python research/omron/hl_test.py --port COM6
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.protocols.hostlink import Cj1m, HostLinkError, open_port  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--unite", type=int, default=0, help="numero d'unite Host Link")
    p.add_argument("--texte", default="HELLO CJ1M")
    a = p.parse_args()

    ser = open_port(a.port, a.baud)
    try:
        ok = Cj1m(ser, a.unite).test(a.texte)
    except HostLinkError as e:
        raise SystemExit(f"-> {e}")
    finally:
        ser.close()
    if ok:
        print("-> OK : echo identique, FCS valide. Host Link fonctionne.")
    else:
        raise SystemExit("-> reponse recue mais differente de la trame envoyee")


if __name__ == "__main__":
    main()
