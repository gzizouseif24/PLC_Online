"""Siemens driver checks that run anywhere (the real link needs Windows + STEP 7)."""
import ctypes
import importlib.util
import io
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from plc_online.protocols.s7 import (
    DEFAULT_DLL, S7300, S7Error, load_dll, parse_identity, parse_protection, parse_szl, word_be)


def szl(szl_id, index, records):
    """SZL answer as the manual describes it: SZL-ID, INDEX, LENTHDR, N_DR, records."""
    size = len(records[0]) if records else 2
    return struct.pack(">HHHH", szl_id, index, size, len(records)) + b"".join(records)


def text(s, n):
    return s.encode("latin-1").ljust(n, b"\x00")


class FakeNodave:
    """Stands in for libnodave.dll: answers daveReadSZL from a table of SZL answers,
    built from the record layouts of Siemens manual A5E02790053-01, chapter 34."""

    def __init__(self):
        order = text("6ES7 314-6CG03-0AB0 ", 20)
        self.lists = {
            (0x0111, 1): szl(0x0111, 1, [struct.pack(">H", 1) + order + struct.pack(">HHH", 0, 3, 0)]),
            (0x0111, 6): szl(0x0111, 6, [struct.pack(">H", 6) + order + struct.pack(">HHH", 0, 0x5602, 0x0600)]),
            (0x0111, 7): szl(0x0111, 7, [struct.pack(">H", 7) + bytes(20) + struct.pack(">HHH", 0, 0x5602, 0x060B)]),
            (0x011C, 2): szl(0x011C, 2, [struct.pack(">H", 2) + text("CPU 314C-2 DP", 32)]),
            (0x011C, 5): szl(0x011C, 5, [struct.pack(">H", 5) + text("S C-X4U421302009", 32)]),
            (0x0232, 4): szl(0x0232, 4, [struct.pack(">11H", 4, 1, 0, 1, 2, 0, 1, 0x1234, 0x5678, 0x9ABC, 0xDEF0)
                                          + bytes(16)]),
        }
        # two diagnostic buffer entries, most recent first: DP station failure, then STOP by switch
        self.lists[(0x00A0, 0)] = szl(0x00A0, 0, [
            bytes.fromhex("39C4 C4 01 0001 0000 0003 0000") + bytes.fromhex("26 10 31 09 15 42 12 35"),
            bytes.fromhex("4303 FF 84 4304 0000 0000 0000") + bytes.fromhex("26 10 31 08 59 07 00 05"),
        ])
        self.led_state = {1: (0, 0), 4: (1, 0), 5: (0, 0), 6: (0, 0), 8: (0, 0), 11: (0, 0)}  # no BUS2F
        self.answer_len = 0
        self.calls = []

    def daveReadSZL(self, dc, szl_id, index, buf, buflen):
        self.calls.append((szl_id, index))
        if szl_id == 0x0174 and index in self.led_state:
            data = szl(0x0174, index, [bytes([0, index]) + bytes(self.led_state[index])])
        elif (szl_id, index) in self.lists:
            data = self.lists[(szl_id, index)]
        else:
            return 0x8104            # object does not exist
        ctypes.memmove(buf, data, len(data))
        self.answer_len = len(data)
        return 0

    def daveGetAnswLen(self, dc):
        return self.answer_len

    def daveStrerror(self, code):
        return b"the desired item is not available in the PLC"


def connected(fake):
    plc = S7300()
    plc.nd, plc.dc = fake, object()
    return plc


class SiemensTests(unittest.TestCase):
    def test_big_endian_word(self):
        self.assertEqual(word_be(bytes([0x04, 0xD2, 0, 0]), 0), 1234)  # MW0 from the report

    def test_dll_is_shipped(self):
        self.assertTrue(DEFAULT_DLL.is_file())

    @unittest.skipIf(ctypes.sizeof(ctypes.c_void_p) == 4 and hasattr(ctypes, "WinDLL"),
                     "32-bit Windows: the DLL can really load here")
    def test_clear_error_outside_32bit_windows(self):
        with self.assertRaises(S7Error):
            load_dll()


class SzlTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNodave()
        self.plc = connected(self.fake)

    def test_parse_header_and_records(self):
        index, recs = parse_szl(szl(0x0174, 4, [b"\x00\x04\x01\x00"]), 0x0174)
        self.assertEqual((index, recs), (4, [b"\x00\x04\x01\x00"]))

    def test_wrong_szl_id_is_rejected(self):
        with self.assertRaises(S7Error):
            parse_szl(szl(0x0111, 1, [b"\x00\x01"]), 0x0174)

    def test_identity(self):
        info = self.plc.identity()
        self.assertEqual(info["reference"], "6ES7 314-6CG03-0AB0")
        self.assertEqual(info["materiel"], "V2.6.0")
        self.assertEqual(info["firmware"], "V2.6.11")
        self.assertEqual(info["nom_module"], "CPU 314C-2 DP")
        self.assertEqual(info["numero_serie"], "S C-X4U421302009")
        self.assertNotIn("nom_automate", info)        # index 1 not answered: skipped

    def test_identity_of_old_cpu_315_2af03(self):
        """Raw answers from the bench CPU 315-2DP (6ES7 315-2AF03-0AB0), 31/10/2026."""
        idx1 = bytes.fromhex("0111 0001 001c 0001 0000 3645 5337 2033 3135 2d32 4146 3033 2d30 4142 3020 00c0 0001 0000")
        idx7 = bytes.fromhex("0111 0001 001c 0001 0007 2020 2020 2020 2020 2020 2020 2020 2020 2020 2020 00c0 5601 0200")
        info = parse_identity(parse_szl(idx1, 0x0111)[1], 1)
        info6 = parse_identity(parse_szl(idx1, 0x0111)[1], 6)          # index 6 answered like index 1
        info.update(parse_identity(parse_szl(idx7, 0x0111)[1], 7))
        self.assertEqual(info["reference"], "6ES7 315-2AF03-0AB0")
        self.assertEqual(info["firmware"], "V1.2.0")
        self.assertNotIn("materiel", info6)

    def test_protection_of_old_cpu(self):
        raw = bytes.fromhex("0232 0004 0028 0001 0004 0001 0000 0001 0002 0000 0000 5656 2a00 6647 0600 16d8"
                            + "00" * 16)
        p = parse_protection(parse_szl(raw, 0x0232)[1])
        self.assertEqual((p["cle"], p["protection_effective"], p["sommes_valides"]), ("RUN-P", 1, False))
        self.assertEqual(p["somme_programme"], (0x6647, 0x0600))

    def test_short_record_is_skipped(self):
        self.assertEqual(parse_identity([b"\x00\x01"]), {})

    def test_leds_only_those_the_cpu_has(self):
        leds = self.plc.leds()
        self.assertEqual(leds["RUN"], (1, 0))
        self.assertNotIn("BUS2F", leds)

    def test_protection_and_checksums(self):
        p = self.plc.protection()
        self.assertEqual(p["cle"], "RUN-P")
        self.assertEqual((p["protection_cle"], p["protection_parametree"], p["protection_effective"]), (1, 0, 1))
        self.assertEqual(p["somme_programme"], (0x9ABC, 0xDEF0))
        self.assertEqual(p["somme_config_materielle"], (0x1234, 0x5678))

    def test_diagnostic_buffer(self):
        d = self.plc.diagnostic_buffer()
        self.assertEqual(len(d), 2)
        self.assertEqual(d[0]["texte"], "Distributed I/Os: station failure, entering state")
        self.assertEqual(d[0]["date"], "31/10/2026 09:15:42.123")
        self.assertTrue(d[0]["entrant"])
        self.assertEqual(d[1]["texte"], "STOP caused by stop switch being activated")
        self.assertEqual(d[1]["classe"], "changement de mode")

    def test_unknown_event_and_old_dates(self):
        from plc_online.protocols.s7 import parse_date_and_time, parse_diag_entry
        self.assertEqual(parse_date_and_time(bytes.fromhex("98 01 02 03 04 05 99 90")), "02/01/1998 03:04:05.999")
        e = parse_diag_entry(bytes.fromhex("A123" + "00" * 10) + bytes.fromhex("26 10 31 00 00 00 00 00"))
        self.assertEqual((e["texte"], e["classe"]), ("", "evenement utilisateur libre"))

    def test_unsupported_list_raises(self):
        with self.assertRaisesRegex(S7Error, "SZL 0424"):
            self.plc.read_szl(0x0424, 0)


class SiemensCliTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "siemens.py"
        spec = importlib.util.spec_from_file_location("siemens_cli", path)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)
        self.fake = FakeNodave()

    def etat(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.cli.cmd_etat(connected(self.fake))
        return out.getvalue()

    def test_etat_running_without_fault(self):
        out = self.etat()
        self.assertIn("6ES7 314-6CG03-0AB0   CPU 314C-2 DP", out)
        self.assertIn("firmware V2.6.11", out)
        self.assertIn("Mode       : RUN", out)
        self.assertIn("aucun voyant de defaut allume", out)
        self.assertIn("Cle        : RUN-P", out)
        self.assertIn("sans mot de passe", out)
        self.assertIn("programme 9ABC-DEF0", out)

    def test_diagnostic_command(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.cli.cmd_diagnostic(connected(self.fake))
        out = out.getvalue()
        self.assertIn("2 evenement(s)", out)
        self.assertIn("31/10/2026 09:15:42.123  39C4  Distributed I/Os: station failure, entering state", out)

    def test_etat_shows_faults_and_forces(self):
        self.fake.led_state.update({1: (1, 0), 6: (1, 0), 11: (0, 1)})
        out = self.etat()
        self.assertIn("!! SF", out)
        self.assertIn("!! FRCE   FORCAGE ACTIF", out)
        self.assertIn("!! BUS1F", out)
        self.assertIn("(clignote)", out)


if __name__ == "__main__":
    unittest.main()
