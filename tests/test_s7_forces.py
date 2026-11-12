"""S7-300 forces read through TIS user data (layout from the Wireshark S7 dissector)."""
import ctypes
import io
import importlib.util
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from plc_online.protocols.s7 import S7300
from plc_online.protocols.s7_forces import item_address, parse_force_job, parse_job_list, tis_request, value_text


def force_job_answer(items):
    """'read job' answer for a force job: [(address 6 bytes, transport size, length, value)]."""
    job = struct.pack(">H", len(items)) + b"".join(a for a, _, _, _ in items)
    for _, tsize, length, value in items:
        job += bytes([0xFF, tsize]) + struct.pack(">H", length) + value + (b"\x00" if len(value) % 2 else b"")
    return tis_request(data=struct.pack(">HH", 0, len(job)) + job)


A4_0 = bytes([0x20, 0, 0, 0, 0, 4])           # A4.0
MW10 = bytes([0x02, 1, 0, 0, 0, 10])          # MW10
DB5_DBD2 = bytes([0x73, 1, 0, 5, 0, 2])       # DB5.DBD2
JOB = force_job_answer([(A4_0, 3, 1, b"\x01"), (MW10, 4, 16, b"\x00\x2a"), (DB5_DBD2, 4, 32, b"\x00\x00\x01\x00")])


class ParseTests(unittest.TestCase):
    def test_addresses(self):
        self.assertEqual([item_address(a) for a in (A4_0, MW10, DB5_DBD2)], ["A4.0", "MW10", "DB5.DBD2"])
        self.assertEqual(item_address(bytes([0x10, 7, 0, 0, 0, 1])), "E1.7")
        self.assertEqual(item_address(bytes([0x70, 3, 0, 9, 0, 2])), "DB9.DBX2.3")
        self.assertEqual(item_address(bytes([0x54, 1, 0, 0, 0, 3])), "T3")

    def test_force_job(self):
        got = [(a, value_text(a, v)) for a, v in parse_force_job(JOB)]
        self.assertEqual(got, [("A4.0", "1"), ("MW10", "16#002A (42)"), ("DB5.DBD2", "16#00000100 (256)")])

    def test_job_list(self):
        self.assertEqual(parse_job_list(bytes([0x09, 0x01, 0, 0, 0x02, 0x03, 0, 1])), [(0x09, 1), (0x02, 3)])


class FakeTis:
    """daveBuildAndSendPDU answering read job list / read job like a CPU with one force job."""

    def __init__(self, jobs, job_answer):
        self.jobs, self.job_answer, self.sent, self.keep = jobs, job_answer, [], []

    def daveBuildAndSendPDU(self, dc, pdu_ref, pa, psize, ud, usize):
        sub, data = pa[6], bytes(ud[:usize])
        self.sent.append((pa[5], sub, data))
        if sub == 0x10:
            answer = tis_request(data=b"".join(bytes([f, s, 0, 0]) for f, s in self.jobs))
        else:
            answer = self.job_answer
        buf = ctypes.create_string_buffer(answer, len(answer))
        self.keep.append(buf)
        pdu = pdu_ref._obj
        pdu.udata, pdu.udlen = ctypes.addressof(buf), len(answer)
        return 0

    def daveReadSZL(self, dc, szl_id, index, buf, buflen):
        data = struct.pack(">HHHH", 0x0174, index, 4, 1) + bytes([0, index, 1 if index == 6 else 0, 0])
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


class PlcTests(unittest.TestCase):
    def test_forces_reads_force_jobs_only(self):
        fake = FakeTis([(0x02, 4), (0x09, 1)], JOB)
        forces = connected(fake).forces()
        self.assertEqual([(seq, [a for a, _ in items]) for seq, items in forces], [(1, ["A4.0", "MW10", "DB5.DBD2"])])
        self.assertEqual(fake.sent[0], (0x41, 0x10, bytes.fromhex("0000 0004 00000000")))
        self.assertEqual(fake.sent[1], (0x41, 0x11, bytes.fromhex("0000 0002 0901")))

    def test_forcages_command(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "siemens.py"
        spec = importlib.util.spec_from_file_location("siemens_cli_forces", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_forcages(connected(FakeTis([(0x09, 1)], JOB)))
        self.assertIn("Voyant FRCE : ALLUME", out.getvalue())
        self.assertIn("A4.0           = 1", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.cmd_forcages(connected(FakeTis([], JOB)))
        self.assertIn("Aucun forcage actif.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
