"""TSX Micro / Premium par le port TER (Uni-Telway), sans PL7.

    python tools/tsx.py etat                          identification, defauts, pile, horloge
    python tools/tsx.py lire MW 0 20                  lire %MW0..%MW19  (zones : MW MD KW KD SW S M)
    python tools/tsx.py surveiller MW 0 100           afficher ce qui change (Ctrl+C pour arreter)
    python tools/tsx.py ecrire MW 10 1234             ecrire UN mot, avec confirmation et relecture
    python tools/tsx.py run | stop                    passer l'automate en RUN / STOP, avec confirmation
    python tools/tsx.py horloge                       mettre l'horloge de l'automate a l'heure du PC
    python tools/tsx.py sauvegarder [FICHIER]         copier le programme de l'automate dans un fichier
    python tools/tsx.py comparer FICHIER              le programme a-t-il change depuis cette sauvegarde ?
    python tools/tsx.py restaurer FICHIER             remettre un programme dans l'automate (.tsxbak ou .stx PL7)
    python tools/tsx.py sauvegarder-donnees [FICHIER] copier tous les %MW et %KW dans un fichier CSV
    python tools/tsx.py restaurer-donnees FICHIER     remettre les %MW du fichier (CSV ou .dat PL7) dans l'automate
    python tools/tsx.py chercher-es [POSITIONS]       trouver ou l'automate garde les %I (une entree a basculer)

Options : --port COM6, --trace (octets), --adresses 1-3 (plus rapide, a l'essai : 1 par defaut).
Fermer PL7 avant. Cable TSX PCX 1031 : meme position qu'avec PL7.
Protocole : plc_online/protocols/unitelway.py.

Confirme sur TSX 3721 : etat, lecture %S, %SW. Probable : %MW, %MD, %KW, %KD, %M.
Teste sur TSX 3721 : toutes les commandes ci-dessus.

pip install pyserial
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.unitelway import *  # noqa: E402,F401,F403  (protocole, partage avec l'interface web)
from plc_online.protocols.unitelway import (  # noqa: E402
    BIT_ZONES, SYSTEM_BITS, BACKUP_ERRORS, WORD_ZONES, Slave, Tsx, UniteError, changed_runs,
    parse_addrs, read_data_csv, read_program_file, read_whole_zone, signed)


# ---------- affichage ----------

def fmt(zone, addr, v):
    if zone in BIT_ZONES:
        return f"%{zone}{addr:<5} = {v}"
    size = WORD_ZONES[zone][2]
    return f"%{zone}{addr:<5} = {v:{10 if size == 4 else 5}d}   (signe {signed(v, size)}, hex {v:0{2 * size}X})"


def cmd_etat(tsx):
    ident = tsx.identification()
    print(f"Automate : {ident['nom']}")
    bits = tsx.read("S", 0, 72)
    print("\nDefauts (bits systeme) :")
    alarms = 0
    for n, (label, bad) in SYSTEM_BITS.items():
        flag = bits[n] == bad
        alarms += flag
        print(f"   {'!!' if flag else 'ok'}  %S{n:<3} = {bits[n]}   {label}")
    if not alarms:
        print("   aucun defaut signale par les bits systeme")
    sw = tsx.read("SW", 49, 5)
    print(f"\nHorloge automate : {sw[3] & 0xFF:02X}/{sw[3] >> 8:02X}/{sw[4]:04X} "
          f"{sw[2] >> 8:02X}:{sw[2] & 0xFF:02X}:{sw[1] >> 8:02X}  (jour {sw[0]})")
    print(f"Chien de garde : {tsx.read('SW', 11, 1)[0]} ms")
    show_flash_backup(tsx.read("SW", 96, 2))


def show_flash_backup(sw):
    """%SW96 / %SW97 (TSX 37 uniquement) : sauvegarde du programme en Flash EPROM interne."""
    sw96, sw97 = sw
    valid = sw96 >> 6 & 1
    print("\nSauvegarde interne (Flash EPROM, TSX 37) :")
    print(f"   programme : {'VALIDE' if valid else 'aucune sauvegarde valide'}   (%SW96 bit 6 = {valid})")
    if sw97 == 0xFFFF:
        print("   %MW sauvegardes : aucun   (%SW97 = -1)")
    else:
        print(f"   %MW sauvegardes : {sw97} premiers %MW   (%SW97 = {sw97})")
    if sw96 >> 1 & 1:
        if sw96 >> 2 & 1:
            code = sw96 >> 8
            print(f"   derniere sauvegarde : ERREUR {code} {BACKUP_ERRORS.get(code, '')}")
        else:
            print("   derniere sauvegarde : terminee sans erreur")


def cmd_lire(tsx, zone, start, count):
    for i, v in enumerate(tsx.read(zone, start, count)):
        print("   " + fmt(zone, start + i, v))


def cmd_surveiller(tsx, zone, start, count):
    print(f"Surveillance %{zone}{start}..%{zone}{start + count - 1} (Ctrl+C pour arreter)\n")
    base = tsx.read(zone, start, count)
    t0 = time.time()
    while True:
        cur = tsx.read(zone, start, count)
        for i, (old, new) in enumerate(zip(base, cur)):
            if old != new:
                print(f"   {time.time() - t0:8.1f} s   %{zone}{start + i}: {old} -> {new}")
        base = cur


def confirm(question):
    return input(f"{question} (tapez OUI) > ").strip() == "OUI"


def cmd_ecrire(tsx, zone, addr, value):
    old = tsx.read(zone, addr, 1)[0]
    print(f"%{zone}{addr} actuel = {old}")
    if not confirm(f"Ecrire %{zone}{addr} : {old} -> {value} ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    tsx.write(zone, addr, value)
    back = tsx.read(zone, addr, 1)[0]
    size = WORD_ZONES[zone][2]
    ok = back == value & ((1 << 8 * size) - 1)
    print(f"Relu : %{zone}{addr} = {back}  {'OK' if ok else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python tools/tsx.py ecrire {zone} {addr} {old}")


def cmd_horloge(tsx):
    import datetime
    from plc_online.protocols.unitelway import set_clock
    sw = tsx.read("SW", 49, 5)
    print(f"Horloge automate : {sw[3] & 0xFF:02X}/{sw[3] >> 8:02X}/{sw[4]:04X} "
          f"{sw[2] >> 8:02X}:{sw[2] & 0xFF:02X}:{sw[1] >> 8:02X}")
    now = datetime.datetime.now()
    if not confirm(f"Mettre l'automate a l'heure du PC ({now:%d/%m/%Y %H:%M:%S}) ?"):
        print("Annule.")
        return
    r = set_clock(tsx)
    sw = tsx.read("SW", 49, 5)
    print(f"Ecrit (requete {r['methode']}). Relu : {sw[3] & 0xFF:02X}/{sw[3] >> 8:02X}/{sw[4]:04X} "
          f"{sw[2] >> 8:02X}:{sw[2] & 0xFF:02X}:{sw[1] >> 8:02X}")


def cmd_run_stop(tsx, what):
    if not confirm(f"Passer l'automate en {what.upper()} ? Machine en securite ?"):
        print("Annule.")
        return
    (tsx.run if what == "run" else tsx.stop)()
    print(f"{what.upper()} accepte par l'automate. Verifier le voyant RUN.")


def do_upload(tsx):
    """Reserve, lit l'application, libere. Renvoie (infos, contenu)."""
    ident = tsx.identification()
    tsx.reserve()
    try:
        info = tsx.app_info()
        print(f"Automate {ident['nom']}, application '{info['nom']}' : lecture...")
        t0 = time.time()
        blob = tsx.upload(lambda seg, n: print(f"\r   segment {seg:4d}   {n:7d} octets", end="", flush=True))
        print(f"\r   {len(blob)} octets lus en {time.time() - t0:.0f} s" + " " * 20)
    finally:
        try:
            tsx.release()
        except UniteError:
            pass
    return ident, info, blob


def cmd_sauvegarder(tsx, filename=None):
    import hashlib
    ident, info, blob = do_upload(tsx)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = filename or f"{info['nom'] or 'application'}_{stamp}.tsxbak"
    with open(filename, "wb") as f:
        f.write(blob)
    with open(filename + ".txt", "w", encoding="utf-8") as f:
        f.write(f"date            : {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"automate        : {ident['nom']}\n")
        f.write(f"application     : {info['nom']}\n")
        f.write(f"taille          : {len(blob)} octets\n")
        f.write(f"sha256          : {hashlib.sha256(blob).hexdigest()}\n")
        f.write(f"identification  : {ident['brut']}\n")
        f.write(f"infos appli (92): {info['brut']}\n")
    print(f"Sauvegarde : {filename}  (+ {filename}.txt)")


def cmd_restaurer(tsx, filename):
    """Remet un .tsxbak (ou un .stx PL7) dans l'automate, comme le transfert PC -> automate de PL7."""
    blob, target = read_program_file(filename)
    if target:
        plc = tsx.identification()["nom"]
        print(f"Application faite pour : {target}   automate connecte : {plc}")
        if target.replace(" ", "") != plc.replace(" ", "") and not confirm(
                "ATTENTION : automate different de celui de l'application. Continuer quand meme ?"):
            print("Annule, rien n'a ete ecrit.")
            return
    if len(blob) < 16 or not blob[:16].rstrip(b"\0 ").strip():
        raise UniteError(f"{filename} ne ressemble pas a une sauvegarde de programme")
    name = blob[:14].split(b"\0")[0].decode("ascii", "replace").strip()
    print(f"Fichier {filename} : {len(blob)} octets, application '{name}'")
    try:
        state = tsx.read_cpu()[1] & 0x0F        # 1 STOP, 2 RUN, 4 pas d'application
    except (UniteError, IndexError):
        state = 0
    if state == 4:
        print("L'automate est vide (pas d'application) : rien a sauvegarder avant.")
    else:
        print("Avant de remplacer le programme, sauvegarde automatique du programme actuel :")
        cmd_sauvegarder(tsx, f"avant_restauration_{time.strftime('%Y%m%d_%H%M%S')}.tsxbak")
    if not confirm("Remplacer le programme de l'automate ? Il passera en STOP. Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    if state not in (1, 4):                     # PL7 n'envoie pas STOP a un automate vide
        tsx.stop()
    tsx.reserve(RESERVE_DOWNLOAD)
    try:
        t0 = time.time()
        tsx.download(blob, lambda seg, n: print(f"\r   segment {seg:4d}   {n:7d} / {len(blob)} octets", end="", flush=True))
        print(f"\r   {len(blob)} octets ecrits en {time.time() - t0:.0f} s" + " " * 20)
    finally:
        try:
            tsx.release()
        except UniteError:
            pass
    print("Verification : relecture du programme...")
    _, _, back = do_upload(tsx)
    if back == blob:
        print("Restauration OK : le programme relu est identique au fichier. L'automate est en STOP (python tools/tsx.py run).")
    else:
        print(f"!! Le programme relu ({len(back)} octets) differe du fichier ({len(blob)} octets).")
        print("   Le programme d'avant est dans le fichier avant_restauration_*.tsxbak.")


def cmd_comparer(tsx, filename):
    saved, _ = read_program_file(filename)
    _, _, blob = do_upload(tsx)
    if blob == saved:
        print(f"IDENTIQUE : le programme de l'automate est le meme que {filename}")
        return
    diffs = [i for i in range(min(len(blob), len(saved))) if blob[i] != saved[i]]
    print(f"DIFFERENT de {filename} : automate {len(blob)} octets, fichier {len(saved)} octets, "
          f"{len(diffs)} octets differents")
    if diffs:
        print(f"   premiere difference a l'octet {diffs[0]}, derniere a l'octet {diffs[-1]}")


def cmd_sauvegarder_donnees(tsx, filename=None):
    ident = tsx.identification()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    filename = filename or f"donnees_{stamp}.csv"
    zones = {}
    for zone in ("MW", "KW"):
        print(f"Lecture %{zone}...", end="", flush=True)
        zones[zone] = read_whole_zone(tsx, zone)
        print(f" {len(zones[zone])} mots")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# date;{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# automate;{ident['nom']}\n")
        f.write("zone;adresse;valeur;signe;hex\n")
        for zone, values in zones.items():
            for i, v in enumerate(values):
                f.write(f"%{zone};{i};{v};{signed(v, 2)};{v:04X}\n")
    print(f"Donnees : {filename}")


def cmd_restaurer_donnees(tsx, filename):
    zones, plc_in_file = read_data_csv(filename)
    ident = tsx.identification()
    if plc_in_file and plc_in_file != ident["nom"]:
        print(f"ATTENTION : fichier pris sur {plc_in_file}, automate connecte : {ident['nom']}")
    if "KW" in zones:
        print(f"%KW du fichier ignores ({len(zones['KW'])} mots) : restauration des constantes pas encore validee.")
    saved = zones.get("MW", {})
    if not saved:
        print("Aucun %MW dans le fichier.")
        return
    current = read_whole_zone(tsx, "MW")
    beyond = [a for a in saved if a >= len(current)]
    if beyond:
        print(f"ATTENTION : {len(beyond)} %MW du fichier n'existent pas dans l'automate "
              f"(il a {len(current)} %MW) : ils seront ignores.")
    diffs = {a: v for a, v in saved.items() if a < len(current) and current[a] != v}
    if not diffs:
        print(f"Rien a restaurer : les {len(saved) - len(beyond)} %MW de l'automate sont deja identiques au fichier.")
        return
    print(f"{len(diffs)} %MW different du fichier, par exemple :")
    for a in sorted(diffs)[:10]:
        print(f"   %MW{a:<5} automate = {current[a]:6d}   fichier = {diffs[a]:6d}")
    if not confirm(f"Ecrire ces {len(diffs)} %MW dans l'automate ? Automate en STOP / machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    for start, length in changed_runs(diffs):
        tsx.write_words("MW", start, [saved[a] for a in range(start, start + length)])
    after = read_whole_zone(tsx, "MW")
    wrong = [a for a in saved if a < len(after) and after[a] != saved[a]]
    if wrong:
        print(f"!! {len(wrong)} %MW differents apres ecriture, premier : %MW{wrong[0]}")
    else:
        print(f"Restauration OK : {len(diffs)} %MW ecrits et verifies.")


# ---- chercher-es : ou sont les %I / %Q ? (lecture seule, aussi dans l'onglet Diagnostic de l'interface) ----

def cmd_chercher_es(tsx, positions):
    from plc_online.web import tsx_web
    print("Recherche des %I : lecture seule, rien n'est ecrit.\n"
          "Il faut UNE entree que vous pouvez faire passer de 0 a 1 (24 V sur une voie d'un module d'entrees).")
    print("Segments de memoire de mise au point...")
    segments = tsx_web.io_segments(tsx)
    print(f"   segments qui repondent : {' '.join(f'{s:02X}' for s in segments) or 'aucun'}")
    info = {"positions": positions, "segments": segments, "requests": tsx_web.io_probe_requests(positions, segments)}
    shots = {}
    for key, text in (("A", "Entree a 0. Appuyer sur Entree pour lire"),
                      ("B", "Mettre l'entree a 1 (noter laquelle, ex. %I1.3), puis Entree"),
                      ("C", "Remettre l'entree a 0, puis Entree")):
        input(f"\n{text} > ")
        shots[key] = tsx_web.io_snapshot(tsx, info["requests"],
                                         lambda t, i, n: print(f"\r   {i}/{n}", end="", flush=True))
        print()
    which = input("\nQuelle entree avez-vous basculee (ex. %I1.3) ? > ").strip()
    found, log = tsx_web.io_result(info, shots, which)
    filename = f"chercher_es_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    Path(filename).write_text(log, encoding="utf-8")
    print(f"\nReponses qui suivent l'entree (0 -> 1 -> 0) : {len(found)}")
    for name in found:
        print(f"   {name}\n      0 : {shots['A'][name]}\n      1 : {shots['B'][name]}")
    print(f"\nJournal complet : {filename}  -> a envoyer")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["etat", "lire", "surveiller", "ecrire", "run", "stop",
                                        "sauvegarder", "comparer", "sauvegarder-donnees", "horloge",
                                        "restaurer-donnees", "restaurer", "chercher-es"])
    p.add_argument("args", nargs="*", help="zone, adresse, nombre ou valeur")
    p.add_argument("--port", default="COM6")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="O", choices=["O", "E", "N"])
    p.add_argument("--adresses", default="1", help="adresses esclave du PC : 1 (sur) ou 1-3 (plus rapide, a l'essai)")
    p.add_argument("--categorie", type=int, default=0, choices=range(8))
    p.add_argument("--trace", action="store_true")
    a = p.parse_args()

    zones = sorted(WORD_ZONES) + sorted(BIT_ZONES)
    if a.commande in ("lire", "surveiller", "ecrire"):
        if len(a.args) != 3 or a.args[0].upper() not in zones:
            p.error(f"usage : {a.commande} ZONE ADRESSE {'VALEUR' if a.commande == 'ecrire' else 'NOMBRE'}"
                    f"   (zones : {' '.join(zones)})")
        zone, n1, n2 = a.args[0].upper(), int(a.args[1]), int(a.args[2])
    if a.commande == "comparer" and len(a.args) != 1:
        p.error("usage : comparer FICHIER.tsxbak (ou .stx)")
    if a.commande == "restaurer" and len(a.args) != 1:
        p.error("usage : restaurer FICHIER.tsxbak (ou .stx de PL7)")
    if a.commande == "restaurer-donnees" and len(a.args) != 1:
        p.error("usage : restaurer-donnees FICHIER.csv (ou .dat de PL7)")

    import serial
    ser = serial.Serial(a.port, a.baud, bytesize=8, parity=a.parite, stopbits=1, timeout=0.02)
    tsx = Tsx(Slave(ser, parse_addrs(a.adresses), a.categorie, a.trace))
    try:
        if a.commande == "etat":
            cmd_etat(tsx)
        elif a.commande == "lire":
            cmd_lire(tsx, zone, n1, n2)
        elif a.commande == "surveiller":
            cmd_surveiller(tsx, zone, n1, n2)
        elif a.commande == "ecrire":
            cmd_ecrire(tsx, zone, n1, n2)
        elif a.commande == "horloge":
            cmd_horloge(tsx)
        elif a.commande == "sauvegarder":
            cmd_sauvegarder(tsx, a.args[0] if a.args else None)
        elif a.commande == "comparer":
            cmd_comparer(tsx, a.args[0])
        elif a.commande == "sauvegarder-donnees":
            cmd_sauvegarder_donnees(tsx, a.args[0] if a.args else None)
        elif a.commande == "restaurer":
            cmd_restaurer(tsx, a.args[0])
        elif a.commande == "restaurer-donnees":
            cmd_restaurer_donnees(tsx, a.args[0])
        elif a.commande == "chercher-es":
            cmd_chercher_es(tsx, [int(x) for x in a.args] or list(range(1, 9)))
        else:
            cmd_run_stop(tsx, a.commande)
    except UniteError as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
