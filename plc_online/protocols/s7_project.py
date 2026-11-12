"""Blocks of a STEP 7 V5 project folder (the folder with the .s7p file), rebuilt as the CPU
holds them, so they can be compared or loaded like a copy taken from the CPU.

Where STEP 7 keeps them (layout from DotNetSiemensPLCToolBoxLibrary, BlocksOfflineFolder.cs):
  ombstx/offline/BSTCNTOF.DBF        one row per S7 program: ID, NAME
  ombstx/offline/<ID 8 hex>/BAUSTEIN.DBF   one row per block: ID, TYP (08 OB, 0A DB, 0B SDB,
                                     0C FC, 0E FB), NUMMER
  ombstx/offline/<ID 8 hex>/SUBBLK.DBF     several rows per block (OBJECTID = block ID); the
                                     row with SUBBLKTYP = block type holds the compiled block:
                                     MC5CODE (code, or DB current values), SSBPART (interface),
                                     ADDINFO (network table), TIMESTAMP1/2, USERNAME, BLOCKNAME,
                                     BLOCKFNAME, VERSION, BLKLANG, CHECKSUM...
Files are dBase with memo fields in the .DBT file next to them.

Block as the CPU holds it: header 36 | MC5CODE | SSBPART | ADDINFO | footer 36.
"""
import struct
from pathlib import Path

from plc_online.protocols.s7 import S7Error

TYPES = {0x08: "OB", 0x0A: "DB", 0x0B: "SDB", 0x0C: "FC", 0x0E: "FB"}


# ---- dBase ----

def _memo_reader(dbt: Path):
    if not dbt.exists():
        return lambda n: b""
    data = dbt.read_bytes()
    size = struct.unpack_from("<H", data, 20)[0] or 512          # dBase IV block length

    def read(n):
        pos = n * size
        if n <= 0 or pos + 8 > len(data):
            return b""
        if data[pos:pos + 2] == b"\xff\xff":                      # dBase IV: FF FF, start, length
            start, length = struct.unpack_from("<Hi", data, pos + 2)
            return data[pos + start:pos + length]
        end = data.find(b"\x1a\x1a", pos)                         # dBase III: ends with 1A 1A
        return data[pos:end if end >= 0 else len(data)]
    return read


def read_dbf(path) -> list:
    """Rows of a dBase file as dicts. C fields stay bytes (latin-1 text with S7 timestamps in
    it), N fields are int (or None), M fields are the memo bytes, 'DELETED' is a bool."""
    path = Path(path)
    data = path.read_bytes()
    count, header_len, record_len = struct.unpack_from("<IHH", data, 4)
    fields, pos = [], 32
    while pos < header_len - 1 and data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("latin-1")
        fields.append((name, chr(data[pos + 11]), data[pos + 16]))
        pos += 32
    memo = _memo_reader(next((p for p in path.parent.iterdir()
                              if p.name.lower() == path.stem.lower() + ".dbt"), path.with_suffix(".DBT")))
    rows = []
    for i in range(count):
        rec = data[header_len + i * record_len:header_len + (i + 1) * record_len]
        if len(rec) < record_len:
            break
        row, off = {"DELETED": rec[:1] == b"*"}, 1
        for name, kind, size in fields:
            raw = rec[off:off + size]
            off += size
            if kind in "NF":
                text = raw.strip(b" \0")
                try:
                    row[name] = int(text) if text else None
                except ValueError:
                    row[name] = float(text)
            elif kind == "M":
                text = raw.strip(b" \0")
                row[name] = memo(int(text)) if text.isdigit() else (memo(struct.unpack("<I", raw)[0]) if size == 4 else b"")
            elif kind == "L":
                row[name] = raw in (b"T", b"t", b"Y", b"y")
            else:
                row[name] = raw
        rows.append(row)
    return rows


def num(row: dict, name: str) -> int:
    """Numeric column (N field, or C field holding digits as in SUBBLKTYP '00008')."""
    v = row.get(name)
    if isinstance(v, bytes):
        v = v.strip(b" \0")
        return int(v) if v.isdigit() else 0
    return v or 0


def _find(folder: Path, name: str):
    """File in folder, whatever its case (projects copied from Windows)."""
    if not folder.is_dir():
        return None
    return next((p for p in folder.iterdir() if p.name.lower() == name.lower()), None)


def _text(raw: bytes) -> str:
    return raw.replace(b"\0", b"").decode("latin-1").strip()


# ---- project ----

def find_project(path) -> Path:
    """The project folder: the given folder, or the one holding the .s7p file (or ombstx) under it."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".s7p":
        return path.parent
    if _find(path, "ombstx"):
        return path
    if path.is_dir():
        found = sorted(p.parent for p in path.rglob("*") if p.suffix.lower() == ".s7p" and _find(p.parent, "ombstx"))
        found = found or sorted(p.parent for p in path.rglob("*") if p.is_dir() and p.name.lower() == "ombstx")
        if found:
            return found[0]
    raise S7Error(f"pas de projet STEP 7 V5 (dossier avec un .s7p et ombstx) dans {path}")


def is_project(path) -> bool:
    try:
        find_project(path)
        return True
    except S7Error:
        return False


def project_name(path) -> str:
    project = find_project(path)
    s7p = next((p for p in project.iterdir() if p.suffix.lower() == ".s7p"), None)
    return s7p.stem if s7p else project.name


def program_names(project: Path) -> dict:
    """{block folder ID: S7 program name ('S7-Programm(1)')} from hrs/S7RESOFF.DBF and
    hrs/linkhrs.lnk (512-byte records; the block folder ID follows 01 60 11 00)."""
    hrs = _find(project, "hrs")
    table, link = _find(hrs, "S7RESOFF.DBF") if hrs else None, _find(hrs, "linkhrs.lnk") if hrs else None
    if not table or not link:
        return {}
    data, out = link.read_bytes(), {}
    for row in read_dbf(table):
        if row["DELETED"]:
            continue
        record = data[num(row, "RSRVD4_L"):num(row, "RSRVD4_L") + 0x200]
        pos = record.find(b"\x01\x60\x11\x00")
        if pos >= 0 and pos + 6 <= len(record):
            out[struct.unpack_from("<H", record, pos + 4)[0]] = _text(row.get("NAME", b""))
    return out


def programs(project) -> list:
    """[{'id', 'nom', 'dossier'}] of the S7 programs (block folders) of the project."""
    project = find_project(project)
    names = program_names(project)
    offline = _find(_find(project, "ombstx"), "offline")
    if not offline:
        raise S7Error("projet sans dossier ombstx/offline")
    out, seen = [], set()
    table = _find(offline, "BSTCNTOF.DBF")
    for row in read_dbf(table) if table else []:
        if row["DELETED"] or row.get("ID") is None:
            continue
        folder = _find(offline, f"{row['ID']:08X}")
        if folder and _find(folder, "SUBBLK.DBF"):
            out.append({"id": row["ID"], "nom": names.get(row["ID"]) or _text(row.get("NAME", b"")) or f"{row['ID']:08X}",
                        "dossier": folder})
            seen.add(folder.name.upper())
    for folder in sorted(offline.iterdir()):                     # folders missing from the table
        if folder.is_dir() and folder.name.upper() not in seen and _find(folder, "SUBBLK.DBF"):
            out.append({"id": int(folder.name, 16) if all(c in "0123456789abcdefABCDEF" for c in folder.name) else 0,
                        "nom": folder.name, "dossier": folder})
    if not out:
        raise S7Error("aucun programme S7 dans le projet")
    twice = {p["nom"] for p in out if sum(q["nom"] == p["nom"] for q in out) > 1}
    for p in out:                                                # same name twice: add the folder ID
        if p["nom"] in twice:
            p["nom"] = f"{p['nom']} ({p['dossier'].name})"
    return out


def code_rows(folder) -> list:
    """[(kind, number, row)] : the SUBBLK row holding each compiled block of a program folder."""
    folder = Path(folder)
    blocks = {}
    for row in read_dbf(_find(folder, "BAUSTEIN.DBF")):
        if not row["DELETED"] and num(row, "TYP") in TYPES:
            blocks[num(row, "ID")] = (num(row, "TYP"), num(row, "NUMMER"))
    out = []
    for row in read_dbf(_find(folder, "SUBBLK.DBF")):
        b = blocks.get(num(row, "OBJECTID"))
        if b and not row["DELETED"] and num(row, "SUBBLKTYP") == b[0]:
            out.append((TYPES[b[0]], b[1], row))
    return out


def _stamp(raw: bytes) -> bytes:
    """6-byte S7 time stamp from the C field (dBase may have cut a trailing 0x20)."""
    return (raw + b" " * 6)[:6]


def _name(raw: bytes) -> bytes:
    """8-byte footer text as STEP 7 stored it (zero padded in projects, kept as is)."""
    return raw[:8].ljust(8, b"\0")


def _memo(row, field, length_field) -> bytes:
    data, n = row.get(field) or b"", num(row, length_field)
    return data[:n] if n else b""


def build_block(kind: str, number: int, row: dict) -> bytes:
    """The block as uploaded from a CPU, from its SUBBLK row."""
    code, iface, nets = _memo(row, "MC5CODE", "MC5LEN"), _memo(row, "SSBPART", "SSBLEN"), _memo(row, "ADDINFO", "ADDLEN")
    total = 36 + len(code) + len(iface) + len(nets) + 36
    type_code = next(t for t, k in TYPES.items() if k == kind)
    header = (b"\x70\x70" + bytes([num(row, "AGRESERVED") & 0xFF, num(row, "ATTRIBUTE") & 0xFF, num(row, "BLKLANG") & 0xFF, type_code])
              + struct.pack(">HII", number, total, num(row, "PASSWORD"))
              + _stamp(row.get("TIMESTAMP1") or b"") + _stamp(row.get("TIMESTAMP2") or b"")
              + struct.pack(">HHHH", len(iface), len(nets), num(row, "LOCDATALEN"), len(code)))
    footer = (_name(row.get("USERNAME") or b"") + _name(row.get("BLOCKFNAME") or b"") + _name(row.get("BLOCKNAME") or b"")
              + bytes([num(row, "VERSION") & 0xFF, 0]) + struct.pack("<H", num(row, "CHECKSUM") & 0xFFFF) + b"\0" * 8)
    return header + code + iface + nets + footer


def project_blocks(folder) -> dict:
    """{('OB', 1): bytes, ...} of a program folder (one entry of programs())."""
    return {(kind, n): build_block(kind, n, row) for kind, n, row in code_rows(folder)}


def load_project(path, name=None) -> tuple:
    """(blocks, program name) of the project; name picks the S7 program when there are several."""
    progs = programs(path)
    if name:
        chosen = [p for p in progs if p["nom"].lower() == str(name).lower()]
        if not chosen:
            raise S7Error(f"programme {name} absent du projet ({', '.join(p['nom'] for p in progs)})")
    elif len(progs) > 1:
        raise S7Error("plusieurs programmes dans le projet, choisir avec --programme : "
                      + ", ".join(p["nom"] for p in progs))
    else:
        chosen = progs
    blocks = project_blocks(chosen[0]["dossier"])
    if not blocks:
        raise S7Error(f"aucun bloc compile dans le programme {chosen[0]['nom']}")
    return blocks, chosen[0]["nom"]
