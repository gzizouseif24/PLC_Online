"""Import a STEDS backup zip (the SRLDATA tables) into a SQLite database.

Usage from code:  import_zip("backup.zip", "data/steds.db", "backup.zip")
Only the tables listed in TABLES are read; everything else in the zip
(passwords, payroll, indexes, scratch tables) is ignored.
"""
import datetime as dt
import decimal
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from dbfread import DBF, FieldParser

ENCODING = "cp1252"  # STEDS tables are Windows ANSI (code page 1252)

# Table name in SRLDATA -> label shown in the app
TABLES = {
    "fclt": "Clients",
    "four": "Fournisseurs",
    "comrep": "Dossiers de réparation",
    "int": "Interventions internes",
    "inte": "Interventions externes",
    "sinte": "Pièces des interventions",
    "dev": "Devis",
    "sdev": "Lignes de devis",
    "bon": "Bons de livraison",
    "sbon": "Lignes de BL",
    "fact": "Factures",
    "sfact": "Lignes de factures",
    "avoir": "Avoirs",
    "sav": "Lignes d'avoirs",
    "fcpcl": "Comptes clients",
    "reg": "Règlements par facture",
    "fstk": "Articles en stock",
    "ent": "Achats locaux",
    "sent": "Lignes d'achats",
    "imp": "Importations",
    "simp": "Lignes d'importations",
    "machine": "Machines",
    "marque": "Marques",
    "ftyp": "Types",
    "etat": "Etats des dossiers",
    "fam": "Familles",
    "tech": "Techniciens",
    "finance": "Taux TVA et timbre",
    "soc": "Société",
}

# For sensitive tables, keep only these columns (tech holds salaries, ID and bank numbers)
KEEP_COLUMNS = {
    "tech": {"tcod", "tnopr", "tqual", "tfonc"},
}

# (table, column) indexes for fast search; skipped when the column doesn't exist
INDEXES = [
    ("fclt", "clcod"), ("fclt", "clrais"),
    ("comrep", "cn"), ("comrep", "ccl"),
    ("fact", "fn"), ("fact", "fcl"), ("sfact", "sfn"),
    ("bon", "bn"), ("bon", "bcl"), ("sbon", "sbn"),
    ("dev", "dn"), ("avoir", "an"), ("sav", "san"),
    ("inte", "icn"), ("sinte", "sicn"), ("int", "icn"),
    ("fcpcl", "cpcl"), ("reg", "rclt"), ("fstk", "stcod"),
]

SQL_TYPES = {
    "C": "TEXT", "M": "TEXT", "V": "TEXT", "D": "TEXT", "T": "TEXT",
    "L": "INTEGER", "I": "INTEGER", "N": "NUMERIC",
    "F": "REAL", "B": "REAL", "Y": "REAL",
}


class ImportFailed(Exception):
    """Error with a message meant for the user."""


class SafeParser(FieldParser):
    """Return None instead of crashing on a corrupt value (bad date, bad number)."""

    def parse(self, field, data):
        try:
            return super().parse(field, data)
        except (ValueError, decimal.InvalidOperation):
            return None


def import_zip(zip_path, db_path, source_name=""):
    """Build a fresh database from the zip, then swap it in place of the old one."""
    db_path = Path(db_path)
    tmp_db = db_path.with_name(db_path.name + ".new")
    if tmp_db.exists():
        tmp_db.unlink()

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise ImportFailed("Ce fichier n'est pas un zip valide.")

    try:
        with zf, tempfile.TemporaryDirectory() as work:
            stats = _build(zf, Path(work), tmp_db, source_name)
    except Exception:
        if tmp_db.exists():
            tmp_db.unlink()
        raise

    os.replace(tmp_db, db_path)
    return stats


def _build(zf, work, db_file, source_name):
    con = sqlite3.connect(db_file)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")

        stats = []
        for name, label in TABLES.items():
            member = _find(zf, name + ".dbf")
            if member is None:
                continue
            dbf_file = work / (name + ".dbf")
            _extract(zf, member, dbf_file)
            memo = _find_sibling(zf, member, ".fpt")
            if memo is not None:
                _extract(zf, memo, work / (name + ".fpt"))
            rows = _load_table(con, name, dbf_file)
            stats.append((name, label, rows, member.filename))

        found = {s[0] for s in stats}
        if not {"fclt", "fact"} <= found:
            raise ImportFailed(
                "Tables STEDS introuvables dans ce zip (fclt.dbf, fact.dbf…). "
                "Vérifiez qu'il contient le dossier SRLDATA."
            )

        _create_indexes(con)

        con.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO _meta VALUES (?, ?)", [
            ("imported_at", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")),
            ("source", source_name),
        ])
        con.execute("CREATE TABLE _tables (name TEXT, label TEXT, rows INTEGER, path TEXT)")
        con.executemany("INSERT INTO _tables VALUES (?, ?, ?, ?)", stats)
        con.commit()
        return stats
    finally:
        con.close()


def _load_table(con, name, path):
    """Copy one DBF table into SQLite. Deleted records are skipped, like STEDS does."""
    table = DBF(
        str(path),
        encoding=ENCODING,
        char_decode_errors="replace",
        ignore_missing_memofile=True,
        parserclass=SafeParser,
        load=False,
    )
    keep = KEEP_COLUMNS.get(name)
    fields = [
        f for f in table.fields
        if not f.name.lower().startswith("_nullflags")
        and (keep is None or f.name.lower() in keep)
    ]
    if not fields:
        return 0

    cols = [f.name.lower() for f in fields]
    col_defs = ", ".join(_q(c) + " " + SQL_TYPES.get(f.type, "TEXT") for c, f in zip(cols, fields))
    con.execute("DROP TABLE IF EXISTS " + _q(name))
    con.execute("CREATE TABLE " + _q(name) + " (" + col_defs + ")")

    sql = (
        "INSERT INTO " + _q(name)
        + " (" + ", ".join(_q(c) for c in cols) + ")"
        + " VALUES (" + ", ".join("?" * len(cols)) + ")"
    )
    names = [f.name for f in fields]
    batch, count = [], 0
    for record in table:
        batch.append([_to_sql(record.get(n)) for n in names])
        if len(batch) >= 2000:
            con.executemany(sql, batch)
            count += len(batch)
            batch.clear()
    if batch:
        con.executemany(sql, batch)
        count += len(batch)
    return count


def _create_indexes(con):
    for table, col in INDEXES:
        existing = {row[1] for row in con.execute("PRAGMA table_info(" + _q(table) + ")")}
        if col in existing:
            con.execute(
                "CREATE INDEX " + _q("ix_" + table + "_" + col)
                + " ON " + _q(table) + " (" + _q(col) + ")"
            )


def _to_sql(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    return None  # binary fields: not needed


def _norm(name):
    return name.replace("\\", "/").lower()


def _find(zf, filename):
    """Find a table in the zip. Prefers a SRLDATA folder, then the shallowest path, then the newest."""
    candidates = [
        zi for zi in zf.infolist()
        if not zi.is_dir() and _norm(zi.filename).rsplit("/", 1)[-1] == filename
    ]
    if not candidates:
        return None

    def rank(zi):
        folders = _norm(zi.filename).split("/")[:-1]
        newest_first = tuple(-x for x in zi.date_time)
        return ("srldata" not in folders, len(folders), newest_first)

    return min(candidates, key=rank)


def _find_sibling(zf, member, ext):
    """The memo file (.fpt) sitting next to a table in the zip, whatever its case."""
    target = _norm(member.filename)[: -len(".dbf")] + ext
    for zi in zf.infolist():
        if _norm(zi.filename) == target:
            return zi
    return None


def _extract(zf, member, dest):
    """Copy one zip member to dest (fixed name: no path tricks from the zip)."""
    with zf.open(member) as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)


def _q(identifier):
    return '"' + identifier.replace('"', '""') + '"'