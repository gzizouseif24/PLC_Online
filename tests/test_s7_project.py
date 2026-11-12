"""STEP 7 V5 project import: dBase reading, block rebuild, program names, CLI and web import.

The project is written by the tests (same columns as STEP 7 5.x). The layout was checked byte
for byte against CPU copies of the DotNetSiemensPLCToolBoxLibrary unit test project (code blocks
identical, DB identical except the checksum the CPU recomputes)."""
import base64
import io
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from plc_online.protocols import s7_project
from plc_online.protocols.s7 import S7Error, parse_block
from plc_online.protocols.s7_program import load_program

ROOT = Path(__file__).resolve().parents[1]

SUBBLK_FIELDS = [("OBJECTID", "N", 11), ("BLKLANG", "C", 5), ("SUBBLKTYP", "C", 5), ("BLKNUMBER", "C", 5),
                 ("ATTRIBUTE", "N", 3), ("AGRESERVED", "N", 3), ("BLKTOTLEN", "N", 11), ("MC5LEN", "N", 11),
                 ("SSBLEN", "N", 11), ("ADDLEN", "N", 11), ("LOCDATALEN", "N", 5), ("TIMESTAMP1", "C", 6),
                 ("TIMESTAMP2", "C", 6), ("PASSWORD", "N", 11), ("USERNAME", "C", 8), ("BLOCKFNAME", "C", 8),
                 ("BLOCKNAME", "C", 8), ("VERSION", "N", 3), ("RESERVED1", "N", 3), ("CHECKSUM", "N", 5),
                 ("CPUTYPE", "N", 11), ("RESERVED2", "N", 11), ("MC5CODE", "M", 10), ("SSBPART", "M", 10),
                 ("ADDINFO", "M", 10)]


def write_dbf(path: Path, fields, rows):
    """dBase IV table (+ .DBT memo file when there are M fields), as STEP 7 writes them."""
    memo_blocks = [b"\0" * 512]
    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(f[2] for f in fields)
    out = bytearray(struct.pack("<BBBBIHH", 0x8B, 100, 1, 1, len(rows), header_len, record_len) + b"\0" * 20)
    for name, kind, size in fields:
        out += name.encode().ljust(11, b"\0") + kind.encode() + b"\0" * 4 + bytes([size, 0]) + b"\0" * 14
    out += b"\x0d"
    for row in rows:
        out += b"*" if row.get("DELETED") else b" "
        for name, kind, size in fields:
            v = row.get(name)
            if kind == "M":
                if not v:
                    raw = b" " * size
                else:
                    raw = str(len(memo_blocks)).encode().rjust(size)
                    block = b"\xff\xff\x08\x00" + struct.pack("<i", len(v) + 8) + v
                    block += b"\0" * (-len(block) % 512)
                    memo_blocks.append(block)
            elif kind == "N":
                raw = (b"" if v is None else str(v).encode()).rjust(size)
            else:
                raw = (v if isinstance(v, bytes) else str(v if v is not None else "").encode("latin-1")).ljust(size, b"\0")[:size]
            out += raw
    out += b"\x1a"
    path.write_bytes(bytes(out))
    if any(f[1] == "M" for f in fields):
        head = bytearray(memo_blocks[0])
        struct.pack_into("<I", head, 0, sum(len(b) // 512 for b in memo_blocks))
        struct.pack_into("<H", head, 20, 512)
        path.with_suffix(".DBT").write_bytes(bytes(head) + b"".join(memo_blocks[1:]))


# STEP 7 pads text columns with zeros (C fields above too)
# OB1 as the CPU gives it: header, MC7 code, interface, network table, footer
OB1_CODE = bytes.fromhex("7000 0b01 fb7c 4100 0a01 6510 0001")
OB1_IFACE = bytes.fromhex("01 0000 0600 0400 0000")
OB1_NETS = bytes.fromhex("0100 0e00")
STAMP1, STAMP2 = bytes.fromhex("0359 2d80 3cfd"), bytes.fromhex("04ef 6d80 1220")


def expected_ob1() -> bytes:
    total = 36 + len(OB1_CODE) + len(OB1_IFACE) + len(OB1_NETS) + 36
    header = (b"\x70\x70\x01\x01\x01\x08" + struct.pack(">HII", 1, total, 0) + STAMP1 + STAMP2
              + struct.pack(">HHHH", len(OB1_IFACE), len(OB1_NETS), 20, len(OB1_CODE)))
    footer = b"SEIF\0\0\0\0" + b"LIGNE\0\0\0" + b"CYCLE\0\0\0" + bytes([0x11, 0]) + bytes([0x34, 0x12]) + b"\0" * 8
    return header + OB1_CODE + OB1_IFACE + OB1_NETS + footer


def code_row(objid, typ, number, code, iface=b"", nets=b"", **extra):
    row = {"OBJECTID": objid, "BLKLANG": "00001", "SUBBLKTYP": f"{typ:05d}", "BLKNUMBER": f"{number:05d}",
           "ATTRIBUTE": 1, "AGRESERVED": 1, "MC5LEN": len(code), "SSBLEN": len(iface), "ADDLEN": len(nets),
           "LOCDATALEN": 0, "TIMESTAMP1": STAMP1, "TIMESTAMP2": STAMP2, "PASSWORD": 0, "VERSION": 1,
           "CHECKSUM": 0, "MC5CODE": code, "SSBPART": iface, "ADDINFO": nets}
    row.update(extra)
    return row


def make_project(root: Path, name="Banc", two_programs=False) -> Path:
    """Project folder like STEP 7 5.x: Banc/Banc.s7p, ombstx/offline, hrs."""
    project = root / name
    offline = project / "ombstx" / "offline"
    (offline / "00000001").mkdir(parents=True)
    (project / f"{name}.s7p").write_bytes(b"\0" * 96)
    write_dbf(offline / "BSTCNTOF.DBF", [("ID", "N", 11), ("NAME", "C", 24)],
              [{"ID": 1, "NAME": "Blocs"}] + ([{"ID": 2, "NAME": "Blocs"}] if two_programs else []))
    write_dbf(offline / "00000001" / "BAUSTEIN.DBF", [("ID", "N", 11), ("TYP", "C", 5), ("NUMMER", "C", 5)],
              [{"ID": 1, "TYP": "00008", "NUMMER": "00001"}, {"ID": 2, "TYP": "00010", "NUMMER": "00010"},
               {"ID": 3, "TYP": "00012", "NUMMER": "00005", "DELETED": True},
               {"ID": 4, "TYP": "00000", "NUMMER": "00001"}])                    # UDT1: not a CPU block
    write_dbf(offline / "00000001" / "SUBBLK.DBF", SUBBLK_FIELDS, [
        code_row(1, 3, 1, b"VAR_TEMP ... END_VAR"),                              # interface source text
        code_row(1, 8, 1, OB1_CODE, OB1_IFACE, OB1_NETS, LOCDATALEN=20, USERNAME="SEIF", BLOCKFNAME="LIGNE",
                 BLOCKNAME="CYCLE", VERSION=0x11, CHECKSUM=0x1234),
        code_row(2, 10, 10, bytes(range(8)), bytes.fromhex("05 0a00 0400 0200 0505"), BLKLANG="00005"),
        code_row(3, 12, 5, b"\x65\x00", DELETED=True),
        code_row(4, 0, 1, b"STRUCT END_STRUCT"),
    ])
    if two_programs:
        (offline / "00000002").mkdir()
        for f in ("BAUSTEIN.DBF", "SUBBLK.DBF", "SUBBLK.DBT"):
            (offline / "00000002" / f).write_bytes((offline / "00000001" / f).read_bytes())
        hrs = project / "hrs"
        hrs.mkdir()
        write_dbf(hrs / "S7RESOFF.DBF", [("ID", "N", 11), ("NAME", "C", 24), ("RSRVD4_L", "N", 11)],
                  [{"ID": 7, "NAME": "Presse", "RSRVD4_L": 0}, {"ID": 8, "NAME": "Four", "RSRVD4_L": 512}])
        link = bytearray(1024)
        link[40:46] = b"\x01\x60\x11\x00\x01\x00"
        link[512 + 40:512 + 46] = b"\x01\x60\x11\x00\x02\x00"
        (hrs / "linkhrs.lnk").write_bytes(bytes(link))
    return project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rebuilds_ob_as_the_cpu_holds_it(self):
        blocks, prog = s7_project.load_project(make_project(self.root))
        self.assertEqual(prog, "Blocs")
        self.assertEqual(sorted(blocks), [("DB", 10), ("OB", 1)])      # deleted FC5 and UDT1 left out
        self.assertEqual(blocks[("OB", 1)], expected_ob1())

    def test_db_block_parses(self):
        blocks, _ = s7_project.load_project(make_project(self.root))
        db = parse_block(blocks[("DB", 10)])
        self.assertEqual(db["longueur"], len(blocks[("DB", 10)]))
        self.assertEqual(blocks[("DB", 10)][4:8], b"\x05\x0a\x00\x0a")    # language DB, type DB, number 10

    def test_finds_project_from_parent_folder_or_s7p(self):
        make_project(self.root)
        self.assertEqual(s7_project.find_project(self.root).name, "Banc")
        self.assertEqual(s7_project.find_project(self.root / "Banc" / "Banc.s7p").name, "Banc")
        with self.assertRaises(S7Error):
            s7_project.find_project(self.root / "Banc" / "hrs")

    def test_program_names_from_hrs(self):
        project = make_project(self.root, two_programs=True)
        self.assertEqual([p["nom"] for p in s7_project.programs(project)], ["Presse", "Four"])
        with self.assertRaises(S7Error):                               # two programs: name needed
            s7_project.load_project(project)
        self.assertEqual(s7_project.load_project(project, "four")[1], "Four")

    def test_same_names_get_the_folder(self):
        project = make_project(self.root, two_programs=True)
        for f in (project / "hrs").iterdir():
            f.unlink()
        self.assertEqual([p["nom"] for p in s7_project.programs(project)], ["Blocs (00000001)", "Blocs (00000002)"])

    def test_load_program_accepts_a_project(self):
        project = make_project(self.root)
        self.assertEqual(load_program(project)[("OB", 1)], expected_ob1())

    def test_cli_importer_projet(self):
        make_project(self.root)
        out = subprocess.run([sys.executable, str(ROOT / "tools" / "siemens.py"), "importer-projet",
                              str(self.root / "Banc"), str(self.root / "copie")],
                             capture_output=True, text=True, cwd=self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual((self.root / "copie" / "OB1.bin").read_bytes(), expected_ob1())
        self.assertIn("programme Blocs", (self.root / "copie" / "programme.txt").read_text())


class WebImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backups = self.root / "sauvegardes"
        self.backups.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def project_files(self, **kw):
        (self.root / "src").mkdir()
        project = make_project(self.root / "src", **kw)
        return [(str(f.relative_to(self.root / "src")).replace("\\", "/"), f.read_bytes())
                for f in project.rglob("*") if f.is_file()]

    def test_folder_import(self):
        from plc_online.web.drivers import import_files
        r = import_files(self.backups, self.project_files(two_programs=True))
        self.assertEqual(r["projet"], "Banc")
        self.assertEqual([p["programme"] for p in r["programmes"]], ["Presse", "Four"])
        copy = self.backups / r["id"]
        self.assertEqual((copy / "OB1.bin").read_bytes(), expected_ob1())
        self.assertIn("Presse", (copy / "info.json").read_text(encoding="utf-8"))

    def test_zip_import_like_a_step7_archive(self):
        from plc_online.web.drivers import import_files
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in self.project_files():
                z.writestr(name, data)
            z.writestr("Banc/hOmSave7/gros.bin", b"x" * 100)               # not needed: skipped
        r = import_files(self.backups, [("Banc.zip", buf.getvalue())])
        self.assertEqual(r["programmes"][0]["comptes"], "1 OB, 1 DB")

    def test_api_import(self):
        from plc_online.web.drivers import Session
        session = Session(backups=self.backups)
        files = [(n, base64.b64decode(base64.b64encode(d))) for n, d in self.project_files()]
        r = session.import_backup(files, "projet client")
        self.assertEqual(session.list_backups()["sauvegardes"][0]["note"], "projet client")
        self.assertEqual(r["comptes"], "1 OB, 1 DB")


if __name__ == "__main__":
    unittest.main()
