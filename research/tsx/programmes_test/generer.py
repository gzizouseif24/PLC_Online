#!/usr/bin/env python3
"""Genere des programmes de test PL7 (.FEF) qui ne different que d'une instruction.

But : apprendre comment PL7 code chaque instruction dans le programme compile, en
comparant les sauvegardes (.tsxbak) de deux programmes voisins (compare_programmes.py).

Chaque fichier est TEST_A.FEF (meme configuration : TSX 3721 + 2 DEZ 32D2 + AEZ 414) avec
seulement le corps de la section MAST MAIN remplace.

    python research/tsx/programmes_test/generer.py

Sur le poste PL7, pour chaque fichier, dans l'ordre :
    1. PL7 : nouvelle application vide -> Fichier -> Importer une application -> Pxx_....FEF
    2. PL7 : transfert PC -> automate (automate en STOP)
    3. python tools/tsx.py sauvegarder Pxx_....tsxbak
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent

# nom : lignes du programme (ST). Une seule difference avec le programme de reference
# indique en commentaire ; P00 = programme vide, la reference de tout.
PROGRAMS = {
    "P00_vide": [],
    "P01_M0_egal_I1_0": ["%M0 := %I1.0;"],                       # base d'une bobine
    "P02_M1_egal_I1_0": ["%M1 := %I1.0;"],                       # vs P01 : adresse de la bobine
    "P03_M0_egal_I1_1": ["%M0 := %I1.1;"],                       # vs P01 : adresse du contact (bit)
    "P04_M0_egal_I2_0": ["%M0 := %I2.0;"],                       # vs P01 : adresse du contact (module)
    "P05_M0_egal_M5": ["%M0 := %M5;"],                           # vs P01 : contact bit interne
    "P06_M0_egal_not_I1_0": ["%M0 := NOT %I1.0;"],               # vs P01 : contact inverse
    "P07_M0_egal_I1_0_and_I1_1": ["%M0 := %I1.0 AND %I1.1;"],    # vs P01 : ET
    "P08_M0_egal_I1_0_or_I1_1": ["%M0 := %I1.0 OR %I1.1;"],      # vs P01 : OU
    "P09_deux_lignes": ["%M0 := %I1.0;", "%M1 := %I1.1;"],       # vs P01 : enchainement
    "P10_MW0_egal_5": ["%MW0 := 5;"],                            # mot : constante
    "P11_MW0_egal_300": ["%MW0 := 300;"],                        # vs P10 : constante plus grande
    "P12_MW0_egal_MW1": ["%MW0 := %MW1;"],                       # mot : copie
    "P13_MW0_egal_MW1_plus_MW2": ["%MW0 := %MW1 + %MW2;"],       # addition
    "P14_M0_egal_MW1_sup_10": ["%M0 := %MW1 > 10;"],             # comparaison
    "P15_if": ["IF %I1.0 THEN %MW0 := 1; END_IF;"],              # condition
    "P16_combinaison": ["%M0 := (%I1.0 OR %M0) AND NOT %I1.1;"],  # auto-maintien classique
}


def main():
    ref = (HERE / "TEST_A.FEF").read_bytes().decode("latin-1")
    lines = ref.split("\r\n")
    start = lines.index("PROGRAM", lines.index("ADDRESS = MAST MAIN ")) + 1
    end = lines.index("END_PROGRAM", start)
    for name, body in PROGRAMS.items():
        out = lines[:start] + [f"! {line}" for line in body] + lines[end:]
        (HERE / f"{name}.FEF").write_bytes("\r\n".join(out).encode("latin-1"))
        print(f"{name}.FEF")


if __name__ == "__main__":
    main()
