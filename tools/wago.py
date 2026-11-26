#!/usr/bin/env python3
"""WAGO 750 (coupleur Ethernet 750-352...) en Modbus TCP, sans WAGO-I/O-CHECK.

    python tools/wago.py chercher                   coupleurs Modbus sur les reseaux du PC
    python tools/wago.py etat --ip 192.168.1.10     identite, voyant I/O, modules et valeurs
    python tools/wago.py surveiller --ip ...        valeurs des modules en direct (Ctrl+C)
    python tools/wago.py registres --ip ...         ce que le coupleur repond, registre par registre

Le PC doit etre dans le meme reseau que le coupleur (ex. PC 192.168.1.200).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols import wago as wg  # noqa: E402
from plc_online.web import wago_web  # noqa: E402


def cmd_chercher(extra):
    r = wg.search(extra)
    print("Reseaux du PC :", ", ".join(r["reseaux"]) or "aucun")
    for t in r["trouves"]:
        print(f"   {t['ip']:15}  {t['modele']}  {t.get('firmware', '')}")
    if not r["trouves"]:
        print("Aucun appareil Modbus TCP (port 502). Verifier cable, voyant LINK, adresse IP du PC.")


def cmd_etat(plc):
    st = wago_web.wago_status(plc)
    print(f"{st['modele']}  {st['reference']}  : {st['mode']}")
    for k, v in st["details"]:
        print(f"   {k:16} {v}")
    d = wago_web.wago_diagnostic(plc)
    print("\nModules :")
    for m in d["modules"]:
        print(f"   {m['emplacement']:2}  {m['configure']['nom']:10} {m['type']:24} {m['adresse']:10} {m['en_place']['nom']}")
    for a in d["alertes"]:
        print("!!", a)


def cmd_surveiller(plc):
    while True:
        d = wago_web.wago_diagnostic(plc)
        print(time.strftime("%H:%M:%S"), " | ".join(f"{m['emplacement']}: {m['en_place']['nom']}"
                                                   for m in d["modules"][1:]))
        time.sleep(1)


def cmd_registres(plc):
    for r in wago_web.raw_registers(plc):
        print(f"{r['texte']:28} {r['adresse']:16} : {r['valeur']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["chercher", "etat", "surveiller", "registres"])
    p.add_argument("--ip", default="")
    a = p.parse_args()
    if a.commande == "chercher":
        return cmd_chercher([a.ip] if a.ip else [])
    if not a.ip:
        p.error("--ip ADRESSE (trouver avec : python tools/wago.py chercher)")
    try:
        plc = wg.Wago(a.ip)
    except OSError as e:
        raise SystemExit(f"ECHEC : pas de reponse de {a.ip}:502 ({e})")
    try:
        if a.commande == "etat":
            cmd_etat(plc)
        elif a.commande == "registres":
            cmd_registres(plc)
        else:
            cmd_surveiller(plc)
    except wg.ModbusError as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        plc.close()


if __name__ == "__main__":
    main()
