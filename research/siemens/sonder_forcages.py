#!/usr/bin/env python3
"""Essaie plusieurs formes de la demande "liste des taches" (groupe 1, fonction 10) et
"lire tache" (fonction 11) pour trouver celle que la CPU accepte. Lecture seule : ces
demandes ne changent rien dans la CPU.

    py -3.14-32 research/siemens/sonder_forcages.py [--mpi 2]

Mettre un forcage dans STEP 7 avant (ex. A4.0 = 1), puis envoyer toute la sortie.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.protocols.s7 import S7300, S7Error  # noqa: E402

LIST_VARIANTS = [
    ("tp0 td4 + 4 zeros (Wireshark)", bytes.fromhex("0000 0004 00000000")),
    ("4 zeros, sans tailles", bytes.fromhex("00000000")),
    ("tp0 td0", bytes.fromhex("0000 0000")),
    ("tp0 td2 + 2 zeros", bytes.fromhex("0000 0002 0000")),
    ("2 zeros", bytes.fromhex("0000")),
    ("tp4 td0 + 4 zeros", bytes.fromhex("0004 0000 00000000")),
    ("tp0 td4 + 0000 0009", bytes.fromhex("0000 0004 0000 0009")),
    ("tp0 td4 + 0009 0000", bytes.fromhex("0000 0004 0009 0000")),
    ("vide", b""),
]


def try_one(plc, sub, label, data):
    try:
        answer = plc.userdata(1, sub, data)
        print(f"   OK   {label:<32} -> {len(answer)} octets : {answer.hex(' ')}")
        return answer
    except S7Error as e:
        print(f"   --   {label:<32} -> {e}")
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mpi", type=int, default=2)
    a = p.parse_args()
    with S7300(a.mpi) as plc:
        leds = plc.leds()
        print(f"Voyant FRCE : {leds.get('FRCE')}")
        try:
            rec = plc.read_szl(0x0132, 2)[1]
            print(f"SZL 0132 index 2 (fonctions de test) : {rec[0].hex(' ') if rec else 'vide'}")
        except S7Error as e:
            print(f"SZL 0132 index 2 : {e}")
        print("\nListe des taches (fonction 10) :")
        found = []
        for label, data in LIST_VARIANTS:
            answer = try_one(plc, 0x10, label, data)
            if answer is not None:
                found.append(answer)
        print("\nLire tache forcage (fonction 11), numeros 0 a 3 :")
        for seq in range(4):
            for label, data in ((f"tp0 td2 09 {seq:02X}", bytes.fromhex(f"0000 0002 09{seq:02X}")),
                                (f"09 {seq:02X} sans tailles", bytes.fromhex(f"09{seq:02X}"))):
                try_one(plc, 0x11, label, data)


if __name__ == "__main__":
    main()
