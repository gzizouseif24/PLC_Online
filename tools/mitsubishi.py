#!/usr/bin/env python3
"""Mitsubishi FX (FX1N...) par le port de programmation (cable SC-09 / USB-SC09), sans GX Developer.

    python tools/mitsubishi.py info                    CPU, mode, erreurs, cycle, horloge
    python tools/mitsubishi.py lire D100 X27 M8000     lire des adresses (X / Y en octal)
    python tools/mitsubishi.py ecrire D100 1234        ecrire UNE adresse, avec confirmation et relecture
    python tools/mitsubishi.py test-entrees --modele FX1N-40MR --extensions FX2N-16EX
                                                       cocher chaque entree vue a 1 puis a 0 (Ctrl+C)
    python tools/mitsubishi.py programme               copier la memoire programme (lecture seule) en .bin

Options : --port COM5, --baud 9600 (7E1).
Ecriture : machine ARRETEE, avec l'accord du client, apres sauvegarde.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols import melsec_fx as fx  # noqa: E402
from plc_online.web import melsec_web as mw  # noqa: E402


def cmd_info(plc):
    st = plc.status()
    m, d = st["m"], st["d"]
    print(f"CPU        : {fx.cpu_text(d[8001])}, {d[8002]}k pas, {fx.MEMORY_TYPES.get(d[8003], d[8003])}")
    print(f"Mode       : {'RUN' if m[8000] else 'STOP'}")
    print(f"Cycle      : {d[8010] / 10:.1f} ms (mini {d[8011] / 10:.1f}, maxi {d[8012] / 10:.1f}), "
          f"chien de garde {d[8000]} ms")
    print(f"Horloge    : {mw.clock_text(st)}")
    errs = mw.faults(st)
    print("Erreurs    : " + ("aucune" if not errs else ""))
    for e in errs:
        print("   " + e)


def cmd_lire(plc, names):
    for n in names:
        try:
            v = mw.FxVar(n)
            print(f"{n:>10} = {v.decode(v.read(plc))}")
        except (ValueError, fx.FxError) as e:
            print(f"{n:>10} : {e}")


def cmd_ecrire(plc, name, text):
    v = mw.FxVar(name)
    old = v.decode(v.read(plc))
    if input(f"{name} = {old} -> {text} ? Taper OUI : ").strip() != "OUI":
        print("Rien ecrit.")
        return
    r = mw.write_values(plc, [{"adresse": name, "valeur": text}])["resultats"][0]
    print(f"{name} : avant {r['avant']}, relu {r['relu']} {'OK' if r['ok'] else '(different : reecrit par le programme ?)'}")


def cmd_test_entrees(plc, modele, extensions):
    units = mw.layout(modele, [mw.expansion_model(e) for e in extensions])
    inputs = [(first + i, label) for label, _, (first, count), _ in units for i in range(count)]
    if not inputs:
        print("Aucune entree a tester.")
        return
    print(f"Test de {len(inputs)} entrees : " + ", ".join(
        f"{label} {fx.name('X', first)}..{fx.name('X', first + count - 1)}"
        for label, _, (first, count), _ in units if count))
    print("Cablage (manuel materiel FX1N 5.1.2) : entrees de la base sur S/S commun.\n"
          "  NPN / contact vers 0V : S/S relie au 24V.  PNP / contact vers +24V : S/S relie au 0V.")
    print("Mettre chaque entree a 1 puis la relacher. (Ctrl+C pour arreter)\n")
    top = max(n for n, _ in inputs)
    seen_on, done, last = set(), set(), None
    try:
        while len(done) < len(inputs):
            bits = plc.read_bits("X", 0, top + 1)
            state = {n: bits[n] for n, _ in inputs}
            if last is None:
                stuck = [fx.name("X", n) for n, v in state.items() if v]
                if stuck:
                    print("   Deja a 1 au depart : " + ", ".join(stuck) + " (a relacher, ou entree en court-circuit)")
                    seen_on.update(n for n, v in state.items() if v)
            else:
                for n, v in state.items():
                    if v != last[n]:
                        if v:
                            seen_on.add(n)
                        elif n in seen_on:
                            done.add(n)
                        print(f"   {time.strftime('%H:%M:%S')}  {fx.name('X', n)} -> {int(v)}   {'OK' if n in done else ''}")
            last = state
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    missing = [fx.name("X", n) for n, _ in inputs if n not in done]
    print(f"\n{len(done)}/{len(inputs)} entrees testees." + (f" Pas vues : {', '.join(missing)}" if missing else " Toutes bonnes."))


def cmd_programme(plc, out_dir):
    d = plc.status()["d"]
    steps = d[8002] * 1000 if d[8002] in (2, 4, 8) else 8000
    print(f"{fx.cpu_text(d[8001])}, {steps} pas : lecture de la memoire programme (8000h, lecture seule)...")
    data = plc.read_program_memory(steps, lambda n, total: print(f"\r   {n}/{total} octets", end="", flush=True))
    print()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"fx_programme_{time.strftime('%Y%m%d_%H%M%S')}.bin"
    path.write_bytes(data)
    print(f"Ecrit : {path} ({len(data)} octets : 92 de parametres + programme). Copie brute, a comparer entre "
          "deux lectures ; pas de rechargement dans l'automate par cet outil.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["info", "lire", "ecrire", "test-entrees", "programme"])
    p.add_argument("args", nargs="*")
    p.add_argument("--port", default="COM5")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--modele", default="FX1N-40MR", help="unite de base (test-entrees)")
    p.add_argument("--extensions", default="", help="ex. FX2N-16EX,FX0N-8ER (dans l'ordre, test-entrees)")
    p.add_argument("--dossier", default="sauvegardes/mitsubishi", help="programme : dossier de sortie")
    a = p.parse_args()
    try:
        plc = fx.FxPlc(fx.open_port(a.port, a.baud))
    except Exception as e:
        sys.exit(f"Port {a.port} : {e}")
    try:
        if a.commande == "info":
            cmd_info(plc)
        elif a.commande == "lire":
            cmd_lire(plc, a.args or ["D8001", "M8000"])
        elif a.commande == "ecrire":
            if len(a.args) != 2:
                sys.exit("ecrire ADRESSE VALEUR (ex. ecrire D100 1234)")
            cmd_ecrire(plc, *a.args)
        elif a.commande == "test-entrees":
            cmd_test_entrees(plc, a.modele, [e for e in a.extensions.split(",") if e.strip()])
        else:
            cmd_programme(plc, Path(a.dossier))
    except fx.FxError as e:
        sys.exit(f"Erreur : {e}. Cable sur le port de programmation ? GX Developer ferme ? Bon port COM ?")
    finally:
        plc.close()


if __name__ == "__main__":
    main()
