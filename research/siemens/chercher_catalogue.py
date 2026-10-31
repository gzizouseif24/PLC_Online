#!/usr/bin/env python3
"""Cherche ou STEP 7 range son catalogue materiel (codes de type des modules -> references).

Lecture seule : ne modifie rien. A lancer sur le PC ou STEP 7 est installe :

    python research/siemens/chercher_catalogue.py
    python research/siemens/chercher_catalogue.py "C:\\Program Files (x86)\\Siemens\\Step7"
    python research/siemens/chercher_catalogue.py --texte "315-2AF03" "C:\\...\\Step7\\S7DATA"
        (--texte : fichiers qui contiennent ce texte, avec les textes lisibles autour)

Cherche dans chaque fichier :
  - des references de modules S7-300 ("6ES7 321", "6ES7322"...), en ASCII et en UTF-16 ;
  - pres de ces references (a moins de 512 octets), les codes de type deja connus sur le banc
    (9FC3, AFD0, AFD8, AFC8), sous forme binaire (2 ordres d'octets), hexa ou decimale.
Une seule lecture par fichier ; saute l'aide, les images, les installeurs et les fichiers > 30 Mo.
Ctrl+C arrete la recherche et garde ce qui a deja ete trouve.
Ecrit catalogue_rapport.txt : les fichiers les plus prometteurs, avec un extrait autour des
trouvailles. Envoyer ce rapport (pas les fichiers Siemens eux-memes).
"""
import mmap
import os
import re
import sys
import time

KNOWN_CODES = [0x9FC3, 0xAFD0, 0xAFD8, 0xAFC8]          # bench S7-300, 31/10/2026
ORDER_PREFIXES = ["6ES7 321", "6ES7321", "6ES7 322", "6ES7322"]
DEFAULT_DIRS = [r"C:\Program Files (x86)\Siemens", r"C:\Program Files\Siemens", r"C:\ProgramData\Siemens"]
MAX_SIZE = 30 * 1024 * 1024
WINDOW = 512
SKIP_EXT = {
    ".chm", ".hlp", ".pdf", ".htm", ".html", ".css", ".js", ".gif", ".png", ".jpg", ".jpeg", ".bmp",
    ".ico", ".cur", ".avi", ".wav", ".mp4", ".swf", ".cab", ".msi", ".msp", ".zip", ".7z", ".log",
    ".ttf", ".fon", ".manifest", ".cat", ".mui", ".tlb", ".pdb", ".lib", ".h", ".py",
}
SKIP_DIRS = {"help", "manuals", "doc", "docs", "examples", "samples", "$recycle.bin"}


def regex(patterns):
    return re.compile(b"|".join(re.escape(p) for p in patterns))


def build():
    orders = []
    for p in ORDER_PREFIXES:
        orders += [p.encode("ascii"), p.encode("utf-16-le")]
    code_of, forms = {}, []
    for c in KNOWN_CODES:
        for f in (c.to_bytes(2, "big"), c.to_bytes(2, "little"), f"{c:04X}".encode(), f"{c:04x}".encode(),
                  str(c).encode(), f"{c:04X}".encode("utf-16-le"), str(c).encode("utf-16-le")):
            code_of[f] = c
            forms.append(f)
    return regex(orders), regex(forms), code_of


def printable(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def scan_file(path, order_re, code_re, code_of):
    try:
        size = os.path.getsize(path)
        if size == 0 or size > MAX_SIZE:
            return None
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            order_hits = [m.start() for _, m in zip(range(2000), order_re.finditer(data))]
            if not order_hits:
                return None
            near = {}
            for o in order_hits[:200]:                      # only look around the references
                lo = max(0, o - WINDOW)
                for m in code_re.finditer(data, lo, o + WINDOW):
                    near.setdefault(code_of[m.group()], []).append((m.start(), m.group()))
            spots = [(order_hits[0], b"(1re reference)")] + [h for hits in near.values() for h in hits[:2]]
            excerpts = [(i, form, bytes(data[max(0, i - 48):i + 80])) for i, form in spots[:6]]
    except (OSError, ValueError):
        return None
    return {"path": path, "size": size, "orders": len(order_hits), "near": near, "excerpts": excerpts}


def write_report(dirs, n_files, results, interrupted):
    results.sort(key=lambda r: (-len(r["near"]), -r["orders"]))
    with open("catalogue_rapport.txt", "w", encoding="utf-8") as f:
        f.write(f"Dossiers : {dirs}\nFichiers lus : {n_files}{' (arrete par Ctrl+C)' if interrupted else ''}\n\n")
        for r in results[:40]:
            f.write(f"=== {r['path']}\n    {r['size']} octets, {r['orders']} references, "
                    f"codes connus a proximite : {', '.join(f'{c:04X}' for c in r['near']) or 'aucun'}\n")
            for i, form, chunk in r["excerpts"]:
                f.write(f"    @{i} {form!r}\n      {chunk.hex(' ')}\n      {printable(chunk)}\n")
            f.write("\n")
    print("Rapport : catalogue_rapport.txt (a envoyer)")
    for r in results[:10]:
        print(f"   {len(r['near'])} codes  {r['orders']:5d} refs  {r['path']}")


def strings_around(data, i, span=400):
    """Readable texts (ASCII and UTF-16) near offset i."""
    chunk = bytes(data[max(0, i - span):i + span])
    found = [m.group().decode("ascii") for m in re.finditer(rb"[ -~]{4,}", chunk)]
    found += [m.group().decode("utf-16-le") for m in re.finditer(rb"(?:[ -~]\x00){4,}", chunk)]
    return found


def search_text(text, dirs):
    pat = regex([text.encode("ascii"), text.encode("utf-16-le")])
    n_files, hits = 0, []
    try:
        for top in dirs:
            for root, subdirs, files in os.walk(top):
                subdirs[:] = [d for d in subdirs if d.lower() not in SKIP_DIRS]
                print(f"\r{n_files:6d} fichiers, {len(hits)} trouves  {root[-60:]:<60}", end="", flush=True)
                for name in files:
                    if os.path.splitext(name)[1].lower() in SKIP_EXT:
                        continue
                    n_files += 1
                    path = os.path.join(root, name)
                    try:
                        size = os.path.getsize(path)
                        if size == 0 or size > MAX_SIZE:
                            continue
                        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                            ms = [m.start() for _, m in zip(range(5), pat.finditer(data))]
                            if ms:
                                hits.append((path, size, [strings_around(data, i) for i in ms[:2]]))
                    except (OSError, ValueError):
                        continue
    except KeyboardInterrupt:
        pass
    print(f"\n{n_files} fichiers lus, {len(hits)} contiennent {text!r}.")
    with open("catalogue_rapport.txt", "w", encoding="utf-8") as f:
        for path, size, groups in hits[:60]:
            f.write(f"=== {path}  ({size} octets)\n")
            for g in groups:
                f.write("    " + " | ".join(g)[:1500] + "\n")
            f.write("\n")
    print("Rapport : catalogue_rapport.txt (a envoyer)")
    for path, _, groups in hits[:15]:
        print(f"   {path}")
        print(f"      {' | '.join(groups[0])[:300]}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["--texte"] and len(args) >= 2:
        dirs = args[2:] or [d for d in DEFAULT_DIRS if os.path.isdir(d)]
        return search_text(args[1], dirs)
    dirs = args or [d for d in DEFAULT_DIRS if os.path.isdir(d)]
    if not dirs:
        raise SystemExit("Aucun dossier Siemens trouve : donner le dossier d'installation de STEP 7 en argument.")
    order_re, code_re, code_of = build()
    results, n_files, t0, interrupted = [], 0, time.time(), False
    try:
        for top in dirs:
            for root, subdirs, files in os.walk(top):
                subdirs[:] = [d for d in subdirs if d.lower() not in SKIP_DIRS]
                print(f"\r{n_files:6d} fichiers, {len(results)} trouves  {root[-60:]:<60}", end="", flush=True)
                for name in files:
                    if os.path.splitext(name)[1].lower() in SKIP_EXT:
                        continue
                    n_files += 1
                    r = scan_file(os.path.join(root, name), order_re, code_re, code_of)
                    if r:
                        results.append(r)
    except KeyboardInterrupt:
        interrupted = True
    print(f"\n{n_files} fichiers lus en {time.time() - t0:.0f} s, {len(results)} contiennent des references.")
    write_report(dirs, n_files, results, interrupted)


if __name__ == "__main__":
    main()
