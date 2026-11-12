#!/usr/bin/env python3
"""Compare des sauvegardes de programme TSX (.tsxbak) octet par octet.

Sert a apprendre le codage des instructions : deux programmes qui ne different que
d'une instruction (programmes_test/generer.py) ne different que la ou elle est codee.

    python research/tsx/compare_programmes.py P00_vide.tsxbak P01_M0_egal_I1_0.tsxbak
    python research/tsx/compare_programmes.py P01_*.tsxbak P02_*.tsxbak P03_*.tsxbak

Le premier fichier est la reference ; chaque autre fichier lui est compare.
"""
import difflib
import sys
from pathlib import Path


def hexs(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def show(ref: bytes, other: bytes, context: int = 8):
    """Print each changed region: offsets, reference bytes, new bytes, with context."""
    sm = difflib.SequenceMatcher(None, ref, other, autojunk=False)
    regions = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not regions:
        print("   identiques")
        return
    for tag, i1, i2, j1, j2 in regions:
        what = {"replace": "remplace", "delete": "supprime", "insert": "insere"}[tag]
        print(f"   {what:<9} ref {i1:5d}..{i2:5d} ({i2 - i1:3d} o)   nouveau {j1:5d}..{j2:5d} ({j2 - j1:3d} o)")
        print(f"      avant  : {hexs(ref[max(0, i1 - context):i1])}")
        print(f"      ref    : {hexs(ref[i1:i2]) or '-'}")
        print(f"      nouveau: {hexs(other[j1:j2]) or '-'}")
        print(f"      apres  : {hexs(ref[i2:i2 + context])}")


def main():
    files = [Path(f) for f in sys.argv[1:]]
    if len(files) < 2:
        raise SystemExit(__doc__)
    ref = files[0].read_bytes()
    print(f"Reference : {files[0].name} ({len(ref)} octets)")
    for f in files[1:]:
        data = f.read_bytes()
        print(f"\n{f.name} ({len(data)} octets, {len(data) - len(ref):+d})")
        show(ref, data)


if __name__ == "__main__":
    main()
