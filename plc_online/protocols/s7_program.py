"""S7-300 program as a set of blocks: copy out of the CPU, save / load a folder, compare,
load back. Shared by tools/siemens.py and the web interface.

A copy is a folder with one file per block as uploaded (OB1.bin, FC5.bin, DB10.bin, SDB0.bin...)
and programme.txt (date, CPU, one line per block).
"""
import re
from pathlib import Path

from plc_online.protocols.s7 import BLOCK_TYPES, PROGRAM_KINDS, S7Error, compare_blocks, parse_block

BLOCK_FILE = re.compile(r"^(OB|FB|FC|DB|SDB)(\d+)\.bin$", re.IGNORECASE)
RESTORE_ORDER = ["DB", "FC", "FB", "OB"]          # called blocks before the blocks that call them


def sort_key(key):
    kind, n = key
    return (PROGRAM_KINDS.index(kind) if kind in PROGRAM_KINDS else 9, n)


def upload_program(plc, progress=None) -> dict:
    """{("OB", 1): bytes, ...} for every block of PROGRAM_KINDS in the CPU.
    progress(kind, done, total) is called before each block."""
    blocks = {}
    for kind in PROGRAM_KINDS:
        numbers = plc.list_blocks(kind)
        for i, n in enumerate(numbers, 1):
            if progress:
                progress(kind, i, len(numbers))
            blocks[(kind, n)] = plc.upload_block(kind, n)
    return blocks


def load_program(folder, program_name=None) -> dict:
    """Blocks of a copy folder, or of a STEP 7 V5 project folder (program_name if it has several)."""
    from plc_online.protocols import s7_project
    if not any(BLOCK_FILE.match(f.name) for f in Path(folder).iterdir()) and s7_project.is_project(folder):
        return s7_project.load_project(folder, program_name)[0]
    blocks = {}
    for f in Path(folder).iterdir():
        m = BLOCK_FILE.match(f.name)
        if m:
            blocks[(m.group(1).upper(), int(m.group(2)))] = f.read_bytes()
    if not blocks:
        raise S7Error(f"aucun bloc (OB1.bin, DB10.bin...) dans {folder}")
    return blocks


def block_info(kind, n, data) -> dict:
    """{'bloc': 'OB1', 'type', 'numero', 'taille', 'date', 'somme'} for lists and tables."""
    try:
        b = parse_block(data)
        return {"bloc": f"{kind}{n}", "type": kind, "numero": n, "taille": b["longueur"],
                "date": b["date"], "somme": f"{b['somme']:04X}"}
    except S7Error:
        return {"bloc": f"{kind}{n}", "type": kind, "numero": n, "taille": len(data), "date": "", "somme": ""}


def block_line(kind, n, data) -> str:
    i = block_info(kind, n, data)
    if not i["date"]:
        return f"{kind:<3} {n:<5} {i['taille']:6d} octets  (format non reconnu)"
    return f"{kind:<3} {n:<5} {i['taille']:6d} octets  {i['date']}  somme {i['somme']}"


def save_program(folder, blocks: dict, header: list) -> Path:
    """Write the blocks and programme.txt; header = first lines ('date : ...', 'CPU  : ...')."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for (kind, n), data in sorted(blocks.items(), key=lambda kv: sort_key(kv[0])):
        (folder / f"{kind}{n}.bin").write_bytes(data)
        lines.append(block_line(kind, n, data))
    (folder / "programme.txt").write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return folder


def count_text(blocks) -> str:
    return ", ".join(f"{sum(1 for k in blocks if k[0] == kind)} {kind}" for kind in PROGRAM_KINDS
                     if any(k[0] == kind for k in blocks))


def restore_plan(plc, backup: dict, with_db=False) -> dict:
    """What a restore would do: blocks to load (in order) and blocks in the CPU missing from the copy."""
    kinds = RESTORE_ORDER if with_db else [k for k in RESTORE_ORDER if k != "DB"]
    todo = sorted((k for k in backup if k[0] in kinds), key=lambda k: (RESTORE_ORDER.index(k[0]), k[1]))
    current = {kind: set(plc.list_blocks(kind)) for kind in kinds}
    extra = sorted(((kind, n) for kind in kinds for n in current[kind] if (kind, n) not in backup),
                   key=lambda k: (RESTORE_ORDER.index(k[0]), k[1]))
    return {"kinds": kinds, "todo": todo, "extra": extra,
            "db_skipped": not with_db and any(k[0] == "DB" for k in backup)}


def restore(plc, backup: dict, plan: dict, delete_extra=False, progress=None, max_pdu=240) -> dict:
    """Load the planned blocks (CPU put in STOP first if it runs), delete the extra ones if asked,
    then read the program back and compare. Returns {'charges', 'supprimes', 'erreur',
    'differences', 'etait_en_run'}. The CPU is left in STOP: restarting is a separate decision."""
    from plc_online.protocols.s7_download import DELETE, DownloadError
    out = {"charges": [], "supprimes": [], "erreur": "", "differences": [], "etait_en_run": plc.is_running()}
    if out["etait_en_run"]:
        plc.stop()
    seq = plc.download_sequence()
    total = len(plan["todo"]) + (len(plan["extra"]) if delete_extra else 0)
    try:
        for i, (kind, n) in enumerate(plan["todo"], 1):
            if progress:
                progress(f"chargement {kind}{n}", i, total)
            seq.download(BLOCK_TYPES[kind], n, backup[(kind, n)], max_pdu)
            out["charges"].append(f"{kind}{n}")
        if delete_extra:
            for j, (kind, n) in enumerate(plan["extra"], len(plan["todo"]) + 1):
                if progress:
                    progress(f"suppression {kind}{n}", j, total)
                seq.control(BLOCK_TYPES[kind], n, DELETE)
                out["supprimes"].append(f"{kind}{n}")
    except DownloadError as e:
        out["erreur"] = str(e)
        return out
    if progress:
        progress("verification (relecture du programme)", total, total)
    kinds = plan["kinds"]
    now = upload_program(plc)
    diffs = [d for d in compare_blocks({k: v for k, v in backup.items() if k[0] in kinds},
                                       {k: v for k, v in now.items() if k[0] in kinds}) if d[2] != "valeurs"]
    if not delete_extra:
        diffs = [d for d in diffs if (d[0], d[1]) not in plan["extra"]]
    out["differences"] = [f"{k}{n} {v}" for k, n, v, _ in diffs]
    return out


def comparison(ref: dict, now: dict) -> dict:
    """{'identique', 'differences': [{bloc, verdict, detail}], 'valeurs_db': ['DB10', ...]}."""
    diffs = compare_blocks(ref, now)
    changes = [{"bloc": f"{k}{n}", "type": k, "numero": n, "verdict": v, "detail": d}
               for k, n, v, d in diffs if v != "valeurs"]
    return {"identique": not changes, "differences": changes,
            "valeurs_db": [f"DB{n}" for _, n, v, _ in diffs if v == "valeurs"]}
