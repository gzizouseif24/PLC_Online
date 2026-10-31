#!/usr/bin/env python3
"""S7-300 via le pilote S7ONLINE de STEP 7 (adaptateur USB MPI), sans SIMATIC Manager.

    py -3.14-32 tools/siemens.py etat                         identification, voyants, cle, protection
    py -3.14-32 tools/siemens.py diagnostic                   tampon de diagnostic (evenements dates)
    py -3.14-32 tools/siemens.py modules                      modules du rack 0 : emplacement, adresse, etat
    py -3.14-32 tools/siemens.py sauvegarder-config [DOSSIER] copie de la config materielle (SDB) de la CPU
    py -3.14-32 tools/siemens.py config                       modules configures dans la CPU (HW Config)
    python tools/siemens.py config --depuis DOSSIER           meme chose depuis une copie (sans CPU)
    py -3.14-32 tools/siemens.py sauvegarder-programme        copie de tous les blocs (OB FB FC DB SDB)
    py -3.14-32 tools/siemens.py comparer DOSSIER             le programme de la CPU a-t-il change ?
    python tools/siemens.py comparer DOSSIER --depuis AUTRE   compare deux copies (sans CPU)
    py -3.14-32 tools/siemens.py lire                         MB0..MB3
    py -3.14-32 tools/siemens.py lire --zone DB --db 10 --debut 20 --nombre 2

Options : --mpi 2, --rack 0, --slot 2, --trace (etapes de connexion).
Python 32 bits obligatoire, SIMATIC Manager ferme, interface PG/PC = USB.S7USB.1.
Lecture seule.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.s7 import (  # noqa: E402
    AREAS, DEFAULT_DLL, PROGRAM_KINDS, S7300, S7Error, compare_blocks, parse_block, parse_sdb1_modules, word_be)
from plc_online.protocols.s7_catalogue import catalog_name  # noqa: E402
from plc_online.protocols.s7_modules import MODULES  # noqa: E402

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
        name = info.get("nom_module") or catalog_name(info.get("reference"))
        print(f"CPU        : {info.get('reference', '?')}   {name}")
        versions = [f"{k} {info[v]}" for k, v in (("materiel", "materiel"), ("firmware", "firmware")) if info.get(v)]
        if versions:
            print(f"Versions   : {', '.join(versions)}")
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


def module_text(code):
    """'SM 322 DO16x... : 6ES7 322-1BH01, -1BH10' : one code covers a family of modules
    (same channel count and direction), so give the common name and every order number."""
    entries = MODULES.get(code)
    if not entries:
        return ""
    names = [n for _, n in entries]
    common = names[0]
    for n in names[1:]:
        while not n.startswith(common):
            common = common[:-1]
    if len(names) > 1 and "x" in common:
        common = common[:common.rindex("x") + 1] + "..."
    if len(common.strip()) < 4:
        return f"code partage par {len(entries)} modules differents (ex. {names[0]})"
    orders = [o for o, _ in entries]
    shown = ", ".join(o if i == 0 else o[5:] for i, o in enumerate(orders[:4]))
    return f"{common.strip()} : {shown}" + (", ..." if len(orders) > 4 else "")


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
        if m["type"] != "CPU":
            conf, real = module_text(m["type_config"]), module_text(m["type_reel"])
            if real:
                print(f"         en place  : {real}")
            if conf and m["different"]:
                print(f"         configure : {conf}")
    if any(m["different"] for m in mods):
        print("\n   La configuration materielle chargee dans la CPU ne correspond pas aux modules en place :")
        print("   corriger HW Config (ou les modules) puis recharger la configuration.")


def cmd_sauvegarder_config(plc, folder=None):
    """Raw copy of the hardware configuration: every system data block (SDB) of the CPU.
    The SDB format is not published by Siemens: decoding comes later, by comparison."""
    import hashlib
    import time
    numbers = plc.list_blocks("SDB")
    if not numbers:
        print("Aucun SDB dans la CPU (pas de configuration materielle chargee ?).")
        return
    folder = Path(folder or f"config_s7_{time.strftime('%Y%m%d_%H%M%S')}")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"{len(numbers)} SDB dans la CPU : {', '.join(str(n) for n in numbers)}")
    lines, total = [], 0
    for n in numbers:
        data = plc.upload_block("SDB", n)
        (folder / f"SDB{n}.bin").write_bytes(data)
        total += len(data)
        lines.append(f"SDB {n:<6} {len(data):6d} octets  sha256 {hashlib.sha256(data).hexdigest()[:16]}")
        print(f"   SDB {n:<6} {len(data):6d} octets")
    with open(folder / "resume.txt", "w", encoding="utf-8") as f:
        f.write(f"date : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            info = plc.identity()
            f.write(f"CPU  : {info.get('reference', '?')} firmware {info.get('firmware', '?')}\n")
        except S7Error:
            pass
        f.write("\n".join(lines) + "\n\nModules (SZL 0D91, type configure / reel) :\n")
        try:
            for m in plc.modules():
                f.write(f"   emplacement {m['emplacement']}: {m['type']} adresse {m['adresse']} "
                        f"type {m['type_config']:04X}/{m['type_reel']:04X} {m['etat']}\n")
        except S7Error as e:
            f.write(f"   non lus ({e})\n")
    print(f"\nConfiguration copiee : {folder}/  ({len(numbers)} SDB, {total} octets, + resume.txt)")


def cmd_config(sdb1: bytes, actual=None):
    """Configured modules (SDB 1); with `actual` (SZL 0D91 list) also what is plugged in."""
    info = parse_block(sdb1)
    mods = parse_sdb1_modules(sdb1)
    print(f"Configuration materielle (SDB 1, compilee le {info['date']}) :")
    real = {m["emplacement"]: m["type_reel"] for m in actual or []}
    differences = 0
    for m in mods:
        if m["type"] == "CPU":
            print(f"   emplacement {m['emplacement']:<2}  CPU   type {m['code']:04X}")
            continue
        prefix = {"entrees": "EB", "sorties": "AB"}.get(m["type"], "B")
        end = m["adresse"] + max(m["octets"], 1) - 1
        addr = f"{prefix}{m['adresse']}" + (f"..{prefix}{end}" if end > m["adresse"] else "")
        name = module_text(m["code"]) or "module inconnu"
        print(f"   emplacement {m['emplacement']:<2}  {m['type']:<8} {addr:<12} type {m['code']:04X}  {name}")
        if actual is not None:
            got = real.get(m["emplacement"])
            if got is None or got == 0:
                print("      !! aucun module en place a cet emplacement")
                differences += 1
            elif got != m["code"]:
                print(f"      !! en place : {got:04X}  {module_text(got) or 'module inconnu'}")
                differences += 1
    if actual is not None:
        print("\n   Modules en place conformes a la configuration." if not differences else
              f"\n   {differences} emplacement(s) different(s) de la configuration.")


BLOCK_FILE = re.compile(r"^(OB|FB|FC|DB|SDB)(\d+)\.bin$", re.IGNORECASE)


def upload_program(plc, progress=True):
    """{("OB", 1): bytes, ...} for every block of PROGRAM_KINDS in the CPU."""
    blocks = {}
    for kind in PROGRAM_KINDS:
        numbers = plc.list_blocks(kind)
        for i, n in enumerate(numbers, 1):
            if progress:
                print(f"\r   {kind:<3} {i}/{len(numbers)}   ", end="", flush=True)
            blocks[(kind, n)] = plc.upload_block(kind, n)
    if progress:
        print("\r" + " " * 30 + "\r", end="")
    return blocks


def load_program(folder):
    blocks = {}
    for f in Path(folder).iterdir():
        m = BLOCK_FILE.match(f.name)
        if m:
            blocks[(m.group(1).upper(), int(m.group(2)))] = f.read_bytes()
    if not blocks:
        raise S7Error(f"aucun bloc (OB1.bin, DB10.bin...) dans {folder}")
    return blocks


def block_line(kind, n, data):
    try:
        b = parse_block(data)
        return f"{kind:<3} {n:<5} {b['longueur']:6d} octets  {b['date']}  somme {b['somme']:04X}"
    except S7Error:
        return f"{kind:<3} {n:<5} {len(data):6d} octets  (format non reconnu)"


def cmd_sauvegarder_programme(plc, folder=None):
    """Full copy of the CPU: every OB, FB, FC, DB and SDB, as uploaded ("upload to PG")."""
    import time
    blocks = upload_program(plc)
    if not blocks:
        print("Aucun bloc dans la CPU.")
        return
    folder = Path(folder or f"programme_s7_{time.strftime('%Y%m%d_%H%M%S')}")
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for (kind, n), data in sorted(blocks.items(), key=lambda kv: (PROGRAM_KINDS.index(kv[0][0]), kv[0][1])):
        (folder / f"{kind}{n}.bin").write_bytes(data)
        lines.append(block_line(kind, n, data))
    with open(folder / "programme.txt", "w", encoding="utf-8") as f:
        f.write(f"date : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            info = plc.identity()
            f.write(f"CPU  : {info.get('reference', '?')} firmware {info.get('firmware', '?')}\n")
        except S7Error:
            pass
        f.write("\n".join(lines) + "\n")
    counts = ", ".join(f"{sum(1 for k in blocks if k[0] == kind)} {kind}" for kind in PROGRAM_KINDS
                       if any(k[0] == kind for k in blocks))
    print(f"Programme copie : {folder}/  ({counts}, {sum(map(len, blocks.values()))} octets, + programme.txt)")


def cmd_comparer(ref, now, ref_name, now_name):
    diffs = compare_blocks(ref, now)
    print(f"Reference : {ref_name} ({len(ref)} blocs)\nCompare   : {now_name} ({len(now)} blocs)\n")
    changes = [d for d in diffs if d[2] != "valeurs"]
    if not changes:
        print("   PROGRAMME IDENTIQUE a la reference.")
    else:
        print(f"   !! {len(changes)} bloc(s) different(s) :")
        for kind, n, verdict, detail in changes:
            print(f"      {kind:<3} {n:<5} {verdict:<9} {detail}")
    values = [d for d in diffs if d[2] == "valeurs"]
    if values:
        print(f"\n   {len(values)} DB avec d'autres valeurs actuelles (normal en marche) : "
              + ", ".join(f"DB{n}" for _, n, _, _ in values))


def cmd_lire(plc, a):
    data = plc.read_bytes(a.zone, a.debut, a.nombre, a.db)
    print("   octets :", " ".join(f"{x:02X}" for x in data))
    prefix = f"DB{a.db}.DBW" if a.zone == "DB" else f"{a.zone}W"
    for off in range(0, len(data) - 1, 2):
        print(f"   {prefix}{a.debut + off:<4}: {word_be(data, off)}   (big-endian)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", nargs="?", default="lire", choices=["etat", "diagnostic", "modules", "config", "sauvegarder-config",
                                                         "sauvegarder-programme", "comparer", "lire"])
    p.add_argument("--mpi", type=int, default=2, help="adresse MPI de la CPU")
    p.add_argument("--rack", type=int, default=0)
    p.add_argument("--slot", type=int, default=2)
    p.add_argument("--zone", choices=sorted(AREAS), default="M")
    p.add_argument("--db", type=int, default=0, help="numero de DB (zone DB)")
    p.add_argument("--debut", type=int, default=0, help="premier octet")
    p.add_argument("--nombre", type=int, default=4, help="nombre d'octets")
    p.add_argument("--dll", default=str(DEFAULT_DLL), help="chemin de libnodave.dll")
    p.add_argument("--trace", action="store_true", help="afficher les etapes de connexion")
    p.add_argument("--dossier", default=None, help="sauvegarder-config : dossier de destination")
    p.add_argument("--depuis", default=None, help="config / comparer : lire ce dossier au lieu de la CPU")
    p.add_argument("reference", nargs="?", default=None, help="comparer : dossier de reference")
    a = p.parse_args()

    if a.commande == "comparer" and not a.reference:
        p.error("comparer : donner le dossier de reference (copie faite avec sauvegarder-programme)")
    if a.commande == "comparer" and a.depuis:
        try:
            cmd_comparer(load_program(a.reference), load_program(a.depuis), a.reference, a.depuis)
        except (OSError, S7Error) as e:
            raise SystemExit(f"ECHEC : {e}")
        return

    if a.commande == "config" and a.depuis:
        try:
            cmd_config((Path(a.depuis) / "SDB1.bin").read_bytes())
        except (OSError, S7Error) as e:
            raise SystemExit(f"ECHEC : {e}")
        return

    try:
        with S7300(a.mpi, a.rack, a.slot, dll=a.dll, log=print if a.trace else None) as plc:
            if a.commande == "etat":
                cmd_etat(plc)
            elif a.commande == "diagnostic":
                cmd_diagnostic(plc)
            elif a.commande == "modules":
                cmd_modules(plc, a.rack)
            elif a.commande == "config":
                actual = None
                try:
                    actual = plc.modules(a.rack)
                except S7Error:
                    pass
                cmd_config(plc.upload_block("SDB", 1), actual)
            elif a.commande == "sauvegarder-programme":
                cmd_sauvegarder_programme(plc, a.dossier or a.reference)
            elif a.commande == "comparer":
                ref = load_program(a.reference)
                cmd_comparer(ref, upload_program(plc), a.reference, "CPU")
            elif a.commande == "sauvegarder-config":
                cmd_sauvegarder_config(plc, a.dossier)
            else:
                cmd_lire(plc, a)
    except S7Error as e:
        raise SystemExit(f"ECHEC : {e}")


if __name__ == "__main__":
    main()
