"""Watch table: addresses, value formats, grouped reads and writes (no PLC needed)."""
import ctypes
import io
import importlib.util
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from plc_online.protocols.s7 import S7300
from plc_online.protocols.s7_vars import AddressError, Var, load_table, plan_reads, s5time_text


class Memory:
    """Fake libnodave with I / Q / M / DB / T / C memory, for reads and writes."""
    AREAS = {0x81: "I", 0x82: "Q", 0x83: "M", 0x84: "DB", 0x1C: "C", 0x1D: "T"}

    def __init__(self):
        self.mem = {}
        self.reads = []

    def zone(self, area, db):
        return self.mem.setdefault((self.AREAS[area], db), bytearray(1024))

    def daveReadBytes(self, dc, area, db, start, count, buf):
        self.reads.append((self.AREAS[area], db, start, count))
        z = self.zone(area, db)
        size = 2 * count if self.AREAS[area] in ("T", "C") else count
        off = 2 * start if self.AREAS[area] in ("T", "C") else start
        ctypes.memmove(buf, bytes(z[off:off + size]), size)
        return 0

    def daveWriteBytes(self, dc, area, db, start, count, buf):
        self.zone(area, db)[start:start + count] = ctypes.string_at(buf, count)
        return 0

    def daveWriteBits(self, dc, area, db, start, count, buf):
        z = self.zone(area, db)
        byte, bit = divmod(start, 8)
        z[byte] = (z[byte] & ~(1 << bit)) | ((ctypes.string_at(buf, 1)[0] & 1) << bit)
        return 0

    def daveReadSZL(self, dc, szl_id, index, buf, buflen):
        data = struct.pack(">HHHH", 0x0174, index, 4, 1) + bytes([0, index, 1 if index == 4 else 0, 0])
        ctypes.memmove(buf, data, len(data))
        self.answer = len(data)
        return 0

    def daveGetAnswLen(self, dc):
        return self.answer

    def daveStrerror(self, code):
        return b"error"


def connected(fake):
    plc = S7300()
    plc.nd, plc.dc = fake, object()
    return plc


class AddressTests(unittest.TestCase):
    def test_german_and_english_mnemonics(self):
        for text, area, start, bit, size in [("E0.0", "I", 0, 0, 0), ("I1.7", "I", 1, 7, 0), ("AB4", "Q", 4, None, 1),
                                             ("QW4", "Q", 4, None, 2), ("MD10", "M", 10, None, 4),
                                             ("db10.dbx2.1", "DB", 2, 1, 0), ("Z3", "C", 3, None, 2), ("T5", "T", 5, None, 2)]:
            v = Var(text)
            self.assertEqual((v.area, v.start, v.bit, v.size), (area, start, bit, size), text)
        self.assertEqual(Var("DB10.DBW4").db, 10)

    def test_bad_addresses(self):
        for text in ("X1", "MW", "M1.8", "DB1.DBX3", "MW2:real", "MW2:foo"):
            with self.assertRaises(AddressError, msg=text):
                Var(text)

    def test_decode(self):
        self.assertEqual(Var("MW0").decode(b"\xff\xfe"), "-2")
        self.assertEqual(Var("MW0:ud").decode(b"\xff\xfe"), "65534")
        self.assertEqual(Var("MB0").decode(b"\xff"), "255")
        self.assertEqual(Var("MW0:hex").decode(b"\x12\x34"), "16#1234")
        self.assertEqual(Var("MB0:bin").decode(b"\xa5"), "2#1010_0101")
        self.assertEqual(Var("MD0:real").decode(struct.pack(">f", 21.5)), "21.5")
        self.assertEqual(Var("M0.2").decode(b"\x04"), "1")
        self.assertEqual(Var("T1").decode(b"\x21\x50"), "S5T#150S")      # base 1 s x BCD 150
        self.assertEqual(Var("Z1").decode(b"\x01\x23"), "C#123")

    def test_s5time(self):
        self.assertEqual(s5time_text(0x0050), "S5T#0S500MS")     # 50 x 10 ms
        self.assertEqual(s5time_text(0x1050), "S5T#5S")          # 50 x 100 ms
        self.assertEqual(s5time_text(0x3002), "S5T#20S")         # 2 x 10 s

    def test_encode(self):
        self.assertEqual(Var("MW0").encode("-2"), b"\xff\xfe")
        self.assertEqual(Var("MW0").encode("16#ABCD"), b"\xab\xcd")
        self.assertEqual(Var("MB0").encode("2#1010_0101"), b"\xa5")
        self.assertEqual(Var("MD0").encode("1.5"), struct.pack(">f", 1.5))
        self.assertEqual(Var("M0.0").encode("1"), b"\x01")
        for var, value in (("MB0", "256"), ("M0.0", "2"), ("T1", "5")):
            with self.assertRaises(AddressError):
                Var(var).encode(value)

    def test_reads_are_grouped(self):
        vs = [Var(t) for t in ("MW10", "M12.0", "MB14", "MW100", "E0.0", "EB1", "DB5.DBW0", "DB6.DBW0", "T1", "T3")]
        reads = sorted((a, db, s, n) for a, db, s, n, _ in plan_reads(vs))
        self.assertEqual(reads, [("DB", 5, 0, 2), ("DB", 6, 0, 2), ("I", 0, 0, 2), ("M", 0, 10, 5), ("M", 0, 100, 2),
                                 ("T", 0, 1, 3)])

    def test_table_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.txt"
            f.write_text("# machine 1\nE0.0   bouton marche\nMW10:hex  # etat\n\n", encoding="utf-8")
            vs = load_table(f)
            self.assertEqual([(v.name, v.format, v.label) for v in vs], [("E0.0", "bool", "bouton marche"), ("MW10", "hex", "")])
            f.write_text("E9\n", encoding="utf-8")
            with self.assertRaisesRegex(AddressError, "ligne 1"):
                load_table(f)


class PlcTests(unittest.TestCase):
    def setUp(self):
        self.fake = Memory()
        self.plc = connected(self.fake)
        self.fake.zone(0x83, 0)[10:12] = b"\x00\x2a"
        self.fake.zone(0x83, 0)[12] = 0x01
        self.fake.zone(0x1D, 0)[2:4] = b"\x10\x50"       # T1 = 5 s
        path = Path(__file__).resolve().parents[1] / "tools" / "siemens.py"
        spec = importlib.util.spec_from_file_location("siemens_cli_vars", path)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def test_read_vars_in_one_request_per_group(self):
        vs = [Var("MW10"), Var("M12.0"), Var("T1")]
        got = {v.name: v.decode(raw) for v, raw in self.plc.read_vars(vs).items()}
        self.assertEqual(got, {"MW10": "42", "M12.0": "1", "T1": "S5T#5S"})
        self.assertEqual(sorted(self.fake.reads), [("M", 0, 10, 3), ("T", 0, 1, 1)])

    def test_write_word_and_bit(self):
        self.plc.write_var(Var("MW20"), Var("MW20").encode("-1"))
        self.plc.write_var(Var("M12.3"), b"\x01")
        self.assertEqual(bytes(self.fake.zone(0x83, 0)[20:22]), b"\xff\xff")
        self.assertEqual(self.fake.zone(0x83, 0)[12], 0x09)

    def test_journal_shows_changes(self):
        vs = [Var("MW10"), Var("M12.0")]
        reads = iter([b"\x00\x2a\x01", b"\x00\x2b\x01"])
        self.fake.daveReadBytes = lambda dc, area, db, start, count, buf: ctypes.memmove(buf, next(reads), 3) and 0
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(self.cli.time, "sleep"), mock.patch.object(self.cli.os, "system"):
            self.cli.cmd_surveiller(self.plc, vs, journal=True, loops=2)
        self.assertIn("MW10           42 -> 43", out.getvalue())
        self.assertNotIn("M12.0          1 ->", out.getvalue())

    def test_ecrire_asks_and_reads_back(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", return_value="OUI"):
            self.cli.cmd_ecrire(self.plc, [(Var("MW10"), "100"), (Var("M12.3"), "1")])
        self.assertEqual(bytes(self.fake.zone(0x83, 0)[10:12]), b"\x00\x64")
        self.assertEqual(self.fake.zone(0x83, 0)[12], 0x09)
        self.assertIn("MW10           =            100   OK", out.getvalue())
        self.assertIn("ecrire MW10 42 M12.3 0", out.getvalue())
        self.assertIn("RUN", out.getvalue())

    def test_ecrire_cancelled(self):
        with redirect_stdout(io.StringIO()), mock.patch("builtins.input", return_value="non"):
            self.cli.cmd_ecrire(self.plc, [(Var("MW10"), "100")])
        self.assertEqual(bytes(self.fake.zone(0x83, 0)[10:12]), b"\x00\x2a")

    def test_maintenir_writes_until_stopped_then_restores(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", return_value="OUI"), \
                mock.patch.object(self.cli.time, "sleep"), mock.patch.object(self.cli.os, "system"):
            self.cli.cmd_ecrire(self.plc, [(Var("MW10"), "7"), (Var("M12.0"), "0")], hold=True, loops=3)
        self.assertIn("3 ecritures", out.getvalue())
        self.assertEqual(bytes(self.fake.zone(0x83, 0)[10:12]), b"\x00\x2a")     # old values back
        self.assertEqual(self.fake.zone(0x83, 0)[12] & 1, 1)


if __name__ == "__main__":
    unittest.main()
