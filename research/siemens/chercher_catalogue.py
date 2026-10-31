#!/usr/bin/env python3
"""Cherche ou STEP 7 range son catalogue materiel (codes de type des modules -> references).

Lecture seule : ne modifie rien. A lancer sur le PC ou STEP 7 est installe :

    python research/siemens/chercher_catalogue.py
    python research/siemens/chercher_catalogue.py "D:\\Siemens"        (autre dossier)

Cherche dans chaque fichier :
  - des references de modules S7-300 ("6ES7 321", "6ES7322"...), en ASCII et en UTF-16 ;
  - pres de ces references (a moins de 512 octets), les codes de type deja connus sur le banc
    (9FC3, AFD0, AFD8, AFC8), sous forme binaire (2 ordres d'octets), hexa ou decimale.
Ecrit catalogue_rapport.txt : les fichiers les plus prometteurs, avec un extrait autour des
trouvailles. Envoyer ce rapport (pas les fichiers Siemens eux-memes).
"""
import os
import sys
import time

KNOWN_CODES = [0x9FC3, 0xAFD0, 0xAFD8, 0xAFC8]          # bench S7-300, 31/10/2026
ORDER_PREFIXES = ["6ES7 321", "6ES7321", "6ES7 322", "6ES7322"]
DEFAULT_DIRS = [r"C:\Program Files (x86)\Siemens", r"C:\Program Files\Siemens", r"C:\ProgramData\Siemens"]
MAX_SIZE = 200 * 1024 * 1024
WINDOW = 512


def patterns():
    orders = []
    for p in ORDER_PREFIXES:
        orders += [p.encode("ascii"), p.encode("utf-16-le")]
    codes = {}
    for c in KNOWN_CODES:
        forms = [c.to_bytes(2, "big"), c.to_bytes(2, "little"), f"{c:04X}".encode(), f"{c:04x}".encode(),
                 str(c).encode(), f"{c:04X}".encode("utf-16-le"), str(c).encode("utf-16-le")]
        codes[c] = forms
    return orders, codes


def find_all(data, pat, limit=2000):
    out, i = [], data.find(pat)
    while i >= 0 and len(out) < limit:
        out.append(i)
        i = data.find(pat, i + 1)
    return out


def printable(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def scan_file(path, orders, codes):
    try:
        if os.path.getsize(path) > MAX_SIZE:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    order_hits = [i for p in orders for i in find_all(data, p)]
    if not order_hits:
        return None
    near = {}
    for c, forms in codes.items():
        for form in forms:
            for i in find_all(data, form):
                if any(abs(i - o) <= WINDOW for o in order_hits):
                    near.setdefault(c, []).append((i, form))
    spots = [(min(order_hits), b"(1re reference)")] + [hit for hits in near.values() for hit in hits[:2]]
    excerpts = [(i, form, data[max(0, i - 48):i + 80]) for i, form in spots[:6]]
    return {"path": path, "size": len(data), "orders": len(order_hits), "near": near, "excerpts": excerpts}


def main():
    dirs = sys.argv[1:] or [d for d in DEFAULT_DIRS if os.path.isdir(d)]
    if not dirs:
        raise SystemExit("Aucun dossier Siemens trouve : donner le dossier d'installation de STEP 7 en argument.")
    orders, codes = patterns()
    results, n_files, t0 = [], 0, time.time()
    for top in dirs:
        for root, _, files in os.walk(top):
            for name in files:
                n_files += 1
                if n_files % 500 == 0:
                    print(f"\r{n_files} fichiers lus...", end="", flush=True)
                r = scan_file(os.path.join(root, name), orders, codes)
                if r:
                    results.append(r)
    print(f"\r{n_files} fichiers lus en {time.time() - t0:.0f} s, {len(results)} contiennent des references.")
    results.sort(key=lambda r: (-len(r["near"]), -r["orders"]))
    with open("catalogue_rapport.txt", "w", encoding="utf-8") as f:
        f.write(f"Dossiers : {dirs}\nFichiers lus : {n_files}\n\n")
        for r in results[:40]:
            f.write(f"=== {r['path']}\n    {r['size']} octets, {r['orders']} references, "
                    f"codes connus a proximite : {', '.join(f'{c:04X}' for c in r['near']) or 'aucun'}\n")
            for i, form, chunk in r["excerpts"]:
                f.write(f"    @{i} {form!r}\n      {chunk.hex(' ')}\n      {printable(chunk)}\n")
            f.write("\n")
    print("Rapport : catalogue_rapport.txt (a envoyer)")
    for r in results[:10]:
        print(f"   {len(r['near'])} codes  {r['orders']:5d} refs  {r['path']}")


if __name__ == "__main__":
    main()
