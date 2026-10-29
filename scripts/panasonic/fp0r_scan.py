#!/usr/bin/env python3
"""Scanner DT FP0R via MEWTOCOL - lecture seule.

1) affiche les compteurs rapides CH0..CH3 (DT90300..DT90315)
2) scanne un bloc de DT utilisateur et montre ceux qui BOUGENT

    python scripts/panasonic/fp0r_scan.py --port COM6 --debut 0 --fin 200
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.panasonic import Fp0r, dint, open_port  # noqa: E402


def counters(plc):
    # DT90300..DT90315 = CH0..CH3 (valeur courante + consigne, 32 bits chacun)
    # FP0R mono-phase : CH0=X0, CH1=X1, CH2=X3, CH3=X4
    # Schema machine : X3 = codeur lame (cutter), X4 = codeur film
    w = plc.read_dt(90300, 16)
    return {
        "CH0 (X0)": dint(w, 0),
        "CH1 (X1)": dint(w, 4),
        "CH2 lame (X3)": dint(w, 8),
        "CH3 film (X4)": dint(w, 12),
    }


def scan_block(plc, start, end):
    return dict(zip(range(start, end + 1), plc.read_dt(start, end - start + 1)))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)  # 9600 a marche au labo
    p.add_argument("--parite", default="O")
    p.add_argument("--station", type=int, default=1)
    p.add_argument("--debut", type=int, default=0, help="premier DT scanne (mode 2)")
    p.add_argument("--fin", type=int, default=200, help="dernier DT scanne (mode 2)")
    a = p.parse_args()

    ser = open_port(a.port, a.baud, a.parite)
    plc = Fp0r(ser, a.station)
    print(f"{a.port} @ {a.baud} {a.parite}\n")
    try:
        mode = input("1 = compteurs  |  2 = scan DT changeants  > ").strip()
        if mode == "2":
            print(f"\nReference sur DT{a.debut}..DT{a.fin}. "
                  f"Lance la machine, puis observe.\n(Ctrl+C pour arreter)\n")
            base = scan_block(plc, a.debut, a.fin)
            while True:
                time.sleep(0.4)
                cur = scan_block(plc, a.debut, a.fin)
                moved = {k: (base[k], cur[k]) for k in cur if cur[k] != base[k]}
                if moved:
                    print("  ".join(f"DT{k}:{o}->{n}" for k, (o, n) in sorted(moved.items())))
                base = cur
        else:
            print("\nCompteurs rapides  (Ctrl+C pour arreter)\n")
            while True:
                c = counters(plc)
                print("   ".join(f"{k} = {v:>10}" for k, v in c.items()))
                time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
