#!/usr/bin/env python3
"""Siemens S7-200 par le cable RS-232/PPI (port COM), sans Micro/WIN. Tout est aussi dans l'interface web.

    python tools/s7200.py etat                     CPU, RUN / STOP, defauts (SM), modules, cycle
    python tools/s7200.py lire VW0 I0.0 SMB6       lire des adresses
    python tools/s7200.py ecrire VW10 1234         ecrire UNE adresse, avec confirmation et relecture
    python tools/s7200.py televerser [DOSSIER]     lire tous les blocs (OB, DB, SDB) : un fichier .bin par bloc
    python tools/s7200.py charger DOSSIER          recharger les blocs .bin d'un dossier de televerser (STOP, confirmation)
    python tools/s7200.py brut                     octets bruts utiles a la recherche (SMB0..29, zone systeme 03)

Options : --port COM6, --baud 9600, --adresse 2 (automate), --local 0 (PC), --trace (trames PPI).
Cable RS-232/PPI : commutateurs 9600 bauds, mode PPI. Micro/WIN ferme.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols import ppi  # noqa: E402
from plc_online.web import s7200_web as sw  # noqa: E402


def cmd_etat(plc):
    st = sw.s7200_status(plc)
    print(f"{st['modele']}   {st['reference']}\nMode : {st['mode']}")
    for k, v in st["details"]:
        print(f"{k:10} : {v}")
    print("Defauts : " + ("aucun" if not st["alertes"] else ""))
    for a in st["alertes"]:
        print("   " + a)
    for u in sw.layout(plc.cpu, sw.modules(plc.read("SM", 0, 30))):
        io = ", ".join(f"{n} {u[k][1]}" for n, k in (("DI", "di"), ("DQ", "dq"), ("AI", "ai"), ("AQ", "aq")) if u[k][1])
        print(f"   {str(u['position']):4} {u['nom']:20} {io}  {', '.join(u['erreurs'])}")


def cmd_lire(plc, names):
    for n in names:
        try:
            v = sw.S7200Var(n)
            print(f"{n:>12} = {v.decode(v.read(plc))}")
        except (ValueError, ppi.PpiError) as e:
            print(f"{n:>12} : {e}")


def cmd_ecrire(plc, name, text):
    v = sw.S7200Var(name)
    old = v.decode(v.read(plc))
    if input(f"{name} = {old} -> {text} ? Taper OUI : ").strip() != "OUI":
        print("Rien ecrit.")
        return
    r = sw.write_values(plc, [{"adresse": name, "valeur": text}])["resultats"][0]
    print(f"{name} : avant {r['avant']}, relu {r['relu']} {'OK' if r['ok'] else '(different : reecrit par le programme ?)'}")


def cmd_televerser(plc, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    for kind in ("OB", "DB", "SDB"):
        try:
            numbers = plc.list_blocks(kind)
        except ppi.PlcRefused as e:
            print(f"{kind} : liste refusee ({e})")
            continue
        print(f"{kind} : {numbers or 'aucun'}")
        for n in numbers:
            try:
                blob = plc.upload(kind, n)
            except ppi.PlcRefused as e:
                print(f"   {kind}{n} : refuse ({e}) - fichiers essayes : _0{chr(ppi.BLOCK_TYPES[kind])}{n:05d} A / P / B")
                continue
            path = folder / f"{kind}{n}.bin"
            path.write_bytes(blob)
            print(f"   {path}  {sw.block_text(kind, n, blob)}")
            try:
                info = ppi.block_info(blob)
            except ValueError:
                continue
            print(f"      charge le {info['modifie']}, code {info['code']} octets" + (", PROTEGE par mot de passe" if info["protege"] else ""))
            if info["source"]:
                (folder / f"{kind}{n}.source.bin").write_bytes(info["source"])
                print(f"      programme Micro/WIN (decompresse) : {kind}{n}.source.bin, {len(info['source'])} octets")


def cmd_charger(plc, folder: Path):
    files = sorted(folder.glob("*.bin"))
    blocks = {}
    for f in files:
        if f.name.endswith(".source.bin"):
            continue
        data = f.read_bytes()
        try:
            info = ppi.block_info(data)
        except ValueError as e:
            sys.exit(f"{f} : {e}")
        blocks[(info["type"], info["numero"])] = data
    if not blocks:
        sys.exit(f"aucun bloc .bin dans {folder}")
    print("A charger :", ", ".join(sw.block_text(k, n, d) for (k, n), d in sorted(blocks.items())))
    print("Le programme actuel de l'automate sera remplace. L'automate passe en STOP : la machine s'arrete.")
    if input("Machine en securite ? Taper OUI pour charger : ").strip().upper() != "OUI":
        sys.exit("abandon")
    before = folder.parent / f"{folder.name}_avant_{time.strftime('%Y%m%d_%H%M%S')}"
    print(f"Copie de securite du programme actuel dans {before}")
    cmd_televerser(plc, before)
    if sw.running(plc):
        plc.stop()
        time.sleep(0.3)
        if sw.running(plc):
            sys.exit("L'automate reste en RUN : selecteur sur STOP ou TERM.")
    done = sw.load_program(plc, sw.pack_blocks(blocks), lambda text, *_: print("  ", text, end="\r"), [])
    print("\nCharges :", ", ".join(done))
    back = sw.read_program(plc, lambda *_: None)
    problems = [d for d in sw.diff(back, sw.pack_blocks(blocks)) if "absent de la copie" not in d]
    print("Relecture : " + ("identique" if not problems else ", ".join(problems)))
    print("L'automate est en STOP. Pour repartir : RUN dans l'interface web, ou le selecteur.")


def cmd_brut(plc):
    print("SMB0..29 :", plc.read("SM", 0, 30).hex(" "))
    for code, name in ((0x03, "zone 03 (infos systeme)"), (0x05, "zone 05 (SM)")):
        try:
            print(f"{name} :", plc.read_raw_area(code, 0, 40).hex(" "))
        except ppi.PpiError as e:
            print(f"{name} : {e}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["etat", "lire", "ecrire", "televerser", "charger", "brut"])
    p.add_argument("args", nargs="*")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--adresse", type=int, default=2)
    p.add_argument("--local", type=int, default=0)
    p.add_argument("--trace", action="store_true", help="afficher chaque trame PPI")
    a = p.parse_args()
    log = (lambda d, b: print(f"      {d} {b.hex(' ')}")) if a.trace else None
    try:
        plc = ppi.S7200(ppi.Ppi(ppi.open_port(a.port, a.baud), a.adresse, a.local, log=log)).connect()
        sw._identify(plc)
    except Exception as e:
        sys.exit(f"Connexion impossible sur {a.port} : {e}")
    try:
        if a.commande == "etat":
            cmd_etat(plc)
        elif a.commande == "lire":
            cmd_lire(plc, a.args or ["SMB6", "SMW22"])
        elif a.commande == "ecrire":
            if len(a.args) != 2:
                sys.exit("ecrire ADRESSE VALEUR (ex. ecrire VW10 1234)")
            cmd_ecrire(plc, *a.args)
        elif a.commande == "televerser":
            cmd_televerser(plc, Path(a.args[0] if a.args else f"s7200_blocs_{time.strftime('%Y%m%d_%H%M%S')}"))
        elif a.commande == "charger":
            if not a.args:
                sys.exit("charger DOSSIER (un dossier fait par televerser)")
            cmd_charger(plc, Path(a.args[0]))
        else:
            cmd_brut(plc)
    except ppi.PpiError as e:
        sys.exit(f"Erreur : {e}")
    finally:
        plc.close()


if __name__ == "__main__":
    main()
