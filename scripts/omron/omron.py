#!/usr/bin/env python3
"""Omron CJ1M par le port peripherique (Host Link), sans CX-Programmer.

    python omron.py etat                          modele, mode, defauts, pile, horloge
    python omron.py erreurs                       journal d'erreurs de l'automate
    python omron.py lire D 100 20                 lire D100..D119   (zones : CIO W H A D T C)
    python omron.py lire CIO 0 2                  entrees / sorties : mots + bits
    python omron.py surveiller CIO 0 4            afficher ce qui change, bit par bit (Ctrl+C)
    python omron.py ecrire D 100 1234             ecrire UN mot, avec confirmation et relecture
    python omron.py run | monitor | programme     changer de mode, avec confirmation
    python omron.py sauvegarder-donnees [FICHIER] tous les D, H, W dans un fichier CSV
    python omron.py restaurer-donnees FICHIER     remettre les D, H, W du CSV (mots changes seulement)
    python omron.py sauvegarder [FICHIER]         copier le programme dans un fichier (experimental)
    python omron.py comparer FICHIER              le programme a-t-il change ? (experimental)

Options : --port COM6, --baud 9600, --unite 0.
Adresse aussi possible comme dans CX-Programmer : lire D100 20, surveiller W10 2.

Reglages automate (rapport §4.2) : port peripherique en Host Link, 9600 7-E-2, unite 0, DIP 4 ON.
Fermer CX-Programmer avant : il utilise le meme port COM.

Confirme sur CJ1M : echo, lecture D (RD). Commandes C-mode (CIO, H, A, ecriture, mode) : format
du manuel, a valider. Commandes FINS (W, T, C, D10000+, etat, horloge, journal, programme) :
a valider sur l'automate d'essai.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.omron import (  # noqa: E402
    AREAS, FATAL_BITS, FINS_MODES, FINS_STATUS, NON_FATAL_BITS, Cj1m, FinsRefused, HostLinkError,
    PlcRefused,
    error_log_text, open_port, parse_address, signed16)


def bit_name(area, word, bit):
    return f"{'' if area == 'CIO' else area}{word}.{bit:02d}"


def fmt_word(area, addr, v):
    name = f"{area}{addr}" if area != "CIO" else f"CIO {addr}"
    line = f"{name:<9} = {v:5d}   (signe {signed16(v):6d}, hex {v:04X})"
    if AREAS[area]["bits"]:
        b = f"{v:016b}"
        line += f"   bits 15..0 : {b[0:4]} {b[4:8]} {b[8:12]} {b[12:16]}"
    return line


def flags(value, table):
    return [table.get(n, f"bit {n}") for n in range(15, -1, -1) if value >> n & 1]


# ---- commands ----

def cmd_etat(plc):
    try:
        cd = plc.controller_data()
        print(f"Automate : {cd['modele']}   version {cd['version']}")
    except HostLinkError as e:
        try:
            print(f"Automate : code modele {plc.model_code()}   (FINS 0501 : {e})")
        except HostLinkError as e2:
            print(f"Automate : modele non lu ({e2})")

    try:
        s = plc.controller_status()
    except HostLinkError as e:
        print(f"Etat FINS non lu ({e})")
        print(f"Etat brut (MS) : {plc.status_cmode()}")
        return
    mode = FINS_MODES.get(s["mode"], f"inconnu ({s['mode']:02X})")
    run = FINS_STATUS.get(s["etat"], f"etat {s['etat']:02X}")
    print(f"Mode : {mode}   ({run})")

    print("\nDefauts :")
    fatal, non_fatal = flags(s["fatal"], FATAL_BITS), flags(s["non_fatal"], NON_FATAL_BITS)
    for f in fatal:
        print(f"   !! FATAL      {f}")
    for f in non_fatal:
        print(f"   !  non fatal  {f}")
    if not fatal and not non_fatal:
        print("   aucun defaut signale")
    print(f"   (brut : fatal A401 = {s['fatal']:04X}, non fatal A402 = {s['non_fatal']:04X})")
    if s["fal"]:
        print(f"   FAL/FALS numero {s['fal']}")
    if s["message"]:
        print(f"   message : {s['message']}")

    try:
        c = plc.clock()
        print(f"\nHorloge automate : {c['date']} {c['heure']}")
    except HostLinkError as e:
        print(f"\nHorloge non lue ({e})")
    try:
        n = len(plc.error_log())
        print(f"Journal d'erreurs : {n} enregistrement(s){'  -> python omron.py erreurs' if n else ''}")
    except HostLinkError as e:
        print(f"Journal d'erreurs non lu ({e})")


def cmd_erreurs(plc):
    records = plc.error_log()
    if not records:
        print("Journal d'erreurs vide.")
        return
    print(f"Journal d'erreurs : {len(records)} enregistrement(s)\n")
    for i, r in enumerate(records):
        print(f"   {i + 1:3d}   {r['date']}   code {r['code']:04X} / {r['code2']:04X}   {error_log_text(r['code'])}")


def cmd_lire(plc, area, start, count):
    for i, v in enumerate(plc.read(area, start, count)):
        print("   " + fmt_word(area, start + i, v))


def cmd_surveiller(plc, area, start, count):
    print(f"Surveillance {area} {start}..{start + count - 1} (Ctrl+C pour arreter)\n")
    base = plc.read(area, start, count)
    t0 = time.time()
    while True:
        cur = plc.read(area, start, count)
        for i, (old, new) in enumerate(zip(base, cur)):
            if old == new:
                continue
            stamp = f"{time.time() - t0:8.1f} s"
            if AREAS[area]["bits"]:
                for bit in range(16):
                    if (old ^ new) >> bit & 1:
                        print(f"   {stamp}   {bit_name(area, start + i, bit):<9} : {old >> bit & 1} -> {new >> bit & 1}")
            else:
                print(f"   {stamp}   {area}{start + i} : {signed16(old)} -> {signed16(new)}")
        base = cur


def confirm(question):
    return input(f"{question} (tapez OUI) > ").strip() == "OUI"


def cmd_ecrire(plc, area, addr, value):
    old = plc.read(area, addr, 1)[0]
    print(f"{area}{addr} actuel = {old} (signe {signed16(old)})")
    if not confirm(f"Ecrire {area}{addr} : {signed16(old)} -> {value} ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    try:
        plc.write(area, addr, [value])
    except PlcRefused as e:
        if e.code == "01":
            print("Refuse en mode RUN. Passer en MONITOR : python omron.py monitor")
            return
        raise
    back = plc.read(area, addr, 1)[0]
    ok = back == value & 0xFFFF
    print(f"Relu : {area}{addr} = {back} (signe {signed16(back)})  {'OK' if ok else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python omron.py ecrire {area} {addr} {signed16(old)}")


def cmd_mode(plc, mode):
    if not confirm(f"Passer l'automate en {mode.upper()} ? Machine en securite ?"):
        print("Annule.")
        return
    plc.set_mode(mode)
    print(f"{mode.upper()} accepte par l'automate.")
    try:
        print(f"Mode relu : {FINS_MODES.get(plc.controller_status()['mode'], '?')}")
    except HostLinkError:
        print("Verifier le voyant RUN.")


def read_area_to_end(plc, area, progress):
    """Read a whole area; stops where the PLC refuses (memory smaller than the table says)."""
    values, size = [], AREAS[area]["size"]
    n = plc.max_block(area)
    while len(values) < size:
        n = min(n, size - len(values))
        try:
            values += plc.read(area, len(values), n)
        except (PlcRefused, FinsRefused) as e:
            if e.code not in ("04", "15", 0x1103, 0x1104):
                raise
            if n > 1:          # the end is inside this block: smaller blocks
                n //= 2
                continue
            break
        progress(len(values))
    return values


def cmd_sauvegarder_donnees(plc, filename=None, zones=("D", "H", "W")):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = filename or f"omron_donnees_{stamp}.csv"
    data = {}
    for area in zones:
        t0 = time.time()
        data[area] = read_area_to_end(
            plc, area, lambda n: print(f"\rLecture {area}... {n} mots", end="", flush=True))
        print(f"\rLecture {area} : {len(data[area])} mots en {time.time() - t0:.0f} s" + " " * 10)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# date;{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# automate;{model_name(plc)}\n")
        f.write("zone;adresse;valeur;signe;hex\n")
        for area, values in data.items():
            for i, v in enumerate(values):
                f.write(f"{area};{i};{v};{signed16(v)};{v:04X}\n")
    print(f"Donnees : {filename}")


RESTORABLE = ("D", "H", "W")     # never CIO (outputs) or A (system)


def model_name(plc):
    try:
        return plc.controller_data()["modele"]
    except HostLinkError:
        return "?"


def read_data_csv(filename):
    """CSV from sauvegarder-donnees -> ({zone: {address: value}}, model or None)."""
    zones, model = {}, None
    with open(filename, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# automate;"):
                model = line.split(";", 1)[1]
            if not line or line.startswith("#") or line.startswith("zone;"):
                continue
            zone, addr, value = line.split(";")[:3]
            zones.setdefault(zone.upper(), {})[int(addr)] = int(value)
    return zones, model


def changed_runs(addrs):
    """Sorted addresses -> (start, length) of consecutive runs."""
    runs = []
    for a in sorted(addrs):
        if runs and runs[-1][0] + runs[-1][1] == a:
            runs[-1][1] += 1
        else:
            runs.append([a, 1])
    return runs


def read_addresses(plc, area, addrs):
    lo, hi = min(addrs), max(addrs)
    return dict(zip(range(lo, hi + 1), plc.read(area, lo, hi - lo + 1)))


def cmd_restaurer_donnees(plc, filename):
    zones, model_in_file = read_data_csv(filename)
    model = model_name(plc)
    if model_in_file and model_in_file not in ("?", model):
        print(f"ATTENTION : fichier pris sur {model_in_file}, automate connecte : {model}")
    skipped = [z for z in zones if z not in RESTORABLE]
    if skipped:
        print(f"Zones du fichier ignorees : {' '.join(skipped)} (seules {' '.join(RESTORABLE)} sont restaurees)")
    diffs = {}
    for area in RESTORABLE:
        saved = {a: v for a, v in zones.get(area, {}).items() if a < AREAS[area]["size"]}
        if not saved:
            continue
        current = read_addresses(plc, area, saved)
        d = {a: v for a, v in saved.items() if current[a] != v}
        if d:
            diffs[area] = d
            print(f"{area} : {len(d)} mots differents du fichier, par exemple :")
            for a in sorted(d)[:5]:
                print(f"   {area}{a:<6} automate = {signed16(current[a]):6d}   fichier = {signed16(d[a]):6d}")
    if not diffs:
        print("Rien a restaurer : l'automate a deja les valeurs du fichier.")
        return
    total = sum(len(d) for d in diffs.values())
    if not confirm(f"Ecrire ces {total} mots dans l'automate ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    try:
        for area, d in diffs.items():
            for start, length in changed_runs(d):
                plc.write(area, start, [d[a] for a in range(start, start + length)])
    except PlcRefused as e:
        if e.code == "01":
            print("Refuse en mode RUN. Passer en MONITOR (python omron.py monitor) puis relancer.")
            return
        raise
    wrong = []
    for area, d in diffs.items():
        after = read_addresses(plc, area, d)
        wrong += [f"{area}{a}" for a in d if after[a] != d[a]]
    if wrong:
        print(f"!! {len(wrong)} mots differents apres ecriture, premier : {wrong[0]}")
    else:
        print(f"Restauration OK : {total} mots ecrits et verifies.")


def do_upload(plc):
    t0 = time.time()
    blob = plc.read_program(progress=lambda n: print(f"\r   {n:7d} octets", end="", flush=True))
    print(f"\r   {len(blob)} octets lus en {time.time() - t0:.0f} s" + " " * 20)
    return blob


def cmd_sauvegarder(plc, filename=None):
    import hashlib
    model = model_name(plc)
    print(f"Automate {model} : lecture du programme...")
    blob = do_upload(plc)
    filename = filename or f"omron_{time.strftime('%Y%m%d_%H%M%S')}.cjbak"
    with open(filename, "wb") as f:
        f.write(blob)
    with open(filename + ".txt", "w", encoding="utf-8") as f:
        f.write(f"date     : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"automate : {model}\n")
        f.write(f"taille   : {len(blob)} octets\n")
        f.write(f"sha256   : {hashlib.sha256(blob).hexdigest()}\n")
    print(f"Sauvegarde : {filename}  (+ {filename}.txt)")


def cmd_comparer(plc, filename):
    with open(filename, "rb") as f:
        saved = f.read()
    blob = do_upload(plc)
    if blob == saved:
        print(f"IDENTIQUE : le programme de l'automate est le meme que {filename}")
        return
    diffs = [i for i in range(min(len(blob), len(saved))) if blob[i] != saved[i]]
    print(f"DIFFERENT de {filename} : automate {len(blob)} octets, fichier {len(saved)} octets, "
          f"{len(diffs)} octets differents")
    if diffs:
        print(f"   premiere difference a l'octet {diffs[0]}, derniere a l'octet {diffs[-1]}")


# ---- arguments ----

def parse_target(p, cmd, args):
    """'D 100 20' or 'D100 20' -> (area, word, number)."""
    usage = (f"usage : {cmd} ZONE ADRESSE {'VALEUR' if cmd == 'ecrire' else 'NOMBRE'}"
             f"   ou   {cmd} D100 {'VALEUR' if cmd == 'ecrire' else 'NOMBRE'}   (zones : {' '.join(AREAS)})")
    try:
        if len(args) == 3 and args[0].upper() in AREAS:
            area, word, bit = parse_address(args[0] + args[1])
        elif len(args) == 2:
            area, word, bit = parse_address(args[0])
        else:
            p.error(usage)
        if bit is not None:
            p.error(f"{cmd} travaille sur des mots : donner l'adresse du mot, sans .bit "
                    f"(surveiller affiche ensuite chaque bit qui change)")
        value = int(args[-1])
        if cmd == "ecrire" and not -32768 <= value <= 65535:
            p.error("valeur hors -32768..65535")
        return area, word, value
    except ValueError as e:
        p.error(f"{e}\n{usage}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["etat", "erreurs", "lire", "surveiller", "ecrire",
                                        "run", "monitor", "programme",
                                        "sauvegarder-donnees", "restaurer-donnees", "sauvegarder", "comparer"])
    p.add_argument("args", nargs="*", help="zone, adresse, nombre ou valeur ; ou fichier")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--unite", type=int, default=0, help="numero d'unite Host Link")
    p.add_argument("--zones", default="D,H,W", help="sauvegarder-donnees : zones, ex. D,H,W,CIO,A")
    a = p.parse_args()

    if a.commande in ("lire", "surveiller", "ecrire"):
        area, n1, n2 = parse_target(p, a.commande, a.args)
    if a.commande == "comparer" and len(a.args) != 1:
        p.error("usage : comparer FICHIER.cjbak")
    if a.commande == "restaurer-donnees" and len(a.args) != 1:
        p.error("usage : restaurer-donnees FICHIER.csv")
    zones = [z.strip().upper() for z in a.zones.split(",")]
    if any(z not in AREAS for z in zones):
        p.error(f"--zones : zones possibles {' '.join(AREAS)}")

    ser = open_port(a.port, a.baud)
    plc = Cj1m(ser, a.unite)
    try:
        if a.commande == "etat":
            cmd_etat(plc)
        elif a.commande == "erreurs":
            cmd_erreurs(plc)
        elif a.commande == "lire":
            cmd_lire(plc, area, n1, n2)
        elif a.commande == "surveiller":
            cmd_surveiller(plc, area, n1, n2)
        elif a.commande == "ecrire":
            cmd_ecrire(plc, area, n1, n2)
        elif a.commande in ("run", "monitor", "programme"):
            cmd_mode(plc, a.commande)
        elif a.commande == "sauvegarder-donnees":
            cmd_sauvegarder_donnees(plc, a.args[0] if a.args else None, zones)
        elif a.commande == "restaurer-donnees":
            cmd_restaurer_donnees(plc, a.args[0])
        elif a.commande == "sauvegarder":
            cmd_sauvegarder(plc, a.args[0] if a.args else None)
        elif a.commande == "comparer":
            cmd_comparer(plc, a.args[0])
    except (HostLinkError, ValueError) as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
