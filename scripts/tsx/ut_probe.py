#!/usr/bin/env python3
"""TSX Uni-Telway - sonde des requetes de LECTURE, pour trouver les codes objets du TSX.

Envoie une liste fixe de requetes de lecture a l'adresse 0 et affiche celles que
l'automate accepte. Seuls les codes de lecture listes dans READ_ONLY sont autorises :
aucune ecriture, aucun RUN/STOP ne peut partir.

Compte environ 2 a 3 s par requete (attente de la scrutation).
Garder ut_test.py dans le meme dossier : ce script l'utilise.

    python ut_probe.py --port COM6
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ut_test import Slave, UniteError, hexs  # noqa: E402

# Requetes autorisees : lectures uniquement.
#   0x00..0x07 : lectures UNI-TE V1 (0x00 = bit interne %M, confirme sur TSX par x-way-uni-te ;
#                les autres sont des lectures de bits/mots, role exact a confirmer)
#   0x36       : lecture d'objets (segment, type, adresse, nombre)
READ_ONLY = set(range(0x00, 0x08)) | {0x36}

# Segments et types candidats pour 0x36 (0x64 bits internes et 0x68 mots internes probables)
SEGMENTS = [0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B]
TYPES = {0x05: "bit", 0x06: "octet", 0x07: "mot", 0x08: "double mot"}


def candidates():
    for code in sorted(READ_ONLY - {0x36}):
        yield f"V1 code {code:02X} adresse 0", [code, None, 0x00, 0x00]
    for seg in SEGMENTS:
        for typ, name in TYPES.items():
            yield (f"36 segment {seg:02X} type {typ:02X} ({name}) adresse 0 x1",
                   [0x36, None, seg, typ, 0x00, 0x00, 0x01, 0x00])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresse", type=int, default=1)
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    a = p.parse_args()

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    slave = Slave(ser, a.adresse, a.categorie)
    accepted = []
    print(f"{a.port} @ {a.baud} 8{a.parite}1 - lectures seulement\n")
    try:
        for label, unite in candidates():
            assert unite[0] in READ_ONLY, "requete hors liste de lecture"
            unite[1] = a.categorie
            try:
                answer = slave.request(unite, timeout=8)
            except UniteError as e:
                print(f"  {label:<45} -> pas de reponse : {e}")
                continue
            if answer and answer[0] == 0xFD:
                print(f"  {label:<45} -> refuse (FD)")
            else:
                print(f"  {label:<45} -> ACCEPTE : {hexs(answer)}")
                accepted.append((label, answer))
    except KeyboardInterrupt:
        print("  (arrete)")
    finally:
        ser.close()

    print("\nRequetes acceptees :")
    for label, answer in accepted:
        print(f"  {label:<45} {hexs(answer)}")
    if not accepted:
        print("  aucune")


if __name__ == "__main__":
    main()
