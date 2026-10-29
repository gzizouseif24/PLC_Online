#!/usr/bin/env python3
"""FP0R - ecriture d'UN seul registre DT, avec confirmation et relecture.

A utiliser machine ARRETEE, avec l'accord du client, apres sauvegarde.

    python scripts/panasonic/fp0r_write.py --port COM6
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.panasonic import Fp0r, open_port  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O")
    p.add_argument("--station", type=int, default=1)
    a = p.parse_args()

    dt = int(input("Registre DT a modifier (ex. 914) > ").strip())
    ser = open_port(a.port, a.baud, a.parite)
    plc = Fp0r(ser, a.station)
    try:
        old = plc.read_dt(dt, 1)[0]
        print(f"\nDT{dt} actuel = {old}")
        new = int(input(f"Nouvelle valeur pour DT{dt} > ").strip())
        ok = input(f"Ecrire DT{dt} : {old} -> {new} ?  Machine arretee ? (tapez OUI) > ")
        if ok.strip() != "OUI":
            print("Annule, rien n'a ete ecrit.")
            return
        plc.write_dt(dt, new)
        time.sleep(0.2)
        back = plc.read_dt(dt, 1)[0]
        print(f"Relu : DT{dt} = {back}  {'OK' if back == new & 0xFFFF else '!! DIFFERENT'}")
        print(f"Pour revenir en arriere : relancez et ecrivez {old}.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
