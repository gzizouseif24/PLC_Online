#!/usr/bin/env python3
"""Panasonic FP0R par le port outil (MEWTOCOL), sans FPWIN.

    python tools/panasonic.py compteurs              compteurs rapides CH0..CH3 en direct (Ctrl+C)
    python tools/panasonic.py scan --debut 0 --fin 200   afficher les DT qui changent (Ctrl+C)
    python tools/panasonic.py ecrire [DT] [VALEUR]   ecrire UN DT, avec confirmation et relecture
    python tools/panasonic.py programme              copier le programme dans .bin + _hex.txt

Options : --port COM6, --baud (9600 ; 115200 par defaut pour programme), --parite O, --station 1.
Ecriture : machine ARRETEE, avec l'accord du client, apres sauvegarde.
"""
import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.mewtocol import Fp0r, MewtocolError, dint, open_port  # noqa: E402

DEFAULT_CAPACITY = 5000  # FP0R-C16 si RT ne repond pas


# ---- compteurs / scan (lecture seule) ----

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


def cmd_compteurs(plc):
    print("\nCompteurs rapides  (Ctrl+C pour arreter)\n")
    while True:
        c = counters(plc)
        print("   ".join(f"{k} = {v:>10}" for k, v in c.items()))
        time.sleep(0.3)


def scan_block(plc, start, end):
    return dict(zip(range(start, end + 1), plc.read_dt(start, end - start + 1)))


def cmd_scan(plc, start, end):
    print(f"\nReference sur DT{start}..DT{end}. Lance la machine, puis observe.\n(Ctrl+C pour arreter)\n")
    base = scan_block(plc, start, end)
    while True:
        time.sleep(0.4)
        cur = scan_block(plc, start, end)
        moved = {k: (base[k], cur[k]) for k in cur if cur[k] != base[k]}
        if moved:
            print("  ".join(f"DT{k}:{o}->{n}" for k, (o, n) in sorted(moved.items())))
        base = cur


# ---- ecriture d'un DT ----

def cmd_ecrire(plc, dt, new):
    old = plc.read_dt(dt, 1)[0]
    print(f"\nDT{dt} actuel = {old}")
    if new is None:
        new = int(input(f"Nouvelle valeur pour DT{dt} > ").strip())
    ok = input(f"Ecrire DT{dt} : {old} -> {new} ?  Machine arretee ? (tapez OUI) > ")
    if ok.strip() != "OUI":
        print("Annule, rien n'a ete ecrit.")
        return
    plc.write_dt(dt, new)
    time.sleep(0.2)
    back = plc.read_dt(dt, 1)[0]
    print(f"Relu : DT{dt} = {back}  {'OK' if back == new & 0xFFFF else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python tools/panasonic.py ecrire {dt} {old}")


# ---- lecture du programme (commande RP) ----

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


def cmd_programme(plc):
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["compteurs", "scan", "ecrire", "programme"])
    p.add_argument("args", nargs="*", help="ecrire : DT et VALEUR (sinon demandes)")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=None, help="9600 par defaut, 115200 pour programme")
    p.add_argument("--parite", default="O")
    p.add_argument("--station", type=int, default=1)
    p.add_argument("--debut", type=int, default=0, help="scan : premier DT")
    p.add_argument("--fin", type=int, default=200, help="scan : dernier DT")
    a = p.parse_args()

    baud = a.baud or (115200 if a.commande == "programme" else 9600)
    dt = new = None
    if a.commande == "ecrire":
        if len(a.args) > 2:
            p.error("usage : ecrire [DT] [VALEUR]")
        dt = int(a.args[0]) if a.args else int(input("Registre DT a modifier (ex. 914) > ").strip())
        new = int(a.args[1]) if len(a.args) == 2 else None

    ser = open_port(a.port, baud, a.parite, timeout=5 if a.commande == "programme" else 1)
    plc = Fp0r(ser, a.station)
    print(f"{a.port} @ {baud} {a.parite}")
    try:
        if a.commande == "compteurs":
            cmd_compteurs(plc)
        elif a.commande == "scan":
            cmd_scan(plc, a.debut, a.fin)
        elif a.commande == "ecrire":
            cmd_ecrire(plc, dt, new)
        else:
            cmd_programme(plc)
    except MewtocolError as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
