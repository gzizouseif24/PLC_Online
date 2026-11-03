#!/usr/bin/env python3
"""Delta DVP (DVP28SV...) par COM1 (RS-232) ou COM2 (RS-485), Modbus, sans WPLSoft / ISPSoft.

    python tools/delta.py chercher                 trouver vitesse / format / station
    python tools/delta.py ecouter-ihm --port COM5  ecouter l'IHM sur le RS-485 : ses reglages et la station
    python tools/delta.py etat                     RUN, erreurs (codes et pas), cycle, horloge
    python tools/delta.py lire D100 20             lire (D, T, C : mots ; X, Y, M, S : bits)
    python tools/delta.py surveiller X0 16         afficher en direct (Ctrl+C)
    python tools/delta.py ecrire D100 1234         ecrire UN mot D, avec confirmation et relecture
    python tools/delta.py horloge                  mettre l'horloge a l'heure du PC
    python tools/delta.py sauvegarder-donnees      tous les D dans un CSV
    python tools/delta.py restaurer-donnees F.csv  reecrire les D differents du fichier

Options : --port COM6, --mode ascii|rtu, --baud 9600, --parite E, --stop 1, --station 1, --trace.
Defaut Delta : ASCII 9600 7E1, station 1. X et Y en octal (X0..X17).
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plc_online.protocols.delta import (  # noqa: E402
    BIT_AREAS, CLOCK, SEARCH, STATUS_BITS, STATUS_WORDS, DeltaError, ModbusError, PlcRefused,
    error_text, name, open_delta, parse_address, signed16)
from plc_online.protocols.delta import changed_runs as delta_changed_runs  # noqa: E402

DAYS = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"]


def confirm(question):
    return input(f"{question} (tapez OUI) > ").strip() == "OUI"


# ---- chercher ----

def request_frame(mode, station):
    """Read D1000 (1 word): the frame sent by chercher."""
    import struct
    from plc_online.protocols.modbus import crc16, lrc
    body = bytes([station]) + struct.pack(">BHH", 3, 0x1000 + 1000, 1)
    if mode == "ascii":
        return b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n"
    return body + struct.pack("<H", crc16(body))


def probe(port, mode, baud, parity, stop, station):
    """Bytes received after the request (b'' = silence)."""
    from plc_online.protocols.modbus import open_serial
    try:
        ser = open_serial(port, baud, parity, stop, 0.4, bytesize=7 if mode == "ascii" else 8)
    except Exception as e:
        raise SystemExit(f"ECHEC : port {port} : {e}")
    try:
        ser.reset_input_buffer()
        ser.write(request_frame(mode, station))
        got = ser.read(64)
        while True:                     # keep the rest of a slow reply
            more = ser.read(64)
            if not more:
                return got
            got += more
    finally:
        ser.close()


def answered(mode, station, got):
    """True if got is a valid reply from station (data or Modbus exception)."""
    try:
        if mode == "ascii":
            data = bytes.fromhex(got.strip()[1:].decode("ascii"))
            from plc_online.protocols.modbus import lrc
            return got.strip().startswith(b":") and lrc(data[:-1]) == data[-1] and data[0] == station
        from plc_online.protocols.modbus import crc16
        import struct
        return len(got) >= 5 and got[0] == station and crc16(got[:-2]) == struct.unpack("<H", got[-2:])[0]
    except (ValueError, IndexError):
        return False


def cmd_chercher(port, station):
    print(f"Recherche sur {port}, station {station}...")
    echo_seen = noise_seen = False
    for mode, baud, parity, stop in SEARCH:
        bits = 7 if mode == "ascii" else 8
        print(f"   {mode.upper():5} {baud:6} {bits}{parity}{stop} ... ", end="", flush=True)
        got = probe(port, mode, baud, parity, stop, station)
        if got and answered(mode, station, got):
            print("REPOND")
            print(f"\nA utiliser : --mode {mode} --baud {baud} --parite {parity} --stop {stop} --station {station}")
            return
        if not got:
            print("rien recu")
        elif got.startswith(request_frame(mode, station)):
            echo_seen = True
            print(f"ECHO de notre trame seulement ({len(got)} octets)")
        else:
            noise_seen = True
            print(f"{len(got)} octets illisibles : {got[:24].hex(' ')}")
    if not echo_seen and not noise_seen:
        print("\nSilence total. Causes probables :\n"
              "   - fils + / - inverses : les permuter et relancer\n"
              "   - COM2 utilise par le programme en MAITRE (variateurs, MODRD/MODWR) : il ne repond pas\n"
              "     -> passer par COM1 (RS-232, mini-DIN 8, cable DVPACAB230 ou equivalent)\n"
              "   - mauvais port COM / adaptateur : verifier dans le Gestionnaire de peripheriques")
    elif noise_seen:
        print("\nDes octets arrivent mais illisibles : vitesse/format non standard, ou un autre appareil parle sur la ligne.")
        print("Envoyer cette sortie.")
    else:
        print("\nSeulement l'echo : l'adaptateur renvoie ce qu'il envoie, l'automate ne repond pas (voir causes ci-dessus).")
    print(f"\nEssai des stations 1..32 en ASCII 9600 7E1 et RTU 9600 8E1 (environ 30 s)...")
    for mode in ("ascii", "rtu"):
        for st in range(1, 33):
            got = probe(port, mode, 9600, "E", 1, st)
            if got and answered(mode, st, got):
                print(f"REPOND : --mode {mode} --baud 9600 --parite E --stop 1 --station {st}")
                return
    print("Aucune station ne repond.")


# ---- ecouter-ihm ----

def cmd_ecouter_ihm(port, seconds=1.5):
    from plc_online.protocols.delta import LISTEN_SETTINGS, ascii_frames, com_word, rtu_frames
    from plc_online.protocols.modbus import open_serial
    print(f"Ecoute de la ligne RS-485 sur {port} (l'IHM doit etre allumee et interroger l'automate)...")
    for mode, baud, bits, parity, stop in LISTEN_SETTINGS:
        print(f"   {mode.upper():5} {baud:6} {bits}{parity}{stop} ... ", end="", flush=True)
        try:
            ser = open_serial(port, baud, parity, stop, 0.1, bytesize=bits)
        except Exception as e:
            raise SystemExit(f"ECHEC : port {port} : {e}")
        buf, end = b"", time.time() + seconds
        try:
            ser.reset_input_buffer()
            while time.time() < end:
                buf += ser.read(256)
        finally:
            ser.close()
        frames = ascii_frames(buf) if mode == "ascii" else rtu_frames(buf)
        if not frames:
            print("rien" if not buf else f"{len(buf)} octets illisibles")
            continue
        stations = sorted({st for st, _ in frames})
        print(f"TROUVE : {len(frames)} trames, station(s) {stations}")
        try:
            code = com_word(baud, bits, parity, stop)
            print(f"\nL'IHM parle en {mode.upper()} {baud} {bits}{parity}{stop} a la station {stations[0]}.")
            print("Reglages a mettre dans l'automate (onglet Surveillance, ou au demarrage du programme sous M1002) :")
            print(f"   D1120 = 16#{code:02X}   M1143 = {1 if mode == 'rtu' else 0}   D1121 = {stations[0]}   M1120 = 1")
        except KeyError:
            print("Vitesse non prise en charge par le DVP.")
        return
    print("\nAucune trame lue. Verifier : adaptateur branche en parallele sur D+ / D- de l'IHM (essayer d'inverser),"
          " IHM allumee et en erreur de communication (elle interroge toujours).")


# ---- etat ----

def cmd_etat(plc):
    st = plc.status()
    b, w = st["bits"], st["mots"]
    print(f"\nMode        : {'RUN' if b[1000] else 'STOP'}")
    print(f"Firmware    : {w[1001] >> 8}.{w[1001] & 0xFF:02X}   (D1001 = 16#{w[1001]:04X})")
    print(f"Capacite    : {w[1002]} pas")
    print(f"Cycle       : {w[1010] / 10:.1f} ms (mini {w[1011] / 10:.1f}, maxi {w[1012] / 10:.1f}), "
          f"chien de garde {w[1000]} ms")
    print("\nDefauts :")
    faults = 0
    if b[1004] or w[1004]:
        faults += 1
        print(f"   ERREUR SYNTAXE (M1004={int(b[1004])}) : D1004 = 16#{w[1004]:04X} : {error_text(w[1004])}")
        if w[1137]:
            print(f"      pas de l'erreur : D1137 = {w[1137]}")
    if b[1008]:
        faults += 1
        print(f"   CHIEN DE GARDE (M1008) : programme trop long ou boucle, au pas D1008 = {w[1008]}")
    if b[1009]:
        faults += 1
        print("   TENSION 24 V INSUFFISANTE (M1009) : verifier l'alimentation")
    if b[1067] or b[1068] or w[1067]:
        faults += 1
        print(f"   ERREUR D'EXECUTION (M1067={int(b[1067])} M1068={int(b[1068])}) : D1067 = 16#{w[1067]:04X}, "
              f"au pas D1068 = {w[1068]}")
    if not faults:
        print("   aucun")
    print("\nRegistres speciaux :")
    for n, text in STATUS_BITS.items():
        print(f"   M{n:<5} = {int(b[n])}   {text}")
    for n, text in STATUS_WORDS.items():
        print(f"   D{n:<5} = {w[n]:6d}  16#{w[n]:04X}   {text}")
    try:
        show_clock(plc)
    except (ModbusError, DeltaError) as e:
        print(f"\nHorloge : non lue ({e})")


def show_clock(plc):
    s, mi, h, d, mo, wd, y = plc.clock()
    print(f"\nHorloge     : {d:02d}/{mo:02d}/{2000 + y if y < 100 else y} {h:02d}:{mi:02d}:{s:02d}"
          f"  ({DAYS[wd % 7] if wd < 7 else wd})   D{CLOCK}..D{CLOCK + 6}")


# ---- lire / surveiller / ecrire ----

def read_any(plc, area, start, count):
    if area in ("X", "Y", "M", "S"):
        return [int(v) for v in plc.read_bits(area, start, count)]
    return plc.read_words(area, start, count)


def show_values(area, start, values):
    for i, v in enumerate(values):
        if area in ("X", "Y", "M", "S"):
            print(f"   {name(area, start + i):<7} = {v}")
        else:
            print(f"   {name(area, start + i):<7} = {signed16(v):6d}   16#{v:04X}")


def cmd_lire(plc, area, start, count):
    show_values(area, start, read_any(plc, area, start, count))


def cmd_surveiller(plc, area, start, count):
    print("(Ctrl+C pour arreter)")
    old = read_any(plc, area, start, count)
    show_values(area, start, old)
    while True:
        time.sleep(0.3)
        cur = read_any(plc, area, start, count)
        moved = [f"{name(area, start + i)}:{o}->{n}" for i, (o, n) in enumerate(zip(old, cur)) if o != n]
        if moved:
            print(time.strftime("%H:%M:%S"), "  ".join(moved))
        old = cur


def cmd_ecrire(plc, area, n, value):
    if area != "D":
        raise DeltaError("ecriture limitee aux mots D")
    old = plc.read_words("D", n, 1)[0]
    print(f"D{n} actuel = {signed16(old)}")
    if not confirm(f"Ecrire D{n} : {signed16(old)} -> {value} ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    plc.write_word("D", n, value)
    back = plc.read_words("D", n, 1)[0]
    print(f"Relu : D{n} = {signed16(back)}  {'OK' if back == value & 0xFFFF else '!! DIFFERENT'}")
    print(f"Pour revenir en arriere : python tools/delta.py ecrire D{n} {signed16(old)}")


# ---- horloge ----

def cmd_horloge(plc):
    try:
        show_clock(plc)
    except (ModbusError, DeltaError) as e:
        print(f"Horloge non lue ({e})")
    t = time.localtime()
    new = [t.tm_sec, t.tm_min, t.tm_hour, t.tm_mday, t.tm_mon, (t.tm_wday + 1) % 7, t.tm_year % 100]
    if not confirm(f"Mettre l'horloge a {time.strftime('%d/%m/%Y %H:%M:%S', t)} ?"):
        return
    plc.write_words("D", CLOCK, new)
    time.sleep(1.2)
    show_clock(plc)


# ---- donnees ----

def read_all_d(plc, show=True):
    values = plc.read_all_d((lambda n: print(f"\rLecture D... {n} mots", end="", flush=True)) if show else None)
    if show:
        print()
    return values


def cmd_sauvegarder_donnees(plc, filename=None):
    filename = filename or f"delta_donnees_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    values = read_all_d(plc)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# date;{time.strftime('%Y-%m-%d %H:%M:%S')}\n# automate;Delta DVP\n")
        f.write("zone;adresse;valeur;signe;hex\n")
        for i, v in enumerate(values):
            f.write(f"D;{i};{v};{signed16(v)};{v:04X}\n")
    print(f"Donnees : {filename} ({len(values)} mots D)")


def read_data_csv(filename):
    out = {}
    with open(filename, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("zone;"):
                continue
            zone, addr, value = line.split(";")[:3]
            if zone.upper() == "D":
                out[int(addr)] = int(value) & 0xFFFF
    return out


def cmd_restaurer_donnees(plc, filename):
    saved = read_data_csv(filename)
    current = read_all_d(plc)
    saved = {a: v for a, v in saved.items() if a < len(current)}
    diff = {a: v for a, v in saved.items() if current[a] != v}
    if not diff:
        print("Rien a restaurer : l'automate a deja les valeurs du fichier.")
        return
    print(f"{len(diff)} mots D differents du fichier, par exemple :")
    for a in sorted(diff)[:10]:
        print(f"   D{a:<6} automate = {signed16(current[a]):6d}   fichier = {signed16(diff[a]):6d}")
    if not confirm(f"Ecrire ces {len(diff)} mots ? Machine en securite ?"):
        print("Annule, rien n'a ete ecrit.")
        return
    for start, length in delta_changed_runs(diff):
        plc.write_words("D", start, [diff[a] for a in range(start, start + length)])
    after = read_all_d(plc, show=False)
    wrong = [a for a in diff if after[a] != diff[a]]
    print(f"!! {len(wrong)} mots differents apres ecriture, premier D{wrong[0]}" if wrong
          else f"Restauration OK : {len(diff)} mots ecrits et verifies.")


# ---- arguments ----

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("commande", choices=["chercher", "ecouter-ihm", "etat", "lire", "surveiller", "ecrire", "horloge",
                                        "sauvegarder-donnees", "restaurer-donnees"])
    p.add_argument("args", nargs="*")
    p.add_argument("--port", default="COM6")
    p.add_argument("--mode", default="ascii", choices=["ascii", "rtu"])
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--parite", default="E", choices=["E", "O", "N"])
    p.add_argument("--stop", type=int, default=1, choices=[1, 2])
    p.add_argument("--station", type=int, default=1)
    p.add_argument("--trace", action="store_true", help="afficher les trames (mode ascii)")
    a = p.parse_args()

    if a.commande == "chercher":
        cmd_chercher(a.port, a.station)
        return
    if a.commande == "ecouter-ihm":
        cmd_ecouter_ihm(a.port)
        return
    try:
        if a.commande in ("lire", "surveiller", "ecrire"):
            if len(a.args) != 2:
                p.error(f"usage : {a.commande} ADRESSE {'VALEUR' if a.commande == 'ecrire' else 'NOMBRE'}")
            area, n = parse_address(a.args[0])
            second = int(a.args[1])
        if a.commande == "restaurer-donnees" and len(a.args) != 1:
            p.error("usage : restaurer-donnees FICHIER.csv")
    except (DeltaError, ValueError) as e:
        p.error(str(e))

    try:
        plc = open_delta(a.port, a.mode, a.baud, a.parite, a.stop, a.station, trace=a.trace)
    except Exception as e:
        raise SystemExit(f"ECHEC : port {a.port} : {e}")
    bits = 7 if a.mode == "ascii" else 8
    print(f"Delta DVP sur {a.port} : {a.mode.upper()} {a.baud} {bits}{a.parite}{a.stop}, station {a.station}")
    try:
        if a.commande == "etat":
            cmd_etat(plc)
        elif a.commande == "lire":
            cmd_lire(plc, area, n, second)
        elif a.commande == "surveiller":
            cmd_surveiller(plc, area, n, second)
        elif a.commande == "ecrire":
            cmd_ecrire(plc, area, n, second)
        elif a.commande == "horloge":
            cmd_horloge(plc)
        elif a.commande == "sauvegarder-donnees":
            cmd_sauvegarder_donnees(plc, a.args[0] if a.args else None)
        elif a.commande == "restaurer-donnees":
            cmd_restaurer_donnees(plc, a.args[0])
    except PlcRefused as e:
        print(f"ECHEC : {e}")
    except ModbusError as e:
        print(f"ECHEC : {e}\n   -> lancer d'abord : python tools/delta.py chercher --port {a.port}")
    except DeltaError as e:
        print(f"ECHEC : {e}")
    except KeyboardInterrupt:
        print("\n(arrete)")
    finally:
        plc.close()


if __name__ == "__main__":
    main()
