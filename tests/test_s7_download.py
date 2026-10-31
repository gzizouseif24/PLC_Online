"""S7 block download (restore): PDU layouts (Snap7) and the CPU-driven sequence, on a fake CPU."""
import importlib.util
import io
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from plc_online.protocols.s7_download import (
    DownloadError, Sequence, S7OnlineChannel, ack_data_pdu, control_params, parse_pdu, prepare_block,
    request_download_params, request_pdu, INSERT)
from tests.test_siemens import make_block


class FakeCpu:
    """Answers like an S7-300: ack to request download, asks the data in slices of what the PC
    sends, then 'download ended', then ack to the insert. Keeps the received blocks."""

    def __init__(self, refuse=False):
        self.out, self.pending, self.loaded, self.refuse, self.inserted = [], [], {}, refuse, []
        self.cur, self.buf, self.deleted = None, b"", []

    def send(self, pdu):
        p = parse_pdu(pdu)
        fun = p["params"][:1]
        if p["type"] == 1 and fun == b"\x1a":
            self.cur, self.buf = p["params"][9:18], b""
            err = 0xD20E if self.refuse else 0
            self.pending.append(ack_data_pdu(p["ref"], b"\x1a") if not err else
                                struct.pack(">BBHHHHH", 0x32, 3, 0, p["ref"], 0, 0, err))
            if not err:
                self.pending.append(request_pdu(0x7000, b"\x1b" + bytes(7) + b"\x09" + self.cur))
        elif p["type"] == 3 and fun == b"\x1b":
            n = struct.unpack_from(">H", p["data"])[0]
            self.buf += p["data"][4:4 + n]
            if p["params"][1] == 1:
                self.pending.append(request_pdu(0x7001, b"\x1b" + bytes(7) + b"\x09" + self.cur))
            else:
                self.pending.append(request_pdu(0x7002, b"\x1c" + bytes(7) + b"\x09" + self.cur))
        elif p["type"] == 3 and fun == b"\x1c":
            self.loaded[self.cur] = self.buf
        elif p["type"] == 1 and fun == b"\x28":
            (self.inserted if p["params"][-5:] == b"_INSE" else self.deleted).append(p["params"][13:19])
            self.pending.append(ack_data_pdu(p["ref"], b"\x28"))

    def receive(self):
        return self.pending.pop(0)


BLOCK = make_block(0x08, 1, bytes(range(256)) * 2)      # OB1, 584 bytes: several slices


class PduTests(unittest.TestCase):
    def test_request_download_like_snap7(self):
        p = request_download_params(0x38, 1, prepare_block(BLOCK))
        self.assertEqual(p[:9], bytes([0x1A, 0, 1, 0, 0, 0, 0, 0, 9]))
        self.assertEqual(p[9:18], b"_0800001P")
        self.assertEqual(p[18:], b"\x0d1000584000512")

    def test_insert_and_delete_params(self):
        self.assertEqual(control_params(0x43, 12, INSERT),
                         bytes([0x28, 0, 0, 0, 0, 0, 0, 0xFD, 0, 0x0A, 1, 0, 0x30, 0x43]) + b"00012" + b"\x50\x05_INSE")

    def test_checksum_zeroed_and_padding_cut(self):
        b = prepare_block(BLOCK + bytes(30))
        self.assertEqual(len(b), len(BLOCK))
        self.assertEqual(b[-10:-8], b"\x00\x00")
        self.assertEqual(BLOCK[-10:-8], b"\x12\x34")

    def test_header_of_ack_data(self):
        self.assertEqual(ack_data_pdu(0x1234, b"\x1c"), bytes.fromhex("3203 0000 1234 0001 0000 0000 1c"))

    def test_exchange_header_like_libnodave(self):
        h = S7OnlineChannel.header(6, 18)
        self.assertEqual((h[4], h[7], h[12], h[13], h[35]), (80, 2, 64, 6, 1))
        self.assertEqual(struct.unpack_from("<HHxHH", h, 14), (16642, 18, 18, 80))


class SequenceTests(unittest.TestCase):
    def test_download_in_slices_then_insert(self):
        cpu = FakeCpu()
        Sequence(cpu).download(0x38, 1, BLOCK + bytes(10), max_pdu=240)
        self.assertEqual(cpu.loaded[b"_0800001P"], prepare_block(BLOCK))
        self.assertEqual(cpu.inserted, [b"800001"])

    def test_refused(self):
        with self.assertRaisesRegex(DownloadError, "D20E"):
            Sequence(FakeCpu(refuse=True)).download(0x38, 1, BLOCK)


class FakePlc:
    def __init__(self, blocks, running=True):
        self.blocks, self.running, self.cpu, self.calls = blocks, running, FakeCpu(), []

    def list_blocks(self, kind):
        return sorted(n for k, n in self.blocks if k == kind)

    def is_running(self):
        return self.running

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def start(self):
        self.calls.append("start")

    def download_sequence(self):
        return Sequence(self.cpu)

    def upload_block(self, kind, n):
        name = b"_0" + bytes([{"OB": 0x38, "FC": 0x43, "FB": 0x45, "DB": 0x41, "SDB": 0x42}[kind]]) + f"{n:05d}".encode() + b"P"
        data = self.cpu.loaded.get(name, self.blocks[(kind, n)])
        return data[:-10] + b"\x12\x34" + data[-8:]       # the CPU computes the checksum again


class RestoreCommandTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "tools" / "siemens.py"
        spec = importlib.util.spec_from_file_location("siemens_cli_restore", path)
        self.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cli)

    def run_restore(self, plc, answers, **kw):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "OB1.bin").write_bytes(BLOCK)
            (Path(d) / "FC5.bin").write_bytes(make_block(0x0C, 5, b"\x02" * 10))
            (Path(d) / "DB10.bin").write_bytes(make_block(0x0A, 10, bytes(8)))
            out = io.StringIO()
            with redirect_stdout(out), mock.patch("builtins.input", side_effect=answers):
                self.cli.cmd_restaurer(plc, d, **kw)
        return out.getvalue()

    def test_restore_code_blocks_stop_verify_start(self):
        plc = FakePlc({("OB", 1): make_block(0x08, 1, b"\x09" * 4), ("FC", 5): make_block(0x0C, 5, b"\x00"),
                       ("FC", 7): make_block(0x0C, 7, b"\x00"), ("DB", 10): make_block(0x0A, 10, b"\x05" * 8)})
        out = self.run_restore(plc, ["OUI", "OUI"])
        self.assertIn("a charger (2) : FC5 OB1", out)
        self.assertIn("DB non chargees", out)
        self.assertIn("FC7  -> gardes", out)
        self.assertEqual(plc.calls, ["stop", "start"])
        self.assertEqual(sorted(plc.cpu.loaded), [b"_0800001P", b"_0C00005P"])
        self.assertIn("blocs de la copie identiques", out)

    def test_cancel_changes_nothing(self):
        plc = FakePlc({("OB", 1): BLOCK})
        out = self.run_restore(plc, ["non"])
        self.assertIn("Annule", out)
        self.assertEqual((plc.calls, plc.cpu.loaded), ([], {}))

    def test_with_db_and_delete_extra(self):
        plc = FakePlc({("OB", 1): BLOCK, ("FC", 7): make_block(0x0C, 7, b"\x00"), ("DB", 10): make_block(0x0A, 10, bytes(8))},
                      running=False)
        out = self.run_restore(plc, ["OUI", "non"], with_db=True, delete_extra=True)
        self.assertIn("a charger (3) : DB10 FC5 OB1", out)
        self.assertEqual(plc.cpu.deleted, [b"C00007"])
        self.assertEqual(plc.calls, [])
        self.assertIn("CPU laissee en STOP", out)


if __name__ == "__main__":
    unittest.main()
