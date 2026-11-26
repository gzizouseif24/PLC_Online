#!/usr/bin/env python3
"""Panasonic FP0R par le port outil (MEWTOCOL), sans FPWIN.

    python tools/panasonic.py chercher               trouver vitesse / parite / bits / station du port outil
    python tools/panasonic.py scan --debut 0 --fin 200   afficher les DT qui changent (Ctrl+C)
    python tools/panasonic.py ecrire [DT] [VALEUR]   ecrire UN DT, avec confirmation et relecture
    python tools/panasonic.py programme              copier le programme dans .bin + _hex.txt
    python tools/panasonic.py test-entrees --modeles E16P,E16P   (extensions 1, 2 ; ou --positions 1,2 --entrees 8)
                                                     cocher chaque entree vue a 1 puis a 0 (Ctrl+C)

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


# ---- test des entrees : chaque entree doit passer a 1 puis revenir a 0 ----

def input_names(positions, per_unit):
    """[(word, bit, 'X20'), ...]: position 0 = CPU (X0..), n = extension n (X(2n)0..).
    per_unit: one number for all, or {position: inputs}."""
    out = []
    for pos in positions:
        word = 2 * pos
        count = per_unit[pos] if isinstance(per_unit, dict) else per_unit
        for i in range(count):
            w, b = word + i // 16, i % 16
            out.append((w, b, f"X{w}{b:X}" if w else f"X{b:X}"))
    return out


def cmd_test_entrees(plc, positions, per_unit):
    inputs = input_names(positions, per_unit)
    words = sorted({w for w, _, _ in inputs})
    seen_on, done = set(), set()
    print(f"Test de {len(inputs)} entrees : {inputs[0][2]}..{inputs[-1][2]}"
          f"{' (' + str(len(positions)) + ' positions)' if len(positions) > 1 else ''}")
    print("Mettre chaque entree a 1 puis la relacher. (Ctrl+C pour arreter)\n")
    last = None
    try:
        _test_loop(plc, inputs, words, seen_on, done)
    except KeyboardInterrupt:
        pass
    missing = [n for _, _, n in inputs if n not in done]
    print(f"\n{len(done)}/{len(inputs)} entrees testees."
          + (f" Pas vues : {', '.join(missing)}" if missing else " Toutes bonnes."))


def _test_loop(plc, inputs, words, seen_on, done):
    last = None
    while len(done) < len(inputs):
        values = {}
        for w in words:
            values[w] = plc.read_contact_words("X", w, 1)[0]
        state = {name: (values[w] >> b) & 1 for w, b, name in inputs}
        if last is not None:
            for name, v in state.items():
                if v != last[name]:
                    if v:
                        seen_on.add(name)
                    elif name in seen_on:
                        done.add(name)
                    mark = "OK" if name in done else ""
                    print(f"   {time.strftime('%H:%M:%S')}  {name} -> {v}   {mark}")
        else:
            stuck = [n for n, v in state.items() if v]
            if stuck:
                print("   Deja a 1 au depart : " + ", ".join(stuck) + " (a relacher, ou entree en court-circuit)")
                seen_on.update(stuck)
        last = state
        time.sleep(0.05)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["chercher", "scan", "ecrire", "programme", "test-entrees"])
    p.add_argument("args", nargs="*", help="ecrire : DT et VALEUR (sinon demandes)")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=None, help="9600 par defaut, 115200 pour programme")
    p.add_argument("--parite", default="O")
    p.add_argument("--station", type=int, default=1)
    p.add_argument("--debut", type=int, default=0, help="scan : premier DT")
    p.add_argument("--fin", type=int, default=200, help="scan : dernier DT")
    p.add_argument("--positions", default="1", help="test-entrees : 0 = CPU, 1..3 = extensions (ex. 1,2)")
    p.add_argument("--entrees", type=int, default=8, help="test-entrees : entrees par position (8 pour un E16)")
    p.add_argument("--modeles", default="", help="test-entrees : modeles des extensions 1, 2, 3 (ex. E16P,E32T)")
    a = p.parse_args()

    if a.commande == "chercher":
        from plc_online.web.panasonic_web import search
        print(f"Recherche sur {a.port} (environ 1 min 30)...")
        r = search(a.port, lambda text, i, n: print(f"   {i:2}/{n}  {text}", flush=True))
        print("\n" + r["texte"])
        if r["trouve"]:
            g = r["reglages"]
            print(f"Dans l'interface web : vitesse {g['baud']}, parite {g['parite']}, bits {g['bits']}, stop {g['stop']}, "
                  f"station {g['station']}")
        return

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
        if a.commande == "scan":
            cmd_scan(plc, a.debut, a.fin)
        elif a.commande == "ecrire":
            cmd_ecrire(plc, dt, new)
        elif a.commande == "test-entrees":
            if a.modeles:
                from plc_online.web.panasonic_web import EXPANSIONS, expansion_model
                per = {}
                for pos, text in enumerate(a.modeles.split(","), 1):
                    model = expansion_model(text)
                    kind = EXPANSIONS.get(model) if model else None
                    if model is None or kind == "mots":
                        raise SystemExit(f"{text.strip()} : modele inconnu ou analogique (E8X, E8R, E16X, E16P, E32T...)")
                    if model and kind[0]:
                        per[pos] = kind[0]
                if not per:
                    raise SystemExit("aucune entree TOR dans ces modeles")
                cmd_test_entrees(plc, sorted(per), per)
            else:
                cmd_test_entrees(plc, [int(x) for x in a.positions.split(",") if x.strip()], a.entrees)
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
