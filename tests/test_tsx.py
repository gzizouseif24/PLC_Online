"""TSX tool against a simulated TSX 3721 using the answers captured on the real PLC."""
import contextlib
import importlib.util
import io
import struct
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "tsx", Path(__file__).resolve().parents[1] / "scripts" / "tsx" / "tsx.py")
tsx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tsx)

IDENT = bytes.fromhex("FF 04 65 08") + b"TSX 3721" + bytes.fromhex("08 05 24 30 11 02 00 00")


class FakeTsx:
    """Polls 1..8 like the real TER port; answers like the TSX 3721 seen on the bench."""

    def __init__(self):
        self.mw = [0] * 200
        self.kw = [0] * 200
        self.sw = [0] * 200
        self.sw[11], self.sw[13] = 250, 254
        self.sw[49:54] = [0x0001, 0x0700, 0x0521, 0x0121, 0xA001]   # clock words as read
        self.sw[96:98] = [0x0000, 0xFFFF]                             # as read on the bench
        self.s = [0] * 128
        self.s[4], self.s[8], self.s[30], self.s[51] = 1, 1, 1, 1    # %S10 = 0: I/O fault
        self.mode = "STOP"
        self.out = bytearray()
        self.next_poll = 1
        self.polled_by = []

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        self.frames_sent = getattr(self, "frames_sent", 0) + data.startswith(bytes([tsx.DLE, tsx.STX]))
        self.out += data  # RS-485 echo
        evs = [e for e in tsx.Parser().feed(data) if e[0] == "frame"]
        if not evs:
            return
        addr, npdu = evs[0][1], evs[0][2]
        self.polled_by.append(addr)
        self.out.append(tsx.ACK)
        self.out += tsx.build_frame(addr, [0x20, 0, 0xFE, 0, 0, 0, *self.answer(list(npdu[6:]))])

    def answer(self, u):
        code = u[0]
        if code == tsx.IDENTIFICATION:
            return [0x3F, *IDENT]
        if code == tsx.READ_SYSTEM_BIT:
            g = u[2] | u[3] << 8
            g -= g % 8
            return [0x31, sum(self.s[g + i] << i for i in range(8))]
        if code in (tsx.READ_OBJECTS, tsx.WRITE_OBJECTS):
            seg, typ, addr, n = u[2], u[3], u[4] | u[5] << 8, u[6] | u[7] << 8
            mem = {0x68: self.mw, 0x69: self.kw, 0x6A: self.sw}.get(seg)
            if mem is None or typ not in (7, 8) or (seg == 0x6A and typ == 8):
                return [0xFD]
            words = 1 if typ == 7 else 2
            if code == tsx.READ_OBJECTS:
                data = [b for i in range(n * words) for b in struct.pack("<H", mem[addr * words + i])]
                return [0x66, typ, *data]
            if seg != 0x68:
                return [0xFD]
            raw = bytes(u[8:])
            for i in range(words):
                mem[addr * words + i] = struct.unpack("<H", raw[2 * i:2 * i + 2])[0]
            return [0xFE]
        if code in (tsx.RUN, tsx.STOP):
            self.mode = "RUN" if code == tsx.RUN else "STOP"
            return [0xFE]
        return [0xFD]

    def read(self, n):
        if not self.out:
            self.out += bytes([tsx.DLE, tsx.ENQ, self.next_poll])
            self.next_poll = self.next_poll % 8 + 1
        data, self.out = bytes(self.out[:n]), self.out[n:]
        return data


class TsxTests(unittest.TestCase):
    def setUp(self):
        self.plc = FakeTsx()
        self.tsx = tsx.Tsx(tsx.Slave(self.plc, (1, 2, 3)))

    def test_identification(self):
        self.assertEqual(self.tsx.identification()["nom"], "TSX 3721")

    def test_read_system_words_little_endian(self):
        self.assertEqual(self.tsx.read("SW", 11, 3), [250, 0, 254])

    def test_read_long_block_is_split(self):
        self.plc.mw[:150] = range(150)
        self.assertEqual(self.tsx.read("MW", 0, 150), list(range(150)))

    def test_read_double_words(self):
        self.plc.mw[4], self.plc.mw[5] = 0x5678, 0x1234   # %MD2 = %MW4 (low) + %MW5 (high)
        self.assertEqual(self.tsx.read("MD", 2, 1), [0x12345678])

    def test_read_system_bits_across_groups(self):
        self.assertEqual(self.tsx.read("S", 6, 6), [0, 0, 1, 0, 0, 0])  # %S6..%S11

    def test_write_word_and_read_back(self):
        self.tsx.write("MW", 10, -2)
        self.assertEqual(self.tsx.read("MW", 10, 1), [0xFFFE])

    def test_write_refused_outside_mw_md(self):
        with self.assertRaises(tsx.UniteError):
            self.tsx.write("SW", 11, 0)

    def test_run_stop(self):
        self.tsx.run()
        self.assertEqual(self.plc.mode, "RUN")
        self.tsx.stop()
        self.assertEqual(self.plc.mode, "STOP")

    def test_requests_use_several_addresses(self):
        self.tsx.read("MW", 0, 1)
        self.tsx.read("MW", 0, 1)
        self.tsx.read("MW", 0, 1)
        self.assertEqual(sorted(set(self.plc.polled_by)), [1, 2, 3])

    def test_stale_polls_are_ignored(self):
        # polls piled up while the user typed OUI: the frame must go out once, on a fresh poll
        self.plc.out += bytes([tsx.DLE, tsx.ENQ, 1]) * 5
        self.tsx.run()
        self.assertEqual(self.plc.frames_sent, 1)

    def test_refused_request(self):
        with self.assertRaisesRegex(tsx.UniteError, "FD"):
            self.tsx._ask([tsx.READ_OBJECTS, 0, 0x6B, 7, 0, 0, 1, 0], 0x66)

    def test_etat_reports_io_fault_and_clock(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tsx.cmd_etat(self.tsx)
        text = out.getvalue()
        self.assertIn("TSX 3721", text)
        self.assertIn("!!  %S10", text)
        self.assertIn("!!  %S51", text)
        self.assertIn("05:21:07", text)
        self.assertIn("250 ms", text)
        self.assertIn("aucune sauvegarde valide", text)   # %SW96 = 0 on the bench PLC
        self.assertIn("%SW97 = -1", text)

    def test_flash_backup_valid_with_words(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tsx.show_flash_backup([0x0042, 100])   # bit 6 valid, bit 1 done, no error
        self.assertIn("VALIDE", out.getvalue())
        self.assertIn("100 premiers %MW", out.getvalue())
        self.assertIn("sans erreur", out.getvalue())


if __name__ == "__main__":
    unittest.main()
