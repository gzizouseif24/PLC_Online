#!/usr/bin/env python3
"""Omron CJ1M par le port peripherique (Host Link), sans CX-Programmer.

    python tools/omron.py etat                          modele, mode, defauts, pile, horloge
    python tools/omron.py erreurs                       journal d'erreurs de l'automate
    python tools/omron.py parametres [FICHIER]          reglages automate, unites, table E/S (lecture)
    python tools/omron.py lire D 100 20                 lire D100..D119   (zones : CIO W H A D T C)
    python tools/omron.py lire CIO 0 2                  entrees / sorties : mots + bits
    python tools/omron.py surveiller CIO 0 4            afficher ce qui change, bit par bit (Ctrl+C)
    python tools/omron.py ecrire D 100 1234             ecrire UN mot, avec confirmation et relecture
    python tools/omron.py run | monitor | programme     changer de mode, avec confirmation
    python tools/omron.py sauvegarder-donnees [FICHIER] tous les D, H, W dans un fichier CSV
    python tools/omron.py restaurer-donnees FICHIER     remettre les D, H, W du CSV (mots changes seulement)
    python tools/omron.py sauvegarder [FICHIER]         copier le programme dans un fichier (experimental)
    python tools/omron.py comparer FICHIER              le programme a-t-il change ? (experimental)

Options : --port COM6, --baud 9600, --unite 0, --trace (affiche les trames).
Adresse aussi possible comme dans CX-Programmer : lire D100 20, surveiller W10 2.

Reglages automate (rapport §4.2) : port peripherique en Host Link, 9600 7-E-2, unite 0, DIP 4 ON.
Fermer CX-Programmer avant : il utilise le meme port COM.

Confirme sur CJ1M : echo, lecture D (RD). Le reste suit le manuel Omron W342 (commandes de
communication CS/CJ) mais n'a pas encore tourne sur l'automate : C-mode (CIO, H, A, ecriture,
mode) et FINS (W, T, C, D10000+, etat, horloge, journal, programme).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.hostlink import (  # noqa: E402
    AREAS, FATAL_BITS, FINS_MODES, NON_FATAL_BITS, decode_plc_setup, rack_layout, Cj1m, FinsRefused, HostLinkError,
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

    fins = True
    try:
        s = plc.controller_status()
    except HostLinkError as e:
        fins = False
        print(f"(FINS 0601 refuse : {e})\n(etat lu par MS + A400..A402)")
        s = plc.status_area_a()
    mode = FINS_MODES.get(s["mode"], f"inconnu ({s['mode']:02X})")
    if fins:
        run = "CPU en attente (standby)" if s["standby"] else (
            "programme en cours" if s["en_cours"] else "programme arrete")
        print(f"Mode : {mode}   ({run})")
        print(f"Pile : {'presente' if s['pile_presente'] else '!! ABSENTE'}")
    else:
        print(f"Mode : {mode}")

    print("\nDefauts :")
    fatal, non_fatal = flags(s["fatal"], FATAL_BITS), flags(s["non_fatal"], NON_FATAL_BITS)
    for f in fatal:
        print(f"   !! FATAL      {f}")
    for f in non_fatal:
        print(f"   !  non fatal  {f}")
    if not fatal and not non_fatal:
        print("   aucun defaut signale")
    print(f"   (brut : fatal = {s['fatal']:04X}, non fatal = {s['non_fatal']:04X})")
    if s["code_erreur"]:
        print(f"   erreur la plus grave : code {s['code_erreur']:04X}  {error_log_text(s['code_erreur'])}")
    if s["message"]:
        print(f"   message : {s['message']}")

    try:
        c = plc.clock()
        source = ""
    except HostLinkError:
        c, source = plc.clock_area_a(), "   (lue dans A351..A354)"
    print(f"\nHorloge automate : {c['date']} {c['heure']}{source}")
    show_cards(plc)
    n = len(read_error_log(plc)[0])
    print(f"Journal d'erreurs : {n} enregistrement(s){'  -> python tools/omron.py erreurs' if n else ''}")


def read_error_log(plc):
    """FINS 2102, or A100..A199 if FINS is refused. Returns (records, source)."""
    try:
        return plc.error_log(), "FINS 2102"
    except HostLinkError:
        return plc.error_log_area_a(), "A100..A199"


def cmd_erreurs(plc):
    records, source = read_error_log(plc)
    if not records:
        print("Journal d'erreurs vide.")
        return
    print(f"Journal d'erreurs : {len(records)} enregistrement(s)   (lu par {source})\n")
    for i, r in enumerate(records):
        print(f"   {i + 1:3d}   {r['date']}   code {r['code']:04X} / {r['code2']:04X}   {error_log_text(r['code'])}")


def show_cards(plc):
    """Cards of the registered I/O table, with their CIO word and its live value."""
    try:
        cards = rack_layout(plc.io_table(40))
    except HostLinkError as e:
        print(f"\nCartes : table E/S non lue ({e})")
        return
    print("\nCartes (table E/S enregistree) :")
    if not cards:
        print("   aucune carte enregistree")
    for c in cards:
        where = f"rack {c['rack']} emplacement {c['slot']:02d}"
        if c["cio"] is None:
            print(f"   {where}  {c['model']}")
            continue
        words = plc.read("CIO", c["cio"], c["words"])
        for k, v in enumerate(words):
            b = f"{v:016b}"
            print(f"   {where}  {c['model']:<11} {c['kind']:<18} CIO {c['cio'] + k:<4}"
                  f" = {b[0:4]} {b[4:8]} {b[8:12]} {b[12:16]}  (bits 15..0)")
    if any(c["cio"] is None for c in cards):
        print("   (carte inconnue : envoyer le code et le modele vu dans CX-Programmer pour l'ajouter)")


def hexdump(words, per_line=8):
    lines = []
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        if any(chunk):
            lines.append(f"   +{i:<5d} " + " ".join(f"{w:04X}" for w in chunk))
    return lines


UNIT_KINDS = [(0x00, 0x00, "CPU"), (0x10, 0x1F, "unite CPU Bus n°"), (0x20, 0x7F, "unite E/S speciale n°"),
              (0xE1, 0xE1, "carte interne")]


def cmd_parametres(plc, filename=None):
    """PLC Setup, units and registered I/O table (read-only)."""
    setup = plc.plc_setup()
    print("Reglages automate (PLC Setup) :")
    for label, text in decode_plc_setup(setup):
        print(f"   {label:<48} {text}")

    print("\nUnites declarees (CPU, CPU Bus, E/S speciales ; pas les cartes E/S simples) :")
    try:
        for addr, model in plc.units():
            kind = next((f"{t}{addr - lo if lo else ''}" for lo, hi, t in UNIT_KINDS if lo <= addr <= hi),
                        f"adresse {addr:02X}")
            print(f"   {addr:02X}  {kind:<24} {model}")
    except HostLinkError as e:
        print(f"   non lues ({e})")

    table = plc.io_table()
    used = [i for i, w in enumerate(table) if w]
    print(f"\nTable E/S enregistree : {len(used)} mots non nuls sur {len(table)} (format non documente,"
          f" a comparer avec la table E/S de CX-Programmer)")
    for c in rack_layout(table):
        cio = f"CIO {c['cio']}" if c["cio"] is not None else "CIO ?"
        print(f"   rack {c['rack']} emplacement {c['slot']:02d} : {c['code']:04X}  {c['model']} {c['kind']}  {cio}")
    print("   Brut :")
    for line in hexdump(table)[:16]:
        print("   " + line)

    filename = filename or f"omron_parametres_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# date {time.strftime('%Y-%m-%d %H:%M:%S')}\n# PLC Setup (zone 8010), 512 mots, hex\n")
        f.write("\n".join(hexdump(setup)) + "\n")
        f.write("# Table E/S enregistree (zone 8012), 1280 mots, hex (lignes nulles omises)\n")
        f.write("\n".join(hexdump(table)) + "\n")
    print(f"\nCopie brute : {filename}")


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


def current_mode(plc):
    try:
        return FINS_MODES.get(plc.controller_status()["mode"], "?")
    except HostLinkError:
        return plc.status_cmode()["mode"]


def make_writable(plc):
    """Writes are refused in RUN (W342: end code 01). Offer MONITOR: the program keeps running.
    Returns the mode to go back to afterwards ('RUN' or None), or False if the user declines."""
    if current_mode(plc) != "RUN":
        return None
    print("L'automate est en RUN : il refuse les ecritures.")
    if not confirm("Passer en MONITOR pour ecrire ? (le programme continue de tourner)"):
        print("Annule, rien n'a ete ecrit.")
        return False
    plc.set_mode("monitor")
    print(f"Mode : {current_mode(plc)}")
    return "RUN"


def back_to_run(plc, previous):
    if previous == "RUN" and confirm("Remettre l'automate en RUN ?"):
        plc.set_mode("run")
        print(f"Mode : {current_mode(plc)}")
    elif previous == "RUN":
        print("L'automate reste en MONITOR (python tools/omron.py run pour revenir en RUN).")


def cmd_ecrire(plc, area, addr, value):
    old = plc.read(area, addr, 1)[0]
    print(f"{area}{addr} actuel = {old} (signe {signed16(old)})")
    if not confirm(f"Ecrire {area}{addr} : {signed16(old)} -> {value} ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    previous = make_writable(plc)
    if previous is False:
        return
    plc.write(area, addr, [value])
    back = plc.read(area, addr, 1)[0]
    ok = back == value & 0xFFFF
    print(f"Relu : {area}{addr} = {back} (signe {signed16(back)})  {'OK' if ok else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python tools/omron.py ecrire {area} {addr} {signed16(old)}")
    back_to_run(plc, previous)


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


def read_addresses(plc, area, addrs, show=False):
    """Current values of the words from min(addrs) to max(addrs), with a progress counter."""
    lo, hi = min(addrs), max(addrs)
    values, t0 = [], time.time()
    while lo + len(values) <= hi:
        n = min(300, hi + 1 - lo - len(values))
        values += plc.read(area, lo + len(values), n)
        if show:
            print(f"\rLecture {area} dans l'automate... {len(values)} / {hi - lo + 1} mots", end="", flush=True)
    if show:
        print(f"\rLecture {area} : {len(values)} mots en {time.time() - t0:.0f} s" + " " * 20)
    return dict(zip(range(lo, hi + 1), values))


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
        current = read_addresses(plc, area, saved, show=True)
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
    previous = make_writable(plc)
    if previous is False:
        return
    for area, d in diffs.items():
        for start, length in changed_runs(d):
            plc.write(area, start, [d[a] for a in range(start, start + length)])
    wrong = []
    for area, d in diffs.items():
        after = read_addresses(plc, area, d)
        wrong += [f"{area}{a}" for a in d if after[a] != d[a]]
    if wrong:
        print(f"!! {len(wrong)} mots differents apres ecriture, premier : {wrong[0]}")
    else:
        print(f"Restauration OK : {total} mots ecrits et verifies.")
    back_to_run(plc, previous)


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
    if filename.lower().endswith(".csv") or saved.startswith(b"# date;"):
        print(f"{filename} est une sauvegarde de DONNEES (CSV), pas de programme.\n"
              f"   comparer attend un fichier .cjbak (python tools/omron.py sauvegarder).\n"
              f"   Pour les donnees : python tools/omron.py restaurer-donnees {filename} "
              f"(montre les mots differents et demande avant d'ecrire).")
        return
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
    p.add_argument("commande", choices=["etat", "erreurs", "parametres", "lire", "surveiller", "ecrire",
                                        "run", "monitor", "programme",
                                        "sauvegarder-donnees", "restaurer-donnees", "sauvegarder", "comparer"])
    p.add_argument("args", nargs="*", help="zone, adresse, nombre ou valeur ; ou fichier")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--unite", type=int, default=0, help="numero d'unite Host Link")
    p.add_argument("--zones", default="D,H,W", help="sauvegarder-donnees : zones, ex. D,H,W,CIO,A")
    p.add_argument("--trace", action="store_true", help="afficher les trames envoyees et recues")
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

    try:
        ser = open_port(a.port, a.baud)
    except HostLinkError as e:
        raise SystemExit(f"ECHEC : {e}")
    plc = Cj1m(ser, a.unite, trace=a.trace)
    try:
        if a.commande == "etat":
            cmd_etat(plc)
        elif a.commande == "erreurs":
            cmd_erreurs(plc)
        elif a.commande == "parametres":
            cmd_parametres(plc, a.args[0] if a.args else None)
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
