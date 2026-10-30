#!/usr/bin/env python3
"""S7-300 - lecture d'octets via le pilote S7ONLINE de STEP 7, lecture seule.

Python 32 bits obligatoire, SIMATIC Manager ferme, interface PG/PC = USB.S7USB.1.

    py -3.14-32 tools/siemens.py                 (MB0..MB3)
    py -3.14-32 tools/siemens.py --zone DB --db 10 --debut 20 --nombre 2
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.s7 import AREAS, DEFAULT_DLL, S7300, S7Error, word_be  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mpi", type=int, default=2, help="adresse MPI de la CPU")
    p.add_argument("--rack", type=int, default=0)
    p.add_argument("--slot", type=int, default=2)
    p.add_argument("--zone", choices=sorted(AREAS), default="M")
    p.add_argument("--db", type=int, default=0, help="numero de DB (zone DB)")
    p.add_argument("--debut", type=int, default=0, help="premier octet")
    p.add_argument("--nombre", type=int, default=4, help="nombre d'octets")
    p.add_argument("--dll", default=str(DEFAULT_DLL), help="chemin de libnodave.dll")
    a = p.parse_args()

    try:
        with S7300(a.mpi, a.rack, a.slot, dll=a.dll, log=print) as plc:
            data = plc.read_bytes(a.zone, a.debut, a.nombre, a.db)
    except S7Error as e:
        raise SystemExit(f"   ECHEC : {e}")

    print("   octets :", " ".join(f"{x:02X}" for x in data))
    prefix = f"DB{a.db}.DBW" if a.zone == "DB" else f"{a.zone}W"
    for off in range(0, len(data) - 1, 2):
        print(f"   {prefix}{a.debut + off:<4}: {word_be(data, off)}   (big-endian)")


if __name__ == "__main__":
    main()
