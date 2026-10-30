"""TSX tool against a simulated TSX 3721 using the answers captured on the real PLC."""
import contextlib
import importlib.util
import io
import struct
import unittest
import unittest.mock
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "tsx", Path(__file__).resolve().parents[1] / "tools" / "tsx.py")
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
        self.app = bytes((i * 7 + 3) % 256 for i in range(4512))   # same size as the bench application
        self.reserved = False
        self.upload_open = False
        self.eots = 0
        self.out = bytearray()
        self.next_poll = 1
        self.polled_by = []

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        self.eots += data == bytes([tsx.EOT])
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
            if (addr + n) * words > len(mem):
                return [0xFD]                                        # beyond the configured zone
            if code == tsx.READ_OBJECTS:
                data = [b for i in range(n * words) for b in struct.pack("<H", mem[addr * words + i])]
                return [0x66, typ, *data]
            if seg != 0x68:
                return [0xFD]
            raw = bytes(u[8:])
            assert len(raw) == 2 * n * words
            for i in range(n * words):
                mem[addr * words + i] = struct.unpack("<H", raw[2 * i:2 * i + 2])[0]
            self.writes = getattr(self, "writes", []) + [(addr, n)]
            return [0xFE]
        if code == tsx.OPEN_DOWNLOAD:
            assert u[1:] == [7, 0, 0, 0, 0, 0, 0, 0, 1] and self.mode == "STOP" and self.reserved
            self.incoming, self.next_seg = bytearray(), 1
            return [0x6A, 0x00]
        if code == tsx.WRITE_DOWNLOAD:
            seg, length = u[2] | u[3] << 8, u[4] | u[5] << 8
            assert seg == self.next_seg and len(u[6:]) == length <= 122
            self.incoming += bytes(u[6:])
            self.next_seg += 1
            return [0x6B, 0x00, seg & 0xFF, seg >> 8]
        if code == tsx.CLOSE_DOWNLOAD:
            self.app = bytes(self.incoming)
            return [0x6C, 0x00]
        if code == tsx.RESERVE:
            assert u[1:] in ([7, 0xFF, 0xFE, 0x90, 0x36], [7, 0xFF, 0xFE, 0x80, 0x0F])
            self.reserved = True
            return [0xFE]
        if code == tsx.RELEASE:
            self.reserved = False
            return [0xFE]
        if code == tsx.APP_INFO:
            return [0xC2, 0x0A, 0x00, *b"STATION", 0, 0, 0, 0, 0, 0, 0, 0x04, 0x30, 0x11, 0x02]
        if code == tsx.OPEN_UPLOAD:
            assert u[1:] == [7, 0, 0, 0, 0, 0, 0, 0, 0, 0x77, 0]   # as PL7 sends it
            self.upload_open = True
            return [0x6D, 0x00]
        if code == tsx.READ_UPLOAD:
            seg = u[2] | u[3] << 8
            chunk = self.app[(seg - 1) * 119:seg * 119]
            last = seg * 119 >= len(self.app)
            return [0x6E, 0x0F if last else 0x00, seg & 0xFF, seg >> 8, len(chunk) & 0xFF, len(chunk) >> 8, *chunk]
        if code == tsx.CLOSE_UPLOAD:
            self.upload_open = False
            return [0x6F, 0x00]
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

    def test_upload_reads_whole_application(self):
        blob = self.tsx.upload()
        self.assertEqual(blob, self.plc.app)
        self.assertFalse(self.plc.upload_open)

    def test_answer_polls_with_eot_while_waiting(self):
        class Slow(FakeTsx):
            """Answers only after polling the PC once more, like a busy PLC."""
            def write(self, data):
                if data == bytes([tsx.EOT]):
                    self.eots += 1
                    if self.pending:
                        self.out += self.pending
                        self.pending = None
                    return
                before = len(self.out)
                super().write(data)
                if data.startswith(bytes([tsx.DLE, tsx.STX])):
                    reply = bytes(self.out[before + len(data) + 1:])          # keep echo + ACK
                    del self.out[before + len(data) + 1:]
                    self.pending = reply
            pending = None
        plc = Slow()
        x = tsx.Tsx(tsx.Slave(plc, (1,)))
        self.assertEqual(x.read("SW", 11, 1), [250])
        self.assertGreaterEqual(plc.eots, 1)

    def test_sauvegarder_and_comparer(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "bench.tsxbak")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                tsx.cmd_sauvegarder(self.tsx, f)
                tsx.cmd_comparer(self.tsx, f)
            self.assertEqual(Path(f).read_bytes(), self.plc.app)
            self.assertIn("application     : STATION", Path(f + ".txt").read_text())
            self.assertIn("IDENTIQUE", out.getvalue())
            self.assertFalse(self.plc.reserved)
            self.plc.app = self.plc.app[:100] + b"X" + self.plc.app[101:]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                tsx.cmd_comparer(self.tsx, f)
            self.assertIn("1 octets differents", out.getvalue())
            self.assertIn("octet 100", out.getvalue())

    def test_sauvegarder_donnees_reads_up_to_the_end_of_each_zone(self):
        import os, tempfile
        self.plc.mw = [i * 3 for i in range(512)]   # 512 %MW and 128 %KW, as configured on the bench
        self.plc.kw = [0xFFFF] * 128
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "data.csv")
            with contextlib.redirect_stdout(io.StringIO()):
                tsx.cmd_sauvegarder_donnees(self.tsx, f)
            lines = Path(f).read_text().splitlines()
        self.assertIn("%MW;511;1533;1533;05FD", lines)
        self.assertIn("%KW;127;65535;-1;FFFF", lines)
        self.assertEqual(sum(l.startswith("%MW;") for l in lines), 512)
        self.assertEqual(sum(l.startswith("%KW;") for l in lines), 128)

    def test_restaurer_donnees_writes_only_changed_words(self):
        import os, tempfile
        self.plc.mw = [i for i in range(512)]
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "data.csv")
            with contextlib.redirect_stdout(io.StringIO()):
                tsx.cmd_sauvegarder_donnees(self.tsx, f)
            saved = list(self.plc.mw)
            for a in (3, 4, 5, 200, 511):            # the machine changed some words
                self.plc.mw[a] = 9999
            self.plc.writes = []
            out = io.StringIO()
            with contextlib.redirect_stdout(out), unittest.mock.patch("builtins.input", return_value="OUI"):
                tsx.cmd_restaurer_donnees(self.tsx, f)
        self.assertEqual(self.plc.mw, saved)
        self.assertEqual(self.plc.writes, [(3, 3), (200, 1), (511, 1)])
        self.assertIn("5 %MW ecrits et verifies", out.getvalue())

    def test_restaurer_donnees_cancel_writes_nothing(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "data.csv")
            Path(f).write_text("# automate;TSX 3721\nzone;adresse;valeur;signe;hex\n%MW;7;42;42;002A\n")
            self.plc.writes = []
            with contextlib.redirect_stdout(io.StringIO()), unittest.mock.patch("builtins.input", return_value="non"):
                tsx.cmd_restaurer_donnees(self.tsx, f)
        self.assertEqual(self.plc.writes, [])
        self.assertEqual(self.plc.mw[7], 0)

    def test_write_words_split_in_blocks_of_60(self):
        self.plc.writes = []
        self.tsx.write_words("MW", 10, list(range(130)))
        self.assertEqual(self.plc.writes, [(10, 60), (70, 60), (130, 10)])
        self.assertEqual(self.plc.mw[10:140], list(range(130)))

    def test_restaurer_puts_program_back_and_keeps_a_backup(self):
        import os, tempfile
        wanted = bytes((i * 13) % 256 for i in range(4576))   # 37 x 122 + 62, like the capture
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "prog.tsxbak")
            Path(f).write_bytes(b"STATION\0" + wanted[8:])
            old_program = self.plc.app
            cwd = os.getcwd()
            os.chdir(d)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out), unittest.mock.patch("builtins.input", return_value="OUI"):
                    tsx.cmd_restaurer(self.tsx, f)
                backups = [x for x in os.listdir(d) if x.startswith("avant_restauration_") and x.endswith(".tsxbak")]
                self.assertEqual(len(backups), 1)
                self.assertEqual(Path(d, backups[0]).read_bytes(), old_program)
            finally:
                os.chdir(cwd)
        self.assertEqual(self.plc.app, b"STATION\0" + wanted[8:])
        self.assertEqual(self.plc.next_seg - 1, 38)
        self.assertIn("Restauration OK", out.getvalue())
        self.assertFalse(self.plc.reserved)

    def test_restaurer_cancel_writes_nothing(self):
        import os, tempfile
        before = self.plc.app
        self.plc.mode = "RUN"
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "prog.tsxbak")
            Path(f).write_bytes(b"STATION\0" + bytes(500))
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()), unittest.mock.patch("builtins.input", return_value="non"):
                    tsx.cmd_restaurer(self.tsx, f)
            finally:
                os.chdir(cwd)
        self.assertEqual(self.plc.app, before)
        self.assertEqual(self.plc.mode, "RUN")      # no STOP sent when cancelled

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
