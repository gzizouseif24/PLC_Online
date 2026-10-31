#!/usr/bin/env python3
"""S7-300 via le pilote S7ONLINE de STEP 7 (adaptateur USB MPI), sans SIMATIC Manager.

    py -3.14-32 tools/siemens.py etat                         identification, voyants, cle, protection
    py -3.14-32 tools/siemens.py diagnostic                   tampon de diagnostic (evenements dates)
    py -3.14-32 tools/siemens.py modules                      modules du rack 0 : emplacement, adresse, etat
    py -3.14-32 tools/siemens.py lire                         MB0..MB3
    py -3.14-32 tools/siemens.py lire --zone DB --db 10 --debut 20 --nombre 2

Options : --mpi 2, --rack 0, --slot 2, --trace (etapes de connexion).
Python 32 bits obligatoire, SIMATIC Manager ferme, interface PG/PC = USB.S7USB.1.
Lecture seule.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.s7 import AREAS, DEFAULT_DLL, S7300, S7Error, word_be  # noqa: E402

BLINK = {0: "", 1: " (clignote)", 2: " (clignote lentement)"}
LED_TEXT = {
    "SF": "defaut groupe",
    "RUN": "RUN",
    "STOP": "STOP",
    "FRCE": "FORCAGE ACTIF",
    "BAF": "defaut pile",
    "BUS1F": "defaut bus interface 1 (MPI/DP)",
    "BUS2F": "defaut bus interface 2 (DP)",
}


def cmd_etat(plc):
    try:
        info = plc.identity()
        print(f"CPU        : {info.get('reference', '?')}   {info.get('nom_module', '')}")
        print(f"Versions   : materiel {info.get('materiel', '?')}, firmware {info.get('firmware', '?')}")
        if info.get("numero_serie"):
            print(f"N° serie   : {info['numero_serie']}")
        if info.get("nom_automate") or info.get("repere_installation"):
            print(f"Station    : {info.get('nom_automate', '')}   {info.get('repere_installation', '')}")
    except S7Error as e:
        print(f"Identification non lue ({e})")

    leds = plc.leds()
    if leds:
        on = {name for name, (state, blink) in leds.items() if state or blink}
        mode = "RUN" if "RUN" in on and "STOP" not in on else "STOP" if "STOP" in on else "?"
        print(f"\nMode       : {mode}")
        print("Voyants    :")
        for name, (state, blink) in leds.items():
            if state or blink:
                alarm = "!!" if name in ("SF", "FRCE", "BAF", "BUS1F", "BUS2F") else "  "
                print(f"   {alarm} {name:<6} {LED_TEXT.get(name, '')}{BLINK.get(blink, '')}")
        if not on & {"SF", "FRCE", "BAF", "BUS1F", "BUS2F"}:
            print("   aucun voyant de defaut allume")
    else:
        print("\nVoyants non lus (SZL 0174 non supportee par cette CPU ?)")

    try:
        p = plc.protection()
        print(f"\nCle        : {p['cle']}")
        print(f"Protection : niveau {p['protection_effective']} "
              f"(cle {p['protection_cle']}, parametre {p['protection_parametree']}"
              f"{', sans mot de passe' if p['protection_parametree'] == 0 else ''})")
        hw, prog = p["somme_config_materielle"], p["somme_programme"]
        if any(hw + prog):
            print(f"Sommes     : programme {prog[0]:04X}-{prog[1]:04X}, config materielle {hw[0]:04X}-{hw[1]:04X}"
                  + ("" if p["sommes_valides"] else "   (marquees non garanties par la CPU)"))
            print("             a noter : elles doivent changer si le programme ou la config change")
    except S7Error as e:
        print(f"\nCle et protection non lues ({e})")


def cmd_diagnostic(plc):
    entries = plc.diagnostic_buffer()
    if not entries:
        print("Tampon de diagnostic vide.")
        return
    print(f"Tampon de diagnostic : {len(entries)} evenement(s), le plus recent en premier\n")
    for i, e in enumerate(entries, 1):
        text = e["texte"] or f"evenement non repertorie ({e['classe']})"
        print(f"{i:3d}  {e['date']}  {e['id']:04X}  {text}")
        print(f"     {'':23}       {e['classe']}, info {e['info']}")


def cmd_modules(plc, rack=0):
    mods = plc.modules(rack)
    if not mods:
        print(f"Aucun module dans le rack {rack}.")
        return
    print(f"Rack {rack} :   (type = code du module : configure dans la CPU / reellement present)")
    for m in mods:
        addr = "" if m["type"] == "CPU" else f"adresse {m['adresse']}"
        alarm = "  " if m["etat"] == "ok" else "!!"
        codes = f"{m['type_config']:04X} / {m['type_reel']:04X}"
        print(f"   {alarm} emplacement {m['emplacement']:<2}  {m['type']:<8} {addr:<13} type {codes}   {m['etat']}")
    if any(m["different"] for m in mods):
        print("\n   La configuration materielle chargee dans la CPU ne correspond pas aux modules en place :")
        print("   corriger HW Config (ou les modules) puis recharger la configuration.")


def cmd_lire(plc, a):
    data = plc.read_bytes(a.zone, a.debut, a.nombre, a.db)
    print("   octets :", " ".join(f"{x:02X}" for x in data))
    prefix = f"DB{a.db}.DBW" if a.zone == "DB" else f"{a.zone}W"
    for off in range(0, len(data) - 1, 2):
        print(f"   {prefix}{a.debut + off:<4}: {word_be(data, off)}   (big-endian)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", nargs="?", default="lire", choices=["etat", "diagnostic", "modules", "lire"])
    p.add_argument("--mpi", type=int, default=2, help="adresse MPI de la CPU")
    p.add_argument("--rack", type=int, default=0)
    p.add_argument("--slot", type=int, default=2)
    p.add_argument("--zone", choices=sorted(AREAS), default="M")
    p.add_argument("--db", type=int, default=0, help="numero de DB (zone DB)")
    p.add_argument("--debut", type=int, default=0, help="premier octet")
    p.add_argument("--nombre", type=int, default=4, help="nombre d'octets")
    p.add_argument("--dll", default=str(DEFAULT_DLL), help="chemin de libnodave.dll")
    p.add_argument("--trace", action="store_true", help="afficher les etapes de connexion")
    a = p.parse_args()

    try:
        with S7300(a.mpi, a.rack, a.slot, dll=a.dll, log=print if a.trace else None) as plc:
            if a.commande == "etat":
                cmd_etat(plc)
            elif a.commande == "diagnostic":
                cmd_diagnostic(plc)
            elif a.commande == "modules":
                cmd_modules(plc, a.rack)
            else:
                cmd_lire(plc, a)
    except S7Error as e:
        raise SystemExit(f"ECHEC : {e}")


if __name__ == "__main__":
    main()
